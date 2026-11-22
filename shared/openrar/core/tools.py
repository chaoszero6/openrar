"""Locate optional external helpers (7-Zip, UnRAR, bsdtar) used for read-only formats."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from functools import lru_cache

from .util import IS_WINDOWS

OVERRIDES: dict[str, str] = {}      # filled from settings: {"7z": path, "unrar": path}


def _candidates(names, extra_dirs):
    for n in names:
        p = shutil.which(n)
        if p:
            yield p
    for d in extra_dirs:
        for n in names:
            p = os.path.join(d, n if n.endswith(".exe") or not IS_WINDOWS else n + ".exe")
            if os.path.isfile(p):
                yield p


def _program_dirs(sub):
    dirs = []
    for env in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        base = os.environ.get(env)
        if base:
            dirs.append(os.path.join(base, sub))
    if getattr(sys, "frozen", False):
        dirs.append(os.path.join(os.path.dirname(sys.executable), "tools"))
    return dirs


@lru_cache(maxsize=None)
def find_7z() -> str | None:
    if OVERRIDES.get("7z") and os.path.isfile(OVERRIDES["7z"]):
        return OVERRIDES["7z"]
    for p in _candidates(["7z", "7zz", "7za"], _program_dirs("7-Zip")):
        return p
    return None


@lru_cache(maxsize=None)
def find_unrar() -> str | None:
    if OVERRIDES.get("unrar") and os.path.isfile(OVERRIDES["unrar"]):
        return OVERRIDES["unrar"]
    for p in _candidates(["unrar", "UnRAR"], _program_dirs("WinRAR")):
        return p
    return None


@lru_cache(maxsize=None)
def find_bsdtar() -> str | None:
    if IS_WINDOWS:
        p = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "tar.exe")
        return p if os.path.isfile(p) else None
    return shutil.which("bsdtar")


def reset_cache():
    find_7z.cache_clear()
    find_unrar.cache_clear()
    find_bsdtar.cache_clear()


def run(args, **kw):
    """subprocess.run without flashing console windows on Windows."""
    if IS_WINDOWS:
        kw.setdefault("creationflags", 0x08000000)  # CREATE_NO_WINDOW
    return subprocess.run(args, capture_output=True, **kw)


def tool_status() -> dict[str, str | None]:
    return {"7-Zip": find_7z(), "UnRAR": find_unrar(), "bsdtar": find_bsdtar()}
