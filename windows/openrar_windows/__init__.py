"""Windows integration for OpenRAR: Explorer context menu and file associations (per-user registry),
DPAPI-protected saved passwords, Microsoft Defender virus scan."""
from __future__ import annotations

import base64
import os
import sys

from openrar.platform import Platform as _Base

HERE = os.path.dirname(os.path.abspath(__file__))


class Platform(_Base):
    name = "windows"
    file_manager = "Explorer"
    system_theme_label = "Follow Windows"
    supports_integration = True
    integration_title = "Explorer integration"
    integration_note = ("Windows 11 shows these commands under \"Show more options\" (or Shift+Right-click). "
                        "If Windows keeps another default app, use Open with → Choose another app → Always.")
    protection_note = "They are protected with your Windows account (DPAPI)."
    tool_filter = "Programs (*.exe);;All files (*)"

    def startup(self):
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("OpenRAR.ArchiveManager")
        except Exception:
            pass

    def app_command(self) -> list[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable]
        exe = sys.executable
        if exe.lower().endswith("python.exe") and os.path.exists(exe[:-10] + "pythonw.exe"):
            exe = exe[:-10] + "pythonw.exe"
        return [exe, os.path.join(os.path.dirname(HERE), "run_openrar.pyw")]

    def reveal(self, path: str):
        from openrar.gui import launch
        launch.popen(["explorer", "/select,", os.path.normpath(path)])

    def virus_scanner(self):
        base = os.environ.get("ProgramFiles", r"C:\Program Files")
        p = os.path.join(base, "Windows Defender", "MpCmdRun.exe")
        if os.path.exists(p):
            return [p, "-Scan", "-ScanType", "3", "-DisableRemediation", "-File"]
        return super().virus_scanner()

    def config_base(self) -> str:
        return os.environ.get("APPDATA") or os.path.expanduser("~")

    def protect(self, text: str) -> str:
        from .dpapi import crypt
        return "dpapi:" + base64.b64encode(crypt(text.encode("utf-8"), True)).decode()

    def unprotect(self, blob: str) -> str:
        if blob.startswith("dpapi:"):
            from .dpapi import crypt
            return crypt(base64.b64decode(blob[6:]), False).decode("utf-8")
        return super().unprotect(blob)

    def install_integration(self, cfg: dict):
        from . import shell
        shell.install(cfg, self.app_command())

    def uninstall_integration(self):
        from . import shell
        shell.uninstall()

