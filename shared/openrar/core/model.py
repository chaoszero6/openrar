"""Shared data types, errors, progress reporting and user-interaction hooks."""
from __future__ import annotations

import posixpath
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional


# --------------------------------------------------------------------------- errors
class ArchiveError(Exception):
    """Base class for all archive errors."""


class PasswordRequired(ArchiveError):
    pass


class BadPassword(ArchiveError):
    pass


class Cancelled(ArchiveError):
    pass


class UnsupportedOperation(ArchiveError):
    pass


class ArchiveLocked(ArchiveError):
    def __init__(self, path: str = ""):
        super().__init__(f"The archive is locked and cannot be modified: {path}")


class MissingTool(ArchiveError):
    pass


class CorruptArchive(ArchiveError):
    pass


# --------------------------------------------------------------------------- entries
@dataclass
class Entry:
    name: str                       # POSIX style path inside archive, no trailing slash
    is_dir: bool = False
    size: int = 0
    packed: Optional[int] = None
    mtime: Optional[datetime] = None
    crc: Optional[int] = None
    encrypted: bool = False
    method: str = ""
    mode: int = 0                   # unix permission bits if known

    @property
    def basename(self) -> str:
        return posixpath.basename(self.name)

    @property
    def parent(self) -> str:
        return posixpath.dirname(self.name)


@dataclass
class ArchiveSummary:
    path: str
    format: str
    disk_size: int = 0
    volumes: int = 1
    files: int = 0
    dirs: int = 0
    unpacked: int = 0
    packed: int = 0
    encrypted: bool = False
    header_encrypted: bool = False
    solid: bool = False
    comment: str = ""
    locked: bool = False
    recovery: str = ""              # description of recovery data if present
    sfx: bool = False
    methods: list = field(default_factory=list)

    @property
    def ratio(self) -> float:
        if not self.unpacked:
            return 0.0
        return 100.0 * self.packed / self.unpacked


@dataclass
class CreateOptions:
    format: str = "zip"             # zip, 7z, tar, tar.gz, tar.bz2, tar.xz, tar.zst
    level: int = 3                  # 0 store, 1 fastest, 2 fast, 3 normal, 4 good, 5 best
    method: str = ""                # format-specific method name, "" = default
    password: str = ""
    encrypt_names: bool = False     # 7z only: encrypt file names (header encryption)
    volume_size: int = 0            # bytes; 0 = single file
    sfx: bool = False               # build a self-extracting archive
    sfx_options: dict = field(default_factory=dict)
    sfx_target: str = ""            # "windows" (.exe, ZIP inside) or "linux" (.run, TAR.GZ inside); "" = from name
    recovery_percent: float = 0     # 0 = no recovery record
    lock: bool = False
    comment: str = ""
    delete_after: bool = False
    test_after: bool = False
    exclude: list = field(default_factory=list)
    update_mode: str = "add"        # add, update, freshen, sync
    store_paths: str = "relative"   # relative, none, full
    include_empty_dirs: bool = True
    each_to_separate: bool = False  # put each file into a separate archive
    zip_unicode: bool = True


@dataclass
class ExtractOptions:
    dest: str = "."
    paths: str = "full"             # full, none
    overwrite: str = "ask"          # ask, always, never, rename, newer
    keep_broken: bool = False
    set_times: bool = True
    strip_prefix: str = ""          # archive folder the user is currently inside
    password: str = ""


# --------------------------------------------------------------------------- progress
ProgressCallback = Callable[[int, int, str], None]


class Reporter:
    """Thread-safe progress, pause and cancel support shared by all operations."""

    def __init__(self, on_progress: Optional[ProgressCallback] = None, total: int = 0,
                 on_message: Optional[Callable[[str], None]] = None):
        self.on_progress = on_progress
        self.on_message = on_message
        self.total = total
        self.done = 0
        self.current = ""
        self.cancel_event = threading.Event()
        self.pause_event = threading.Event()
        self._last = 0.0
        self.messages: list[str] = []

    # control
    def cancel(self):
        self.cancel_event.set()
        self.pause_event.clear()

    def check(self):
        if self.cancel_event.is_set():
            raise Cancelled("Operation cancelled")
        while self.pause_event.is_set():
            time.sleep(0.1)
            if self.cancel_event.is_set():
                raise Cancelled("Operation cancelled")

    # reporting
    def set_total(self, total: int):
        self.total = max(0, int(total))
        self._emit(force=True)

    def begin(self, name: str):
        self.current = name
        self.check()
        self._emit(force=True)

    def advance(self, n: int):
        self.done += n
        self.check()
        self._emit()

    def message(self, text: str):
        self.messages.append(text)
        if self.on_message:
            self.on_message(text)

    def _emit(self, force: bool = False):
        if not self.on_progress:
            return
        now = time.monotonic()
        if force or now - self._last > 0.05:
            self._last = now
            self.on_progress(self.done, self.total, self.current)


class Interaction:
    """Questions an operation may need to ask the user. GUI and CLI subclass this."""

    def ask_overwrite(self, path: str, entry: Entry) -> str:
        """Return one of: yes, no, yes_all, no_all, rename, cancel."""
        return "no"

    def ask_password(self, archive: str, entry: str = "") -> Optional[str]:
        return None


class CountingReader:
    """File-like wrapper that reports bytes read to a Reporter."""

    def __init__(self, fileobj, reporter: Optional[Reporter]):
        self._f = fileobj
        self._r = reporter

    def read(self, n: int = -1) -> bytes:
        data = self._f.read(n)
        if self._r and data:
            self._r.advance(len(data))
        return data

    def readinto(self, b) -> int:
        data = self.read(len(b))
        b[:len(data)] = data
        return len(data)

    def seek(self, *a):
        return self._f.seek(*a)

    def tell(self):
        return self._f.tell()

    def close(self):
        self._f.close()

    def readable(self):
        return True

    def seekable(self):
        return getattr(self._f, "seekable", lambda: False)()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
