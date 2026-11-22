"""Start other programs without handing them OpenRAR's bundled runtime.

The PyInstaller build sets QT_PLUGIN_PATH / QML2_IMPORT_PATH, puts its own folder first on PATH and sets
_PYI_* variables, and on Windows its bootloader calls SetDllDirectory(<bundle>). Programs started from
OpenRAR (a video player opened from an archive, Explorer, a virus scanner...) inherit all of that. Another
Qt application then loads our Qt plugins, which belong to a different Qt version, and fails with "no Qt
platform plugin could be initialized".
"""
from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys

IS_WINDOWS = sys.platform == "win32"
FROZEN = bool(getattr(sys, "frozen", False))
_BUNDLE = os.path.normcase(os.path.abspath(getattr(sys, "_MEIPASS", ""))) if FROZEN else ""
_QT_VARS = ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH", "QML_IMPORT_PATH")


def _in_bundle(p: str) -> bool:
    if not _BUNDLE or not p:
        return False
    p = os.path.normcase(os.path.abspath(p))
    return p == _BUNDLE or p.startswith(_BUNDLE + os.sep)


def clean_environment(env=None) -> dict:
    """A copy of `env` (default os.environ) without the bundle's variables and PATH entries."""
    env = dict(os.environ if env is None else env)
    if not FROZEN:
        return env
    for k in list(env):
        if k in _QT_VARS and all(_in_bundle(p) for p in env[k].split(os.pathsep) if p):
            del env[k]
        elif k.upper().startswith("_PYI_") or k.upper() == "_MEIPASS2":
            del env[k]
    # PyInstaller on Linux points LD_LIBRARY_PATH at the bundle and keeps the original in *_ORIG
    for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        if var + "_ORIG" in env:
            env[var] = env.pop(var + "_ORIG")
    for k in list(env):
        if k.upper() in ("PATH", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
            kept = os.pathsep.join(p for p in env[k].split(os.pathsep) if p and not _in_bundle(p))
            if kept or k.upper() == "PATH":
                env[k] = kept
            else:
                del env[k]
    return env


def sanitize_process_environment():
    """Call once after QApplication exists. Freezes our own Qt plugin paths, then removes the bundle's
    variables from os.environ so every program started later gets a clean environment."""
    if not FROZEN:
        return
    try:
        from PySide6.QtCore import QCoreApplication
        QCoreApplication.setLibraryPaths(QCoreApplication.libraryPaths())
    except Exception:
        pass
    clean = clean_environment()
    for k in list(os.environ):
        if k not in clean:
            del os.environ[k]
        elif os.environ[k] != clean[k]:
            os.environ[k] = clean[k]


@contextlib.contextmanager
def _default_dll_search():
    """Undo the bootloader's SetDllDirectory while a child process is created (children inherit it)."""
    if not (IS_WINDOWS and FROZEN):
        yield
        return
    import ctypes
    k32 = ctypes.windll.kernel32
    k32.SetDllDirectoryW(None)
    try:
        yield
    finally:
        k32.SetDllDirectoryW(sys._MEIPASS)


def open_with_default_app(path: str):
    """Open a file with the program Windows associates with it."""
    if IS_WINDOWS:
        with _default_dll_search():
            os.startfile(path)  # noqa: S606 - the user's own file with its associated program
        return
    opener = shutil.which("xdg-open") or shutil.which("open")
    if opener:
        popen([opener, path], start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
              stderr=subprocess.DEVNULL)
    else:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def popen(args, **kw):
    kw.setdefault("env", clean_environment())
    with _default_dll_search():
        return subprocess.Popen(args, **kw)


def run(args, **kw):
    kw.setdefault("env", clean_environment())
    with _default_dll_search():
        return subprocess.run(args, **kw)
