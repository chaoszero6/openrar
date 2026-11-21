"""Run archive operations on a worker thread with a progress window.

Workers may need to ask the user something (overwrite? password?). Those
questions are marshalled to the GUI thread and the worker blocks until the
user answers.
"""
from __future__ import annotations

import threading
import time
import traceback

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget,
                               QMessageBox, QProgressBar, QPushButton, QVBoxLayout)

from ..core.model import Cancelled, Entry, Interaction, Reporter
from ..core.util import human_size
from . import icons


class _Request:
    def __init__(self, kind, args):
        self.kind, self.args = kind, args
        self.event = threading.Event()
        self.result = None


class _Bridge(QObject):
    ask = Signal(object)

    def __init__(self, handler):
        super().__init__()
        self.handler = handler
        self.ask.connect(self._on_ask, Qt.QueuedConnection)

    @Slot(object)
    def _on_ask(self, req):
        try:
            req.result = self.handler(req.kind, *req.args)
        finally:
            req.event.set()


class GuiInteraction(Interaction):
    """Interaction that shows dialogs on the GUI thread."""

    def __init__(self, parent=None, known_passwords=None):
        self.parent = parent
        self.known_passwords = list(known_passwords or [])
        self.last_password = None
        self._bridge = _Bridge(self._handle)

    def _ask(self, kind, *args):
        if QThread.currentThread() is QApplication.instance().thread():
            return self._handle(kind, *args)
        req = _Request(kind, args)
        self._bridge.ask.emit(req)
        req.event.wait()
        return req.result

    def ask_overwrite(self, path, entry: Entry):
        return self._ask("overwrite", path, entry)

    def ask_password(self, archive, entry=""):
        # try passwords from the organizer silently first
        if self.known_passwords:
            return self.known_passwords.pop(0)
        pw = self._ask("password", archive, entry)
        self.last_password = pw
        return pw

    def _handle(self, kind, *args):
        from .dialogs import OverwriteDialog, PasswordDialog

        parent = QApplication.activeWindow() or self.parent
        if kind == "overwrite":
            return OverwriteDialog.ask(parent, *args)
        if kind == "password":
            return PasswordDialog.ask(parent, *args)
        return None


class _Worker(QThread):
    progress = Signal(object, object, str)
    message = Signal(str)

    def __init__(self, fn, reporter, ui):
        super().__init__()
        self.fn, self.reporter, self.ui = fn, reporter, ui
        self.result = None
        self.error = None
        self.tb = ""

    def run(self):
        try:
            self.result = self.fn(self.reporter, self.ui)
        except BaseException as exc:  # noqa: BLE001 - reported to the user
            self.error = exc
            self.tb = traceback.format_exc()


class ProgressDialog(QDialog):
    def __init__(self, parent, title):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowIcon(icons.icon("archive"))
        self.setMinimumWidth(520)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        lay = QVBoxLayout(self)
        self.op = QLabel(title)
        self.op.setObjectName("heading")
        self.file = QLabel("")
        self.file.setObjectName("muted")
        self.file.setMinimumWidth(480)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        row = QHBoxLayout()
        self.elapsed = QLabel("Elapsed: 0:00")
        self.remaining = QLabel("")
        self.speed = QLabel("")
        for w in (self.elapsed, self.remaining, self.speed):
            w.setObjectName("muted")
        row.addWidget(self.elapsed)
        row.addStretch()
        row.addWidget(self.speed)
        row.addStretch()
        row.addWidget(self.remaining)
        lay.addWidget(self.op)
        lay.addWidget(self.file)
        lay.addWidget(self.bar)
        lay.addLayout(row)
        btns = QHBoxLayout()
        btns.addStretch()
        self.bg = QPushButton("&Background")
        self.pause = QPushButton(icons.icon("pause", False), "&Pause")
        self.cancel = QPushButton("Cancel")
        for b in (self.bg, self.pause, self.cancel):
            btns.addWidget(b)
        lay.addLayout(btns)
        self.start = time.monotonic()
        self.reporter: Reporter | None = None
        self.bg.clicked.connect(self._background)
        self.pause.clicked.connect(self._toggle_pause)
        self.cancel.clicked.connect(self._cancel)

    def _background(self):
        if self.parent():
            self.parent().showMinimized()
        self.showMinimized()

    def _toggle_pause(self):
        if not self.reporter:
            return
        if self.reporter.pause_event.is_set():
            self.reporter.pause_event.clear()
            self.pause.setText("&Pause")
            self.pause.setIcon(icons.icon("pause", False))
        else:
            self.reporter.pause_event.set()
            self.pause.setText("&Resume")
            self.pause.setIcon(icons.icon("play", False))

    def _cancel(self):
        if self.reporter:
            self.reporter.cancel()
        self.cancel.setEnabled(False)
        self.op.setText("Cancelling...")

    def closeEvent(self, ev):
        self._cancel()
        ev.ignore()

    def reject(self):
        self._cancel()

    @Slot(object, object, str)
    def update_progress(self, done, total, current):
        if current:
            self.file.setText(self.file.fontMetrics().elidedText(current, Qt.ElideMiddle, self.file.width()))
        el = time.monotonic() - self.start
        self.elapsed.setText(f"Elapsed: {_fmt(el)}")
        if total:
            frac = min(1.0, done / total)
            self.bar.setValue(int(frac * 1000))
            self.bar.setFormat(f"{frac * 100:.0f}%")
            if el > 1 and done:
                rate = done / el
                self.speed.setText(f"{human_size(rate)}/s")
                if 0.01 < frac < 1:
                    self.remaining.setText(f"Remaining: {_fmt((total - done) / rate)}")
        else:
            self.bar.setRange(0, 0)
        if total and self.bar.maximum() == 0:
            self.bar.setRange(0, 1000)


def _fmt(sec):
    sec = int(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class _Signals(QObject):
    progress = Signal(object, object, str)
    message = Signal(str)


def run_task(parent, title, fn, ui: GuiInteraction | None = None, show_errors=True, delay_ms=250):
    """Run fn(reporter, ui) on a thread. Returns (ok, result, exception, messages)."""
    ui = ui or GuiInteraction(parent)
    sig = _Signals()
    messages: list[str] = []
    rep = Reporter(on_progress=lambda d, t, c: sig.progress.emit(d, t, c), on_message=lambda m: sig.message.emit(m))
    dlg = ProgressDialog(parent, title)
    dlg.reporter = rep
    sig.progress.connect(dlg.update_progress, Qt.QueuedConnection)
    sig.message.connect(messages.append, Qt.QueuedConnection)
    w = _Worker(fn, rep, ui)
    w.finished.connect(dlg.accept)
    # short operations finish without flashing a window
    w.start()
    w.wait(delay_ms)
    if w.isRunning():
        dlg.exec()
    w.wait()
    QApplication.processEvents()
    err = w.error
    if err is not None and show_errors and not isinstance(err, Cancelled):
        show_error(parent, title, err, w.tb)
    return err is None, w.result, err, messages


def show_error(parent, title, exc, tb=""):
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle(title)
    from ..core.model import ArchiveError
    if isinstance(exc, (ArchiveError, OSError, ValueError, KeyError)):
        box.setText(str(exc) or exc.__class__.__name__)
    else:
        box.setText(f"Unexpected error: {exc}")
        box.setDetailedText(tb)
    box.exec()


class DiagnosticsDialog(QDialog):
    """Lists warnings/errors after an operation (corrupt files, CRC failures, ...)."""

    def __init__(self, parent, title, lines, ok_text=""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(640, 320)
        lay = QVBoxLayout(self)
        if ok_text:
            lay.addWidget(QLabel(ok_text))
        lst = QListWidget()
        lst.addItems(lines)
        lay.addWidget(lst)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        copy = bb.addButton("Copy to clipboard", QDialogButtonBox.ActionRole)
        copy.clicked.connect(lambda: QApplication.clipboard().setText("\n".join(lines)))
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
