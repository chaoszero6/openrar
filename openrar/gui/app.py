"""Application start-up: main window, Explorer context-menu entry points, CLI with progress UI."""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QByteArray, QEventLoop, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from .. import APP_NAME
from . import theme
from .settings import Settings, resource

_settings: Settings | None = None


def settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def _app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("OpenRAR.ArchiveManager")
            except Exception:
                pass
        app = QApplication(sys.argv[:1])
        app.setApplicationName(APP_NAME)
        app.setOrganizationName(APP_NAME)
        app.setWindowIcon(QIcon(resource("icon.ico")))
        theme.apply(app, settings()["theme"])
    return app


# --------------------------------------------------------------------------- Explorer multi-select collection
def _collect(kind: str, items: list[str], wait_ms: int = 600) -> list[str] | None:
    """Explorer starts one process per selected file. The first becomes the collector; others hand over
    their file and exit (returning None)."""
    name = f"OpenRAR-{kind}-{os.environ.get('USERNAME') or os.environ.get('USER') or 'user'}"
    sock = QLocalSocket()
    sock.connectToServer(name)
    if sock.waitForConnected(150):
        sock.write(QByteArray("\n".join(items).encode("utf-8") + b"\0"))
        sock.flush()
        sock.waitForBytesWritten(1000)
        sock.disconnectFromServer()
        return None
    server = QLocalServer()
    QLocalServer.removeServer(name)
    if not server.listen(name):
        return items
    collected = list(items)
    buffers = {}

    def on_conn():
        while server.hasPendingConnections():
            c = server.nextPendingConnection()
            buffers[c] = b""

            def read(c=c):
                buffers[c] += bytes(c.readAll())
                if buffers[c].endswith(b"\0"):
                    for line in buffers[c][:-1].decode("utf-8").splitlines():
                        if line and line not in collected:
                            collected.append(line)
                    deadline.start(wait_ms)       # more may still be coming
            c.readyRead.connect(read)
    server.newConnection.connect(on_conn)
    loop = QEventLoop()
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    deadline.start(wait_ms)
    loop.exec()
    server.close()
    return collected


# --------------------------------------------------------------------------- entry points
def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("--register-shell", "--unregister-shell"):
        from . import shell
        s = settings()
        if argv[0] == "--register-shell":
            cfg = dict(s["shell"])
            if not cfg.get("associate"):
                cfg["associate"] = [".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".001"]
            shell.install(cfg)
            s["shell"] = cfg
            s.save()
        else:
            shell.uninstall()
        return 0
    app = _app()
    from .main_window import MainWindow

    if argv and argv[0] in ("--add-dialog", "--add-quick", "--extract-dialog"):
        mode, items = argv[0], [os.path.abspath(a) for a in argv[1:]]
        items = _collect(mode.strip("-"), items)
        if items is None:
            return 0
        win = MainWindow(settings(), start_path=os.path.dirname(items[0]) if items else None)
        if mode == "--add-dialog":
            win.add_dialog(items)
        elif mode == "--add-quick":
            from ..core import operations as ops
            from ..core.model import CreateOptions
            from ..core.util import default_archive_name
            from .tasks import run_task
            s = settings()
            fmt = s["default_format"] or "zip"
            dest = default_archive_name(items, fmt)
            opts = CreateOptions(format=fmt, level=s["default_level"])
            run_task(None, f"Creating {os.path.basename(dest)}", lambda r, ui: ops.create(dest, items, opts, r, ui))
        else:
            win.extract_dialog([i for i in items if os.path.isfile(i)])
        settings().save()
        return 0

    start = None
    if argv:
        start = os.path.abspath(argv[0])
    win = MainWindow(settings(), start_path=start)
    win.show()
    return app.exec()


def run_cli_with_gui(cmd, rr_pct, sw, args) -> int:
    """Run a CLI command (e.g. from the Explorer context menu) with a progress window."""
    app = _app()
    from ..cli import run
    from .tasks import GuiInteraction, run_task

    ui = GuiInteraction(None, known_passwords=[p for _l, p in settings().passwords()])
    ok, code, err, msgs = run_task(None, APP_NAME, lambda rep, ui_: run(cmd, rr_pct, sw, args, rep, ui_), ui)
    if ok and code and cmd in ("x", "e", "t"):
        QMessageBox.warning(None, APP_NAME, "The operation completed with errors. Use Test in the main window "
                            "for details.")
    elif ok and cmd == "t":
        QMessageBox.information(None, APP_NAME, "No errors found.")
    for m in msgs:
        QMessageBox.information(None, APP_NAME, m)
    del app
    return 0 if ok and not code else (code or 2)
