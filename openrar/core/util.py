"""Filesystem and path helpers."""
from __future__ import annotations

import fnmatch
import os
import posixpath
import re
import sys
from datetime import datetime
from typing import Iterable

IS_WINDOWS = sys.platform == "win32"
_WIN_BAD = re.compile(r'[<>:"|?*\x00-\x1f]')
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                 *(f"LPT{i}" for i in range(1, 10))}


def human_size(n: float | None) -> str:
    if n is None:
        return ""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def parse_size(text: str) -> int:
    """Parse '700m', '4.7 GB', '1440k', '123' (bytes) into a byte count."""
    t = text.strip().lower().replace(" ", "").replace(",", "")
    if not t:
        return 0
    m = re.fullmatch(r"([0-9]*\.?[0-9]+)([kmgt]?)(i?b)?", t)
    if not m:
        raise ValueError(f"Invalid size: {text!r}")
    mult = {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3, "t": 1024 ** 4}[m.group(2)]
    if m.group(2) == "" and m.group(3) is None:
        mult = 1
    return int(float(m.group(1)) * mult)


def normalize_arcname(name: str) -> str:
    name = name.replace("\\", "/")
    while name.startswith("./"):
        name = name[2:]
    return name.strip("/")


def safe_relpath(name: str) -> str:
    """Turn an archive member name into a safe relative path (no traversal, no drive)."""
    name = name.replace("\\", "/")
    name = re.sub(r"^[a-zA-Z]:", "", name)
    parts = []
    for p in name.split("/"):
        if p in ("", ".", ".."):
            continue
        if IS_WINDOWS:
            p = _WIN_BAD.sub("_", p).rstrip(" .") or "_"
            if p.split(".")[0].upper() in _WIN_RESERVED:
                p = "_" + p
        parts.append(p)
    return os.path.join(*parts) if parts else ""


def unique_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    i = 1
    while True:
        cand = f"{root} ({i}){ext}"
        if not os.path.exists(cand):
            return cand
        i += 1


def matches_any(name: str, patterns: Iterable[str]) -> bool:
    base = posixpath.basename(name)
    for p in patterns:
        p = p.strip()
        if not p:
            continue
        p = p.replace("\\", "/")
        if fnmatch.fnmatch(base, p) or fnmatch.fnmatch(name, p) or fnmatch.fnmatch(name, "*/" + p):
            return True
    return False


def gather_sources(paths: Iterable[str], store_paths: str = "relative",
                   exclude: Iterable[str] = (), include_empty_dirs: bool = True,
                   base_dir: str | None = None):
    """Expand files/folders into a list of (absolute_path, arcname, is_dir).

    store_paths:
      relative - names are relative to the folder that contains each selected item
      none     - only the file names are stored
      full     - full path without drive letter
    """
    exclude = [e for e in exclude if e.strip()]
    out: list[tuple[str, str, bool]] = []
    seen: set[str] = set()

    def arcname_for(abs_path: str, root_parent: str) -> str:
        if store_paths == "none":
            return os.path.basename(abs_path)
        if store_paths == "full":
            p = os.path.splitdrive(abs_path)[1]
            return normalize_arcname(p)
        return normalize_arcname(os.path.relpath(abs_path, root_parent))

    def add(abs_path: str, arc: str, is_dir: bool):
        arc = normalize_arcname(arc)
        if not arc or arc in seen or matches_any(arc, exclude):
            return
        seen.add(arc)
        out.append((abs_path, arc, is_dir))

    for src in paths:
        src = os.path.abspath(src)
        parent = base_dir or os.path.dirname(src)
        if os.path.isdir(src) and not os.path.islink(src):
            if matches_any(arcname_for(src, parent), exclude):
                continue
            if store_paths != "none":
                add(src, arcname_for(src, parent), True)
            for root, dirs, files in os.walk(src):
                dirs.sort()
                dirs[:] = [d for d in dirs if not matches_any(arcname_for(os.path.join(root, d), parent), exclude)]
                for d in dirs:
                    full = os.path.join(root, d)
                    if store_paths != "none" and (include_empty_dirs or os.listdir(full)):
                        add(full, arcname_for(full, parent), True)
                for f in sorted(files):
                    full = os.path.join(root, f)
                    add(full, arcname_for(full, parent), False)
        elif os.path.exists(src):
            add(src, arcname_for(src, parent), False)
        else:
            raise FileNotFoundError(src)
    return out


def file_mtime(path: str) -> datetime:
    return datetime.fromtimestamp(os.path.getmtime(path))


def default_archive_name(paths: list[str], ext: str) -> str:
    """Suggest an archive path the way the shell 'Add to archive' command does."""
    paths = [os.path.abspath(p) for p in paths]
    if len(paths) == 1:
        p = paths[0].rstrip("\\/")
        base = os.path.basename(p) if os.path.isdir(p) else os.path.splitext(os.path.basename(p))[0]
        parent = os.path.dirname(p)
    else:
        parent = os.path.dirname(paths[0])
        base = os.path.basename(parent) or "Archive"
    return os.path.join(parent, f"{base or 'Archive'}.{ext}")


def strip_archive_ext(path: str) -> str:
    """'C:/x/foo.tar.gz' -> 'C:/x/foo'; 'bar.part1.rar' -> 'bar'; 'baz.7z.001' -> 'baz'."""
    base = path
    base = re.sub(r"\.\d{3}$", "", base)
    base = re.sub(r"(?i)\.part\d+\.rar$", "", base)
    low = base.lower()
    for ext in (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst", ".tgz", ".tbz2", ".txz"):
        if low.endswith(ext):
            return base[: -len(ext)]
    return os.path.splitext(base)[0]
