"""Windows Explorer integration (per-user, no administrator rights needed).

Everything lives under HKEY_CURRENT_USER\\Software\\Classes so it can be
removed cleanly and never touches machine-wide settings.
"""
from __future__ import annotations

import os
import sys

from openrar.gui.settings import resource
from openrar.integration import ARCHIVE_TYPES as _TYPES
from openrar.integration import verbs as _shared_verbs

PROGID = "OpenRAR.Archive"
ROOT = r"Software\Classes"
MENU_KEY = "OpenRAR"
# Without this, Explorer hides these menu entries when more than 15 items are selected ("Player": up to 100).
MULTI = {"MultiSelectModel": "Player"}
ARCHIVE_TYPES = [e for e in _TYPES if e != ".run"]


def _cmd(app: list[str], *args: str) -> str:
    """Build a registry command line; every non-switch argument is quoted ("%1" included)."""
    return " ".join(p if p.startswith("-") else f'"{p}"' for p in list(app) + list(args))


def _verbs(cfg, for_archives: bool, app: list[str]):
    """Return list of (key, label, icon, command)."""
    return [(v.key, v.label, v.icon, _cmd(app, *v.args, "%1")) for v in _shared_verbs(cfg, for_archives)]


def install(cfg: dict, app: list[str]):
    if sys.platform != "win32":
        raise OSError("Explorer integration is only available on Windows")
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
        verbs = _verbs(cfg, for_archives, app)
        if not verbs:
            return
        if cfg.get("cascaded"):
            setkey(rf"{base}\shell\{MENU_KEY}", {"MUIVerb": "OpenRAR", "Icon": exe_icon, "SubCommands": "", **MULTI})
            for key, label, _ic, cmd in verbs:
                setkey(rf"{base}\shell\{MENU_KEY}\shell\{key}", {"MUIVerb": label, "Icon": exe_icon, **MULTI})
                setkey(rf"{base}\shell\{MENU_KEY}\shell\{key}\command", {"": cmd})
        else:
            for key, label, _ic, cmd in verbs:
                setkey(rf"{base}\shell\{MENU_KEY}.{key}", {"MUIVerb": label, "Icon": exe_icon, **MULTI})
                setkey(rf"{base}\shell\{MENU_KEY}.{key}\command", {"": cmd})

    add_menu(rf"{ROOT}\*", for_archives=False)
    add_menu(rf"{ROOT}\Directory", for_archives=False)
    for ext in ARCHIVE_TYPES:
        add_menu(rf"{ROOT}\SystemFileAssociations\{ext}", for_archives=True)

    # file associations
    setkey(rf"{ROOT}\{PROGID}", {"": "Archive", "FriendlyTypeName": "Archive"})
    setkey(rf"{ROOT}\{PROGID}\DefaultIcon", {"": arc_icon})
    setkey(rf"{ROOT}\{PROGID}\shell\open\command", {"": _cmd(app, "%1")})
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
    exe = os.path.basename(app[0])
    setkey(rf"{ROOT}\Applications\{exe}\shell\open\command", {"": _cmd(app, "%1")})
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
    if sys.platform != "win32":
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
