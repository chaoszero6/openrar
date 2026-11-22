"""List model shared by folder browsing and archive browsing."""
from __future__ import annotations

import os
import string
import tempfile
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QAbstractTableModel, QFileInfo, QMimeData, QModelIndex, Qt, QUrl
from PySide6.QtWidgets import QFileIconProvider

from ..core.formats import looks_like_archive
from ..core.util import IS_WINDOWS, human_size
from . import icons


@dataclass
class Row:
    name: str
    is_dir: bool
    size: int | None = None
    packed: int | None = None
    mtime: datetime | None = None
    crc: int | None = None
    encrypted: bool = False
    key: str = ""             # absolute path (fs) or archive member path
    kind: str = ""            # "up", "drive", "dir", "file"
    method: str = ""

    @property
    def type_name(self) -> str:
        if self.kind == "up":
            return ""
        if self.kind == "drive":
            return "Drive"
        if self.is_dir:
            return "File folder"
        ext = os.path.splitext(self.name)[1].upper().lstrip(".")
        if looks_like_archive(self.name):
            return f"{ext} archive"
        return f"{ext} file" if ext else "File"


class IconCache:
    def __init__(self):
        self.provider = QFileIconProvider()
        self.cache = {}
        self.dummy_dir = os.path.join(tempfile.gettempdir(), "openrar-icons")
        os.makedirs(self.dummy_dir, exist_ok=True)

    def for_row(self, row: Row):
        if row.kind == "up":
            return icons.icon("up", False)
        if row.kind == "drive":
            return self.provider.icon(QFileIconProvider.Drive)
        if row.is_dir:
            return icons.icon("folder")
        ext = os.path.splitext(row.name)[1].lower()
        if ext in (".exe", ".lnk", ".ico", ".url") and row.key and os.path.isabs(row.key) and os.path.exists(row.key):
            return self.provider.icon(QFileInfo(row.key))
        ic = self.cache.get(ext)
        if ic is None:
            if looks_like_archive(row.name) and ext not in (".docx", ".xlsx", ".pptx", ".odt", ".epub", ".apk", ".jar"):
                ic = icons.icon("archive")
            else:
                dummy = os.path.join(self.dummy_dir, "x" + ext)
                try:
                    if not os.path.exists(dummy):
                        open(dummy, "wb").close()
                    ic = self.provider.icon(QFileInfo(dummy))
                except OSError:
                    ic = self.provider.icon(QFileIconProvider.File)
            self.cache[ext] = ic
        return ic


FS_COLUMNS = ["Name", "Size", "Type", "Modified"]
ARC_COLUMNS = ["Name", "Size", "Packed", "Ratio", "Type", "Modified", "CRC32"]


class FileListModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[Row] = []
        self.mode = "fs"
        self.icons = IconCache()
        self.sort_col, self.sort_order = 0, Qt.AscendingOrder

    @property
    def columns(self):
        return ARC_COLUMNS if self.mode == "archive" else FS_COLUMNS

    def set_rows(self, rows: list[Row], mode: str):
        self.beginResetModel()
        self.mode = mode
        self.rows = rows
        self._sort()
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.columns)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.columns[section]
        if orientation == Qt.Horizontal and role == Qt.TextAlignmentRole and self.columns[section] in ("Size", "Packed", "Ratio"):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r = self.rows[index.row()]
        col = self.columns[index.column()]
        if role == Qt.DisplayRole:
            if col == "Name":
                return ".." if r.kind == "up" else r.name + ("*" if r.encrypted and not r.is_dir else "")
            if r.kind == "up":
                return ""
            if col == "Size":
                if r.kind == "drive":
                    return ""
                return human_size(r.size) if r.size is not None and (not r.is_dir or self.mode == "archive") else ""
            if col == "Packed":
                return human_size(r.packed) if r.packed is not None else ""
            if col == "Ratio":
                if r.packed is not None and r.size:
                    return f"{100 * r.packed / r.size:.0f}%"
                return ""
            if col == "Type":
                return r.type_name
            if col == "Modified":
                return r.mtime.strftime("%Y-%m-%d %H:%M") if r.mtime else ""
            if col == "CRC32":
                return f"{r.crc:08X}" if r.crc is not None and not r.is_dir else ""
        if role == Qt.DecorationRole and col == "Name":
            return self.icons.for_row(r)
        if role == Qt.TextAlignmentRole and col in ("Size", "Packed", "Ratio"):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ToolTipRole and col == "Name" and r.kind not in ("up",):
            tip = r.key or r.name
            if r.encrypted:
                tip += "\n(encrypted)"
            if r.method:
                tip += f"\nMethod: {r.method}"
            return tip
        if role == Qt.UserRole:
            return r
        return None

    def flags(self, index):
        f = super().flags(index)
        if index.isValid() and self.rows[index.row()].kind not in ("up", "drive"):
            f |= Qt.ItemIsDragEnabled
        return f

    def sort(self, column, order=Qt.AscendingOrder):
        self.sort_col, self.sort_order = column, order
        self.layoutAboutToBeChanged.emit()
        self._sort()
        self.layoutChanged.emit()

    def _sort(self):
        col = self.columns[min(self.sort_col, len(self.columns) - 1)]
        rev = self.sort_order == Qt.DescendingOrder

        def key(r: Row):
            if col == "Size":
                v = r.size or 0
            elif col == "Packed":
                v = r.packed or 0
            elif col == "Ratio":
                v = (r.packed or 0) / r.size if r.size else 0
            elif col == "Type":
                v = r.type_name.lower()
            elif col == "Modified":
                v = r.mtime.timestamp() if r.mtime else 0
            elif col == "CRC32":
                v = r.crc or 0
            else:
                v = r.name.lower()
            return v
        ups = [r for r in self.rows if r.kind == "up"]
        dirs = sorted([r for r in self.rows if r.kind != "up" and r.is_dir], key=key, reverse=rev)
        files = sorted([r for r in self.rows if not r.is_dir and r.kind != "up"], key=key, reverse=rev)
        self.rows = ups + dirs + files

    def mimeTypes(self):
        return ["text/uri-list"]

    def mimeData(self, indexes):
        # file system mode: real files; archive mode is handled by the view (extract first)
        md = QMimeData()
        if self.mode == "fs":
            seen, urls = set(), []
            for i in indexes:
                r = self.rows[i.row()]
                if r.key and r.key not in seen and r.kind in ("dir", "file"):
                    seen.add(r.key)
                    urls.append(QUrl.fromLocalFile(r.key))
            md.setUrls(urls)
        return md


def list_directory(path: str) -> list[Row]:
    rows: list[Row] = []
    if not path:                       # "Computer": list drives
        if IS_WINDOWS:
            for letter in string.ascii_uppercase:
                d = f"{letter}:\\"
                if os.path.exists(d):
                    rows.append(Row(name=d, is_dir=True, key=d, kind="drive"))
        else:
            rows.append(Row(name="/", is_dir=True, key="/", kind="drive"))
        return rows
    rows.append(Row(name="..", is_dir=True, kind="up"))
    try:
        with os.scandir(path) as it:
            for de in it:
                try:
                    st = de.stat()
                    is_dir = de.is_dir()
                except OSError:
                    continue
                if IS_WINDOWS:
                    attrs = getattr(st, "st_file_attributes", 0)
                    if attrs & 0x2 and attrs & 0x4:      # hidden + system
                        continue
                rows.append(Row(name=de.name, is_dir=is_dir, size=None if is_dir else st.st_size,
                                mtime=datetime.fromtimestamp(st.st_mtime), key=de.path,
                                kind="dir" if is_dir else "file"))
    except OSError:
        pass
    return rows


class ArchiveIndex:
    """Folder tree built from a flat entry list (adds implicit folders, folder sizes)."""

    def __init__(self, entries):
        self.folders: dict[str, dict[str, Row]] = {"": {}}
        self.entries = {e.name: e for e in entries}
        sizes: dict[str, list[int]] = {}
        for e in entries:
            parts = [p for p in e.name.split("/") if p]
            if not parts:
                continue
            for i in range(len(parts) - 1):
                folder = "/".join(parts[:i])
                path = "/".join(parts[:i + 1])
                d = self.folders.setdefault(folder, {})
                if parts[i] not in d:
                    d[parts[i]] = Row(name=parts[i], is_dir=True, key=path, kind="dir")
                self.folders.setdefault(path, {})
                if not e.is_dir:
                    s = sizes.setdefault(path, [0, 0])
                    s[0] += e.size
                    s[1] += e.packed or 0
            parent = "/".join(parts[:-1])
            name = parts[-1]
            full = "/".join(parts)
            d = self.folders.setdefault(parent, {})
            if e.is_dir:
                row = d.get(name) or Row(name=name, is_dir=True, key=full, kind="dir")
                row.mtime = e.mtime or row.mtime
                row.encrypted = e.encrypted
                d[name] = row
                self.folders.setdefault(full, {})
            else:
                d[name] = Row(name=name, is_dir=False, size=e.size, packed=e.packed, mtime=e.mtime, crc=e.crc,
                              encrypted=e.encrypted, key=full, kind="file", method=e.method)
        for path, (s, p) in sizes.items():
            parent, _, name = path.rpartition("/")
            row = self.folders.get(parent, {}).get(name)
            if row:
                row.size, row.packed = s, (p or None)

    def rows(self, folder: str) -> list[Row]:
        return [Row(name="..", is_dir=True, kind="up")] + list(self.folders.get(folder, {}).values())

    def exists(self, folder: str) -> bool:
        return folder in self.folders

    def files_under(self, names) -> list[str]:
        out = []
        for n in names:
            if n in self.entries and not self.entries[n].is_dir:
                out.append(n)
            else:
                out += [k for k, e in self.entries.items() if k.startswith(n + "/") and not e.is_dir]
        return out
