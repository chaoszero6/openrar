"""Info, Find, Benchmark, Convert, Settings, Password organizer, Favorites, Wizard, About."""
from __future__ import annotations

import bz2
import fnmatch
import lzma
import os
import random
import threading
import time
import zlib

from PySide6.QtCore import QThread, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QMessageBox, QPlainTextEdit, QPushButton, QRadioButton,
                               QTableWidget, QTableWidgetItem, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from .. import APP_NAME, __version__
from ..core import operations as ops
from ..core import tools
from ..core.formats import looks_like_archive, open_archive
from ..core.model import ArchiveSummary, CreateOptions
from ..core.util import IS_WINDOWS, human_size
from ..platform import current
from ..core.writers import FORMATS, LEVEL_NAMES, METHODS
from . import icons

try:
    import zstandard
except ImportError:  # pragma: no cover
    zstandard = None


# =========================================================================== Info
class InfoDialog(QDialog):
    def __init__(self, parent, s: ArchiveSummary, on_edit_comment=None):
        super().__init__(parent)
        self.setWindowTitle(f"Archive information — {os.path.basename(s.path)}")
        self.setWindowIcon(icons.icon("info"))
        self.resize(560, 480)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        g = QWidget()
        f = QFormLayout(g)

        def yes(v):
            return "Yes" if v else "No"
        rows = [
            ("Archive", s.path), ("Format", s.format), ("Volumes", str(s.volumes)),
            ("Size on disk", f"{human_size(s.disk_size)} ({s.disk_size:,} bytes)"),
            ("Files", f"{s.files:,}"), ("Folders", f"{s.dirs:,}"),
            ("Total unpacked size", f"{human_size(s.unpacked)} ({s.unpacked:,} bytes)"),
            ("Packed size", f"{human_size(s.packed)}"),
            ("Ratio", f"{s.ratio:.1f}%" if s.unpacked else "—"),
            ("Methods", ", ".join(s.methods) or "—"),
            ("Solid", yes(s.solid)), ("Encrypted", yes(s.encrypted)),
            ("File names encrypted", yes(s.header_encrypted)), ("Self-extracting", yes(s.sfx)),
            ("Locked", yes(s.locked)), ("Recovery record", s.recovery or "Absent"),
        ]
        for k, v in rows:
            lbl = QLabel(v)
            lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lbl.setWordWrap(True)
            f.addRow(k + ":", lbl)
        tabs.addTab(g, "General")
        c = QWidget()
        cl = QVBoxLayout(c)
        txt = QPlainTextEdit(s.comment)
        txt.setReadOnly(True)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        txt.setFont(mono)
        cl.addWidget(txt)
        if on_edit_comment:
            b = QPushButton(icons.icon("comment"), "Edit comment...")
            b.clicked.connect(lambda: (self.accept(), on_edit_comment()))
            cl.addWidget(b, 0, Qt.AlignLeft)
        tabs.addTab(c, "Comment")
        lay.addWidget(tabs)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class CommentDialog(QDialog):
    def __init__(self, parent, text: str):
        super().__init__(parent)
        self.setWindowTitle("Archive comment")
        self.setWindowIcon(icons.icon("comment"))
        self.resize(560, 380)
        lay = QVBoxLayout(self)
        self.edit = QPlainTextEdit(text)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self.edit.setFont(mono)
        lay.addWidget(self.edit)
        row = QHBoxLayout()
        load = QPushButton("Load from file...")
        load.clicked.connect(self._load)
        row.addWidget(load)
        row.addStretch()
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _load(self):
        p, _ = QFileDialog.getOpenFileName(self, "Load comment", "", "Text files (*.txt);;All files (*)")
        if p:
            with open(p, encoding="utf-8", errors="replace") as f:
                self.edit.setPlainText(f.read())


# =========================================================================== Find
class _FindWorker(QThread):
    found = Signal(str, str, int, str)     # location, name, size, kind
    status = Signal(str)

    def __init__(self, root, masks, text, case, sub, archives):
        super().__init__()
        self.root, self.masks, self.text, self.case, self.sub, self.archives = root, masks, text, case, sub, archives
        self.stop = threading.Event()

    def _match_name(self, name):
        base = os.path.basename(name)
        return any(fnmatch.fnmatch(base.lower(), m.lower()) for m in self.masks)

    def _match_data(self, data: bytes) -> bool:
        if not self.text:
            return True
        needles = [self.text.encode("utf-8"), self.text.encode("utf-16-le")]
        if not self.case:
            data = data.lower()
            needles = [self.text.lower().encode("utf-8"), self.text.lower().encode("utf-16-le")]
        return any(n in data for n in needles)

    def run(self):
        for root, dirs, files in os.walk(self.root):
            if self.stop.is_set():
                return
            if not self.sub:
                dirs[:] = []
            self.status.emit(root)
            for fn in files:
                if self.stop.is_set():
                    return
                p = os.path.join(root, fn)
                if self._match_name(fn):
                    try:
                        if not self.text or self._match_file(p):
                            self.found.emit(root, fn, os.path.getsize(p), "file")
                    except OSError:
                        pass
                if self.archives and looks_like_archive(fn):
                    self._search_archive(p)

    def _match_file(self, p):
        with open(p, "rb") as f:
            return self._match_data(f.read(64 * 1024 * 1024))

    def _search_archive(self, p):
        try:
            h = open_archive(p)
        except Exception:
            return
        try:
            for e in h.entries():
                if self.stop.is_set():
                    return
                if e.is_dir or not self._match_name(e.name):
                    continue
                if self.text:
                    try:
                        if e.size > 64 * 1024 * 1024 or not self._match_data(h.read_bytes(e.name)):
                            continue
                    except Exception:
                        continue
                self.found.emit(p, e.name, e.size, "archive")
        except Exception:
            pass
        finally:
            h.close()


class FindDialog(QDialog):
    open_location = Signal(str, str, str)      # location, name, kind

    def __init__(self, parent, start_dir: str):
        super().__init__(parent)
        self.setWindowTitle("Find files")
        self.setWindowIcon(icons.icon("find"))
        self.resize(760, 520)
        lay = QVBoxLayout(self)
        f = QFormLayout()
        self.masks = QLineEdit("*")
        self.text = QLineEdit()
        self.text.setPlaceholderText("Optional text to search for inside files")
        self.where = QLineEdit(start_dir)
        row = QHBoxLayout()
        row.addWidget(self.where, 1)
        b = QPushButton("Browse...")
        b.clicked.connect(self._browse)
        row.addWidget(b)
        f.addRow("File names to find:", self.masks)
        f.addRow("String to find:", self.text)
        f.addRow("Disk and folder:", row)
        lay.addLayout(f)
        opts = QHBoxLayout()
        self.sub = QCheckBox("Search in subfolders")
        self.sub.setChecked(True)
        self.arc = QCheckBox("Search inside archives")
        self.arc.setChecked(True)
        self.case = QCheckBox("Match case")
        for w in (self.sub, self.arc, self.case):
            opts.addWidget(w)
        opts.addStretch()
        self.go = QPushButton(icons.icon("find"), "Find")
        self.go.setDefault(True)
        self.go.clicked.connect(self._toggle)
        opts.addWidget(self.go)
        lay.addLayout(opts)
        self.results = QTreeWidget()
        self.results.setHeaderLabels(["Name", "Size", "Location"])
        self.results.setRootIsDecorated(False)
        self.results.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.results.itemDoubleClicked.connect(self._open)
        lay.addWidget(self.results)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        lay.addWidget(self.status)
        self.worker = None

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Folder", self.where.text())
        if d:
            self.where.setText(os.path.normpath(d))

    def _toggle(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop.set()
            return
        self.results.clear()
        masks = [m.strip() for m in self.masks.text().replace(",", ";").split(";") if m.strip()] or ["*"]
        self.worker = _FindWorker(self.where.text(), masks, self.text.text(), self.case.isChecked(),
                                  self.sub.isChecked(), self.arc.isChecked())
        self.worker.found.connect(self._add)
        self.worker.status.connect(lambda s: self.status.setText(self.status.fontMetrics().elidedText(
            "Searching " + s, Qt.ElideMiddle, self.status.width())))
        self.worker.finished.connect(self._done)
        self.go.setText("Stop")
        self.worker.start()

    def _add(self, loc, name, size, kind):
        it = QTreeWidgetItem([name, human_size(size), loc])
        it.setData(0, Qt.UserRole, (loc, name, kind))
        it.setIcon(0, icons.icon("archive" if kind == "archive" else "report", kind == "archive"))
        self.results.addTopLevelItem(it)

    def _done(self):
        self.go.setText("Find")
        self.status.setText(f"{self.results.topLevelItemCount()} file(s) found")

    def _open(self, item):
        loc, name, kind = item.data(0, Qt.UserRole)
        self.open_location.emit(loc, name, kind)

    def closeEvent(self, ev):
        if self.worker:
            self.worker.stop.set()
            self.worker.wait()
        super().closeEvent(ev)


# =========================================================================== Benchmark
def _bench_data(size=16 * 1024 * 1024) -> bytes:
    """Mixed, moderately compressible data (text-like words + some binary noise)."""
    rnd = random.Random(1234)
    words = [bytes(rnd.choice(b"abcdefghijklmnopqrstuvwxyz") for _ in range(rnd.randint(2, 10))) for _ in range(4000)]
    out = bytearray()
    while len(out) < size:
        if rnd.random() < 0.08:
            out += rnd.randbytes(rnd.randint(16, 256))
        else:
            out += b" ".join(rnd.choice(words) for _ in range(40)) + b"\n"
    return bytes(out[:size])


def _codecs(level_preset=6):
    cs = [
        ("Deflate (ZIP)", lambda d: zlib.compress(d, level_preset), zlib.decompress),
        ("BZip2", lambda d: bz2.compress(d, 9), bz2.decompress),
        ("LZMA2 (7Z/XZ)", lambda d: lzma.compress(d, preset=level_preset), lzma.decompress),
    ]
    if zstandard is not None:
        c = zstandard.ZstdCompressor(level=6)
        dd = zstandard.ZstdDecompressor()
        cs.append(("Zstandard", c.compress, dd.decompress))
    return cs


class _BenchWorker(QThread):
    result = Signal(str, float, float, float)

    def __init__(self, size_mb):
        super().__init__()
        self.size = size_mb * 1024 * 1024
        self.stop = threading.Event()

    def run(self):
        data = _bench_data(self.size)
        while not self.stop.is_set():
            for name, comp, decomp in _codecs():
                if self.stop.is_set():
                    return
                t0 = time.perf_counter()
                c = comp(data)
                t1 = time.perf_counter()
                decomp(c)
                t2 = time.perf_counter()
                self.result.emit(name, len(data) / (t1 - t0), len(data) / (t2 - t1), 100 * len(c) / len(data))


class BenchmarkDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Benchmark")
        self.setWindowIcon(icons.icon("benchmark"))
        self.resize(620, 300)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Test data size:"))
        self.size = QComboBox()
        self.size.addItems(["8 MB", "16 MB", "32 MB", "64 MB"])
        self.size.setCurrentIndex(1)
        top.addWidget(self.size)
        top.addStretch()
        self.restart = QPushButton(icons.icon("refresh", False), "Restart")
        self.restart.clicked.connect(self._start)
        top.addWidget(self.restart)
        lay.addLayout(top)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Method", "Compress", "Decompress", "Ratio", "Runs"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        lay.addWidget(self.table)
        note = QLabel("Speeds are averaged over all runs while this window stays open. Single-threaded engine speed.")
        note.setObjectName("muted")
        lay.addWidget(note)
        self.rows: dict[str, list] = {}
        self.worker = None
        QTimer.singleShot(100, self._start)

    def _start(self):
        if self.worker:
            self.worker.stop.set()
            self.worker.wait()
        self.rows.clear()
        self.table.setRowCount(0)
        self.worker = _BenchWorker(int(self.size.currentText().split()[0]))
        self.worker.result.connect(self._on_result)
        self.worker.start()

    def _on_result(self, name, cs, ds, ratio):
        r = self.rows.get(name)
        if r is None:
            row = self.table.rowCount()
            self.table.insertRow(row)
            r = self.rows[name] = [row, 0.0, 0.0, 0]
        r[1] += cs
        r[2] += ds
        r[3] += 1
        n = r[3]
        for col, val in enumerate([name, f"{human_size(r[1] / n)}/s", f"{human_size(r[2] / n)}/s",
                                   f"{ratio:.1f}%", str(n)]):
            self.table.setItem(r[0], col, QTableWidgetItem(val))

    def closeEvent(self, ev):
        if self.worker:
            self.worker.stop.set()
            self.worker.wait()
        super().closeEvent(ev)

    def reject(self):
        if self.worker:
            self.worker.stop.set()
            self.worker.wait()
        super().reject()


# =========================================================================== Convert
class ConvertDialog(QDialog):
    def __init__(self, parent, archives: list[str]):
        super().__init__(parent)
        self.setWindowTitle("Convert archives")
        self.setWindowIcon(icons.icon("convert"))
        self.resize(600, 480)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Archives to convert:"))
        self.list = QListWidget()
        self.list.addItems(archives)
        lay.addWidget(self.list)
        row = QHBoxLayout()
        a = QPushButton("Add...")
        r = QPushButton("Remove")
        a.clicked.connect(self._add)
        r.clicked.connect(lambda: [self.list.takeItem(self.list.row(i)) for i in self.list.selectedItems()])
        row.addWidget(a)
        row.addWidget(r)
        row.addStretch()
        lay.addLayout(row)
        f = QFormLayout()
        self.fmt = QComboBox()
        for k, v in FORMATS.items():
            self.fmt.addItem(v, k)
        self.fmt.addItem("SFX for Windows (.exe)", "sfx")
        self.fmt.addItem("SFX for Linux (.run)", "sfx-linux")
        self.level = QComboBox()
        self.level.addItems(LEVEL_NAMES)
        self.level.setCurrentIndex(3)
        self.method = QComboBox()
        self.fmt.currentIndexChanged.connect(self._fmt_changed)
        self.dest = QLineEdit()
        self.dest.setPlaceholderText("Same folder as each archive")
        drow = QHBoxLayout()
        drow.addWidget(self.dest, 1)
        b = QPushButton("Browse...")
        b.clicked.connect(lambda: self.dest.setText(QFileDialog.getExistingDirectory(self, "Folder") or self.dest.text()))
        drow.addWidget(b)
        self.pw_mode = QComboBox()
        self.pw_mode.addItems(["No encryption", "Keep the original password", "Set a new password..."])
        self.pw_mode.currentIndexChanged.connect(self._pw)
        self.new_pw = ""
        self.new_names = False
        self.delete = QCheckBox("Delete original archives")
        self.rr = QCheckBox("Add recovery record (3%)")
        f.addRow("Archive types:", self.fmt)
        f.addRow("Compression:", self.level)
        f.addRow("Algorithm:", self.method)
        f.addRow("Destination:", drow)
        f.addRow("Encryption:", self.pw_mode)
        f.addRow("", self.rr)
        f.addRow("", self.delete)
        lay.addLayout(f)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._fmt_changed()

    def _fmt_changed(self):
        k = self.fmt.currentData()
        self.method.clear()
        self.method.addItems(METHODS.get({"sfx": "zip", "sfx-linux": "tar.gz"}.get(k, k), ["Default"]))

    def _pw(self, idx):
        if idx == 2:
            from .dialogs import SetPasswordDialog
            d = SetPasswordDialog(self, self.fmt.currentData(), self.new_pw, self.new_names)
            if d.exec() == QDialog.Accepted and d.p1.text():
                self.new_pw = d.p1.text()
                self.new_names = d.names.isChecked()
                if self.new_names and self.fmt.currentData() != "7z":
                    self.fmt.setCurrentIndex(self.fmt.findData("7z"))   # only 7Z can hide file names
            else:
                self.pw_mode.setCurrentIndex(0)

    def _add(self):
        fs, _ = QFileDialog.getOpenFileNames(self, "Add archives")
        self.list.addItems([os.path.normpath(x) for x in fs])

    def jobs(self):
        from ..core.util import strip_archive_ext
        k = self.fmt.currentData()
        out = []
        for i in range(self.list.count()):
            src = self.list.item(i).text()
            folder = self.dest.text().strip() or os.path.dirname(src)
            ext = {"sfx": "exe", "sfx-linux": "run"}.get(k, k)
            dest = os.path.join(folder, os.path.basename(strip_archive_ext(src)) + "." + ext)
            if os.path.abspath(dest) == os.path.abspath(src):
                dest = os.path.join(folder, os.path.basename(strip_archive_ext(src)) + "_converted." + ext)
            pw = {0: "", 1: "<same>", 2: self.new_pw}[self.pw_mode.currentIndex()]
            sfx_kind = {"sfx": "windows", "sfx-linux": "linux"}.get(k, "")
            opts = CreateOptions(format={"sfx": "zip", "sfx-linux": "tar.gz"}.get(k, k), level=self.level.currentIndex(),
                                 method=self.method.currentText(), password="" if sfx_kind else pw, sfx_target=sfx_kind,
                                 encrypt_names=self.pw_mode.currentIndex() == 2 and self.new_names and k == "7z",
                                 sfx=bool(sfx_kind), recovery_percent=3 if self.rr.isChecked() else 0)
            out.append((src, dest, opts))
        return out


# =========================================================================== Password organizer
class PasswordOrganizer(QDialog):
    def __init__(self, parent, pick=False):
        super().__init__(parent)
        from .app import settings
        self.s = settings()
        self.setWindowTitle("Organize passwords")
        self.setWindowIcon(icons.icon("key"))
        self.resize(460, 340)
        self.picked = None
        lay = QVBoxLayout(self)
        note = QLabel("Saved passwords are tried automatically when opening encrypted archives." +
                      " " + current().protection_note)
        note.setWordWrap(True)
        note.setObjectName("muted")
        lay.addWidget(note)
        self.items = self.s.passwords()
        self.list = QListWidget()
        self._refresh()
        lay.addWidget(self.list)
        row = QHBoxLayout()
        for label, fn in [("Add...", self._add), ("Edit...", self._edit), ("Delete", self._del)]:
            b = QPushButton(label)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        if pick:
            bb.button(QDialogButtonBox.Ok).setText("Use selected")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.pick = pick

    def _refresh(self):
        self.list.clear()
        self.list.addItems([label for label, _ in self.items])

    def _ask(self, label="", pw=""):
        label, ok = QInputDialog.getText(self, "Password", "Label (shown in lists):", text=label)
        if not ok or not label:
            return None
        pw, ok = QInputDialog.getText(self, "Password", "Password:", QLineEdit.Password, pw)
        if not ok or not pw:
            return None
        return label, pw

    def _add(self):
        r = self._ask()
        if r:
            self.items.append(r)
            self._refresh()

    def _edit(self):
        i = self.list.currentRow()
        if i >= 0:
            r = self._ask(*self.items[i])
            if r:
                self.items[i] = r
                self._refresh()

    def _del(self):
        i = self.list.currentRow()
        if i >= 0:
            del self.items[i]
            self._refresh()

    def _ok(self):
        self.s.set_passwords(self.items)
        self.s.save()
        if self.pick and self.list.currentRow() >= 0:
            self.picked = self.items[self.list.currentRow()][1]
        self.accept()


# =========================================================================== Favorites
class FavoritesDialog(QDialog):
    def __init__(self, parent, favorites: list[dict]):
        super().__init__(parent)
        self.setWindowTitle("Organize favorites")
        self.setWindowIcon(icons.icon("star"))
        self.resize(560, 320)
        self.favs = [dict(f) for f in favorites]
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        lay.addWidget(self.list)
        row = QHBoxLayout()
        for label, fn in [("Rename...", self._rename), ("Delete", self._del), ("Move up", lambda: self._move(-1)),
                          ("Move down", lambda: self._move(1))]:
            b = QPushButton(label)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._refresh()

    def _refresh(self, sel=0):
        self.list.clear()
        for f in self.favs:
            self.list.addItem(f"{f['name']}    —    {f['path']}")
        if self.favs:
            self.list.setCurrentRow(max(0, min(sel, len(self.favs) - 1)))

    def _rename(self):
        i = self.list.currentRow()
        if i >= 0:
            name, ok = QInputDialog.getText(self, "Rename", "Name:", text=self.favs[i]["name"])
            if ok and name:
                self.favs[i]["name"] = name
                self._refresh(i)

    def _del(self):
        i = self.list.currentRow()
        if i >= 0:
            del self.favs[i]
            self._refresh(i)

    def _move(self, d):
        i = self.list.currentRow()
        j = i + d
        if 0 <= i < len(self.favs) and 0 <= j < len(self.favs):
            self.favs[i], self.favs[j] = self.favs[j], self.favs[i]
            self._refresh(j)


# =========================================================================== Settings
ASSOC_EXTS = [".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".iso", ".cab", ".arj", ".lzh",
              ".001", ".cbz", ".cbr", ".wim"] + ([] if IS_WINDOWS else [".run"])


class SettingsDialog(QDialog):
    def __init__(self, parent, s):
        super().__init__(parent)
        self.s = s
        self.setWindowTitle("Settings")
        self.setWindowIcon(icons.icon("settings"))
        self.resize(600, 520)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)

        # General
        g = QWidget()
        gf = QFormLayout(g)
        self.theme = QComboBox()
        self.theme.addItem(current().system_theme_label, "system")
        self.theme.addItem("Light", "light")
        self.theme.addItem("Dark", "dark")
        self.theme.setCurrentIndex(max(0, self.theme.findData(s["theme"])))
        self.fmt = QComboBox()
        for k, v in FORMATS.items():
            self.fmt.addItem(v, k)
        self.fmt.setCurrentIndex(max(0, self.fmt.findData(s["default_format"])))
        self.level = QComboBox()
        self.level.addItems(LEVEL_NAMES)
        self.level.setCurrentIndex(s["default_level"])
        self.confirm = QCheckBox("Ask before deleting files from archives")
        self.confirm.setChecked(s["confirm_delete"])
        self.tree = QCheckBox("Show folder tree")
        self.tree.setChecked(s["show_tree"])
        gf.addRow("Theme:", self.theme)
        gf.addRow("Default archive format:", self.fmt)
        gf.addRow("Default compression:", self.level)
        gf.addRow("", self.confirm)
        gf.addRow("", self.tree)
        tabs.addTab(g, "General")

        # Paths
        p = QWidget()
        pf = QFormLayout(p)
        self.ext_default = QLineEdit(s["extract_default"])
        self.ext_default.setPlaceholderText("Next to the archive")
        pf.addRow("Default extraction folder:", self._browse_row(self.ext_default))
        self.temp = QLineEdit(s["temp_dir"])
        self.temp.setPlaceholderText("System temporary folder")
        pf.addRow("Folder for temporary files:", self._browse_row(self.temp))
        tabs.addTab(p, "Paths")

        # Integration
        it = QWidget()
        il = QVBoxLayout(it)
        sh = s["shell"]
        cm = QGroupBox(f"{current().file_manager[:1].upper()}{current().file_manager[1:]} context menu")
        cml = QVBoxLayout(cm)
        self.sh = {}
        for key, label in [("cascaded", "Cascaded context menu (all items under an OpenRAR submenu)"),
                           ("add", "Add to archive..."), ("add_quick", 'Add to "<name>.zip"'),
                           ("extract", "Extract files..."), ("extract_here", "Extract Here"),
                           ("extract_to", "Extract to <folder>\\"), ("test", "Test archive")]:
            cb = QCheckBox(label)
            cb.setChecked(sh.get(key, False))
            self.sh[key] = cb
            cml.addWidget(cb)
        il.addWidget(cm)
        ab = QGroupBox("Open these archive types with OpenRAR")
        al = QHBoxLayout(ab)
        self.assoc = QListWidget()
        self.assoc.setFlow(QListWidget.LeftToRight)
        self.assoc.setWrapping(True)
        self.assoc.setMaximumHeight(90)
        for e in ASSOC_EXTS:
            from PySide6.QtWidgets import QListWidgetItem
            item = QListWidgetItem(e)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if e in sh.get("associate", []) else Qt.Unchecked)
            self.assoc.addItem(item)
        al.addWidget(self.assoc)
        il.addWidget(ab)
        row = QHBoxLayout()
        apply_b = QPushButton("Apply integration")
        remove_b = QPushButton("Remove all integration")
        apply_b.clicked.connect(self._apply_shell)
        remove_b.clicked.connect(self._remove_shell)
        row.addWidget(apply_b)
        row.addWidget(remove_b)
        row.addStretch()
        il.addLayout(row)
        note = QLabel(current().integration_note)
        note.setWordWrap(True)
        note.setObjectName("muted")
        il.addWidget(note)
        il.addStretch()
        if not current().supports_integration:
            for w in (cm, ab, apply_b, remove_b):
                w.setEnabled(False)
        tabs.addTab(it, "Integration")

        # Tools
        t = QWidget()
        tf = QFormLayout(t)
        self.t7z = QLineEdit(s["tool_7z"])
        self.t7z.setPlaceholderText(tools.find_7z() or "not found — install 7-Zip from 7-zip.org")
        self.tunrar = QLineEdit(s["tool_unrar"])
        self.tunrar.setPlaceholderText(tools.find_unrar() or "not found (optional)")
        tf.addRow("7-Zip:", self._browse_row(self.t7z, file=True))
        tf.addRow("UnRAR:", self._browse_row(self.tunrar, file=True))
        info = QLabel("OpenRAR creates and edits ZIP, 7Z and TAR archives on its own. Unpacking compressed RAR "
                      "data and formats such as ISO, CAB, ARJ, LZH, WIM and DMG uses the free 7-Zip (or UnRAR) "
                      "when it is installed." + (" Windows 10/11's built-in tar.exe is used as a fallback." if IS_WINDOWS
                                                  else " On Linux install p7zip-full (or 7zip) and unrar."))
        info.setWordWrap(True)
        info.setObjectName("muted")
        tf.addRow(info)
        st = "\n".join(f"{k}: {v or 'not found'}" for k, v in tools.tool_status().items())
        tf.addRow("Detected:", QLabel(st))
        tabs.addTab(t, "Tools")

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _browse_row(self, edit, file=False):
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(edit, 1)
        b = QPushButton("Browse...")

        def go():
            if file:
                p, _ = QFileDialog.getOpenFileName(self, "Select program", edit.text(), current().tool_filter)
            else:
                p = QFileDialog.getExistingDirectory(self, "Select folder", edit.text())
            if p:
                edit.setText(os.path.normpath(p))
        b.clicked.connect(go)
        row.addWidget(b)
        return w

    def _shell_cfg(self):
        cfg = {k: cb.isChecked() for k, cb in self.sh.items()}
        cfg["associate"] = [self.assoc.item(i).text() for i in range(self.assoc.count())
                            if self.assoc.item(i).checkState() == Qt.Checked]
        return cfg

    def _apply_shell(self):
        try:
            current().install_integration(self._shell_cfg())
            QMessageBox.information(self, "Integration", f"{current().integration_title} updated.")
        except Exception as exc:
            QMessageBox.warning(self, "Integration", f"Could not update integration: {exc}")

    def _remove_shell(self):
        try:
            current().uninstall_integration()
            QMessageBox.information(self, "Integration", f"{current().integration_title} removed.")
        except Exception as exc:
            QMessageBox.warning(self, "Integration", f"Could not remove integration: {exc}")

    def _ok(self):
        s = self.s
        s["theme"] = self.theme.currentData()
        s["default_format"] = self.fmt.currentData()
        s["default_level"] = self.level.currentIndex()
        s["confirm_delete"] = self.confirm.isChecked()
        s["show_tree"] = self.tree.isChecked()
        s["extract_default"] = self.ext_default.text().strip()
        s["temp_dir"] = self.temp.text().strip()
        s["tool_7z"] = self.t7z.text().strip()
        s["tool_unrar"] = self.tunrar.text().strip()
        s["shell"] = self._shell_cfg()
        s.apply_tools()
        s.save()
        self.accept()


# =========================================================================== Wizard
class WizardDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Wizard")
        self.setWindowIcon(icons.icon("wizard"))
        self.resize(460, 260)
        lay = QVBoxLayout(self)
        h = QLabel("What would you like to do?")
        h.setObjectName("heading")
        lay.addWidget(h)
        self.group = QButtonGroup(self)
        for i, (k, label, desc) in enumerate([
                ("extract", "Unpack an archive", "Choose an archive and where to extract it"),
                ("create", "Create a new archive", "Choose files and folders to pack"),
                ("add", "Add files to an existing archive", "Choose the archive, then the files")]):
            rb = QRadioButton(label)
            rb.setProperty("k", k)
            rb.setChecked(i == 0)
            self.group.addButton(rb, i)
            lay.addWidget(rb)
            d = QLabel("      " + desc)
            d.setObjectName("muted")
            lay.addWidget(d)
        lay.addStretch()
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Next >")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def choice(self):
        return self.group.checkedButton().property("k")


# =========================================================================== About
class AboutDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        from PySide6.QtGui import QPixmap
        from .settings import resource
        self.setWindowTitle(f"About {APP_NAME}")
        lay = QHBoxLayout(self)
        pic = QLabel()
        pic.setPixmap(QPixmap(resource("icon.png")).scaled(96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        lay.addWidget(pic, 0, Qt.AlignTop)
        right = QVBoxLayout()
        t = QLabel(f"{APP_NAME} {__version__}")
        t.setObjectName("heading")
        right.addWidget(t)
        txt = QLabel(
            "A free archive manager for Windows and Linux.\n\n"
            "Create: ZIP, 7Z, TAR, TAR.GZ, TAR.BZ2, TAR.XZ, TAR.ZST, self-extracting EXE\n"
            "Open: all of the above plus RAR (v1.5–5), ISO, CAB, ARJ, LZH, WIM and more\n"
            "AES-256 encryption, split volumes, recovery records, repair, lock, comments.\n\n"
            "RAR is a proprietary format: OpenRAR can open and extract .rar archives but, like every "
            "program other than WinRAR, cannot create them.")
        txt.setWordWrap(True)
        right.addWidget(txt)
        st = QLabel("Helpers: " + ", ".join(f"{k} {'✓' if v else '—'}" for k, v in tools.tool_status().items()))
        st.setObjectName("muted")
        right.addWidget(st)
        bb = QDialogButtonBox(QDialogButtonBox.Ok)
        bb.accepted.connect(self.accept)
        right.addWidget(bb)
        lay.addLayout(right)
        self.setFixedWidth(560)


# =========================================================================== SFX options
def sfx_type_combo(name: str = "") -> QComboBox:
    c = QComboBox()
    c.addItem("Windows (.exe)", "windows")
    c.addItem("Linux / Unix (.run)", "linux")
    c.setCurrentIndex(c.findData(ops.sfx_target(name if name.lower().endswith((".exe", ".run")) else "")))
    return c


def apply_sfx_placeholders(target: str, path_edit: QLineEdit, setup_edit: QLineEdit):
    if target == "windows":
        path_edit.setPlaceholderText(r"e.g. %ProgramFiles%\MyApp  (default: next to the .exe)")
        setup_edit.setPlaceholderText("e.g. setup.exe /quiet")
    else:
        path_edit.setPlaceholderText("e.g. $HOME/MyApp  (default: next to the .run file)")
        setup_edit.setPlaceholderText("e.g. sh ./install.sh")


class SfxDialog(QDialog):
    """Options for turning an existing archive into a self-extracting .exe (Windows) or .run (Linux)."""

    def __init__(self, parent, archive: str, current: dict | None = None):
        super().__init__(parent)
        from ..core.util import strip_archive_ext
        cur = current or {}
        self.setWindowTitle("Convert archive to SFX")
        self.setWindowIcon(icons.icon("sfx"))
        self.resize(560, 460)
        lay = QVBoxLayout(self)
        f = QFormLayout()
        self._base = strip_archive_ext(archive)
        self.kind = sfx_type_combo(archive)
        self.out = QLineEdit(self._base + "." + ops.sfx_extension(self.kind.currentData()))
        self.title = QLineEdit(cur.get("Title", ""))
        self.text = QPlainTextEdit(cur.get("Text", "").replace("\\n", "\n"))
        self.text.setMaximumHeight(80)
        self.path = QLineEdit(cur.get("Path", ""))
        self.setup = QLineEdit(cur.get("Setup", ""))
        self.over = QComboBox()
        self.over.addItems(["Ask before overwrite", "Overwrite all files", "Skip existing files"])
        self.over.setCurrentIndex(int(cur.get("Overwrite", 0) or 0))
        self.silent = QCheckBox("Hide all (silent mode)")
        self.silent.setChecked(cur.get("Silent") == "1")
        self.temp = QCheckBox("Unpack to temporary folder, run setup, then delete")
        self.temp.setChecked(cur.get("TempMode") == "1")
        self.license = QPlainTextEdit(cur.get("License", "").replace("\\n", "\n"))
        self.license.setMaximumHeight(80)
        f.addRow("SFX type:", self.kind)
        f.addRow("SFX file name:", self.out)
        f.addRow("Title:", self.title)
        f.addRow("Text to display:", self.text)
        f.addRow("Path to extract:", self.path)
        f.addRow("Run after extraction:", self.setup)
        f.addRow("Overwrite mode:", self.over)
        f.addRow("", self.silent)
        f.addRow("", self.temp)
        f.addRow("License:", self.license)
        lay.addLayout(f)
        self.note = QLabel()
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self._kind_changed()

    def target(self) -> str:
        return self.kind.currentData()

    def _kind_changed(self, *_):
        t = self.target()
        name = self.out.text().strip()
        if name.lower().endswith((".exe", ".run")):
            self.out.setText(name[:-4] + "." + ops.sfx_extension(t))
        apply_sfx_placeholders(t, self.path, self.setup)
        self.note.setText("Encrypted and non-ZIP archives are repacked as ZIP for the Windows SFX module."
                          if t == "windows" else
                          "The Linux SFX is a shell script with a TAR.GZ archive inside (no encryption). Run it "
                          "with: sh name.run [--target DIR] [--silent]")

    def options(self) -> dict:
        return {"Title": self.title.text(), "Text": self.text.toPlainText(), "Path": self.path.text(),
                "Setup": self.setup.text(), "Silent": self.silent.isChecked(),
                "Overwrite": self.over.currentIndex() or "", "TempMode": self.temp.isChecked(),
                "License": self.license.toPlainText()}
