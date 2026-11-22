"""Read access to every supported archive format behind one interface."""
from __future__ import annotations

import bz2
import gzip
import io
import lzma
import os
import re
import shutil
import tarfile
import tempfile
import zlib
from datetime import datetime

import py7zr
import py7zr.exceptions as p7e
import pyzipper

from . import tools
from .model import (ArchiveError, ArchiveSummary, BadPassword, Cancelled, CorruptArchive, Entry,
                    Interaction, MissingTool, PasswordRequired, Reporter)
from .volumes import OffsetReader, VolumeReader, first_volume, is_volume, list_volumes

try:
    import zstandard
except ImportError:  # pragma: no cover
    zstandard = None

CHUNK = 1 << 20

# Markers stored in the ZIP archive comment (hidden from the user in the UI).
LOCK_MARKER = "[OpenRAR:locked]"
SFX_BEGIN = ";OpenRAR-SFX-begin"
SFX_END = ";OpenRAR-SFX-end"

MAGIC = [
    (b"7z\xbc\xaf\x27\x1c", "7z"),
    (b"Rar!\x1a\x07", "rar"),
    (b"PK\x03\x04", "zip"),
    (b"PK\x05\x06", "zip"),
    (b"PK\x07\x08", "zip"),
    (b"\x1f\x8b", "gz"),
    (b"BZh", "bz2"),
    (b"\xfd7zXZ\x00", "xz"),
    (b"\x28\xb5\x2f\xfd", "zst"),
]

EXTERNAL_EXTS = {".iso", ".cab", ".arj", ".lzh", ".lha", ".wim", ".z", ".cpio", ".rpm", ".deb", ".dmg",
                 ".vhd", ".vhdx", ".msi", ".chm", ".xar", ".udf", ".img", ".ace", ".uue", ".squashfs",
                 ".apfs", ".hfs", ".lzma", ".nsis", ".qcow2", ".vmdk", ".swm", ".esd", ".zipx", ".ar"}

ARCHIVE_EXTS = {".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".tbz2", ".tbz", ".xz", ".txz",
                ".zst", ".tzst", ".jar", ".apk", ".epub", ".docx", ".xlsx", ".pptx", ".odt", ".exe",
                ".cbz", ".cbr", ".cb7", ".nupkg", ".whl", ".vsix", ".appx", ".msix", ".xpi"} | EXTERNAL_EXTS

ZIP_METHODS = {0: "Store", 8: "Deflate", 9: "Deflate64", 12: "BZip2", 14: "LZMA", 93: "Zstd", 95: "XZ",
               98: "PPMd", 99: "AES"}


def comment_visible(text: str) -> str:
    """Strip internal markers (lock flag, SFX script) from an archive comment."""
    out, skip = [], False
    for line in text.splitlines():
        if line.strip() == SFX_BEGIN:
            skip = True
            continue
        if line.strip() == SFX_END:
            skip = False
            continue
        if skip or line.strip() == LOCK_MARKER:
            continue
        out.append(line)
    return "\n".join(out).strip("\n")


def comment_hidden_parts(text: str) -> tuple[bool, str]:
    """Return (locked, sfx_block) found in a comment."""
    locked = any(line.strip() == LOCK_MARKER for line in text.splitlines())
    m = re.search(re.escape(SFX_BEGIN) + r".*?" + re.escape(SFX_END), text, re.S)
    return locked, (m.group(0) if m else "")


def compose_comment(visible: str, locked: bool, sfx_block: str) -> str:
    parts = []
    if sfx_block:
        parts.append(sfx_block)
    if visible:
        parts.append(visible.replace("\r\n", "\n"))
    if locked:
        parts.append(LOCK_MARKER)
    return "\r\n".join(p.replace("\n", "\r\n").replace("\r\r\n", "\r\n") for p in parts)


# =========================================================================== base
class Handler:
    format = "?"
    writable = False        # supports delete / rename / add
    supports_comment = False
    supports_lock = False

    def __init__(self, path: str, password: str | None = None, ui: Interaction | None = None):
        self.path = path
        self.password = password or None
        self.ui = ui or Interaction()
        self._entries: list[Entry] | None = None

    # ---- listing
    def entries(self) -> list[Entry]:
        if self._entries is None:
            self._entries = self._with_password(self._list)
        return self._entries

    def _list(self) -> list[Entry]:
        raise NotImplementedError

    # ---- data access
    def extract(self, sink, names: set[str] | None = None, reporter: Reporter | None = None) -> list[str]:
        """Stream selected file entries (or all) into sink. Returns error messages."""
        entries = self.entries()
        sel = [e for e in entries if names is None or e.name in names]
        for e in sel:
            if e.is_dir:
                sink.make_dir(e)
        files = [e for e in sel if not e.is_dir]
        if reporter:
            reporter.set_total(sum(e.size for e in files))
        errors = self._with_password(lambda: self._extract(sink, files, reporter)) if files else []
        if hasattr(sink, "finish"):
            sink.finish()
        return errors

    def _extract(self, sink, files: list[Entry], reporter) -> list[str]:
        raise NotImplementedError

    def read_bytes(self, name: str, limit: int | None = None) -> bytes:
        from .sink import NullSink

        buf = io.BytesIO()

        class MemSink(NullSink):
            def open(self_inner, entry):
                return _LimitedWriter(buf, limit)

        e = [x for x in self.entries() if x.name == name and not x.is_dir]
        if not e:
            raise KeyError(name)
        try:
            self._with_password(lambda: self._extract(MemSink(), e, None))
        except _LimitReached:
            pass
        return buf.getvalue()

    def test(self, reporter: Reporter | None = None) -> list[str]:
        from .sink import NullSink

        return self.extract(NullSink(reporter), None, reporter)

    # ---- metadata
    def comment(self) -> str:
        return ""

    def is_locked(self) -> bool:
        return False

    def summary(self) -> ArchiveSummary:
        es = self.entries()
        vols = list_volumes(self.path) if is_volume(self.path) else [self.path]
        s = ArchiveSummary(path=self.path, format=self.format)
        s.volumes = len(vols)
        s.disk_size = sum(os.path.getsize(v) for v in vols if os.path.exists(v))
        s.files = sum(1 for e in es if not e.is_dir)
        s.dirs = sum(1 for e in es if e.is_dir)
        s.unpacked = sum(e.size for e in es if not e.is_dir)
        s.packed = sum((e.packed or 0) for e in es) or s.disk_size
        s.encrypted = any(e.encrypted for e in es) or bool(self.password)
        s.methods = sorted({e.method for e in es if e.method})
        s.comment = self.comment()
        s.locked = self.is_locked()
        from .recovery import record_path, read_header

        rp = record_path(self.path)
        if os.path.exists(rp):
            try:
                h = read_header(rp)
                s.recovery = f"present ({h['percent']:.1f}%, {os.path.basename(rp)})"
            except Exception:
                s.recovery = "present (unreadable)"
        return s

    def close(self):
        pass

    # ---- password retry loop
    def _with_password(self, fn):
        attempts = 0
        while True:
            try:
                return fn()
            except (PasswordRequired, BadPassword) as exc:
                attempts += 1
                if attempts > 5:
                    raise
                pw = self.ui.ask_password(self.path, str(exc) if isinstance(exc, BadPassword) else "")
                if not pw:
                    raise
                self.password = pw
                self.on_password_changed()

    def on_password_changed(self):
        pass


class _LimitReached(Exception):
    pass


class _LimitedWriter:
    def __init__(self, buf, limit):
        self.buf, self.limit = buf, limit

    def write(self, b):
        self.buf.write(b)
        if self.limit is not None and self.buf.tell() >= self.limit:
            raise _LimitReached()
        return len(b)

    def close(self):
        pass


def _copy_stream(src, dst, reporter=None):
    while True:
        chunk = src.read(CHUNK)
        if not chunk:
            break
        if dst is not None:
            dst.write(chunk)
        elif reporter:
            reporter.advance(len(chunk))


def _crc_check(entry: Entry, data_crc: int | None) -> bool:
    return entry.crc is None or data_crc is None or (entry.crc & 0xFFFFFFFF) == (data_crc & 0xFFFFFFFF)


class _CRCWriter:
    """Pass-through writer that tracks CRC32 so every format gets the same integrity check."""

    def __init__(self, inner):
        self.inner = inner
        self.crc = 0

    def write(self, b):
        self.crc = zlib.crc32(b, self.crc)
        if self.inner is not None:
            self.inner.write(b)
        return len(b)


def _deliver(sink, entry: Entry, opener, errors: list[str], reporter=None, check_crc=True):
    """Open sink for entry, stream data from opener(), verify CRC, close."""
    out = sink.open(entry)
    if out is None:
        return
    w = _CRCWriter(out)
    ok = True
    try:
        with opener() as src:
            _copy_stream(src, w)
        if check_crc and not _crc_check(entry, w.crc):
            ok = False
            errors.append(f"{entry.name}: CRC failed (file is corrupt)")
    except (Cancelled, PasswordRequired, BadPassword):
        sink.close(entry, out, ok=False)
        raise
    except Exception as exc:  # corrupt member
        ok = False
        errors.append(f"{entry.name}: {_explain(exc)}")
    sink.close(entry, out, ok=ok)


def _explain(exc: Exception) -> str:
    msg = str(exc) or exc.__class__.__name__
    if "CRC" in msg or "crc" in msg:
        return "CRC failed (file is corrupt)"
    return msg


# =========================================================================== ZIP
class ZipHandler(Handler):
    format = "ZIP"
    writable = True
    supports_comment = True
    supports_lock = True

    def __init__(self, path, password=None, ui=None, fileobj=None):
        super().__init__(path, password, ui)
        self._fileobj = fileobj
        self.zf = self._open()
        self.format = "ZIP (SFX)" if self.is_sfx() else "ZIP"

    def _open(self):
        try:
            return pyzipper.AESZipFile(self._fileobj if self._fileobj is not None else self.path, "r")
        except pyzipper.BadZipFile as exc:
            raise CorruptArchive(f"{os.path.basename(self.path)}: {exc}") from exc

    def is_sfx(self) -> bool:
        if self._fileobj is not None:
            return False
        with open(self.path, "rb") as f:
            return f.read(2) == b"MZ"

    def _list(self):
        out = []
        for zi in self.zf.infolist():
            name = zi.filename.rstrip("/")
            if not name:
                continue
            try:
                mt = datetime(*zi.date_time)
            except ValueError:
                mt = None
            enc = bool(zi.flag_bits & 1)
            method = ZIP_METHODS.get(zi.compress_type, str(zi.compress_type))
            if enc:
                method += "+AES" if getattr(zi, "wz_aes_version", None) or zi.compress_type == 99 else "+ZipCrypto"
            out.append(Entry(name=name, is_dir=zi.is_dir(), size=zi.file_size, packed=zi.compress_size,
                             mtime=mt, crc=None if zi.is_dir() else zi.CRC if zi.CRC else (0 if zi.file_size == 0 else None),
                             encrypted=enc, method=method, mode=(zi.external_attr >> 16) & 0o7777))
        return out

    def _info(self, name):
        try:
            return self.zf.getinfo(name)
        except KeyError:
            return self.zf.getinfo(name + "/")

    def _extract(self, sink, files, reporter):
        errors: list[str] = []
        pwd = self.password.encode() if self.password else None
        for e in files:
            zi = self._info(e.name)

            def opener(zi=zi):
                try:
                    return self.zf.open(zi, "r", pwd=pwd)
                except RuntimeError as exc:
                    m = str(exc).lower()
                    if "bad password" in m:
                        raise BadPassword("Wrong password") from exc
                    if "password" in m:
                        raise PasswordRequired(str(exc)) from exc
                    raise
            try:
                _deliver(sink, e, opener, errors, reporter, check_crc=False)
            except RuntimeError as exc:
                m = str(exc).lower()
                if "password" in m:
                    raise BadPassword("Wrong password") from exc
                raise
        return errors

    def raw_comment(self) -> str:
        c = self.zf.comment or b""
        try:
            return c.decode("utf-8")
        except UnicodeDecodeError:
            return c.decode("cp437")

    def comment(self):
        return comment_visible(self.raw_comment())

    def is_locked(self):
        return comment_hidden_parts(self.raw_comment())[0]

    def summary(self):
        s = super().summary()
        s.sfx = self.is_sfx()
        return s

    def close(self):
        self.zf.close()


# =========================================================================== 7z
class _SinkIO(py7zr.io.Py7zIO):
    def __init__(self, sink, entry, errors, out):
        self.sink, self.entry, self.errors = sink, entry, errors
        self.out = out
        self.w = _CRCWriter(out)
        self.n = 0

    def write(self, s):
        self.w.write(s)
        self.n += len(s)
        return len(s)

    def read(self, size=None):
        return b""

    def seek(self, offset, whence=0):
        return self.n

    def flush(self):
        pass

    def size(self):
        return self.n

    def close(self):
        if self.out is None:
            return
        ok = _crc_check(self.entry, self.w.crc)
        if not ok:
            self.errors.append(f"{self.entry.name}: CRC failed (file is corrupt)")
        self.sink.close(self.entry, self.out, ok=ok)
        self.out = None


class _SinkFactory(py7zr.io.WriterFactory):
    def __init__(self, sink, by_name, errors):
        self.sink, self.by_name, self.errors = sink, by_name, errors
        self.open_ios: list[_SinkIO] = []

    def create(self, filename):
        e = self.by_name.get(filename) or self.by_name.get(filename.replace("\\", "/"))
        if e is None:
            e = Entry(name=filename)
        out = self.sink.open(e)
        io_ = _SinkIO(self.sink, e, self.errors, out)
        self.open_ios.append(io_)
        return io_


class SevenZipHandler(Handler):
    format = "7Z"
    writable = True

    def __init__(self, path, password=None, ui=None, fileobj=None):
        super().__init__(path, password, ui)
        self._fileobj = fileobj
        self.header_encrypted = False
        self._solid = False

    def _sz(self):
        src = self._fileobj if self._fileobj is not None else self.path
        if self._fileobj is not None:
            self._fileobj.seek(0)
        try:
            return py7zr.SevenZipFile(src, "r", password=self.password)
        except p7e.PasswordRequired as exc:
            self.header_encrypted = True
            raise PasswordRequired("Archive headers are encrypted") from exc
        except (p7e.Bad7zFile, lzma.LZMAError, p7e.CrcError, p7e.DecompressionError) as exc:
            if self.password:
                self.header_encrypted = True
                raise BadPassword("Wrong password or corrupt archive") from exc
            raise CorruptArchive(str(exc)) from exc
        except Exception as exc:
            # with encrypted headers a wrong key yields garbage that fails to parse in many ways
            if self.password:
                raise BadPassword("Wrong password or damaged archive") from exc
            raise CorruptArchive(str(exc)) from exc

    def _list(self):
        with self._sz() as sz:
            enc = sz.needs_password()
            try:
                self._solid = bool(sz._is_solid())
            except Exception:
                self._solid = False
            try:
                self._methods = sz._get_method_names()
            except Exception:
                self._methods = []
            out = []
            infos = sz.list()
            # py7zr only knows folders by their attribute bits, which some writers omit: anything that other
            # entries live inside is a folder too
            parents = {fi.filename.rstrip("/").rsplit("/", 1)[0] for fi in infos if "/" in fi.filename.rstrip("/")}
            for fi in infos:
                is_dir = fi.is_directory or (not fi.uncompressed and fi.filename.rstrip("/") in parents)
                # in a solid block the whole block's size is reported on its first file only
                out.append(Entry(name=fi.filename.rstrip("/"), is_dir=is_dir, size=fi.uncompressed or 0,
                                 packed=None if self._solid else fi.compressed, mtime=fi.creationtime,
                                 crc=fi.crc32, encrypted=enc,
                                 method=",".join(self._methods)))
            return out

    def _extract(self, sink, files, reporter):
        errors: list[str] = []
        by_name = {e.name: e for e in files}
        factory = _SinkFactory(sink, by_name, errors)
        try:
            with self._sz() as sz:
                if sz.needs_password() and not self.password:
                    raise PasswordRequired("Password required")
                sz.extract(targets=list(by_name), factory=factory)
        except (Cancelled, PasswordRequired, BadPassword):
            raise
        except (lzma.LZMAError, p7e.CrcError, p7e.Bad7zFile, p7e.DecompressionError, ValueError, OSError,
                EOFError) as exc:
            # with AES, a wrong password surfaces as garbage input to the decompressor
            if self.password and len(factory.open_ios) <= 1:
                raise BadPassword("Wrong password") from exc
            errors.append(_explain(exc))
        for io_ in factory.open_ios:     # entries that never got closed are broken
            if io_.out is not None:
                errors.append(f"{io_.entry.name}: incomplete data")
                sink.close(io_.entry, io_.out, ok=False)
                io_.out = None
        if self.password and errors and all("CRC" in e for e in errors) and len(errors) == len(files):
            raise BadPassword("Wrong password")
        return errors

    def summary(self):
        s = super().summary()
        s.solid = self._solid
        s.header_encrypted = self.header_encrypted or (self.password is not None and self._header_needs_pw())
        return s

    def _header_needs_pw(self):
        src = self._fileobj if self._fileobj is not None else self.path
        if self._fileobj is not None:
            self._fileobj.seek(0)
        try:
            with py7zr.SevenZipFile(src, "r"):
                return False
        except p7e.PasswordRequired:
            return True
        except Exception:
            return False


# =========================================================================== TAR family
def _decompressor(kind: str, raw):
    if kind == "gz":
        return gzip.GzipFile(fileobj=raw, mode="rb")
    if kind == "bz2":
        return bz2.BZ2File(raw, "rb")
    if kind == "xz":
        return lzma.LZMAFile(raw, "rb")
    if kind == "zst":
        if zstandard is None:
            raise MissingTool("zstandard module is not available")
        return zstandard.ZstdDecompressor().stream_reader(raw, read_across_frames=True)
    return raw


class TarHandler(Handler):
    writable = True

    def __init__(self, path, password=None, ui=None, compression="", fileobj=None):
        super().__init__(path, password, ui)
        self.compression = compression
        self._fileobj = fileobj
        self.sfx_offset = fileobj.offset if isinstance(fileobj, OffsetReader) else 0
        self.format = "TAR" + (f".{compression.upper()}" if compression else "") + (" (SFX)" if self.sfx_offset else "")

    def is_sfx(self) -> bool:
        return bool(self.sfx_offset)

    def summary(self):
        s = super().summary()
        s.sfx = self.is_sfx()
        return s

    def close(self):
        if isinstance(self._fileobj, OffsetReader):
            self._fileobj.close()

    def _raw(self):
        if self._fileobj is not None:
            self._fileobj.seek(0)
            return _NoClose(self._fileobj)
        return open(self.path, "rb")

    def open_tar(self):
        raw = self._raw()
        stream = _decompressor(self.compression, raw)
        tf = tarfile.open(fileobj=stream, mode="r|")
        return tf, stream, raw

    def _list(self):
        tf, stream, raw = self.open_tar()
        out = []
        try:
            for m in tf:
                if not (m.isfile() or m.isdir() or m.issym() or m.islnk()):
                    continue
                out.append(Entry(name=m.name.rstrip("/").lstrip("./") if m.name.startswith("./") else m.name.rstrip("/"),
                                 is_dir=m.isdir(), size=m.size if m.isfile() else 0,
                                 packed=None, mtime=datetime.fromtimestamp(m.mtime) if m.mtime else None,
                                 method=self.format, mode=m.mode))
        except (tarfile.ReadError, EOFError, OSError, lzma.LZMAError, zlib.error) as exc:
            if not out:
                raise CorruptArchive(str(exc)) from exc
            out.append(Entry(name="<archive is truncated or damaged>", size=0))
        finally:
            tf.close()
            stream.close()
            raw.close()
        return out

    def _extract(self, sink, files, reporter):
        errors: list[str] = []
        want = {e.name: e for e in files}
        tf, stream, raw = self.open_tar()
        try:
            for m in tf:
                nm = m.name[2:] if m.name.startswith("./") else m.name
                nm = nm.rstrip("/")
                e = want.get(nm)
                if e is None:
                    continue
                if m.isfile():
                    _deliver(sink, e, lambda m=m: tf.extractfile(m), errors, reporter)
                elif m.issym() or m.islnk():
                    out = sink.open(e)
                    if out is not None:
                        out.write(m.linkname.encode("utf-8"))
                        sink.close(e, out)
                want.pop(nm, None)
                if not want:
                    break
        except (tarfile.ReadError, EOFError, OSError, lzma.LZMAError, zlib.error) as exc:
            errors.append(f"Archive data error: {exc}")
        finally:
            tf.close()
            stream.close()
            raw.close()
        for nm in want:
            errors.append(f"{nm}: missing from archive data")
        return errors


class _NoClose:
    def __init__(self, f):
        self._f = f

    def __getattr__(self, k):
        return getattr(self._f, k)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def close(self):
        pass


class SingleFileHandler(Handler):
    """Plain .gz / .bz2 / .xz / .zst that holds one compressed file."""

    def __init__(self, path, password=None, ui=None, compression="gz", fileobj=None):
        super().__init__(path, password, ui)
        self.compression = compression
        self.format = compression.upper()
        self._fileobj = fileobj

    def _inner_name(self):
        base = os.path.basename(self.path)
        root, ext = os.path.splitext(base)
        if self.compression == "gz":
            with self._raw() as f:
                hdr = f.read(10)
                if len(hdr) == 10 and hdr[3] & 0x08:
                    if hdr[3] & 0x04:
                        xlen = int.from_bytes(f.read(2), "little")
                        f.read(xlen)
                    name = bytearray()
                    while (c := f.read(1)) not in (b"", b"\x00") and len(name) < 1024:
                        name += c
                    if name:
                        return os.path.basename(name.decode("latin-1"))
        return root or "data"

    def _raw(self):
        if self._fileobj is not None:
            self._fileobj.seek(0)
            return _NoClose(self._fileobj)
        return open(self.path, "rb")

    def _list(self):
        size = 0
        if self.compression == "gz":
            with self._raw() as f:
                f.seek(-4, 2)
                size = int.from_bytes(f.read(4), "little")
        packed = os.path.getsize(self.path) if self._fileobj is None else None
        return [Entry(name=self._inner_name(), size=size, packed=packed,
                      mtime=datetime.fromtimestamp(os.path.getmtime(self.path)) if os.path.exists(self.path) else None,
                      method=self.format)]

    def _extract(self, sink, files, reporter):
        errors: list[str] = []
        for e in files:
            def opener():
                raw = self._raw()
                return _decompressor(self.compression, raw)
            _deliver(sink, e, opener, errors, reporter)
        return errors


# =========================================================================== RAR (read only)
class RarHandler(Handler):
    format = "RAR"

    def __init__(self, path, password=None, ui=None):
        super().__init__(path, password, ui)
        import rarfile

        self.rarfile = rarfile
        _configure_rarfile(rarfile)
        self.rf = None

    def _rf(self):
        rarfile = self.rarfile
        if self.rf is None:
            try:
                rf = rarfile.RarFile(self.path)
                if self.password:
                    rf.setpassword(self.password)
                elif rf.needs_password() and not rf.infolist():
                    raise PasswordRequired("Archive headers are encrypted")
                self.rf = rf
            except rarfile.NeedFirstVolume as exc:
                first = _first_rar_volume(self.path)
                if first and first != self.path and os.path.exists(first):
                    self.path = first
                    return self._rf()
                raise CorruptArchive("Open the first volume of this multi-volume archive") from exc
            except rarfile.NoCrypto as exc:
                raise MissingTool("This RAR archive has encrypted file names, and the AES module needed to "
                                  "read them is missing. Reinstall OpenRAR, or run: pip install cryptography") \
                    from exc
            except rarfile.PasswordRequired as exc:
                raise PasswordRequired("Archive headers are encrypted") from exc
            except rarfile.RarWrongPassword as exc:
                raise BadPassword("Wrong password") from exc
            except rarfile.BadRarFile as exc:
                if self.password:
                    raise BadPassword(str(exc)) from exc
                raise CorruptArchive(str(exc)) from exc
        return self.rf

    def on_password_changed(self):
        if self.rf is not None:
            self.rf.close()
        self.rf = None

    def _list(self):
        rf = self._rf()
        out = []
        for ri in rf.infolist():
            mt = getattr(ri, "mtime", None)
            if mt is None and ri.date_time:
                try:
                    mt = datetime(*ri.date_time)
                except (TypeError, ValueError):
                    mt = None
            if mt is not None and mt.tzinfo is not None:
                mt = mt.astimezone().replace(tzinfo=None)
            out.append(Entry(name=ri.filename.replace("\\", "/").rstrip("/"), is_dir=ri.is_dir(),
                             size=ri.file_size or 0, packed=ri.compress_size, mtime=mt,
                             crc=ri.CRC if not ri.is_dir() and ri.CRC else None,
                             encrypted=ri.needs_password(), method=f"RAR{ri.extract_version or ''}"))
        return out

    def _extract(self, sink, files, reporter):
        rarfile = self.rarfile
        rf = self._rf()
        errors: list[str] = []
        for e in files:
            ri = rf.getinfo(e.name)
            if ri.needs_password() and not self.password:
                raise PasswordRequired("Password required")

            def opener(ri=ri):
                try:
                    return rf.open(ri, pwd=self.password)
                except rarfile.RarCannotExec as exc:
                    raise MissingTool(_missing_rar_tool_msg()) from exc
                except rarfile.RarWrongPassword as exc:
                    raise BadPassword("Wrong password") from exc
            try:
                _deliver(sink, e, opener, errors, reporter, check_crc=False)
            except MissingTool:
                raise
            if errors and ("password" in errors[-1].lower() or "RarWrongPassword" in errors[-1]):
                errors.pop()
                raise BadPassword("Wrong password")
            if errors and "cannot exec" in errors[-1].lower():
                raise MissingTool(_missing_rar_tool_msg())
        return errors

    def comment(self):
        try:
            return self._rf().comment or ""
        except ArchiveError:
            return ""

    def summary(self):
        s = super().summary()
        try:
            rf = self._rf()
            s.solid = bool(rf.is_solid())
            s.volumes = len(rf.volumelist()) if rf.volumelist() else 1
            s.disk_size = sum(os.path.getsize(v) for v in rf.volumelist() if os.path.exists(v))
            s.header_encrypted = bool(getattr(rf, "_main", None) and getattr(rf._file_parser, "_hdrenc_main", None))
        except Exception:
            pass
        return s

    def close(self):
        if self.rf:
            self.rf.close()


def _first_rar_volume(path: str) -> str | None:
    m = re.match(r"(?i)^(.*\.part)(\d+)(\.rar)$", path)
    if m:
        return f"{m.group(1)}{1:0{len(m.group(2))}d}{m.group(3)}"
    m = re.match(r"(?i)^(.*)\.[r-z]\d\d$", path)
    if m:
        return m.group(1) + ".rar"
    return None


def _missing_rar_tool_msg():
    return ("Extracting compressed RAR data needs 7-Zip or UnRAR (both free). Install 7-Zip from "
            "https://www.7-zip.org or set its path in Options > Settings > Tools. "
            "On Windows 10/11 the built-in tar.exe is also used when available (no password support).")


def _configure_rarfile(rarfile):
    p = tools.find_unrar()
    if p:
        rarfile.UNRAR_TOOL = p
    s = tools.find_7z()
    if s:
        rarfile.SEVENZIP_TOOL = s
        rarfile.SEVENZIP2_TOOL = s
    b = tools.find_bsdtar()
    if b:
        rarfile.BSDTAR_TOOL = b
    try:
        rarfile.tool_setup(force=True)
    except rarfile.RarCannotExec:
        pass


# =========================================================================== external (7-Zip / bsdtar)
class ExternalHandler(Handler):
    """ISO, CAB, ARJ, LZH, WIM, DMG, ... read through 7-Zip (or bsdtar as a fallback)."""

    def __init__(self, path, password=None, ui=None):
        super().__init__(path, password, ui)
        self.seven = tools.find_7z()
        self.bsdtar = tools.find_bsdtar()
        if not self.seven and not self.bsdtar:
            raise MissingTool(f"Opening {os.path.splitext(path)[1]} archives requires 7-Zip. "
                              "Install it from https://www.7-zip.org (free).")
        self.format = os.path.splitext(path)[1].lstrip(".").upper() or "Archive"

    def _pw_arg(self):
        return [f"-p{self.password or ''}"]

    def _list(self):
        if self.seven:
            r = tools.run([self.seven, "l", "-slt", "-ba", "-sccUTF-8", *self._pw_arg(), self.path])
            text = r.stdout.decode("utf-8", "replace")
            err = r.stderr.decode("utf-8", "replace")
            if r.returncode != 0:
                if "Wrong password" in err or "password" in err.lower():
                    raise (BadPassword if self.password else PasswordRequired)("Password required")
                raise CorruptArchive(err.strip() or "7-Zip could not open the archive")
            out = []
            for block in re.split(r"\r?\n\r?\n", text):
                props = dict(line.split(" = ", 1) for line in block.splitlines() if " = " in line)
                if "Path" not in props:
                    continue
                isdir = props.get("Folder") == "+" or "D" in props.get("Attributes", "")[:1]
                mt = None
                if props.get("Modified"):
                    try:
                        mt = datetime.strptime(props["Modified"][:19], "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        pass
                crc = props.get("CRC")
                out.append(Entry(name=props["Path"].replace("\\", "/"), is_dir=isdir,
                                 size=int(props.get("Size") or 0), packed=int(props.get("Packed Size") or 0) or None,
                                 mtime=mt, crc=int(crc, 16) if crc else None,
                                 encrypted=props.get("Encrypted") == "+", method=props.get("Method", "")))
            return out
        r = tools.run([self.bsdtar, "-tf", self.path])
        if r.returncode != 0:
            raise CorruptArchive(r.stderr.decode("utf-8", "replace"))
        names = r.stdout.decode("utf-8", "replace").splitlines()
        return [Entry(name=n.rstrip("/"), is_dir=n.endswith("/")) for n in names if n.strip()]

    def _extract(self, sink, files, reporter):
        errors: list[str] = []
        stage = tempfile.mkdtemp(prefix="openrar-x-")
        try:
            names = [e.name for e in files]
            listfile = os.path.join(stage, "__list.txt")
            with open(listfile, "w", encoding="utf-8") as lf:
                lf.write("\n".join(n.replace("/", os.sep) for n in names))
            outdir = os.path.join(stage, "out")
            if reporter:
                reporter.begin("Unpacking with external tool...")
            if self.seven:
                r = tools.run([self.seven, "x", "-y", "-sccUTF-8", f"-o{outdir}", *self._pw_arg(), self.path,
                               f"-i@{listfile}", "-scsUTF-8"])
                err = r.stderr.decode("utf-8", "replace")
                if r.returncode != 0 and "Wrong password" in err:
                    raise BadPassword("Wrong password")
                if r.returncode not in (0, 1):
                    errors.append(err.strip())
            else:
                os.makedirs(outdir, exist_ok=True)
                r = tools.run([self.bsdtar, "-xf", self.path, "-C", outdir, *names])
                if r.returncode != 0:
                    errors.append(r.stderr.decode("utf-8", "replace").strip())
            for e in files:
                p = os.path.join(outdir, *e.name.split("/"))
                if not os.path.isfile(p):
                    errors.append(f"{e.name}: not extracted")
                    continue
                _deliver(sink, e, lambda p=p: open(p, "rb"), errors, reporter, check_crc=True)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        return errors


# =========================================================================== detection
def sniff(path: str, head: bytes | None = None) -> str | None:
    if head is None:
        with open(path, "rb") as f:
            head = f.read(512)
    for magic, kind in MAGIC:
        if head.startswith(magic):
            return kind
    if len(head) >= 262 and head[257:262] == b"ustar":
        return "tar"
    if head[:2] == b"MZ":
        return "exe"
    from .sfx_linux import is_linux_sfx
    if is_linux_sfx(head):
        return "sfx-sh"
    return None


def _tar_inside(raw_opener, kind) -> bool:
    try:
        with raw_opener() as raw:
            d = _decompressor(kind, raw)
            block = d.read(512)
            return len(block) >= 262 and (block[257:262] == b"ustar" or _plausible_v7_tar(block))
    except Exception:
        return False


def _plausible_v7_tar(block: bytes) -> bool:
    try:
        chk = int(block[148:156].split(b"\0")[0].strip() or b"0", 8)
    except ValueError:
        return False
    calc = sum(block[:148]) + 256 + sum(block[156:512])
    return chk == calc and chk != 256


def open_archive(path: str, password: str | None = None, ui: Interaction | None = None) -> Handler:
    """Open any supported archive and return a Handler."""
    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    fileobj = None
    if is_volume(path) and not re.search(r"(?i)\.rar$", path):
        path = first_volume(path)
        fileobj = VolumeReader(path)
        head = fileobj.read(512)
        fileobj.seek(0)
    else:
        with open(path, "rb") as f:
            head = f.read(512)
    kind = sniff(path, head)
    ext = os.path.splitext(path)[1].lower()

    def raw_opener():
        if fileobj is not None:
            fileobj.seek(0)
            return _NoClose(fileobj)
        return open(path, "rb")

    if kind == "zip" or (kind == "exe" and pyzipper.is_zipfile(fileobj or path)):
        return ZipHandler(path, password, ui, fileobj=fileobj)
    if kind == "7z":
        return SevenZipHandler(path, password, ui, fileobj=fileobj)
    if kind == "rar" or (kind in ("exe", None) and _rar_sfx(path)):
        return RarHandler(path, password, ui)
    if kind == "tar":
        return TarHandler(path, password, ui, "", fileobj=fileobj)
    if kind == "sfx-sh" and fileobj is None:
        from .sfx_linux import payload_offset
        try:
            return TarHandler(path, password, ui, "gz", fileobj=OffsetReader(path, payload_offset(path)))
        except ValueError as exc:
            raise CorruptArchive(str(exc)) from exc
    if kind in ("gz", "bz2", "xz", "zst"):
        if _tar_inside(raw_opener, kind):
            return TarHandler(path, password, ui, kind, fileobj=fileobj)
        return SingleFileHandler(path, password, ui, kind, fileobj=fileobj)
    if ext in EXTERNAL_EXTS or kind == "exe" or kind is None:
        if ext in EXTERNAL_EXTS or tools.find_7z():
            try:
                h = ExternalHandler(path, password, ui)
                h.entries()
                return h
            except (CorruptArchive, MissingTool) as exc:
                if ext in EXTERNAL_EXTS:
                    raise
                raise ArchiveError(f"{os.path.basename(path)} is not an archive or is damaged") from exc
    raise ArchiveError(f"{os.path.basename(path)} is not a recognised archive or is damaged")


def _rar_sfx(path: str) -> bool:
    try:
        import rarfile

        return rarfile.is_rarfile(path) or rarfile.is_rarfile_sfx(path)
    except Exception:
        return False


def looks_like_archive(path: str) -> bool:
    low = path.lower()
    if re.search(r"\.\d{3}$", low):
        return True
    ext = os.path.splitext(low)[1]
    if ext == ".exe":
        return False
    return ext in ARCHIVE_EXTS
