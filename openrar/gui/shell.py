"""Windows Explorer integration (per-user, no administrator rights needed).

Everything lives under HKEY_CURRENT_USER\\Software\\Classes so it can be
removed cleanly and never touches machine-wide settings.
"""
from __future__ import annotations

import os

from ..core.util import IS_WINDOWS
from .settings import app_command, resource

PROGID = "OpenRAR.Archive"
ROOT = r"Software\Classes"
MENU_KEY = "OpenRAR"
ARCHIVE_TYPES = [".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".tbz2", ".xz", ".txz", ".zst", ".tzst",
                 ".iso", ".cab", ".arj", ".lzh", ".lha", ".001", ".cbz", ".cbr", ".cb7", ".wim", ".z"]


def _cmd(*args: str) -> str:
    """Build a registry command line; every non-switch argument is quoted ("%1" included)."""
    return " ".join(p if p.startswith("-") else f'"{p}"' for p in app_command() + list(args))


def _verbs(cfg, for_archives: bool):
    """Return list of (key, label, icon, command)."""
    v = []
    if cfg.get("add"):
        v.append(("a_add", "Add to archive...", "add", _cmd("--add-dialog", "%1")))
    if cfg.get("add_quick"):
        v.append(("a_quick", "Add to .zip archive", "add", _cmd("--add-quick", "%1")))
    if for_archives:
        if cfg.get("extract"):
            v.append(("b_extract", "Extract files...", "extract", _cmd("--extract-dialog", "%1")))
        if cfg.get("extract_here"):
            v.append(("c_here", "Extract Here", "extract", _cmd("x", "-here", "-gui", "%1")))
        if cfg.get("extract_to"):
            v.append(("d_to", "Extract to subfolder", "extract", _cmd("x", "-ad", "-gui", "%1")))
        if cfg.get("test"):
            v.append(("e_test", "Test archive", "test", _cmd("t", "-gui", "%1")))
    return v


def install(cfg: dict):
    if not IS_WINDOWS:
        raise OSError("Shell integration is only available on Windows")
    import winreg

    uninstall()
    exe_icon = resource("icon.ico")
    arc_icon = resource("archive.ico")

    def setkey(path, values: dict):
        k = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_WRITE)
        for name, val in values.items():
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, val)
        winreg.CloseKey(k)

    def add_menu(base: str, for_archives: bool):
        verbs = _verbs(cfg, for_archives)
        if not verbs:
            return
        if cfg.get("cascaded"):
            setkey(rf"{base}\shell\{MENU_KEY}", {"MUIVerb": "OpenRAR", "Icon": exe_icon, "SubCommands": ""})
            for key, label, _ic, cmd in verbs:
                setkey(rf"{base}\shell\{MENU_KEY}\shell\{key}", {"MUIVerb": label, "Icon": exe_icon})
                setkey(rf"{base}\shell\{MENU_KEY}\shell\{key}\command", {"": cmd})
        else:
            for key, label, _ic, cmd in verbs:
                setkey(rf"{base}\shell\{MENU_KEY}.{key}", {"MUIVerb": label, "Icon": exe_icon})
                setkey(rf"{base}\shell\{MENU_KEY}.{key}\command", {"": cmd})

    add_menu(rf"{ROOT}\*", for_archives=False)
    add_menu(rf"{ROOT}\Directory", for_archives=False)
    for ext in ARCHIVE_TYPES:
        add_menu(rf"{ROOT}\SystemFileAssociations\{ext}", for_archives=True)

    # file associations
    setkey(rf"{ROOT}\{PROGID}", {"": "Archive", "FriendlyTypeName": "Archive"})
    setkey(rf"{ROOT}\{PROGID}\DefaultIcon", {"": arc_icon})
    setkey(rf"{ROOT}\{PROGID}\shell\open\command", {"": _cmd("%1")})
    for ext in cfg.get("associate", []):
        setkey(rf"{ROOT}\{ext}\OpenWithProgids", {PROGID: ""})
        k = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, rf"{ROOT}\{ext}", 0, winreg.KEY_READ | winreg.KEY_WRITE)
        try:
            cur, _ = winreg.QueryValueEx(k, "")
        except OSError:
            cur = ""
        if not cur or cur == PROGID:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, PROGID)
        winreg.CloseKey(k)
    # also register in "Open with" list
    exe = os.path.basename(app_command()[0])
    setkey(rf"{ROOT}\Applications\{exe}\shell\open\command", {"": _cmd("%1")})
    _notify()


def _delete_tree(root, path):
    import winreg

    try:
        k = winreg.OpenKey(root, path, 0, winreg.KEY_READ | winreg.KEY_WRITE)
    except OSError:
        return
    while True:
        try:
            sub = winreg.EnumKey(k, 0)
        except OSError:
            break
        _delete_tree(root, path + "\\" + sub)
    winreg.CloseKey(k)
    try:
        winreg.DeleteKey(root, path)
    except OSError:
        pass


def uninstall():
    if not IS_WINDOWS:
        return
    import winreg

    hk = winreg.HKEY_CURRENT_USER
    bases = [rf"{ROOT}\*", rf"{ROOT}\Directory"] + [rf"{ROOT}\SystemFileAssociations\{e}" for e in ARCHIVE_TYPES]
    for base in bases:
        try:
            k = winreg.OpenKey(hk, base + r"\shell")
        except OSError:
            continue
        names = []
        i = 0
        while True:
            try:
                names.append(winreg.EnumKey(k, i))
                i += 1
            except OSError:
                break
        winreg.CloseKey(k)
        for n in names:
            if n == MENU_KEY or n.startswith(MENU_KEY + "."):
                _delete_tree(hk, rf"{base}\shell\{n}")
    for ext in ARCHIVE_TYPES:
        try:
            k = winreg.OpenKey(hk, rf"{ROOT}\{ext}\OpenWithProgids", 0, winreg.KEY_WRITE)
            winreg.DeleteValue(k, PROGID)
            winreg.CloseKey(k)
        except OSError:
            pass
        try:
            k = winreg.OpenKey(hk, rf"{ROOT}\{ext}", 0, winreg.KEY_READ | winreg.KEY_WRITE)
            cur, _ = winreg.QueryValueEx(k, "")
            if cur == PROGID:
                winreg.DeleteValue(k, "")
            winreg.CloseKey(k)
        except OSError:
            pass
    _delete_tree(hk, rf"{ROOT}\{PROGID}")
    _notify()


def _notify():
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED
    except Exception:
        pass
