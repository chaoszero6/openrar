"""Linux integration for OpenRAR: applications menu, MIME defaults, Dolphin / Nemo / Nautilus menus,
keyring-protected saved passwords, xdg-open / FileManager1 for opening and revealing files."""
from __future__ import annotations

import os
import shutil
import sys
from urllib.parse import quote

from openrar.platform import Platform as _Base

HERE = os.path.dirname(os.path.abspath(__file__))


class Platform(_Base):
    name = "linux"
    file_manager = "file manager"
    system_theme_label = "Follow system"
    supports_integration = True
    integration_title = "Desktop integration"
    integration_note = ("Adds OpenRAR to the applications menu and as the default app for the ticked types, and "
                        "puts the commands in Dolphin (KDE), Nemo (Cinnamon) and GNOME Files (right-click › "
                        "Scripts › OpenRAR). Other file managers: use Open With › OpenRAR.")
    tool_filter = "All files (*)"

    @property
    def protection_note(self) -> str:      # type: ignore[override]
        from . import secrets
        if secrets.available():
            return "They are encrypted with a key kept in your desktop keyring."
        return "No desktop keyring (Secret Service / KWallet) was found, so they are only obfuscated."

    def startup(self):
        try:
            from PySide6.QtGui import QGuiApplication
            QGuiApplication.setDesktopFileName("openrar")     # Wayland: window icon from openrar.desktop
        except Exception:
            pass

    def app_command(self) -> list[str]:
        if os.environ.get("APPIMAGE"):                         # AppImage: the mount point changes every run
            return [os.environ["APPIMAGE"]]
        if getattr(sys, "frozen", False):
            exe = os.path.realpath(sys.executable)
            onpath = shutil.which("openrar")
            if onpath and os.path.realpath(onpath) == exe:
                return ["openrar"]                              # .deb: /usr/bin/openrar -> /opt/openrar/openrar
            return [exe]
        return [sys.executable, os.path.join(os.path.dirname(HERE), "run_openrar.py")]

    def reveal(self, path: str):
        from openrar.gui import launch
        uri = "file://" + quote(os.path.abspath(path))
        if shutil.which("dbus-send"):
            r = launch.run(["dbus-send", "--session", "--print-reply", "--dest=org.freedesktop.FileManager1",
                            "/org/freedesktop/FileManager1", "org.freedesktop.FileManager1.ShowItems",
                            f"array:string:{uri}", "string:"], capture_output=True, timeout=5)
            if r.returncode == 0:
                return
        launch.open_with_default_app(os.path.dirname(os.path.abspath(path)))

    def protect(self, text: str) -> str:
        from . import secrets
        return secrets.protect(text) or super().protect(text)

    def unprotect(self, blob: str) -> str:
        from . import secrets
        if blob.startswith(secrets.PREFIX):
            return secrets.unprotect(blob)
        return super().unprotect(blob)

    def install_integration(self, cfg: dict):
        from openrar.gui.settings import resource

        from . import desktop
        desktop.install(cfg, self.app_command(), resource("icon.png"))

    def uninstall_integration(self):
        from . import desktop
        desktop.uninstall()

