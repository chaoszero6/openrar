"""Prompts plus the Add (archive name and parameters) and Extract dialogs."""
from __future__ import annotations

import os

from PySide6.QtCore import QDir, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFileSystemModel, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
                               QRadioButton, QTabWidget, QTreeView, QVBoxLayout, QWidget)

from ..core.model import CreateOptions, Entry, ExtractOptions
from ..core.util import default_archive_name, human_size, parse_size
from ..core.writers import FORMATS, LEVEL_NAMES, METHODS
from . import icons

VOLUME_PRESETS = [
    ("", "Don't split"),
    ("1440k", "1.44 MB (floppy)"),
    ("10m", "10 MB (e-mail)"),
    ("25m", "25 MB (e-mail attachment)"),
    ("100m", "100 MB"),
    ("700m", "700 MB (CD)"),
    ("4095m", "4095 MB (FAT32)"),
    ("4480m", "4.7 GB (DVD)"),
    ("8100m", "8.5 GB (DVD DL)"),
    ("23g", "25 GB (Blu-ray)"),
]
UPDATE_MODES = [("add", "Add and replace files"), ("update", "Add and update files"),
                ("freshen", "Fresh existing files only"), ("sync", "Synchronize archive contents")]
OVERWRITE_MODES = [("ask", "Ask before overwrite"), ("always", "Overwrite without prompt"),
                   ("never", "Skip existing files"), ("rename", "Rename automatically"),
                   ("newer", "Overwrite only older files")]


def _browse_row(edit: QLineEdit, mode="dir", parent=None, caption="Browse", filt=""):
    btn = QPushButton("Browse...")

    def go():
        start = edit.text() or QDir.homePath()
        if mode == "dir":
            p = QFileDialog.getExistingDirectory(parent, caption, start)
        elif mode == "save":
            p, _ = QFileDialog.getSaveFileName(parent, caption, start, filt)
        else:
            p, _ = QFileDialog.getOpenFileName(parent, caption, start, filt)
        if p:
            edit.setText(os.path.normpath(p))
    btn.clicked.connect(go)
    row = QHBoxLayout()
    row.addWidget(edit, 1)
    row.addWidget(btn)
    return row


# =========================================================================== prompts
class OverwriteDialog(QDialog):
    def __init__(self, parent, path: str, entry: Entry):
        super().__init__(parent)
        self.setWindowTitle("Confirm file replace")
        self.answer = "no"
        lay = QVBoxLayout(self)
        t = QLabel("The following file already exists:")
        t.setObjectName("heading")
        lay.addWidget(t)
        lay.addWidget(QLabel(path))
        try:
            st = os.stat(path)
            from datetime import datetime
            old = f"{human_size(st.st_size)}, modified {datetime.fromtimestamp(st.st_mtime):%Y-%m-%d %H:%M:%S}"
        except OSError:
            old = ""
        new = f"{human_size(entry.size)}" + (f", modified {entry.mtime:%Y-%m-%d %H:%M:%S}" if entry.mtime else "")
        g = QFormLayout()
        g.addRow("Existing file:", QLabel(old))
        g.addRow("Replace with:", QLabel(new))
        lay.addLayout(g)
        bb = QGridLayout()
        for i, (key, label) in enumerate([("yes", "&Yes"), ("yes_all", "Yes to &All"), ("rename", "&Rename"),
                                          ("no", "&No"), ("no_all", "N&ever"), ("cancel", "Cancel")]):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, k=key: self._done(k))
            bb.addWidget(b, i // 3, i % 3)
        lay.addLayout(bb)

    def _done(self, k):
        self.answer = k
        self.accept()

    def reject(self):
        self.answer = "cancel"
        super().reject()

    @staticmethod
    def ask(parent, path, entry):
        d = OverwriteDialog(parent, path, entry)
        d.exec()
        return d.answer


class PasswordDialog(QDialog):
    def __init__(self, parent, archive: str, note: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Enter password")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"Enter password for the encrypted archive\n{os.path.basename(archive)}"))
        if note:
            n = QLabel(note)
            n.setObjectName("muted")
            lay.addWidget(n)
        self.edit = QComboBox()
        self.edit.setEditable(True)
        self.edit.lineEdit().setEchoMode(QLineEdit.Password)
        try:
            from .app import settings
            for label, _pw in settings().passwords():
                self.edit.addItem(label)
            self._saved = dict(settings().passwords())
        except Exception:
            self._saved = {}
        self.edit.setCurrentText("")
        lay.addWidget(self.edit)
        self.show_pw = QCheckBox("Show password")
        self.show_pw.toggled.connect(lambda on: self.edit.lineEdit().setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        lay.addWidget(self.show_pw)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def password(self):
        t = self.edit.currentText()
        return self._saved.get(t, t)

    @staticmethod
    def ask(parent, archive, note=""):
        d = PasswordDialog(parent, archive, note)
        if d.exec() == QDialog.Accepted and d.password():
            return d.password()
        return None


class SetPasswordDialog(QDialog):
    def __init__(self, parent, fmt: str, password="", encrypt_names=False):
        super().__init__(parent)
        self.setWindowTitle("Enter password")
        lay = QVBoxLayout(self)
        f = QFormLayout()
        self.p1 = QLineEdit(password)
        self.p2 = QLineEdit(password)
        for e in (self.p1, self.p2):
            e.setEchoMode(QLineEdit.Password)
        f.addRow("Enter password:", self.p1)
        f.addRow("Reenter password:", self.p2)
        lay.addLayout(f)
        self.fmt = fmt
        self.show_pw = QCheckBox("Show password")
        self.show_pw.toggled.connect(self._toggle)
        self.names = QCheckBox("Encrypt file names")
        self.names.setChecked(encrypt_names)
        self.names.setToolTip("Hides the list of files: the password is needed even to see what is inside.\n"
                              "Only the 7Z format supports this.")
        lay.addWidget(self.show_pw)
        lay.addWidget(self.names)
        self.info = QLabel()
        self.info.setObjectName("muted")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.names.toggled.connect(self._update_info)
        self._update_info()
        org = QPushButton(icons.icon("key"), "Organize passwords...")
        org.clicked.connect(self._organize)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.addButton(org, QDialogButtonBox.ResetRole)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _organize(self):
        from .tool_dialogs import PasswordOrganizer
        d = PasswordOrganizer(self, pick=True)
        if d.exec() == QDialog.Accepted and d.picked is not None:
            self.p1.setText(d.picked)
            self.p2.setText(d.picked)

    def _toggle(self, on):
        mode = QLineEdit.Normal if on else QLineEdit.Password
        self.p1.setEchoMode(mode)
        self.p2.setEchoMode(mode)
        self.p2.setVisible(not on)

    def _update_info(self, *_):
        if self.names.isChecked() and self.fmt != "7z":
            text = ("ZIP archives cannot hide file names, so the archive will be created as 7Z."
                    if self.fmt in ("zip", "7z") else
                    "TAR archives cannot be encrypted, so the archive will be created as 7Z.")
        elif self.fmt in ("zip", "7z"):
            text = "ZIP and 7Z use AES-256 encryption."
        else:
            text = "TAR archives cannot be encrypted. Choose ZIP or 7Z."
        self.info.setText(text)

    def _ok(self):
        if not self.show_pw.isChecked() and self.p1.text() != self.p2.text():
            QMessageBox.warning(self, "Password", "Passwords do not match.")
            return
        self.accept()


# =========================================================================== Add dialog
class AddDialog(QDialog):
    """'Archive name and parameters'."""

    def __init__(self, parent, files: list[str], archive: str = "", settings=None, existing: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Archive name and parameters")
        self.setWindowIcon(icons.icon("add"))
        self.resize(620, 560)
        self.settings = settings
        self.files = files
        self.password = ""
        self.encrypt_names = False
        fmt = (settings["default_format"] if settings else "zip") or "zip"
        if archive:
            from ..core.writers import format_from_path
            fmt = format_from_path(archive)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)

        # ---- General
        g = QWidget()
        gl = QVBoxLayout(g)
        self.name = QComboBox()
        self.name.setEditable(True)
        self.name.setCurrentText(archive or default_archive_name(files, fmt) if files else archive)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.name, 1)
        row.addWidget(browse)
        gl.addWidget(QLabel("Archive name:"))
        gl.addLayout(row)

        cols = QHBoxLayout()
        left = QVBoxLayout()
        fbox = QGroupBox("Archive format")
        fl = QGridLayout(fbox)
        self.fmt_group = QButtonGroup(self)
        for i, (key, label) in enumerate(FORMATS.items()):
            rb = QRadioButton(label)
            rb.setProperty("fmt", key)
            self.fmt_group.addButton(rb, i)
            fl.addWidget(rb, i // 2, i % 2)
            if key == fmt:
                rb.setChecked(True)
        left.addWidget(fbox)
        form = QFormLayout()
        self.level = QComboBox()
        self.level.addItems(LEVEL_NAMES)
        self.level.setCurrentIndex(settings["default_level"] if settings else 3)
        self.method = QComboBox()
        self.volume = QComboBox()
        self.volume.setEditable(True)
        for val, label in VOLUME_PRESETS:
            self.volume.addItem(label, val)
        self.volume.setToolTip("Pick a preset or type a size such as 250m, 2g or 1440k")
        self.update_mode = QComboBox()
        for k, label in UPDATE_MODES:
            self.update_mode.addItem(label, k)
        form.addRow("Compression method:", self.level)
        form.addRow("Algorithm:", self.method)
        form.addRow("Split to volumes, size:", self.volume)
        form.addRow("Update mode:", self.update_mode)
        left.addLayout(form)
        cols.addLayout(left, 3)

        obox = QGroupBox("Archiving options")
        ol = QVBoxLayout(obox)
        self.opt_delete = QCheckBox("Delete files after archiving")
        self.opt_sfx = QCheckBox("Create SFX archive (.exe)")
        self.opt_solid = QCheckBox("Create solid archive")
        self.opt_rr = QCheckBox("Add recovery record")
        self.rr_pct = QDoubleSpinBox()
        self.rr_pct.setRange(0.5, 50)
        self.rr_pct.setValue(3)
        self.rr_pct.setSuffix(" %")
        rr = QHBoxLayout()
        rr.addWidget(self.opt_rr)
        rr.addWidget(self.rr_pct)
        self.opt_test = QCheckBox("Test archived files")
        self.opt_lock = QCheckBox("Lock archive")
        self.opt_separate = QCheckBox("Put each file to separate archive")
        for w in (self.opt_delete, self.opt_sfx, self.opt_solid):
            ol.addWidget(w)
        ol.addLayout(rr)
        for w in (self.opt_test, self.opt_lock, self.opt_separate):
            ol.addWidget(w)
        ol.addStretch()
        self.pw_btn = QPushButton(icons.icon("key"), "Set password...")
        self.pw_btn.clicked.connect(self._set_password)
        self.pw_label = QLabel("")
        self.pw_label.setObjectName("muted")
        ol.addWidget(self.pw_btn)
        ol.addWidget(self.pw_label)
        cols.addWidget(obox, 2)
        gl.addLayout(cols)
        tabs.addTab(g, "General")

        # ---- Files
        f = QWidget()
        flay = QVBoxLayout(f)
        flay.addWidget(QLabel("Files to add:"))
        self.files_edit = QPlainTextEdit("\n".join(files))
        self.files_edit.setMaximumHeight(110)
        flay.addWidget(self.files_edit)
        addf = QHBoxLayout()
        b1 = QPushButton("Add files...")
        b2 = QPushButton("Add folder...")
        b1.clicked.connect(self._add_files)
        b2.clicked.connect(self._add_folder)
        addf.addWidget(b1)
        addf.addWidget(b2)
        addf.addStretch()
        flay.addLayout(addf)
        flay.addWidget(QLabel("Files to exclude (masks separated by ';' or new lines, e.g. *.tmp; Thumbs.db):"))
        self.exclude = QPlainTextEdit()
        self.exclude.setMaximumHeight(70)
        flay.addWidget(self.exclude)
        pbox = QGroupBox("File paths")
        pl = QVBoxLayout(pbox)
        self.path_group = QButtonGroup(self)
        for i, (k, label) in enumerate([("relative", "Store relative paths"), ("none", "Do not store paths"),
                                        ("full", "Store full paths (without drive letter)")]):
            rb = QRadioButton(label)
            rb.setProperty("mode", k)
            self.path_group.addButton(rb, i)
            pl.addWidget(rb)
            rb.setChecked(i == 0)
        flay.addWidget(pbox)
        self.empty_dirs = QCheckBox("Include empty folders")
        self.empty_dirs.setChecked(True)
        flay.addWidget(self.empty_dirs)
        flay.addStretch()
        tabs.addTab(f, "Files")

        # ---- Comment
        c = QWidget()
        cl = QVBoxLayout(c)
        cl.addWidget(QLabel("Archive comment (ZIP archives):"))
        self.comment = QPlainTextEdit()
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self.comment.setFont(mono)
        cl.addWidget(self.comment)
        load = QPushButton("Load from file...")
        load.clicked.connect(self._load_comment)
        cl.addWidget(load, 0, Qt.AlignLeft)
        tabs.addTab(c, "Comment")

        # ---- SFX
        self.sfx_tab = QWidget()
        sl = QFormLayout(self.sfx_tab)
        self.sfx_title = QLineEdit()
        self.sfx_text = QPlainTextEdit()
        self.sfx_text.setMaximumHeight(80)
        self.sfx_path = QLineEdit()
        self.sfx_path.setPlaceholderText(r"e.g. %ProgramFiles%\MyApp  (default: next to the .exe)")
        self.sfx_setup = QLineEdit()
        self.sfx_setup.setPlaceholderText("e.g. setup.exe /quiet")
        self.sfx_silent = QCheckBox("Hide all (silent mode)")
        self.sfx_temp = QCheckBox("Unpack to temporary folder, run setup, then delete")
        self.sfx_over = QComboBox()
        self.sfx_over.addItems(["Ask before overwrite", "Overwrite all files", "Skip existing files"])
        self.sfx_license = QPlainTextEdit()
        self.sfx_license.setMaximumHeight(80)
        self.sfx_license.setPlaceholderText("Optional license text the user must accept")
        sl.addRow("Title:", self.sfx_title)
        sl.addRow("Text to display:", self.sfx_text)
        sl.addRow("Path to extract:", self.sfx_path)
        sl.addRow("Run after extraction:", self.sfx_setup)
        sl.addRow("Overwrite mode:", self.sfx_over)
        sl.addRow("", self.sfx_silent)
        sl.addRow("", self.sfx_temp)
        sl.addRow("License:", self.sfx_license)
        tabs.addTab(self.sfx_tab, "SFX")
        self.tabs = tabs

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        self.fmt_group.buttonToggled.connect(self._fmt_changed)
        self.opt_sfx.toggled.connect(self._fmt_changed)
        self.level.currentIndexChanged.connect(self._fmt_changed)
        self._fmt_changed()
        if existing:
            self.update_mode.setCurrentIndex(0)

    # ---- helpers
    def fmt(self) -> str:
        b = self.fmt_group.checkedButton()
        return b.property("fmt") if b else "zip"

    def _fmt_changed(self, *_):
        fmt = self.fmt()
        sfx = self.opt_sfx.isChecked()
        if sfx and fmt != "zip":
            for b in self.fmt_group.buttons():
                if b.property("fmt") == "zip":
                    b.setChecked(True)
            return
        cur = self.method.currentText()
        self.method.clear()
        self.method.addItems(METHODS.get(fmt, ["Default"]))
        if cur in METHODS.get(fmt, []):
            self.method.setCurrentText(cur)
        self.method.setEnabled(fmt in METHODS and self.level.currentIndex() != 0)
        self.opt_solid.setChecked(fmt not in ("zip",))
        self.opt_solid.setEnabled(False)
        self.opt_solid.setToolTip("7Z and TAR archives are always solid (best ratio); ZIP stores files individually "
                                  "(fast random access).")
        self.opt_lock.setEnabled(fmt == "zip")
        self.comment.setEnabled(fmt == "zip")
        self.pw_btn.setEnabled(fmt in ("zip", "7z"))
        if hasattr(self, "pw_label"):
            self._pw_label()
        self.tabs.setTabEnabled(3, sfx)
        self.volume.setEnabled(not sfx)
        name = self.name.currentText().strip()
        if name:
            from ..core.util import strip_archive_ext
            base = strip_archive_ext(name)
            self.name.setCurrentText(base + (".exe" if sfx else "." + fmt))
        if sfx and self.password:
            self.password = ""
            self._pw_label()

    def _pw_label(self):
        if not self.password:
            text = ""
        elif self.encrypt_names and self.fmt() != "7z":
            text = "Password is set (file names stay visible: only 7Z can hide them)"
        else:
            text = "Password is set" + (" (file names encrypted)" if self.encrypt_names else "")
        self.pw_label.setText(text)

    def _browse(self):
        p, _ = QFileDialog.getSaveFileName(self, "Archive name", self.name.currentText(),
                                           "Archives (*.zip *.7z *.tar *.tar.gz *.tar.bz2 *.tar.xz *.tar.zst *.exe)")
        if p:
            self.name.setCurrentText(os.path.normpath(p))
            from ..core.writers import format_from_path
            fmt = format_from_path(p)
            for b in self.fmt_group.buttons():
                if b.property("fmt") == fmt:
                    b.setChecked(True)

    def _set_password(self):
        if self.opt_sfx.isChecked():
            QMessageBox.information(self, "Password", "Self-extracting archives cannot be encrypted.")
            return
        d = SetPasswordDialog(self, self.fmt(), self.password, self.encrypt_names)
        if d.exec() == QDialog.Accepted:
            self.password = d.p1.text()
            self.encrypt_names = bool(self.password) and d.names.isChecked()
            if self.encrypt_names and self.fmt() != "7z":
                self._select_format("7z")      # only 7Z can hide file names
            self._pw_label()

    def _select_format(self, fmt):
        for b in self.fmt_group.buttons():
            if b.property("fmt") == fmt:
                b.setChecked(True)

    def _add_files(self):
        fs, _ = QFileDialog.getOpenFileNames(self, "Add files")
        if fs:
            self.files_edit.appendPlainText("\n".join(os.path.normpath(x) for x in fs))

    def _add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Add folder")
        if d:
            self.files_edit.appendPlainText(os.path.normpath(d))

    def _load_comment(self):
        p, _ = QFileDialog.getOpenFileName(self, "Load comment", "", "Text files (*.txt);;All files (*)")
        if p:
            with open(p, encoding="utf-8", errors="replace") as f:
                self.comment.setPlainText(f.read())

    def _ok(self):
        if not self.archive_path():
            return
        if not self.source_files():
            QMessageBox.warning(self, "Add", "No files to add.")
            return
        vol = self.volume.currentData() if self.volume.currentText() == self.volume.itemText(self.volume.currentIndex()) \
            else self.volume.currentText()
        try:
            parse_size(vol or "")
        except ValueError:
            QMessageBox.warning(self, "Add", f"Invalid volume size: {vol}")
            return
        self.accept()

    def archive_path(self) -> str:
        return os.path.abspath(self.name.currentText().strip()) if self.name.currentText().strip() else ""

    def source_files(self) -> list[str]:
        return [ln.strip() for ln in self.files_edit.toPlainText().splitlines() if ln.strip()]

    def options(self) -> CreateOptions:
        vol_text = self.volume.currentText()
        idx = self.volume.findText(vol_text)
        vol = self.volume.itemData(idx) if idx >= 0 else vol_text
        ex = [m.strip() for part in self.exclude.toPlainText().splitlines() for m in part.split(";") if m.strip()]
        sfx_opts = {}
        if self.opt_sfx.isChecked():
            sfx_opts = {"Title": self.sfx_title.text(), "Text": self.sfx_text.toPlainText(),
                        "Path": self.sfx_path.text(), "Setup": self.sfx_setup.text(),
                        "Silent": self.sfx_silent.isChecked(), "Overwrite": self.sfx_over.currentIndex() or "",
                        "TempMode": self.sfx_temp.isChecked(), "License": self.sfx_license.toPlainText()}
        return CreateOptions(
            format=self.fmt(), level=self.level.currentIndex(),
            method=self.method.currentText() if self.method.isEnabled() else "",
            password=self.password, encrypt_names=self.encrypt_names and self.fmt() == "7z",
            volume_size=parse_size(vol or "") if not self.opt_sfx.isChecked() else 0,
            sfx=self.opt_sfx.isChecked(), sfx_options=sfx_opts,
            recovery_percent=self.rr_pct.value() if self.opt_rr.isChecked() else 0,
            lock=self.opt_lock.isChecked(), comment=self.comment.toPlainText() if self.fmt() == "zip" else "",
            delete_after=self.opt_delete.isChecked(), test_after=self.opt_test.isChecked(),
            exclude=ex, update_mode=self.update_mode.currentData(),
            store_paths=self.path_group.checkedButton().property("mode"),
            include_empty_dirs=self.empty_dirs.isChecked(), each_to_separate=self.opt_separate.isChecked())


# =========================================================================== Extract dialog
class ExtractDialog(QDialog):
    """'Extraction path and options'."""

    def __init__(self, parent, archives: list[str], settings=None, selected_count: int = 0):
        super().__init__(parent)
        self.setWindowTitle("Extraction path and options")
        self.setWindowIcon(icons.icon("extract"))
        self.resize(640, 520)
        self.archives = archives
        self.settings = settings
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Destination path (will be created if it does not exist):"))
        self.dest = QComboBox()
        self.dest.setEditable(True)
        default = self._default_dest()
        self.dest.addItem(default)
        if settings:
            for d in settings["recent_dests"]:
                if d != default:
                    self.dest.addItem(d)
        self.dest.setCurrentIndex(0)
        row = QHBoxLayout()
        row.addWidget(self.dest, 1)
        nf = QPushButton(icons.icon("newfolder", False), "New folder")
        nf.clicked.connect(self._new_folder)
        row.addWidget(nf)
        lay.addLayout(row)

        body = QHBoxLayout()
        left = QVBoxLayout()
        ow = QGroupBox("Overwrite mode")
        owl = QVBoxLayout(ow)
        self.ow_group = QButtonGroup(self)
        cur_ow = settings["extract_overwrite"] if settings else "ask"
        for i, (k, label) in enumerate(OVERWRITE_MODES):
            rb = QRadioButton(label)
            rb.setProperty("mode", k)
            self.ow_group.addButton(rb, i)
            owl.addWidget(rb)
            rb.setChecked(k == cur_ow)
        left.addWidget(ow)
        misc = QGroupBox("Miscellaneous")
        ml = QVBoxLayout(misc)
        self.no_paths = QCheckBox("Extract without paths")
        self.keep_broken = QCheckBox("Keep broken files")
        self.subfolders = QCheckBox("Extract archives to subfolders")
        self.subfolders.setChecked(len(archives) > 1 or (settings["extract_subfolder"] if settings else True))
        self.subfolders.setToolTip("Create a folder named after each archive inside the destination")
        self.open_after = QCheckBox("Open destination folder after extraction")
        self.open_after.setChecked(bool(settings and settings["extract_open_folder"]))
        self.selected_only = QCheckBox(f"Extract only the {selected_count} selected item(s)")
        self.selected_only.setVisible(selected_count > 0)
        self.selected_only.setChecked(selected_count > 0)
        for w in (self.no_paths, self.keep_broken, self.subfolders, self.open_after, self.selected_only):
            ml.addWidget(w)
        left.addWidget(misc)
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        self.pw.setPlaceholderText("Password (asked automatically when needed)")
        left.addWidget(self.pw)
        self.save_default = QCheckBox("Save settings as default")
        left.addWidget(self.save_default)
        left.addStretch()
        body.addLayout(left, 2)

        self.fs = QFileSystemModel(self)
        self.fs.setFilter(QDir.AllDirs | QDir.NoDotAndDotDot | QDir.Drives)
        self.fs.setRootPath("")
        self.tree = QTreeView()
        self.tree.setModel(self.fs)
        for c in (1, 2, 3):
            self.tree.hideColumn(c)
        self.tree.setHeaderHidden(True)
        parent_dir = os.path.dirname(archives[0]) if archives else QDir.homePath()
        self.tree.setCurrentIndex(self.fs.index(parent_dir))
        self.tree.scrollTo(self.fs.index(parent_dir))
        self.tree.clicked.connect(lambda idx: self.dest.setCurrentText(os.path.normpath(self.fs.filePath(idx))))
        body.addWidget(self.tree, 3)
        lay.addLayout(body)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _default_dest(self):
        if not self.archives:
            return QDir.homePath()
        base = (self.settings["extract_default"] if self.settings else "") or os.path.dirname(self.archives[0])
        return os.path.normpath(base)

    def _new_folder(self):
        name, ok = QInputDialog.getText(self, "New folder", "Folder name:")
        if ok and name:
            p = os.path.join(self.dest.currentText(), name)
            os.makedirs(p, exist_ok=True)
            self.dest.setCurrentText(p)

    def overwrite(self):
        return self.ow_group.checkedButton().property("mode")

    def options_for(self, archive: str, strip_prefix: str = "") -> ExtractOptions:
        from ..core.util import strip_archive_ext
        dest = self.dest.currentText().strip()
        if self.subfolders.isChecked():
            dest = os.path.join(dest, os.path.basename(strip_archive_ext(archive)))
        return ExtractOptions(dest=dest, paths="none" if self.no_paths.isChecked() else "full",
                              overwrite=self.overwrite(), keep_broken=self.keep_broken.isChecked(),
                              password=self.pw.text(), strip_prefix=strip_prefix)

    def remember(self):
        if not self.settings:
            return
        self.settings.add_recent_dest(self.dest.currentText().strip())
        if self.save_default.isChecked():
            self.settings["extract_overwrite"] = self.overwrite()
            self.settings["extract_open_folder"] = self.open_after.isChecked()
            self.settings["extract_subfolder"] = self.subfolders.isChecked()
        self.settings.save()
