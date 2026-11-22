"""Operating-system integration, provided by a plugin package next to the shared code:

    windows/openrar_windows   Explorer context menu + file associations (registry), DPAPI, Defender scan
    linux/openrar_linux       .desktop entry + MIME defaults, Dolphin / Nautilus / Nemo menus, Secret Service

The shared engine and Qt interface only talk to `current()`. Without a plugin (for example when running the
shared tests on their own) the generic implementation below is used.
"""
from __future__ import annotations

import base64
import importlib
import os
import shutil
import sys

PLUGINS = {"win32": "openrar_windows", "linux": "openrar_linux"}


class Platform:
    """Generic behaviour. Plugins subclass this and override what their OS does differently."""

    name = "generic"
    file_manager = "the file manager"            # used in UI text: "Add OpenRAR to <file_manager>"
    system_theme_label = "Follow system"
    supports_integration = False
    integration_title = "Desktop integration"
    integration_note = "Desktop integration is not available on this platform."
    protection_note = "Note: on this platform they are only obfuscated, not encrypted."
    tool_filter = "All files (*)"                # file-dialog filter when picking 7-Zip / UnRAR

    # ---- start-up / launching
    def startup(self):
        """Called once before the QApplication is created."""

    def app_command(self) -> list[str]:
        """Command line that starts OpenRAR (used in menus and file associations)."""
        if getattr(sys, "frozen", False):
            return [sys.executable]
        return [sys.executable, "-m", "openrar"]

    def open_with_default_app(self, path: str):
        from .gui import launch
        launch.open_with_default_app(path)

    def reveal(self, path: str):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))

    def virus_scanner(self) -> list[str] | None:
        p = shutil.which("clamscan")
        return [p, "-r", "--no-summary"] if p else None

    # ---- settings
    def config_base(self) -> str:
        return os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")

    # ---- password organizer
    def protect(self, text: str) -> str:
        return "b64:" + base64.b64encode(text.encode("utf-8")).decode()

    def unprotect(self, blob: str) -> str:
        if blob.startswith("b64:"):
            return base64.b64decode(blob[4:]).decode("utf-8")
        raise ValueError("This password was saved on another computer or platform")

    # ---- context menus and file associations
    def install_integration(self, cfg: dict):
        raise OSError(self.integration_note)

    def uninstall_integration(self):
        pass


_current: Platform | None = None


def current() -> Platform:
    global _current
    if _current is None:
        _current = load(sys.platform)
    return _current


def load(platform_name: str) -> Platform:
    mod = PLUGINS.get(platform_name) or ("openrar_linux" if platform_name.startswith(("linux", "freebsd")) else None)
    if mod:
        try:
            return importlib.import_module(mod).Platform()
        except ImportError:
            pass
    return Platform()


def set_current(p: Platform | None):
    """For tests."""
    global _current
    _current = p
