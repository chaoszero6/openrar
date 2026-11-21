"""OpenRAR main window: browse folders and archives, and run every command."""
from __future__ import annotations

import html
import os
import shutil
import subprocess
import tempfile

from PySide6.QtCore import QDir, QFile, QFileSystemWatcher, QItemSelectionModel, QModelIndex, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QDrag, QGuiApplication, QKeySequence, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDialog, QFileDialog, QFileSystemModel,
                               QHBoxLayout, QInputDialog, QLabel, QMainWindow, QMenu, QMessageBox, QSplitter,
                               QToolBar, QToolButton, QTreeView, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..core import operations as ops
from ..core import recovery
from ..core.formats import looks_like_archive, open_archive
from ..core.model import CreateOptions, ExtractOptions
from ..core.util import IS_WINDOWS, human_size, strip_archive_ext
from ..core.writers import format_from_path
from . import icons, theme
from .dialogs import AddDialog, ExtractDialog
from .models import ArchiveIndex, FileListModel, Row, list_directory
from .tasks import DiagnosticsDialog, GuiInteraction, run_task, show_error
from .tool_dialogs import (AboutDialog, BenchmarkDialog, CommentDialog, ConvertDialog, FavoritesDialog, FindDialog,
                           InfoDialog, PasswordOrganizer, SettingsDialog, SfxDialog, WizardDialog)


def open_with_system(path: str):
    if IS_WINDOWS:
        os.startfile(path)  # noqa: S606 - opening the user's own file with its associated program
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def reveal(path: str):
    if IS_WINDOWS:
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))


class FileView(QTreeView):
    """Details view with drag & drop in and out (archive members are extracted on demand)."""

    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setSortingEnabled(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setIconSize(QSize(18, 18))
        self.setContextMenuPolicy(Qt.CustomContextMenu)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and e.source() is not self:
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls() and e.source() is not self:
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        if e.source() is self:
            e.ignore()
            return
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            e.acceptProposedAction()
            QTimer.singleShot(0, lambda: self.win.on_drop(paths))

    def startDrag(self, actions):
        if self.win.mode != "archive":
            return super().startDrag(actions)
        paths = self.win.extract_selection_to_temp()
        if not paths:
            return
        md = self.model().mimeData([])
        md.setUrls([QUrl.fromLocalFile(p) for p in paths])
        drag = QDrag(self)
        drag.setMimeData(md)
        drag.exec(Qt.CopyAction)


class MainWindow(QMainWindow):
    def __init__(self, settings, start_path: str | None = None):
        super().__init__()
        self.s = settings
        self.setWindowTitle(APP_NAME)
        from .settings import resource
        from PySide6.QtGui import QIcon
        self.setWindowIcon(QIcon(resource("icon.ico")))
        self.resize(1100, 680)
        self.mode = "fs"
        self.fs_path = ""
        self.archive_path = ""
        self.archive_password: str | None = None
        self.prefix = ""
        self.index: ArchiveIndex | None = None
        self.archive_meta = None
        self.parents: list[tuple[str, str, str | None]] = []   # nested archive stack: (path, prefix, password)
        self.temp_root = tempfile.mkdtemp(prefix="openrar-", dir=self.s["temp_dir"] or None)
        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self._viewed_file_changed)
        self.watched: dict[str, tuple[str, str, str | None]] = {}   # temp file -> (archive, member, password)
        self._pending_change: set[str] = set()

        self._build_actions()
        self._build_ui()
        self._build_menus()
        if self.s["window_geometry"]:
            try:
                self.restoreGeometry(bytes.fromhex(self.s["window_geometry"]))
            except ValueError:
                pass
        start = start_path or self.s["last_folder"] or QDir.homePath()
        QTimer.singleShot(0, lambda: self.open_path(start))

    # ================================================================ UI construction
    def _act(self, text, icon=None, shortcut=None, slot=None, tip=None, accent=True):
        a = QAction(text, self)          # QAction(None, text, parent) would drop the text
        if icon:
            a.setIcon(icons.icon(icon, accent))
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
            a.setShortcutContext(Qt.WindowShortcut)
        if slot:
            a.triggered.connect(slot)
        if tip:
            a.setToolTip(tip)
            a.setStatusTip(tip)
        self.addAction(a)
        return a

    def _build_actions(self):
        A = self._act
        self.a_add = A("Add", "add", "Alt+A", self.cmd_add, "Add files to archive")
        self.a_extract = A("Extract To", "extract", "Alt+E", self.cmd_extract, "Extract files from archive")
        self.a_extract_here = A("Extract without confirmation", "extract", "Alt+W", self.cmd_extract_quick,
                                "Extract to the default folder without asking")
        self.a_test = A("Test", "test", "Alt+T", self.cmd_test, "Test archived files")
        self.a_view = A("View", "view", "Alt+V", self.cmd_view, "View file")
        self.a_delete = A("Delete", "delete", "Del", self.cmd_delete, "Delete files")
        self.a_rename = A("Rename", "rename", "F2", self.cmd_rename, "Rename file", accent=False)
        self.a_find = A("Find", "find", "F3", self.cmd_find, "Find files")
        self.a_wizard = A("Wizard", "wizard", None, self.cmd_wizard, "Step-by-step help for common tasks")
        self.a_info = A("Info", "info", "Alt+I", self.cmd_info, "Show information")
        self.a_repair = A("Repair", "repair", "Alt+R", self.cmd_repair, "Repair a damaged archive")
        self.a_comment = A("Add archive comment...", "comment", "Alt+M", self.cmd_comment)
        self.a_protect = A("Protect archive (recovery record)...", "protect", "Alt+P", self.cmd_protect)
        self.a_lock = A("Lock archive", "lock", "Alt+L", self.cmd_lock)
        self.a_sfx = A("Convert archive to SFX...", "sfx", "Alt+X", self.cmd_sfx)
        self.a_convert = A("Convert archives...", "convert", "Alt+Q", self.cmd_convert)
        self.a_benchmark = A("Benchmark", "benchmark", "Alt+B", self.cmd_benchmark)
        self.a_virus = A("Scan archive for viruses...", "virus", "Alt+D", self.cmd_virus)
        self.a_report = A("Generate report...", "report", "Alt+G", self.cmd_report)
        self.a_settings = A("Settings...", "settings", "Ctrl+S", self.cmd_settings, accent=False)
        self.a_passwords = A("Organize passwords...", "key", "Ctrl+P", self.cmd_passwords, accent=False)
        self.a_up = A("Up one level", "up", "Backspace", self.go_up, accent=False)
        self.a_open = A("Open archive...", "archive", "Ctrl+O", self.cmd_open)
        self.a_refresh = A("Refresh", "refresh", "F5", self.refresh, accent=False)
        self.a_newfolder = A("New folder...", "newfolder", "Ctrl+Shift+N", self.cmd_new_folder, accent=False)
        self.a_select_all = A("Select all", None, "Ctrl+A", lambda: self.view.selectAll())
        self.a_select_group = A("Select group...", None, "Ctrl++", self.cmd_select_group)
        self.a_invert = A("Invert selection", None, "Ctrl+*", self.cmd_invert)
        self.a_copy_path = A("Copy full name to clipboard", "copy", "Ctrl+C", self.cmd_copy_names, accent=False)
        self.a_tree = A("Show folder tree", "tree", "Ctrl+T", self.toggle_tree, accent=False)
        self.a_tree.setCheckable(True)
        self.a_tree.setChecked(self.s["show_tree"])
        self.a_fav_add = A("Add to favorites...", "star", "Ctrl+F", self.cmd_fav_add)
        self.a_fav_org = A("Organize favorites...", None, None, self.cmd_fav_org)
        self.a_about = A(f"About {APP_NAME}", "help", None, lambda: AboutDialog(self).exec(), accent=False)
        self.a_help = A("Help topics", "help", "F1", self.cmd_help, accent=False)
        self.a_exit = A("Exit", None, "Ctrl+Q", self.close)
        self.a_change_drive = A("Change drive...", "drive", "Ctrl+D", lambda: self.path_box.setFocus())
        self.a_open_with = A("Open with default program", "open", "Ctrl+Return", self.cmd_open_system, accent=False)
        self.a_reveal = A("Show in Explorer", "folder", None, self.cmd_reveal, accent=False)

    def _build_ui(self):
        tb = QToolBar("Main")
        tb.setObjectName("mainToolbar")
        tb.setIconSize(QSize(30, 30))
        tb.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        tb.setMovable(False)
        for a in (self.a_add, self.a_extract, self.a_test, self.a_view, self.a_delete, self.a_find, self.a_wizard,
                  self.a_info, self.a_repair):
            tb.addAction(a)
        self.addToolBar(tb)

        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(6, 6, 6, 0)
        v.setSpacing(6)
        bar = QHBoxLayout()
        up = QToolButton()
        up.setDefaultAction(self.a_up)
        up.setAutoRaise(True)
        self.path_icon = QLabel()
        self.path_box = QComboBox()
        self.path_box.setEditable(True)
        self.path_box.setInsertPolicy(QComboBox.NoInsert)
        self.path_box.lineEdit().returnPressed.connect(lambda: self.open_path(self.path_box.currentText().strip()))
        self.path_box.activated.connect(lambda i: self.open_path(self.path_box.itemText(i)))
        bar.addWidget(up)
        bar.addWidget(self.path_icon)
        bar.addWidget(self.path_box, 1)
        v.addLayout(bar)

        self.split = QSplitter()
        self.fs_tree_model = QFileSystemModel(self)
        self.fs_tree_model.setFilter(QDir.AllDirs | QDir.NoDotAndDotDot | QDir.Drives)
        self.fs_tree_model.setRootPath("")
        self.arc_tree_model = QStandardItemModel(self)
        self.tree = QTreeView()
        self.tree.setHeaderHidden(True)
        self.tree.setModel(self.fs_tree_model)
        for c in (1, 2, 3):
            self.tree.hideColumn(c)
        self.tree.clicked.connect(self._tree_clicked)
        self.tree.setVisible(self.s["show_tree"])
        self.model = FileListModel(self)
        self.view = FileView(self)
        self.view.setModel(self.model)
        self.view.doubleClicked.connect(self._activated)
        self.view.customContextMenuRequested.connect(self._context_menu)
        self.view.selectionModel().selectionChanged.connect(self._update_status)
        self.view.header().setSortIndicator(0, Qt.AscendingOrder)
        self.split.addWidget(self.tree)
        self.split.addWidget(self.view)
        self.split.setStretchFactor(1, 4)
        self.split.setSizes([240, 860])
        v.addWidget(self.split, 1)
        self.setCentralWidget(central)

        self.status_left = QLabel()
        self.status_right = QLabel()
        self.status_flags = QLabel()
        self.statusBar().addWidget(self.status_left, 1)
        self.statusBar().addPermanentWidget(self.status_flags)
        self.statusBar().addPermanentWidget(self.status_right)

    def _build_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&File")
        m.addAction(self.a_open)
        self.recent_menu = m.addMenu("Recent archives")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        m.addAction(self.a_change_drive)
        m.addAction(self.a_newfolder)
        m.addAction(self.a_copy_path)
        m.addSeparator()
        m.addAction(self.a_select_all)
        m.addAction(self.a_select_group)
        m.addAction(self.a_invert)
        m.addSeparator()
        m.addAction(self.a_exit)

        m = mb.addMenu("&Commands")
        for a in (self.a_add, self.a_extract, self.a_extract_here, self.a_test, self.a_view, self.a_delete,
                  self.a_rename, None, self.a_open_with, self.a_reveal, None, self.a_comment, self.a_protect,
                  self.a_lock, self.a_sfx, None, self.a_info, self.a_repair):
            m.addSeparator() if a is None else m.addAction(a)

        m = mb.addMenu("&Tools")
        for a in (self.a_wizard, self.a_find, self.a_convert, self.a_repair, self.a_virus, self.a_benchmark,
                  self.a_report):
            m.addAction(a)

        self.fav_menu = mb.addMenu("F&avorites")
        self.fav_menu.aboutToShow.connect(self._fill_favorites)

        m = mb.addMenu("&Options")
        m.addAction(self.a_settings)
        m.addAction(self.a_passwords)
        m.addAction(self.a_tree)
        tm = m.addMenu(icons.icon("theme", False), "Theme")
        for key, label in (("system", "Follow Windows"), ("light", "Light"), ("dark", "Dark")):
            a = tm.addAction(label)
            a.setCheckable(True)
            a.setChecked(self.s["theme"] == key)
            a.triggered.connect(lambda _=False, k=key: self.set_theme(k))
        self.theme_menu = tm
        m.addAction(self.a_refresh)

        m = mb.addMenu("&Help")
        m.addAction(self.a_help)
        m.addAction(self.a_about)

    # ================================================================ navigation
    def open_path(self, path: str, select: str | None = None):
        if not path:
            return self.show_fs("", select)
        path = os.path.expandvars(os.path.expanduser(path))
        if os.path.isdir(path):
            return self.show_fs(path, select)
        if os.path.isfile(path):
            if looks_like_archive(path) or self._sniff_archive(path):
                return self.open_archive_ui(path)
            return open_with_system(path)
        # "C:\path\archive.zip\folder\inside" style addresses
        probe = path
        while probe and not os.path.exists(probe):
            parent = os.path.dirname(probe)
            if parent == probe:
                break
            probe = parent
        if os.path.isfile(probe):
            inner = os.path.relpath(path, probe).replace("\\", "/")
            return self.open_archive_ui(probe, prefix=inner)
        QMessageBox.warning(self, APP_NAME, f"Cannot find {path}")

    @staticmethod
    def _sniff_archive(path):
        from ..core.formats import sniff
        try:
            k = sniff(path)
        except OSError:
            return False
        if k == "exe":
            import pyzipper
            return pyzipper.is_zipfile(path)
        return k is not None

    def show_fs(self, path: str, select: str | None = None):
        self._leave_archive()
        self.mode = "fs"
        self.fs_path = os.path.normpath(path) if path else ""
        rows = list_directory(self.fs_path)
        self.model.set_rows(rows, "fs")
        self._after_nav(self.fs_path or "Computer", select)
        if self.fs_path:
            self.s["last_folder"] = self.fs_path
            if self.tree.model() is not self.fs_tree_model:
                self.tree.setModel(self.fs_tree_model)
                for c in (1, 2, 3):
                    self.tree.hideColumn(c)
            idx = self.fs_tree_model.index(self.fs_path)
            self.tree.setCurrentIndex(idx)
            self.tree.scrollTo(idx)
        self.path_icon.setPixmap(icons.icon("folder").pixmap(18, 18))

    def open_archive_ui(self, path: str, prefix: str = "", password: str | None = None, nested=False):
        ui = GuiInteraction(self, known_passwords=[p for _l, p in self.s.passwords()])

        def job(rep, ui_):
            rep.begin(f"Reading {os.path.basename(path)}")
            h = open_archive(path, password, ui_)
            try:
                entries = h.entries()
                meta = h.summary()
                return entries, meta, h.password
            finally:
                h.close()
        ok, res, err, _ = run_task(self, "Opening archive", job, ui)
        if not ok:
            return False
        entries, meta, pw = res
        if not nested:
            self.parents.clear()
        self.mode = "archive"
        self.archive_path = os.path.abspath(path)
        self.archive_password = pw
        self.archive_meta = meta
        self.index = ArchiveIndex(entries)
        self.prefix = prefix.strip("/") if self.index.exists(prefix.strip("/")) else ""
        self.s.add_history(self.archive_path)
        self._show_archive_folder()
        self._build_archive_tree()
        return True

    def _show_archive_folder(self, select: str | None = None):
        self.model.set_rows(self.index.rows(self.prefix), "archive")
        shown = os.path.join(self.archive_path, self.prefix.replace("/", os.sep)) if self.prefix else self.archive_path
        self._after_nav(shown, select)
        self.path_icon.setPixmap(icons.icon("archive").pixmap(18, 18))

    def _after_nav(self, shown: str, select: str | None):
        self.path_box.blockSignals(True)
        self.path_box.clear()
        items = [shown]
        if IS_WINDOWS:
            items += [f"{c}:\\" for c in "CDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.exists(f"{c}:\\")]
        items += [f["path"] for f in self.s["favorites"]]
        seen = []
        for i in items:
            if i not in seen:
                seen.append(i)
                self.path_box.addItem(i)
        self.path_box.setCurrentIndex(0)
        self.path_box.blockSignals(False)
        self._autosize_columns()
        self.view.setFocus()
        target = 1 if self.model.rowCount() > 1 else 0
        if select:
            for i, r in enumerate(self.model.rows):
                if r.name == select or r.key == select:
                    target = i
                    break
        if self.model.rowCount():
            idx = self.model.index(target, 0)
            self.view.selectionModel().setCurrentIndex(idx, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
            self.view.scrollTo(idx)
        self._update_status()
        self._update_actions()
        title = os.path.basename(self.archive_path) if self.mode == "archive" else (self.fs_path or "Computer")
        self.setWindowTitle(f"{title} - {APP_NAME}")

    def _autosize_columns(self):
        h = self.view.header()
        widths = self.s["column_widths"].get(self.model.mode, {})
        for i, name in enumerate(self.model.columns):
            w = widths.get(name) or {"Name": 340, "Size": 90, "Packed": 90, "Ratio": 60, "Type": 140,
                                     "Modified": 130, "CRC32": 80}.get(name, 100)
            h.resizeSection(i, w)

    def _save_columns(self):
        h = self.view.header()
        cw = dict(self.s["column_widths"])
        cw[self.model.mode] = {name: h.sectionSize(i) for i, name in enumerate(self.model.columns)}
        self.s["column_widths"] = cw

    def _leave_archive(self):
        if self.mode == "archive":
            self._save_columns()
        self.mode = "fs"
        self.index = None
        self.archive_meta = None

    def go_up(self):
        if self.mode == "archive":
            if self.prefix:
                child = self.prefix.rsplit("/", 1)[-1]
                self.prefix = self.prefix.rsplit("/", 1)[0] if "/" in self.prefix else ""
                self._show_archive_folder(select=child)
                return
            if self.parents:
                path, prefix, pw = self.parents.pop()
                name = os.path.basename(self.archive_path)
                if self.open_archive_ui(path, prefix, pw, nested=True):
                    self._show_archive_folder(select=name)
                return
            folder, name = os.path.split(self.archive_path)
            self._save_columns()
            self.show_fs(folder, select=name)
            return
        if not self.fs_path:
            return
        parent = os.path.dirname(self.fs_path)
        if parent == self.fs_path:
            self.show_fs("" if IS_WINDOWS else "/", select=self.fs_path)
        else:
            self.show_fs(parent, select=os.path.basename(self.fs_path))

    def refresh(self):
        if self.mode == "archive":
            self._reload_archive()
        else:
            sel = self._selected_rows()
            self.show_fs(self.fs_path, select=sel[0].name if sel else None)

    def _reload_archive(self, select=None):
        prefix = self.prefix
        nested = bool(self.parents)
        if not os.path.exists(self.archive_path):
            self.show_fs(os.path.dirname(self.archive_path))
            return
        if self.open_archive_ui(self.archive_path, prefix, self.archive_password, nested=nested):
            self._show_archive_folder(select=select)

    def _activated(self, index: QModelIndex):
        r: Row = self.model.data(index, Qt.UserRole)
        if r is None:
            return
        if r.kind == "up":
            return self.go_up()
        if self.mode == "fs":
            if r.is_dir:
                return self.show_fs(r.key)
            return self.open_path(r.key)
        if r.is_dir:
            self.prefix = r.key
            return self._show_archive_folder()
        if looks_like_archive(r.name):
            return self._open_nested(r)
        self.cmd_view()

    def _open_nested(self, r: Row):
        paths = self._extract_temp([r.key])
        if not paths:
            return
        self.parents.append((self.archive_path, self.prefix, self.archive_password))
        if not self.open_archive_ui(paths[0], nested=True):
            self.parents.pop()

    def _tree_clicked(self, idx):
        if self.tree.model() is self.fs_tree_model:
            self.show_fs(self.fs_tree_model.filePath(idx))
        else:
            key = idx.data(Qt.UserRole)
            if key is not None:
                self.prefix = key
                self._show_archive_folder()

    def _build_archive_tree(self):
        m = self.arc_tree_model
        m.clear()
        root = QStandardItem(icons.icon("archive"), os.path.basename(self.archive_path))
        root.setData("", Qt.UserRole)
        root.setEditable(False)
        m.appendRow(root)
        items = {"": root}
        for folder in sorted(self.index.folders):
            if not folder:
                continue
            parent = folder.rsplit("/", 1)[0] if "/" in folder else ""
            it = QStandardItem(icons.icon("folder"), folder.rsplit("/", 1)[-1])
            it.setData(folder, Qt.UserRole)
            it.setEditable(False)
            items.get(parent, root).appendRow(it)
            items[folder] = it
        self.tree.setModel(m)
        self.tree.expand(root.index())

    def toggle_tree(self, on):
        self.tree.setVisible(on)
        self.s["show_tree"] = on

    # ================================================================ selection helpers
    def _selected_rows(self) -> list[Row]:
        rows = []
        for idx in self.view.selectionModel().selectedRows():
            r = self.model.data(idx, Qt.UserRole)
            if r and r.kind != "up":
                rows.append(r)
        return rows

    def _selected_archives(self) -> list[str]:
        return [r.key for r in self._selected_rows() if r.kind == "file" and looks_like_archive(r.name)]

    def _target_archive(self) -> str | None:
        """Archive the command applies to: the open one, else the selected one."""
        if self.mode == "archive":
            return self.archive_path
        arcs = self._selected_archives()
        if arcs:
            return arcs[0]
        sel = self._selected_rows()
        if sel and sel[0].kind == "file":
            return sel[0].key
        QMessageBox.information(self, APP_NAME, "Select an archive first.")
        return None

    def _update_status(self, *_):
        sel = self._selected_rows()
        rows = [r for r in self.model.rows if r.kind not in ("up",)]
        files = [r for r in rows if not r.is_dir]
        dirs = [r for r in rows if r.is_dir]
        if sel:
            size = sum(r.size or 0 for r in sel)
            self.status_left.setText(f"Selected {len(sel)} item(s), {human_size(size)}")
        else:
            self.status_left.setText("")
        self.status_right.setText(f"Total {len(dirs)} folder(s) and {len(files)} file(s), "
                                  f"{human_size(sum(r.size or 0 for r in files))}")
        flags = []
        if self.mode == "archive" and self.archive_meta:
            m = self.archive_meta
            flags.append(m.format)
            if m.encrypted:
                flags.append("🔒 encrypted")
            if m.locked:
                flags.append("locked")
            if m.recovery:
                flags.append("recovery record")
            if m.solid:
                flags.append("solid")
            if m.volumes > 1:
                flags.append(f"{m.volumes} volumes")
        self.status_flags.setText("  ·  ".join(flags))

    def _update_actions(self):
        in_arc = self.mode == "archive"
        self.a_newfolder.setEnabled(not in_arc)
        self.a_reveal.setEnabled(True)

    # ================================================================ commands: add / extract
    def cmd_add(self):
        if self.mode == "archive":
            files, _ = QFileDialog.getOpenFileNames(self, "Select files to add")
            if files:
                self._add_into_archive(files)
            return
        sel = [r.key for r in self._selected_rows()]
        if not sel:
            if not self.fs_path:
                return
            sel = [self.fs_path]
        self.add_dialog(sel)

    def add_dialog(self, files: list[str], archive: str = "") -> list[str] | None:
        d = AddDialog(self if self.isVisible() else None, files, archive, self.s, existing=bool(archive))
        if d.exec() != QDialog.Accepted:
            return None
        dest = d.archive_path()
        opts = d.options()
        sources = d.source_files()
        self.s["default_format"] = opts.format
        self.s["default_level"] = opts.level
        self.s.save()
        ok, outs, err, msgs = run_task(self, f"Creating {os.path.basename(dest)}",
                                       lambda r, ui: ops.create(dest, sources, opts, r, ui))
        for m in msgs:
            QMessageBox.information(self, APP_NAME, m)
        if ok and self.isVisible() and self.mode == "fs":
            self.show_fs(self.fs_path, select=os.path.basename(outs[0]) if outs else None)
        return outs if ok else None

    def _add_into_archive(self, files: list[str]):
        if self.archive_meta and self.archive_meta.locked:
            QMessageBox.warning(self, APP_NAME, "This archive is locked and cannot be modified.")
            return
        fmt = format_from_path(self.archive_path)
        opts = CreateOptions(format=fmt, password=self.archive_password or "", level=self.s["default_level"])
        if self.archive_password is None and self.archive_meta and self.archive_meta.encrypted:
            opts.password = ""
        ok, *_ = run_task(self, "Adding files", lambda r, ui: ops.add_files(self.archive_path, files, self.prefix,
                                                                            opts, r, ui))
        if ok:
            self._reload_archive(select=os.path.basename(files[0]))

    def on_drop(self, paths: list[str]):
        if self.mode == "archive":
            names = "\n".join(os.path.basename(p) for p in paths[:10])
            if QMessageBox.question(self, "Add files", f"Add to {os.path.basename(self.archive_path)}"
                                    f"{(' / ' + self.prefix) if self.prefix else ''}?\n\n{names}") == QMessageBox.Yes:
                self._add_into_archive(paths)
            return
        if len(paths) == 1 and os.path.isfile(paths[0]) and looks_like_archive(paths[0]):
            self.open_archive_ui(paths[0])
            return
        self.add_dialog(paths)

    def cmd_extract(self):
        if self.mode == "archive":
            sel = self._selected_rows()
            d = ExtractDialog(self, [self.archive_path], self.s, selected_count=len(sel))
            if d.exec() != QDialog.Accepted:
                return
            d.remember()
            names = [r.key for r in sel] if (sel and d.selected_only.isChecked()) else None
            opts = d.options_for(self.archive_path, strip_prefix=self.prefix if names else "")
            if not opts.password and self.archive_password:
                opts.password = self.archive_password
            self._run_extract([(self.archive_path, opts, names)], d.open_after.isChecked())
            return
        arcs = self._selected_archives()
        if not arcs:
            QMessageBox.information(self, APP_NAME, "Select one or more archives to extract.")
            return
        self.extract_dialog(arcs)

    def extract_dialog(self, arcs: list[str]) -> bool:
        d = ExtractDialog(self if self.isVisible() else None, arcs, self.s)
        if d.exec() != QDialog.Accepted:
            return False
        d.remember()
        return self._run_extract([(a, d.options_for(a), None) for a in arcs], d.open_after.isChecked())

    def cmd_extract_quick(self):
        targets = [self.archive_path] if self.mode == "archive" else self._selected_archives()
        jobs = []
        for a in targets:
            base = self.s["extract_default"] or os.path.dirname(a)
            dest = os.path.join(base, os.path.basename(strip_archive_ext(a)))
            jobs.append((a, ExtractOptions(dest=dest, overwrite=self.s["extract_overwrite"],
                                           password=self.archive_password or "" if self.mode == "archive" else ""),
                         None))
        if jobs:
            self._run_extract(jobs, self.s["extract_open_folder"])

    def _run_extract(self, jobs, open_after=False) -> bool:
        ui = GuiInteraction(self, known_passwords=[p for _l, p in self.s.passwords()])

        def job(rep, ui_):
            errors = []
            for archive, opts, names in jobs:
                rep.done = 0
                _done, errs = ops.extract(archive, opts, names, rep, ui_)
                errors += [f"{os.path.basename(archive)}: {e}" for e in errs]
                if self.mode == "archive" and archive == self.archive_path and opts.password:
                    self.archive_password = opts.password
            return errors
        ok, errors, err, _ = run_task(self, "Extracting", job, ui)
        if ok and errors:
            DiagnosticsDialog(self, "Diagnostic messages", errors, "Some files could not be extracted:").exec()
        if ok and open_after and jobs:
            open_with_system(jobs[0][1].dest)
        if ok and self.mode == "fs" and self.isVisible():
            self.refresh()
        return ok

    # ================================================================ commands: test / view / delete / rename
    def cmd_test(self):
        targets = [self.archive_path] if self.mode == "archive" else self._selected_archives()
        if not targets:
            QMessageBox.information(self, APP_NAME, "Select one or more archives to test.")
            return
        pw = self.archive_password if self.mode == "archive" else None
        ui = GuiInteraction(self, known_passwords=[p for _l, p in self.s.passwords()])

        def job(rep, ui_):
            out = []
            for a in targets:
                rep.done = 0
                out += [f"{os.path.basename(a)}: {e}" for e in ops.test(a, pw, rep, ui_)]
            return out
        ok, errors, *_ = run_task(self, "Testing", job, ui)
        if not ok:
            return
        if errors:
            DiagnosticsDialog(self, "Test results", errors, "Errors were found:").exec()
        else:
            QMessageBox.information(self, "Test results", "No errors found in the tested archive(s).")

    def _extract_temp(self, names: list[str], into: str | None = None) -> list[str]:
        dest = into or tempfile.mkdtemp(dir=self.temp_root)
        opts = ExtractOptions(dest=dest, overwrite="always", password=self.archive_password or "",
                              strip_prefix=self.prefix)
        ui = GuiInteraction(self, known_passwords=[p for _l, p in self.s.passwords()])
        ok, res, *_ = run_task(self, "Extracting", lambda r, ui_: ops.extract(self.archive_path, opts, names, r, ui_),
                               ui)
        if not ok:
            return []
        if opts.password:
            self.archive_password = opts.password
        prefix = (self.prefix + "/") if self.prefix else ""
        return [os.path.join(dest, *(n[len(prefix):] if n.startswith(prefix) else n).split("/")) for n in names]

    def extract_selection_to_temp(self) -> list[str]:
        sel = self._selected_rows()
        if not sel:
            return []
        return [p for p in self._extract_temp([r.key for r in sel]) if os.path.exists(p)]

    def cmd_view(self):
        sel = [r for r in self._selected_rows() if not r.is_dir]
        if not sel:
            return
        r = sel[0]
        if self.mode == "fs":
            return open_with_system(r.key)
        paths = self._extract_temp([r.key])
        if not paths or not os.path.exists(paths[0]):
            return
        p = paths[0]
        self.watched[p] = (self.archive_path, r.key, self.archive_password)
        self.watcher.addPath(p)
        open_with_system(p)

    def _viewed_file_changed(self, path):
        self._pending_change.add(path)
        QTimer.singleShot(800, self._ask_update_viewed)

    def _ask_update_viewed(self):
        pending, self._pending_change = self._pending_change, set()
        for p in pending:
            info = self.watched.get(p)
            if not info or not os.path.exists(p):
                continue
            archive, member, pw = info
            if p not in self.watcher.files():
                self.watcher.addPath(p)          # editors that replace the file drop the watch
            if QMessageBox.question(self, APP_NAME, f"{os.path.basename(member)} was modified.\n\n"
                                    f"Update it in {os.path.basename(archive)}?") != QMessageBox.Yes:
                continue
            into = member.rsplit("/", 1)[0] if "/" in member else ""
            opts = CreateOptions(format=format_from_path(archive), password=pw or "")
            ok, *_ = run_task(self, "Updating archive",
                              lambda rep, ui, p=p: ops.add_files(archive, [p], into, opts, rep, ui))
            if ok and self.mode == "archive" and self.archive_path == archive:
                self._reload_archive(select=os.path.basename(member))

    def cmd_open_system(self):
        self.cmd_view()

    def cmd_reveal(self):
        if self.mode == "archive":
            reveal(self.archive_path)
        else:
            sel = self._selected_rows()
            reveal(sel[0].key if sel else self.fs_path)

    def cmd_delete(self):
        sel = self._selected_rows()
        if not sel:
            return
        if self.mode == "fs":
            if QMessageBox.question(self, "Delete", f"Move {len(sel)} item(s) to the Recycle Bin?") != QMessageBox.Yes:
                return
            for r in sel:
                if not QFile.moveToTrash(r.key):
                    try:
                        shutil.rmtree(r.key) if r.is_dir else os.remove(r.key)
                    except OSError as exc:
                        show_error(self, "Delete", exc)
            self.refresh()
            return
        if self.archive_meta and self.archive_meta.locked:
            QMessageBox.warning(self, APP_NAME, "This archive is locked and cannot be modified.")
            return
        if self.s["confirm_delete"] and QMessageBox.question(
                self, "Delete", f"Delete {len(sel)} item(s) from the archive?\nThis cannot be undone.") != QMessageBox.Yes:
            return
        names = [r.key for r in sel]
        ok, *_ = run_task(self, "Deleting", lambda rep, ui: ops.delete(self.archive_path, names,
                                                                       self.archive_password, rep, ui))
        if ok:
            self._reload_archive()

    def cmd_rename(self):
        sel = self._selected_rows()
        if len(sel) != 1:
            return
        r = sel[0]
        new, ok = QInputDialog.getText(self, "Rename", "New name:", text=r.name)
        if not ok or not new or new == r.name or "/" in new or "\\" in new:
            return
        if self.mode == "fs":
            try:
                os.rename(r.key, os.path.join(os.path.dirname(r.key), new))
            except OSError as exc:
                show_error(self, "Rename", exc)
            self.show_fs(self.fs_path, select=new)
            return
        new_key = (self.prefix + "/" + new) if self.prefix else new
        ok, *_ = run_task(self, "Renaming", lambda rep, ui: ops.rename(self.archive_path, r.key, new_key,
                                                                       self.archive_password, rep, ui))
        if ok:
            self._reload_archive(select=new)

    def cmd_new_folder(self):
        if self.mode != "fs" or not self.fs_path:
            return
        name, ok = QInputDialog.getText(self, "New folder", "Folder name:")
        if ok and name:
            try:
                os.makedirs(os.path.join(self.fs_path, name))
            except OSError as exc:
                show_error(self, "New folder", exc)
            self.show_fs(self.fs_path, select=name)

    def cmd_open(self):
        p, _ = QFileDialog.getOpenFileName(self, "Open archive", self.fs_path or QDir.homePath(),
                                           "Archives (*.zip *.7z *.rar *.tar *.gz *.tgz *.bz2 *.xz *.zst *.iso *.cab "
                                           "*.arj *.lzh *.001 *.exe *.wim);;All files (*)")
        if p:
            self.open_archive_ui(p)

    # ================================================================ commands: info / comment / protect / lock / sfx
    def cmd_info(self):
        target = None
        if self.mode == "archive":
            target = self.archive_path
        else:
            arcs = self._selected_archives()
            if arcs:
                target = arcs[0]
        if target is None:
            sel = self._selected_rows()
            if not sel:
                return
            ok, size, *_ = run_task(self, "Calculating", lambda r, ui: _tree_size([x.key for x in sel]))
            if ok:
                files, dirs, total = size
                QMessageBox.information(self, "Information", f"{len(sel)} selected item(s)\n\n"
                                        f"Files: {files:,}\nFolders: {dirs:,}\nTotal size: {human_size(total)} "
                                        f"({total:,} bytes)")
            return
        pw = self.archive_password if target == self.archive_path else None
        ok, summary, *_ = run_task(self, "Reading", lambda r, ui: _summary(target, pw, ui),
                                   GuiInteraction(self, known_passwords=[p for _l, p in self.s.passwords()]))
        if ok:
            InfoDialog(self, summary, on_edit_comment=lambda: self.cmd_comment(target)).exec()

    def cmd_comment(self, target=None):
        target = target if isinstance(target, str) else self._target_archive()
        if not target:
            return
        try:
            h = open_archive(target, self.archive_password if target == self.archive_path else None)
            text, supports = h.comment(), h.supports_comment
            h.close()
        except Exception as exc:
            return show_error(self, "Comment", exc)
        if not supports:
            QMessageBox.information(self, "Comment", "Only ZIP archives can store a comment.")
            return
        d = CommentDialog(self, text)
        if d.exec() != QDialog.Accepted:
            return
        ok, *_ = run_task(self, "Saving comment", lambda r, ui: ops.set_comment(target, d.edit.toPlainText(), ui))
        if ok and self.mode == "archive":
            self._reload_archive()

    def cmd_protect(self):
        target = self._target_archive()
        if not target:
            return
        pct, ok = QInputDialog.getDouble(self, "Protect archive",
                                         "Recovery record size (percent of archive size).\n"
                                         "Damage up to this size in one place can be repaired:", 3.0, 0.5, 50.0, 1)
        if not ok:
            return
        ok, path, *_ = run_task(self, "Adding recovery record", lambda r, ui: recovery.create_record(target, pct, r))
        if ok:
            QMessageBox.information(self, "Protect archive", f"Recovery record created:\n{path}\n\n"
                                    "Keep this file next to the archive. Use Repair if the archive gets damaged.")
            self.refresh()

    def cmd_lock(self):
        target = self._target_archive()
        if not target:
            return
        if QMessageBox.question(self, "Lock archive", "A locked archive cannot be modified by OpenRAR any more "
                                "(no adding, deleting, renaming or comment changes).\n\nLock "
                                f"{os.path.basename(target)}?") != QMessageBox.Yes:
            return
        ok, *_ = run_task(self, "Locking", lambda r, ui: ops.lock(target, ui))
        if ok:
            self.refresh()

    def cmd_sfx(self):
        target = self._target_archive()
        if not target:
            return
        try:
            cur = ops.get_sfx_options(target)
        except Exception:
            cur = {}
        d = SfxDialog(self, target, cur)
        if d.exec() != QDialog.Accepted:
            return
        out = d.out.text().strip()
        ok, res, *_ = run_task(self, "Creating SFX", lambda r, ui: ops.make_sfx(target, out, d.options(), ui, r))
        if ok:
            QMessageBox.information(self, "SFX", f"Self-extracting archive created:\n{res}")
            if self.mode == "fs":
                self.show_fs(self.fs_path, select=os.path.basename(res))

    # ================================================================ commands: repair / convert / tools
    def cmd_repair(self):
        target = self._target_archive()
        if not target:
            return
        folder = QFileDialog.getExistingDirectory(self, "Folder for the repaired archive", os.path.dirname(target))
        if not folder:
            return
        out = os.path.join(folder, "fixed." + os.path.basename(target))
        ok, report, *_ = run_task(self, "Repairing", lambda r, ui: ops.repair(target, out, r))
        if ok:
            QMessageBox.information(self, "Repair", report)
            if self.mode == "fs":
                self.refresh()

    def cmd_convert(self):
        arcs = [self.archive_path] if self.mode == "archive" else self._selected_archives()
        d = ConvertDialog(self, arcs)
        if d.exec() != QDialog.Accepted:
            return
        jobs = d.jobs()
        delete = d.delete.isChecked()
        ui = GuiInteraction(self, known_passwords=[p for _l, p in self.s.passwords()])

        def job(rep, ui_):
            done = []
            for src, dest, opts in jobs:
                rep.done = 0
                ops.convert(src, dest, opts, rep, ui_, delete_original=delete)
                done.append(dest)
            return done
        ok, done, *_ = run_task(self, "Converting", job, ui)
        if ok:
            QMessageBox.information(self, "Convert", f"Converted {len(done)} archive(s).")
            if self.mode == "fs":
                self.refresh()

    def cmd_find(self):
        start = self.fs_path if self.mode == "fs" else os.path.dirname(self.archive_path)
        d = FindDialog(self, start or QDir.homePath())
        d.open_location.connect(self._find_open)
        d.show()

    def _find_open(self, loc, name, kind):
        if kind == "archive":
            folder = name.rsplit("/", 1)[0] if "/" in name else ""
            if self.open_archive_ui(loc, folder):
                self._show_archive_folder(select=name.rsplit("/", 1)[-1])
        else:
            self.show_fs(loc, select=name)
        self.raise_()
        self.activateWindow()

    def cmd_wizard(self):
        d = WizardDialog(self)
        if d.exec() != QDialog.Accepted:
            return
        c = d.choice()
        if c == "extract":
            p, _ = QFileDialog.getOpenFileName(self, "Select archive to unpack", self.fs_path or QDir.homePath())
            if p:
                self.extract_dialog([p])
        elif c == "create":
            fs, _ = QFileDialog.getOpenFileNames(self, "Select files to pack", self.fs_path or QDir.homePath())
            if fs:
                self.add_dialog(fs)
        else:
            a, _ = QFileDialog.getOpenFileName(self, "Select archive", self.fs_path or QDir.homePath())
            if not a:
                return
            fs, _ = QFileDialog.getOpenFileNames(self, "Select files to add", os.path.dirname(a))
            if fs:
                self.add_dialog(fs, archive=a)

    def cmd_benchmark(self):
        BenchmarkDialog(self).exec()

    def cmd_virus(self):
        target = self._target_archive()
        if not target:
            return
        scanner = _find_scanner()
        if not scanner:
            QMessageBox.information(self, "Virus scan", "No virus scanner was found (Microsoft Defender or ClamAV).")
            return
        stage = tempfile.mkdtemp(dir=self.temp_root)
        pw = self.archive_password if target == self.archive_path else None

        def job(rep, ui):
            ops.extract(target, ExtractOptions(dest=stage, overwrite="always", password=pw or ""), None, rep, ui)
            rep.begin("Scanning with " + os.path.basename(scanner[0]))
            rep.set_total(0)
            return subprocess.run(scanner + [stage], capture_output=True, text=True,
                                  creationflags=0x08000000 if IS_WINDOWS else 0)
        ok, res, *_ = run_task(self, "Virus scan", job)
        shutil.rmtree(stage, ignore_errors=True)
        if ok:
            out = (res.stdout or "") + (res.stderr or "")
            clean = res.returncode == 0
            DiagnosticsDialog(self, "Virus scan", out.strip().splitlines()[-40:] or ["(no output)"],
                              "No threats found." if clean else "The scanner reported a problem:").exec()

    def cmd_report(self):
        target = self._target_archive()
        if not target:
            return
        p, filt = QFileDialog.getSaveFileName(self, "Save report", strip_archive_ext(target) + "_report.html",
                                              "HTML (*.html);;Text (*.txt)")
        if not p:
            return
        pw = self.archive_password if target == self.archive_path else None
        ok, res, *_ = run_task(self, "Generating report", lambda r, ui: _write_report(target, p, pw, ui))
        if ok:
            open_with_system(p)

    def cmd_settings(self):
        old_theme = self.s["theme"]
        d = SettingsDialog(self, self.s)
        if d.exec() == QDialog.Accepted:
            if self.s["theme"] != old_theme:
                self.set_theme(self.s["theme"])
            self.a_tree.setChecked(self.s["show_tree"])
            self.toggle_tree(self.s["show_tree"])

    def cmd_passwords(self):
        PasswordOrganizer(self).exec()

    def set_theme(self, key):
        self.s["theme"] = key
        self.s.save()
        theme.apply(QApplication.instance(), key)
        for a in self.theme_menu.actions():
            a.setChecked(a.text().lower().startswith(key) or (key == "system" and a.text().startswith("Follow")))
        # re-tint icons for the new palette
        for name, a in (("add", self.a_add), ("extract", self.a_extract), ("test", self.a_test),
                        ("view", self.a_view), ("delete", self.a_delete), ("find", self.a_find),
                        ("wizard", self.a_wizard), ("info", self.a_info), ("repair", self.a_repair),
                        ("up", self.a_up)):
            a.setIcon(icons.icon(name, name != "up"))
        self.model.layoutChanged.emit()

    def cmd_select_group(self):
        mask, ok = QInputDialog.getText(self, "Select group", "File mask (e.g. *.txt):", text="*")
        if not ok:
            return
        import fnmatch
        sm = self.view.selectionModel()
        for i, r in enumerate(self.model.rows):
            if r.kind != "up" and fnmatch.fnmatch(r.name.lower(), mask.lower()):
                sm.select(self.model.index(i, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)

    def cmd_invert(self):
        sm = self.view.selectionModel()
        for i, r in enumerate(self.model.rows):
            if r.kind != "up":
                sm.select(self.model.index(i, 0), QItemSelectionModel.Toggle | QItemSelectionModel.Rows)

    def cmd_copy_names(self):
        sel = self._selected_rows()
        if self.mode == "archive":
            names = [os.path.join(self.archive_path, r.key.replace("/", os.sep)) for r in sel]
        else:
            names = [r.key for r in sel]
        QGuiApplication.clipboard().setText("\n".join(names))

    def cmd_fav_add(self):
        path = self.path_box.currentText()
        name, ok = QInputDialog.getText(self, "Add to favorites", "Name:", text=os.path.basename(path) or path)
        if ok and name:
            self.s["favorites"] = self.s["favorites"] + [{"name": name, "path": path}]
            self.s.save()

    def cmd_fav_org(self):
        d = FavoritesDialog(self, self.s["favorites"])
        if d.exec() == QDialog.Accepted:
            self.s["favorites"] = d.favs
            self.s.save()

    def _fill_favorites(self):
        m = self.fav_menu
        m.clear()
        m.addAction(self.a_fav_add)
        m.addAction(self.a_fav_org)
        if self.s["favorites"]:
            m.addSeparator()
        for f in self.s["favorites"]:
            a = m.addAction(icons.icon("star"), f["name"])
            a.setToolTip(f["path"])
            a.triggered.connect(lambda _=False, p=f["path"]: self.open_path(p))

    def _fill_recent(self):
        m = self.recent_menu
        m.clear()
        for p in self.s["history"]:
            a = m.addAction(p)
            a.triggered.connect(lambda _=False, p=p: self.open_path(p))
        if not self.s["history"]:
            m.addAction("(empty)").setEnabled(False)

    def cmd_help(self):
        text = (
            "<h3>Keyboard shortcuts</h3><table cellpadding=3>"
            "<tr><td><b>Alt+A</b></td><td>Add files to archive</td></tr>"
            "<tr><td><b>Alt+E</b></td><td>Extract to a chosen folder</td></tr>"
            "<tr><td><b>Alt+W</b></td><td>Extract without confirmation</td></tr>"
            "<tr><td><b>Alt+T</b></td><td>Test archive</td></tr>"
            "<tr><td><b>Alt+V</b></td><td>View file</td></tr>"
            "<tr><td><b>Del</b></td><td>Delete</td></tr><tr><td><b>F2</b></td><td>Rename</td></tr>"
            "<tr><td><b>F3</b></td><td>Find files</td></tr>"
            "<tr><td><b>Alt+I</b></td><td>Archive information</td></tr>"
            "<tr><td><b>Alt+R</b></td><td>Repair</td></tr>"
            "<tr><td><b>Alt+M</b></td><td>Archive comment</td></tr>"
            "<tr><td><b>Alt+P</b></td><td>Protect (recovery record)</td></tr>"
            "<tr><td><b>Alt+L</b></td><td>Lock archive</td></tr>"
            "<tr><td><b>Alt+X</b></td><td>Convert to self-extracting .exe</td></tr>"
            "<tr><td><b>Alt+Q</b></td><td>Convert archives</td></tr>"
            "<tr><td><b>Alt+B</b></td><td>Benchmark</td></tr>"
            "<tr><td><b>Alt+D</b></td><td>Virus scan</td></tr>"
            "<tr><td><b>Ctrl+T</b></td><td>Folder tree</td></tr>"
            "<tr><td><b>Backspace</b></td><td>Up one level</td></tr></table>"
            "<h3>Tips</h3><ul><li>Drag files onto the window to add them to the open archive.</li>"
            "<li>Drag files out of an archive into Explorer to extract them.</li>"
            "<li>Edit a file from an archive in any program; OpenRAR offers to update the archive.</li>"
            "<li>Command line: <code>OpenRAR.exe a -p -v700m backup.7z C:\\Data</code> "
            "(run <code>OpenRAR.exe help</code> in a console for all commands).</li></ul>")
        QMessageBox.about(self, "Help", text)

    # ================================================================ context menu
    def _context_menu(self, pos):
        m = QMenu(self)
        sel = self._selected_rows()
        if self.mode == "archive":
            for a in (self.a_view, self.a_extract, self.a_extract_here, None, self.a_add, self.a_delete,
                      self.a_rename, None, self.a_copy_path, self.a_info):
                m.addSeparator() if a is None else m.addAction(a)
        else:
            if sel and any(looks_like_archive(r.name) for r in sel if r.kind == "file"):
                for a in (self.a_extract, self.a_extract_here, self.a_test, None):
                    m.addSeparator() if a is None else m.addAction(a)
            for a in (self.a_open_with, self.a_add, None, self.a_rename, self.a_delete, self.a_newfolder, None,
                      self.a_copy_path, self.a_reveal, self.a_info):
                m.addSeparator() if a is None else m.addAction(a)
        m.exec(self.view.viewport().mapToGlobal(pos))

    # ================================================================ shutdown
    def closeEvent(self, ev):
        self._save_columns()
        self.s["window_geometry"] = bytes(self.saveGeometry()).hex()
        self.s.save()
        shutil.rmtree(self.temp_root, ignore_errors=True)
        super().closeEvent(ev)


# ==================================================================== helpers
def _tree_size(paths):
    files = dirs = total = 0
    for p in paths:
        if os.path.isdir(p):
            dirs += 1
            for root, ds, fs in os.walk(p):
                dirs += len(ds)
                for f in fs:
                    files += 1
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
        else:
            files += 1
            total += os.path.getsize(p)
    return files, dirs, total


def _summary(path, password, ui):
    h = open_archive(path, password, ui)
    try:
        return h.summary()
    finally:
        h.close()


def _write_report(target, out, password, ui):
    h = open_archive(target, password, ui)
    try:
        s = h.summary()
        entries = h.entries()
    finally:
        h.close()
    if out.lower().endswith(".txt"):
        lines = [f"Archive: {s.path}", f"Format: {s.format}", f"Files: {s.files}  Folders: {s.dirs}",
                 f"Unpacked: {s.unpacked:,} bytes  Packed: {s.packed:,} bytes  Ratio: {s.ratio:.1f}%", "",
                 f"{'Size':>14} {'Packed':>14} {'Modified':19} {'CRC32':8} Name"]
        for e in entries:
            size = "<DIR>" if e.is_dir else str(e.size)
            packed = "" if e.packed is None else str(e.packed)
            mt = e.mtime.strftime("%Y-%m-%d %H:%M:%S") if e.mtime else ""
            crc = f"{e.crc:08X}" if e.crc is not None and not e.is_dir else ""
            lines.append(f"{size:>14} {packed:>14} {mt:19} {crc:8} {e.name}")
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return out
    rows = ""
    for e in entries:
        rows += (f"<tr><td>{html.escape(e.name)}{'/' if e.is_dir else ''}</td>"
                 f"<td class=n>{'' if e.is_dir else f'{e.size:,}'}</td>"
                 f"<td class=n>{'' if e.packed is None else f'{e.packed:,}'}</td>"
                 f"<td>{e.mtime.strftime('%Y-%m-%d %H:%M:%S') if e.mtime else ''}</td>"
                 f"<td>{f'{e.crc:08X}' if e.crc is not None and not e.is_dir else ''}</td></tr>")
    doc = f"""<!doctype html><meta charset=utf-8><title>{html.escape(os.path.basename(s.path))}</title>
<style>body{{font:14px Segoe UI,system-ui,sans-serif;margin:24px;color:#1d2430}}table{{border-collapse:collapse;width:100%}}
td,th{{padding:4px 8px;border-bottom:1px solid #e3e6eb;text-align:left}}th{{background:#f4f6f9}}.n{{text-align:right}}</style>
<h2>{html.escape(s.path)}</h2>
<p>{html.escape(s.format)} · {s.files:,} files · {s.dirs:,} folders · {s.unpacked:,} bytes unpacked ·
{s.packed:,} bytes packed · ratio {s.ratio:.1f}%{' · encrypted' if s.encrypted else ''}</p>
{f'<pre>{html.escape(s.comment)}</pre>' if s.comment else ''}
<table><tr><th>Name</th><th class=n>Size</th><th class=n>Packed</th><th>Modified</th><th>CRC32</th></tr>{rows}</table>"""
    with open(out, "w", encoding="utf-8") as f:
        f.write(doc)
    return out


def _find_scanner():
    if IS_WINDOWS:
        base = os.environ.get("ProgramFiles", r"C:\Program Files")
        p = os.path.join(base, "Windows Defender", "MpCmdRun.exe")
        if os.path.exists(p):
            return [p, "-Scan", "-ScanType", "3", "-DisableRemediation", "-File"]
    p = shutil.which("clamscan")
    if p:
        return [p, "-r", "--no-summary"]
    return None
