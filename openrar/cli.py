"""Command line interface (rar.exe style).

    orar <command> [-switches] <archive> [files...] [dest\\]

Commands
  a        add files to archive (creates it when missing)
  x        extract with full paths            e   extract without paths
  l        list contents                      t   test integrity
  d        delete files from archive          rn  rename: rn <archive> <old> <new>
  c        set archive comment (-z<file> or text after archive name)
  k        lock archive                       r   repair archive
  rr[N]    add recovery record of N percent (default 3)
  s        convert archive into a Windows self-extracting .exe
  cv       convert: cv [-fmt<format>] <source> <dest>
  i        show archive information

Switches
  -p<pwd>   password (encrypt / decrypt)       -hp<pwd>  also encrypt file names (7z)
  -m<0..5>  compression level (0 store .. 5 best)
  -mm<name> method: Deflate, BZip2, LZMA, LZMA2, PPMd, Zstandard, Store
  -fmt<f>   zip, 7z, tar, tar.gz, tar.bz2, tar.xz, tar.zst (default: from archive name)
  -v<size>  split into volumes, e.g. -v700m, -v4g
  -rr<N>    add a recovery record of N percent
  -sfx      create a self-extracting .exe         -k   lock archive
  -df       delete files after archiving          -t   test after archiving
  -u        update (add new + newer files)        -f   freshen (only newer existing)
  -as       synchronise archive with the files
  -x<mask>  exclude files matching mask (repeatable)
  -ep       store names without paths             -ep3 store full paths
  -z<file>  read archive comment from file
  -o+  overwrite existing   -o-  skip existing   -or  rename   -on  only newer
  -y   assume Yes on all queries
  -ad  extract into a folder named after the archive
  -here     smart extract here (folder only if the archive has several top-level items)
  -gui      show a graphical progress window instead of console output
"""
from __future__ import annotations

import getpass
import os
import sys

from .core import operations as ops
from .core import recovery
from .core.formats import open_archive
from .core.model import (ArchiveError, Cancelled, CreateOptions, ExtractOptions, Interaction, Reporter)
from .core.util import human_size, parse_size, strip_archive_ext
from .core.writers import format_from_path

COMMANDS = {"a", "x", "e", "l", "t", "d", "rn", "c", "k", "r", "s", "cv", "i"}


class ConsoleUI(Interaction):
    def __init__(self, yes=False, prompt_password=True):
        self.yes = yes
        self.prompt_password = prompt_password

    def ask_overwrite(self, path, entry):
        if self.yes or not sys.stdin or not sys.stdin.isatty():
            return "yes"
        while True:
            a = input(f"\n{path} already exists. Overwrite? [Y]es, [N]o, [A]ll, n[E]ver, [R]ename, [Q]uit: ").strip().lower()
            m = {"y": "yes", "n": "no", "a": "yes_all", "e": "no_all", "r": "rename", "q": "cancel"}.get(a[:1])
            if m:
                return m

    def ask_password(self, archive, entry=""):
        if not self.prompt_password or not sys.stdin or not sys.stdin.isatty():
            return None
        return getpass.getpass(f"Enter password for {os.path.basename(archive)}{' (' + entry + ')' if entry else ''}: ")


def _console_reporter() -> Reporter:
    state = {"last": ""}

    def on_progress(done, total, current):
        pct = int(done * 100 / total) if total else 0
        line = f"\r{pct:3d}%  {current[-60:]:<60}"
        if line != state["last"]:
            sys.stdout.write(line)
            sys.stdout.flush()
            state["last"] = line

    return Reporter(on_progress=on_progress, on_message=lambda m: print("\n" + m))


def parse(argv):
    if not argv:
        raise SystemExit(__doc__)
    cmd = argv[0].lower()
    rr_pct = None
    if cmd.startswith("rr") and cmd != "rn":
        rr_pct = float(cmd[2:].rstrip("%") or 3)
        cmd = "rr"
    elif cmd not in COMMANDS:
        raise SystemExit(f"Unknown command '{argv[0]}'.\n\n{__doc__}")
    sw = {"exclude": [], "overwrite": None}
    args = []
    switches_done = False
    for a in argv[1:]:
        if a == "--":
            switches_done = True
            continue
        if not switches_done and a.startswith("-") and len(a) > 1 and not os.path.exists(a):
            s = a[1:]
            low = s.lower()
            if low.startswith("hp"):
                sw["password"], sw["encrypt_names"] = s[2:], True
            elif low.startswith("p"):
                sw["password"] = s[1:]
            elif low.startswith("mm"):
                sw["method"] = s[2:]
            elif low.startswith("m") and s[1:].isdigit():
                sw["level"] = int(s[1:])
            elif low.startswith("fmt"):
                sw["format"] = s[3:].lower().lstrip(":=")
            elif low.startswith("v"):
                sw["volume_size"] = parse_size(s[1:])
            elif low.startswith("rr"):
                sw["recovery"] = float(s[2:].rstrip("%") or 3)
            elif low == "sfx":
                sw["sfx"] = True
            elif low == "k":
                sw["lock"] = True
            elif low == "df":
                sw["delete_after"] = True
            elif low == "t":
                sw["test_after"] = True
            elif low == "u":
                sw["update_mode"] = "update"
            elif low == "f":
                sw["update_mode"] = "freshen"
            elif low == "as":
                sw["update_mode"] = "sync"
            elif low.startswith("x"):
                sw["exclude"].append(s[1:])
            elif low == "ep":
                sw["store_paths"] = "none"
            elif low == "ep3":
                sw["store_paths"] = "full"
            elif low.startswith("z"):
                with open(s[1:], encoding="utf-8") as f:
                    sw["comment"] = f.read()
            elif low in ("o+", "o-", "or", "on"):
                sw["overwrite"] = {"o+": "always", "o-": "never", "or": "rename", "on": "newer"}[low]
            elif low == "y":
                sw["yes"] = True
            elif low == "ad":
                sw["ad"] = True
            elif low == "here":
                sw["here"] = True
            elif low == "gui":
                sw["gui"] = True
            else:
                raise SystemExit(f"Unknown switch {a}")
        else:
            args.append(a)
    return cmd, rr_pct, sw, args


def create_options(sw, archive) -> CreateOptions:
    fmt = sw.get("format") or format_from_path(archive)
    return CreateOptions(format=fmt, level=sw.get("level", 3), method=sw.get("method", ""),
                         password=sw.get("password", ""), encrypt_names=sw.get("encrypt_names", False),
                         volume_size=sw.get("volume_size", 0), sfx=sw.get("sfx", False),
                         recovery_percent=sw.get("recovery", 0), lock=sw.get("lock", False),
                         comment=sw.get("comment", ""), delete_after=sw.get("delete_after", False),
                         test_after=sw.get("test_after", False), exclude=sw["exclude"],
                         update_mode=sw.get("update_mode", "add"), store_paths=sw.get("store_paths", "relative"))


def run(cmd, rr_pct, sw, args, reporter: Reporter, ui: Interaction) -> int:
    """Execute a parsed command. Returns process exit code."""
    if not args:
        raise SystemExit("Archive name is missing.")
    archive = args[0]
    rest = args[1:]
    out = print if not sw.get("gui") else (lambda *a, **k: None)

    if cmd == "a":
        if not rest:
            raise SystemExit("No files to add.")
        opts = create_options(sw, archive)
        if opts.sfx and not archive.lower().endswith(".exe"):
            archive = strip_archive_ext(archive) + ".exe"
        outs = ops.create(archive, rest, opts, reporter, ui)
        out(f"\nDone: {', '.join(outs)}")
        return 0

    if cmd in ("x", "e"):
        dest = None
        if rest and (rest[-1].endswith(("\\", "/")) or os.path.isdir(rest[-1])):
            dest = rest.pop()
        if sw.get("here"):
            dest = ops.smart_extract_dest(archive, True, sw.get("password"), ui)
        elif sw.get("ad"):
            dest = os.path.join(dest or os.path.dirname(os.path.abspath(archive)),
                                os.path.basename(strip_archive_ext(archive)))
        dest = dest or os.getcwd()
        ow = sw["overwrite"] or ("always" if sw.get("yes") else "ask")
        opts = ExtractOptions(dest=dest, paths="none" if cmd == "e" else "full", overwrite=ow,
                              password=sw.get("password", ""))
        done, errors = ops.extract(archive, opts, rest or None, reporter, ui)
        out(f"\nExtracted {len(done)} file(s) to {dest}")
        for e in errors:
            out("  ERROR:", e)
        return 3 if errors else 0

    if cmd == "l":
        h = open_archive(archive, sw.get("password"), ui)
        try:
            print(f"Archive: {archive}   [{h.format}]")
            if h.comment():
                print("Comment:", h.comment())
            print(f"{'Size':>14} {'Packed':>14}  {'Modified':19}  {'CRC32':8}  Name")
            print("-" * 80)
            tot = totp = n = 0
            for e in h.entries():
                mt = e.mtime.strftime("%Y-%m-%d %H:%M:%S") if e.mtime else ""
                crc = f"{e.crc:08X}" if e.crc is not None else ""
                flag = "*" if e.encrypted else " "
                print(f"{'<DIR>' if e.is_dir else e.size:>14} {e.packed if e.packed is not None else '':>14}  "
                      f"{mt:19}  {crc:8} {flag}{e.name}{'/' if e.is_dir else ''}")
                if not e.is_dir:
                    tot += e.size
                    totp += e.packed or 0
                    n += 1
            print("-" * 80)
            print(f"{tot:>14} {totp:>14}  {n} file(s)")
        finally:
            h.close()
        return 0

    if cmd == "i":
        h = open_archive(archive, sw.get("password"), ui)
        try:
            s = h.summary()
        finally:
            h.close()
        for k, v in [("Archive", s.path), ("Format", s.format), ("Volumes", s.volumes),
                     ("Size on disk", human_size(s.disk_size)), ("Files", s.files), ("Folders", s.dirs),
                     ("Unpacked size", human_size(s.unpacked)), ("Ratio", f"{s.ratio:.1f}%"),
                     ("Encrypted", s.encrypted), ("File names encrypted", s.header_encrypted),
                     ("Solid", s.solid), ("Locked", s.locked), ("Self-extracting", s.sfx),
                     ("Recovery record", s.recovery or "absent"), ("Methods", ", ".join(s.methods))]:
            print(f"{k:>22}: {v}")
        if s.comment:
            print("\nComment:\n" + s.comment)
        return 0

    if cmd == "t":
        errors = ops.test(archive, sw.get("password"), reporter, ui)
        out("\n" + ("All OK" if not errors else "\n".join("ERROR: " + e for e in errors)))
        return 3 if errors else 0

    if cmd == "d":
        ops.delete(archive, rest, sw.get("password"), reporter, ui)
        out(f"\nDeleted {len(rest)} item(s)")
        return 0

    if cmd == "rn":
        if len(rest) != 2:
            raise SystemExit("Usage: rn <archive> <old name> <new name>")
        ops.rename(archive, rest[0], rest[1], sw.get("password"), reporter, ui)
        return 0

    if cmd == "c":
        text = sw.get("comment")
        if text is None:
            text = " ".join(rest) if rest else sys.stdin.read()
        ops.set_comment(archive, text, ui)
        return 0

    if cmd == "k":
        ops.lock(archive, ui)
        out("Archive locked.")
        return 0

    if cmd == "rr":
        p = recovery.create_record(archive, rr_pct or 3, reporter)
        out(f"\nRecovery record written: {p}")
        return 0

    if cmd == "r":
        out("\n" + ops.repair(archive, rest[0] if rest else None, reporter))
        return 0

    if cmd == "s":
        o = ops.make_sfx(archive, rest[0] if rest else None, {}, ui, reporter)
        out(f"\nCreated {o}")
        return 0

    if cmd == "cv":
        if not rest:
            raise SystemExit("Usage: cv [-fmt<format>] <source> <dest>")
        opts = create_options(sw, rest[0])
        ops.convert(archive, rest[0], opts, reporter, ui, password=sw.get("password"))
        out(f"\nConverted to {rest[0]}")
        return 0
    raise SystemExit(f"Unknown command {cmd}")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "/?", "help"):
        print(__doc__)
        return 0
    cmd, rr_pct, sw, args = parse(argv)
    if sw.get("gui"):
        from .gui.app import run_cli_with_gui
        return run_cli_with_gui(cmd, rr_pct, sw, args)
    try:
        ui = ConsoleUI(sw.get("yes", False), prompt_password="password" not in sw)
        return run(cmd, rr_pct, sw, args, _console_reporter(), ui)
    except Cancelled:
        print("\nCancelled.")
        return 255
    except (ArchiveError, OSError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 255


if __name__ == "__main__":
    sys.exit(main())
