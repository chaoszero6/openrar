"""Application start-up: main window, Explorer context-menu entry points, CLI with progress UI."""
from __future__ import annotations

import os
import sys
import time

from PySide6.QtCore import QByteArray, QDir, QLockFile
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
        from ..platform import current
        current().startup()
        app = QApplication(sys.argv[:1])
        app.setApplicationName(APP_NAME)
        app.setOrganizationName(APP_NAME)
        app.setWindowIcon(QIcon(resource("icon.ico")))
        theme.apply(app, settings()["theme"])
        from .launch import sanitize_process_environment
        sanitize_process_environment()     # programs we start must not inherit the bundled Qt
    return app


# --------------------------------------------------------------------------- Explorer multi-select collection
_ACK = b"OK"


def _collect(kind: str, items: list[str], quiet_ms: int = 1000, give_up_ms: int = 20000) -> list[str] | None:
    """Explorer starts one process per selected file. One becomes the collector; the others hand over
    their file and exit (returning None).

    A lock file decides who collects: "nobody is listening, so I will" is not atomic, and Windows lets
    several processes listen on the same pipe name. A process only exits once the collector has confirmed
    receipt; otherwise it tries again, becoming the next collector if the previous one has finished."""
    name = f"OpenRAR-{kind}-{os.environ.get('USERNAME') or os.environ.get('USER') or 'user'}"
    lock = QLockFile(os.path.join(QDir.tempPath(), name + ".lock"))
    lock.setStaleLockTime(0)          # only a dead owner makes the lock stale, not its age
    deadline = time.monotonic() + give_up_ms / 1000
    while not lock.tryLock(0):
        if _hand_over(name, items):
            return None
        if time.monotonic() > deadline:
            return items              # a second window is better than silently dropping files
        time.sleep(0.05)
    try:
        return _receive(name, items, quiet_ms)
    finally:
        lock.unlock()


def _hand_over(name: str, items: list[str]) -> bool:
    """Send `items` to the collector. True only if it confirmed that it has them."""
    sock = QLocalSocket()
    sock.connectToServer(name)
    if not sock.waitForConnected(150):
        return False
    sock.write(QByteArray("\n".join(items).encode("utf-8") + b"\0"))
    sock.flush()
    reply = b""
    while len(reply) < len(_ACK) and sock.waitForReadyRead(3000):
        reply += bytes(sock.readAll())
    sock.disconnectFromServer()
    return reply == _ACK


def _receive(name: str, items: list[str], quiet_ms: int) -> list[str]:
    """Collect handed-over files until none have arrived for `quiet_ms`."""
    server = QLocalServer()
    QLocalServer.removeServer(name)   # leftover socket of a crashed collector (Unix)
    if not server.listen(name):
        return items
    collected = dict.fromkeys(items)  # ordered, without duplicates
    deadline = time.monotonic() + quiet_ms / 1000
    while (left := deadline - time.monotonic()) > 0:
        server.waitForNewConnection(max(1, int(left * 1000)))
        while server.hasPendingConnections():
            conn = server.nextPendingConnection()
            data = bytes(conn.readAll())
            while not data.endswith(b"\0") and conn.waitForReadyRead(2000):
                data += bytes(conn.readAll())
            if data.endswith(b"\0"):
                collected.update(dict.fromkeys(x for x in data[:-1].decode("utf-8").split("\n") if x))
                conn.write(QByteArray(_ACK))
                conn.flush()
                conn.waitForDisconnected(1000)   # closing first could discard the reply on Windows
                deadline = time.monotonic() + quiet_ms / 1000       # more may still be coming
            conn.close()
    server.close()
    return list(collected)


# --------------------------------------------------------------------------- entry points
def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("--register-shell", "--unregister-shell"):
        from ..integration import DEFAULT_ASSOCIATIONS
        from ..platform import current
        s = settings()
        if argv[0] == "--register-shell":
            cfg = dict(s["shell"])
            if not cfg.get("associate"):
                cfg["associate"] = list(DEFAULT_ASSOCIATIONS)
            current().install_integration(cfg)
            s["shell"] = cfg
            s.save()
        else:
            current().uninstall_integration()
        return 0
    if argv and argv[0] in BATCH_COMMANDS:
        _app()
        items = _collect(argv[0].strip("-"), [os.path.abspath(a) for a in argv[1:]])
        if items is None:
            return 0
        cmd, sw = BATCH_COMMANDS[argv[0]]
        return run_batch_with_gui(cmd, sw, [i for i in items if os.path.isfile(i)])
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


# file-manager menu commands that act on every selected archive in one window
BATCH_COMMANDS = {
    "--extract-here": ("x", {"here": True}),
    "--extract-subfolder": ("x", {"ad": True}),
    "--test-archives": ("t", {}),
}


def run_batch_with_gui(cmd: str, sw: dict, archives: list[str]) -> int:
    app = _app()
    from ..cli import run
    from .tasks import GuiInteraction, run_task

    if not archives:
        return 0
    ui = GuiInteraction(None, known_passwords=[p for _l, p in settings().passwords()])

    def job(rep, ui_):
        failed = []
        for a in archives:
            rep.begin(os.path.basename(a))
            if run(cmd, None, dict(sw, gui=True), [a], rep, ui_):
                failed.append(os.path.basename(a))
        return failed
    title = APP_NAME + (f" - {os.path.basename(archives[0])}" if len(archives) == 1 else f" - {len(archives)} archives")
    ok, failed, _err, msgs = run_task(None, title, job, ui)
    if ok and failed:
        QMessageBox.warning(None, APP_NAME, "Completed with errors in:\n" + "\n".join(failed) +
                            "\n\nUse Test in the main window for details.")
    elif ok and cmd == "t":
        QMessageBox.information(None, APP_NAME, "No errors found." if len(archives) == 1 else
                                f"No errors found in {len(archives)} archives.")
    for m in msgs:
        QMessageBox.information(None, APP_NAME, m)
    del app
    return 0 if ok and not failed else 2


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
