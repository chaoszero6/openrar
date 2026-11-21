"""High-level archive operations shared by the GUI, the CLI and shell integration."""
from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime

from . import recovery, sfx, zipraw
from .formats import (Handler, SevenZipHandler, TarHandler, ZipHandler, comment_hidden_parts, compose_comment,
                      open_archive)
from .model import (ArchiveError, ArchiveLocked, CorruptArchive, CreateOptions, ExtractOptions, Interaction,
                    Reporter, UnsupportedOperation)
from .sink import DiskSink
from .util import gather_sources, normalize_arcname, strip_archive_ext
from .volumes import VolumeWriter
from .writers import SevenZipWriter, TarWriter, ZipWriter, format_from_path, make_writer

_null = Reporter()


# =========================================================================== helpers
def expand_selection(entries, selected) -> set[str] | None:
    """Selected names may be folders: return every entry name at or below them."""
    if selected is None:
        return None
    sel = {normalize_arcname(s) for s in selected}
    out = set()
    for e in entries:
        if e.name in sel or any(e.name.startswith(s + "/") for s in sel):
            out.add(e.name)
    return out


def _decode_name(rec) -> str:
    return rec.name.decode("utf-8" if rec.flags & 0x800 else "cp437", "replace")


def _under(name: str, names: set[str]) -> bool:
    n = name.rstrip("/")
    return n in names or any(n.startswith(x + "/") for x in names)


def _apply_renames(name: str, renames: dict[str, str]) -> str:
    n = name.rstrip("/")
    for old, new in renames.items():
        if n == old:
            return new
        if n.startswith(old + "/"):
            return new + n[len(old):]
    return n


def _rm_sources(sources):
    for abs_path, _arc, is_dir in sources:
        if not is_dir and os.path.isfile(abs_path):
            os.remove(abs_path)
    for abs_path, _arc, is_dir in sorted(sources, key=lambda s: -len(s[0])):
        if is_dir and os.path.isdir(abs_path):
            try:
                os.rmdir(abs_path)
            except OSError:
                pass


def _refresh_recovery(path: str):
    rp = recovery.record_path(path)
    if os.path.exists(rp):
        pct = recovery.read_header(rp)["percent"]
        recovery.create_record(path, pct)


# =========================================================================== create
def create(dest: str, paths: list[str], opts: CreateOptions, reporter: Reporter | None = None,
           ui: Interaction | None = None, sources=None) -> list[str]:
    """Create (or update, if it exists) an archive. Returns the files written."""
    reporter = reporter or Reporter()
    dest = os.path.abspath(dest)
    if opts.each_to_separate and sources is None and len(paths) > 1:
        outs = []
        ext = "exe" if opts.sfx else opts.format
        for p in paths:
            d = os.path.join(os.path.dirname(dest), strip_archive_ext(os.path.basename(p.rstrip("\\/")))
                             + "." + ext) if os.path.isfile(p) else \
                os.path.join(os.path.dirname(dest), os.path.basename(p.rstrip("\\/")) + "." + ext)
            sub = CreateOptions(**{**opts.__dict__, "each_to_separate": False})
            outs += create(d, [p], sub, reporter, ui)
        return outs
    if sources is None:
        sources = gather_sources(paths, opts.store_paths, opts.exclude, opts.include_empty_dirs)
    # never archive the archive itself
    sources = [s for s in sources if os.path.abspath(s[0]) != dest]
    if opts.sfx:
        opts.format = "zip"
    if os.path.exists(dest) and not opts.sfx and not opts.volume_size:
        update(dest, sources, opts, reporter, ui)
        outputs = [dest]
    else:
        outputs = _write_new(dest, sources, opts, reporter)
    _post_process(outputs, sources, opts, reporter, ui)
    return outputs


def _write_new(dest, sources, opts: CreateOptions, reporter: Reporter) -> list[str]:
    total = sum(os.path.getsize(a) for a, _n, d in sources if not d and os.path.isfile(a))
    reporter.set_total(total)
    tmp = dest + ".openrar-tmp"
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    if opts.volume_size and not opts.sfx:
        f = VolumeWriter(dest, opts.volume_size)
    else:
        f = open(tmp, "w+b")
        if opts.sfx:
            with open(sfx.stub_path(), "rb") as stub:
                shutil.copyfileobj(stub, f)
    ok = False
    try:
        w = make_writer(f, opts)
        try:
            for abs_path, arc, is_dir in sources:
                reporter.begin(arc)
                if is_dir:
                    w.add_dir(abs_path, arc)
                else:
                    w.add_file(abs_path, arc, reporter)
            if isinstance(w, ZipWriter):
                script = sfx.build_script(opts.sfx_options) if opts.sfx else ""
                w.set_comment(compose_comment(opts.comment, opts.lock, script))
            elif opts.comment:
                reporter.message(f"Note: {opts.format.upper()} archives cannot store comments; comment ignored.")
            if opts.lock and not isinstance(w, ZipWriter):
                reporter.message("Note: archive locking is only available for ZIP archives.")
        finally:
            w.close()
        ok = True
    finally:
        f.close()
        if not ok:
            if isinstance(f, VolumeWriter):
                for p in getattr(f, "paths", []):
                    _silent_remove(p)
            else:
                _silent_remove(tmp)
    if isinstance(f, VolumeWriter):
        return f.paths
    os.replace(tmp, dest)
    return [dest]


def _post_process(outputs, sources, opts: CreateOptions, reporter: Reporter, ui):
    if opts.recovery_percent:
        for o in outputs:
            recovery.create_record(o, opts.recovery_percent, reporter)
    if opts.test_after:
        reporter.begin("Testing archive")
        h = open_archive(outputs[0], opts.password or None, ui)
        try:
            errs = h.test(reporter)
        finally:
            h.close()
        if errs:
            raise ArchiveError("Test after archiving failed; source files were kept.\n" + "\n".join(errs))
    if opts.delete_after:
        _rm_sources(sources)


def _silent_remove(p):
    try:
        os.remove(p)
    except OSError:
        pass


# =========================================================================== update / modify
def update(path: str, sources, opts: CreateOptions, reporter: Reporter | None = None,
           ui: Interaction | None = None):
    reporter = reporter or Reporter()
    h = open_archive(path, opts.password or None, ui)
    try:
        _check_writable(h)
        existing = {e.name: e for e in h.entries()}
        add, src_names = [], set()
        for abs_path, arc, is_dir in sources:
            src_names.add(arc)
            ex = existing.get(arc)
            if is_dir:
                if ex is None and opts.update_mode != "freshen":
                    add.append((abs_path, arc, True))
                continue
            newer = ex is None or ex.mtime is None or \
                datetime.fromtimestamp(os.path.getmtime(abs_path)).timestamp() > ex.mtime.timestamp() + 2
            if opts.update_mode == "add" or (opts.update_mode in ("update", "sync") and newer) \
                    or (opts.update_mode == "freshen" and ex is not None and newer):
                add.append((abs_path, arc, False))
        remove = {arc for _a, arc, d in add if not d and arc in existing}
        if opts.update_mode == "sync":
            remove |= {n for n in existing if n not in src_names}
        if not add and not remove:
            reporter.message("Nothing to update.")
            return
        password = h.password or opts.password or None
        _rebuild(h, remove=remove, renames={}, additions=add, opts=opts, password=password, reporter=reporter,
                 ui=ui)
    finally:
        h.close()
    if opts.comment and isinstance(h, ZipHandler):
        set_comment(path, opts.comment, ui=ui)
    if opts.lock and isinstance(h, ZipHandler):
        lock(path, ui=ui)
    _refresh_recovery(path)


def _check_writable(h: Handler):
    if h.is_locked():
        raise ArchiveLocked(h.path)
    if not h.writable:
        raise UnsupportedOperation(f"{h.format} archives are read-only in OpenRAR. "
                                   "Use Convert to turn it into a ZIP or 7Z archive.")
    if getattr(h, "_fileobj", None) is not None:
        raise UnsupportedOperation("Split (multi-volume) archives cannot be modified. "
                                   "Convert them into a single archive first.")


def delete(path: str, names, password: str | None = None, reporter: Reporter | None = None,
           ui: Interaction | None = None):
    h = open_archive(path, password, ui)
    try:
        _check_writable(h)
        _rebuild(h, remove={normalize_arcname(n) for n in names}, renames={}, additions=[], opts=None,
                 password=h.password, reporter=reporter or Reporter(), ui=ui)
    finally:
        h.close()
    _refresh_recovery(path)


def rename(path: str, old: str, new: str, password: str | None = None, reporter: Reporter | None = None,
           ui: Interaction | None = None):
    old, new = normalize_arcname(old), normalize_arcname(new)
    h = open_archive(path, password, ui)
    try:
        _check_writable(h)
        if any(e.name == new for e in h.entries()):
            raise ArchiveError(f"'{new}' already exists in the archive")
        _rebuild(h, remove=set(), renames={old: new}, additions=[], opts=None, password=h.password,
                 reporter=reporter or Reporter(), ui=ui)
    finally:
        h.close()
    _refresh_recovery(path)


def add_files(path: str, paths: list[str], into: str = "", opts: CreateOptions | None = None,
              reporter: Reporter | None = None, ui: Interaction | None = None):
    """Add files/folders into a folder `into` of an existing archive (drag & drop)."""
    opts = opts or CreateOptions(format=format_from_path(path))
    sources = gather_sources(paths, "relative", opts.exclude, True)
    prefix = normalize_arcname(into)
    if prefix:
        sources = [(a, f"{prefix}/{n}", d) for a, n, d in sources]
    update(path, sources, opts, reporter, ui)


def _zip_prefix(path: str, records) -> bytes:
    """Bytes before the first entry (an SFX stub), preserved on rewrite."""
    if not records:
        with open(path, "rb") as f:
            head = f.read(2)
        if head == b"MZ":
            from .sfx import stub_path
            with open(stub_path(), "rb") as s:
                return s.read()
        return b""
    first = min(r.offset for r in records)
    if first <= 0:
        return b""
    with open(path, "rb") as f:
        return f.read(first)


def _rebuild(h: Handler, remove: set[str], renames: dict[str, str], additions, opts: CreateOptions | None,
             password, reporter: Reporter, ui, new_comment: str | None = None):
    path = h.path
    tmp = path + ".openrar-tmp"
    try:
        if isinstance(h, ZipHandler):
            _rebuild_zip(h, path, tmp, remove, renames, additions, opts, password, reporter, new_comment)
        elif isinstance(h, TarHandler):
            _rebuild_tar(h, path, tmp, remove, renames, additions, opts, reporter)
        elif isinstance(h, SevenZipHandler):
            _rebuild_7z(h, path, tmp, remove, renames, additions, opts, password, reporter, ui)
        else:
            raise UnsupportedOperation(f"Cannot modify {h.format} archives")
        h.close()
        os.replace(tmp, path)
    except BaseException:
        _silent_remove(tmp)
        raise


def _rebuild_zip(h, path, tmp, remove, renames, additions, opts, password, reporter, new_comment):
    with open(path, "rb") as f:
        records, raw_comment, _concat = zipraw.read_central_directory(f)
    keep = []
    for rec in records:
        nm = _decode_name(rec)
        if _under(nm, remove):
            continue
        new = _apply_renames(nm, renames)
        if nm.endswith("/"):
            new += "/"
        keep.append((rec, new.encode("utf-8") if new != nm else rec.name))
    comment = raw_comment if new_comment is None else new_comment.encode("utf-8")[:65535]
    reporter.set_total(sum(r.csize for r, _ in keep) + sum(os.path.getsize(a) for a, _n, d in additions if not d))
    reporter.begin("Copying unchanged files")
    with open(tmp, "wb") as out:
        zipraw.rewrite(path, out, keep, comment, prefix=_zip_prefix(path, records), reporter=reporter)
    if additions:
        o = CreateOptions(**{**(opts.__dict__ if opts else {}), "format": "zip", "password": password or ""})
        w = ZipWriter(tmp, o, mode="a")
        try:
            for abs_path, arc, is_dir in additions:
                reporter.begin(arc)
                if is_dir:
                    w.add_dir(abs_path, arc)
                else:
                    w.add_file(abs_path, arc, reporter)
        finally:
            w.close()


def _rebuild_tar(h: TarHandler, path, tmp, remove, renames, additions, opts, reporter):
    fmt = "tar" + (f".{h.compression}" if h.compression else "")
    o = CreateOptions(**{**(opts.__dict__ if opts else {}), "format": fmt, "password": ""})
    with open(tmp, "wb") as out:
        w = TarWriter(out, o)
        tf, stream, raw = h.open_tar()
        try:
            for m in tf:
                nm = m.name[2:] if m.name.startswith("./") else m.name
                if _under(nm, remove):
                    continue
                reporter.begin(nm)
                new = _apply_renames(nm, renames)
                if new != nm.rstrip("/"):
                    m.name = new
                    m.pax_headers = {k: v for k, v in m.pax_headers.items() if k != "path"}
                w.add_member(m, tf.extractfile(m) if m.isfile() else None)
            for abs_path, arc, is_dir in additions:
                reporter.begin(arc)
                if is_dir:
                    w.add_dir(abs_path, arc)
                else:
                    w.add_file(abs_path, arc, reporter)
        finally:
            tf.close()
            stream.close()
            raw.close()
            w.close()


def _rebuild_7z(h: SevenZipHandler, path, tmp, remove, renames, additions, opts, password, reporter, ui):
    stage = tempfile.mkdtemp(prefix="openrar-7z-")
    try:
        entries = h.entries()
        kept = [e for e in entries if not _under(e.name, remove)]
        sink = DiskSink(ExtractOptions(dest=stage, overwrite="always"), ui, reporter)
        reporter.begin("Unpacking unchanged files")
        errs = h.extract(sink, {e.name for e in kept}, reporter)
        if errs:
            raise ArchiveError("Cannot rewrite a damaged archive:\n" + "\n".join(errs))
        # keep existing name encryption; turn it on when the caller asked for it
        hdr_enc = bool(password) and (h.summary().header_encrypted or bool(opts and opts.encrypt_names))
        o = CreateOptions(**{**(opts.__dict__ if opts else {}), "format": "7z", "password": password or "",
                             "encrypt_names": hdr_enc})
        reporter.done = 0
        with open(tmp, "w+b") as out:
            w = SevenZipWriter(out, o)
            try:
                for e in kept:
                    p = os.path.join(stage, *e.name.split("/"))
                    arc = _apply_renames(e.name, renames)
                    reporter.begin(arc)
                    if e.is_dir:
                        w.add_dir(p, arc)
                    elif os.path.exists(p):
                        w.add_file(p, arc, reporter)
                for abs_path, arc, is_dir in additions:
                    reporter.begin(arc)
                    if is_dir:
                        w.add_dir(abs_path, arc)
                    else:
                        w.add_file(abs_path, arc, reporter)
            finally:
                w.close()
    finally:
        shutil.rmtree(stage, ignore_errors=True)


# =========================================================================== comment / lock
def _zip_only(h, what):
    if not isinstance(h, ZipHandler):
        raise UnsupportedOperation(f"{what} is supported for ZIP archives only ({h.format} has no place to store it).")


def set_comment(path: str, text: str, ui: Interaction | None = None, force: bool = False):
    h = open_archive(path, None, ui)
    try:
        _zip_only(h, "Archive comments")
        if h.is_locked() and not force:
            raise ArchiveLocked(path)
        locked, sfx_block = comment_hidden_parts(h.raw_comment())
        _rebuild(h, set(), {}, [], None, None, Reporter(), ui, new_comment=compose_comment(text, locked, sfx_block))
    finally:
        h.close()
    _refresh_recovery(path)


def lock(path: str, ui: Interaction | None = None):
    h = open_archive(path, None, ui)
    try:
        _zip_only(h, "Locking")
        if h.is_locked():
            return
        _locked, sfx_block = comment_hidden_parts(h.raw_comment())
        _rebuild(h, set(), {}, [], None, None, Reporter(), ui,
                 new_comment=compose_comment(h.comment(), True, sfx_block))
    finally:
        h.close()
    _refresh_recovery(path)


def get_sfx_options(path: str) -> dict:
    h = open_archive(path)
    try:
        if not isinstance(h, ZipHandler):
            return {}
        return sfx.parse_script(comment_hidden_parts(h.raw_comment())[1])
    finally:
        h.close()


def make_sfx(path: str, out: str | None = None, sfx_options: dict | None = None, ui=None,
             reporter: Reporter | None = None) -> str:
    """Convert an archive into a Windows self-extracting .exe."""
    reporter = reporter or Reporter()
    out = out or (strip_archive_ext(path) + ".exe")
    h = open_archive(path, None, ui)
    try:
        if isinstance(h, ZipHandler) and not h.is_sfx() and not any(e.encrypted for e in h.entries()):
            with open(path, "rb") as f:
                records, raw_comment, _ = zipraw.read_central_directory(f)
            locked, _ = comment_hidden_parts(h.raw_comment())
            comment = compose_comment(h.comment(), locked, sfx.build_script(sfx_options or {})).encode("utf-8")
            with open(sfx.stub_path(), "rb") as s:
                stub = s.read()
            tmp = out + ".openrar-tmp"
            with open(tmp, "wb") as o:
                zipraw.rewrite(path, o, [(r, r.name) for r in records], comment, prefix=stub, reporter=reporter)
            os.replace(tmp, out)
            return out
    finally:
        h.close()
    # other formats (or encrypted zips, which the stub can't decrypt): convert through a staging folder
    opts = CreateOptions(format="zip", level=3, sfx=True, sfx_options=sfx_options or {})
    convert(path, out, opts, reporter=reporter, ui=ui)
    return out


# =========================================================================== extract / test
def extract(path: str, opts: ExtractOptions, names=None, reporter: Reporter | None = None,
            ui: Interaction | None = None) -> tuple[list[str], list[str]]:
    reporter = reporter or Reporter()
    h = open_archive(path, opts.password or None, ui)
    try:
        sel = expand_selection(h.entries(), names)
        sink = DiskSink(opts, ui, reporter)
        errors = h.extract(sink, sel, reporter)
        opts.password = h.password or ""
        return sink.extracted, errors
    finally:
        h.close()


def smart_extract_dest(path: str, here: bool, password=None, ui=None) -> str:
    """'Extract Here' keeps a single top-level folder as-is, otherwise creates a folder."""
    parent = os.path.dirname(os.path.abspath(path))
    folder = os.path.join(parent, os.path.basename(strip_archive_ext(path)))
    if not here:
        return folder
    h = open_archive(path, password, ui)
    try:
        tops = {e.name.split("/", 1)[0] for e in h.entries()}
    finally:
        h.close()
    return parent if len(tops) <= 1 else folder


def test(path: str, password: str | None = None, reporter: Reporter | None = None,
         ui: Interaction | None = None) -> list[str]:
    reporter = reporter or Reporter()
    try:
        h = open_archive(path, password, ui)
    except CorruptArchive as exc:
        return [f"The archive is damaged: {exc}"]
    try:
        errors = h.test(reporter)
    except CorruptArchive as exc:
        errors = [f"The archive is damaged: {exc}"]
    finally:
        h.close()
    rp = recovery.record_path(path)
    if os.path.exists(rp):
        reporter.begin("Checking recovery record")
        try:
            intact, bad = recovery.verify(path)
            if not intact:
                errors.append(f"Recovery record reports {len(bad)} damaged sector(s); use Repair.")
        except ArchiveError as exc:
            errors.append(str(exc))
    return errors


# =========================================================================== repair / convert
def repair(path: str, out: str | None = None, reporter: Reporter | None = None) -> str:
    from . import ziprepair

    reporter = reporter or Reporter()
    folder, base = os.path.split(os.path.abspath(path))
    out = out or os.path.join(folder, "fixed." + base)
    if os.path.exists(recovery.record_path(path)):
        return recovery.repair(path, out, reporter)
    with open(path, "rb") as f:
        head = f.read(4)
    if head == b"PK\x03\x04" or path.lower().endswith(".zip"):
        if out.lower().endswith(".exe"):
            out = out[:-4] + ".zip"
        return ziprepair.salvage(path, out, reporter)
    raise ArchiveError("This archive has no recovery record. Without one, only damaged ZIP archives can be "
                       "salvaged. Add recovery records to important archives (Commands > Protect).")


def convert(src: str, dest: str, opts: CreateOptions, reporter: Reporter | None = None,
            ui: Interaction | None = None, delete_original: bool = False, password: str | None = None) -> list[str]:
    reporter = reporter or Reporter()
    stage = tempfile.mkdtemp(prefix="openrar-conv-")
    try:
        reporter.begin(f"Unpacking {os.path.basename(src)}")
        h = open_archive(src, password, ui)
        try:
            src_comment = h.comment()
            src_pw = h.password
            sink = DiskSink(ExtractOptions(dest=stage, overwrite="always"), ui, reporter)
            errs = h.extract(sink, None, reporter)
        finally:
            h.close()
        if errs:
            raise ArchiveError("Source archive has errors:\n" + "\n".join(errs))
        if not opts.comment and src_comment and opts.format == "zip":
            opts.comment = src_comment
        if opts.password == "<same>":
            opts.password = src_pw or ""
        reporter.done = 0
        items = [os.path.join(stage, n) for n in sorted(os.listdir(stage))]
        if os.path.exists(dest):
            os.remove(dest)
        outs = create(dest, items, opts, reporter, ui,
                      sources=gather_sources(items, "relative", opts.exclude, True, base_dir=stage))
        if delete_original and os.path.abspath(src) != os.path.abspath(dest):
            os.remove(src)
        return outs
    finally:
        shutil.rmtree(stage, ignore_errors=True)
