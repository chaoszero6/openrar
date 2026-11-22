"""Persistent settings stored as JSON (%APPDATA%\\OpenRAR on Windows, ~/.config/OpenRAR on Linux)."""
from __future__ import annotations

import json
import os
import sys

from ..core import tools

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
    from ..platform import current
    d = os.path.join(current().config_base(), "OpenRAR")
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


# --------------------------------------------------------------------------- password protection
def protect(text: str) -> str:
    from ..platform import current
    return current().protect(text)


def unprotect(blob: str) -> str:
    from ..platform import current
    return current().unprotect(blob)


def app_command() -> list[str]:
    """Command that starts this application (frozen executable or the source launcher)."""
    from ..platform import current
    return current().app_command()


def resource(name: str) -> str:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        p = os.path.join(base, "openrar", "resources", name)
        if os.path.exists(p):
            return p
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources", name)
