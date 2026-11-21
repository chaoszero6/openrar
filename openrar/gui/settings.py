"""Persistent settings stored as JSON in %APPDATA%\\OpenRAR\\settings.json."""
from __future__ import annotations

import base64
import json
import os
import sys

from ..core import tools
from ..core.util import IS_WINDOWS

DEFAULTS = {
    "theme": "system",                 # system, light, dark
    "default_format": "zip",
    "default_level": 3,
    "default_method": "",
    "show_tree": False,
    "last_folder": "",
    "extract_default": "",             # empty = next to the archive
    "extract_open_folder": False,
    "extract_overwrite": "ask",
    "extract_subfolder": True,
    "favorites": [],                   # [{"name":..., "path":...}]
    "history": [],                     # recently opened archives
    "passwords": [],                   # [{"label":..., "secret": protected string}]
    "tool_7z": "",
    "tool_unrar": "",
    "column_widths": {},
    "window_geometry": "",
    "confirm_delete": True,
    "temp_dir": "",
    "shell": {"cascaded": True, "add": True, "add_quick": True, "extract": True, "extract_here": True,
              "extract_to": True, "test": False, "associate": []},
    "recent_dests": [],
}


def config_dir() -> str:
    if IS_WINDOWS:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    d = os.path.join(base, "OpenRAR")
    os.makedirs(d, exist_ok=True)
    return d


class Settings:
    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(config_dir(), "settings.json")
        self.data = json.loads(json.dumps(DEFAULTS))
        try:
            with open(self.path, encoding="utf-8") as f:
                stored = json.load(f)
            for k, v in stored.items():
                if isinstance(v, dict) and isinstance(self.data.get(k), dict):
                    self.data[k].update(v)
                else:
                    self.data[k] = v
        except (OSError, ValueError):
            pass
        self.apply_tools()

    def __getitem__(self, k):
        return self.data.get(k, DEFAULTS.get(k))

    def __setitem__(self, k, v):
        self.data[k] = v

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)

    def apply_tools(self):
        tools.OVERRIDES["7z"] = self.data.get("tool_7z", "")
        tools.OVERRIDES["unrar"] = self.data.get("tool_unrar", "")
        tools.reset_cache()

    def add_history(self, path: str):
        h = [p for p in self.data["history"] if p != path]
        self.data["history"] = [path] + h[:14]

    def add_recent_dest(self, path: str):
        h = [p for p in self.data["recent_dests"] if p != path]
        self.data["recent_dests"] = [path] + h[:9]

    # ---- password organizer (protected with Windows DPAPI for the current user)
    def passwords(self) -> list[tuple[str, str]]:
        out = []
        for item in self.data.get("passwords", []):
            try:
                out.append((item["label"], unprotect(item["secret"])))
            except Exception:
                continue
        return out

    def set_passwords(self, items: list[tuple[str, str]]):
        self.data["passwords"] = [{"label": label, "secret": protect(pw)} for label, pw in items]


# --------------------------------------------------------------------------- DPAPI
if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _crypt(data: bytes, encrypt: bool) -> bytes:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        buf = ctypes.create_string_buffer(data, len(data))
        blob_in = _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_out = _BLOB()
        fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
        ok = fn(ctypes.byref(blob_in), None, None, None, None, 0x01, ctypes.byref(blob_out))
        if not ok:
            raise OSError("DPAPI call failed")
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)

    def protect(text: str) -> str:
        return "dpapi:" + base64.b64encode(_crypt(text.encode("utf-8"), True)).decode()

    def unprotect(blob: str) -> str:
        if blob.startswith("dpapi:"):
            return _crypt(base64.b64decode(blob[6:]), False).decode("utf-8")
        return base64.b64decode(blob.split(":", 1)[-1]).decode("utf-8")
else:
    def protect(text: str) -> str:
        return "b64:" + base64.b64encode(text.encode("utf-8")).decode()

    def unprotect(blob: str) -> str:
        return base64.b64decode(blob.split(":", 1)[-1]).decode("utf-8")


def app_command() -> list[str]:
    """Command that starts this application (frozen exe or python -m openrar)."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    exe = sys.executable
    if IS_WINDOWS and exe.lower().endswith("python.exe"):
        w = exe[:-10] + "pythonw.exe"
        if os.path.exists(w):
            exe = w
    return [exe, "-m", "openrar"]


def resource(name: str) -> str:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        p = os.path.join(base, "openrar", "resources", name)
        if os.path.exists(p):
            return p
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources", name)
