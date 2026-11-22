"""Extraction targets. Format handlers stream member data into a Sink."""
from __future__ import annotations

import os
import shutil
import time

from .model import Cancelled, Entry, ExtractOptions, Interaction, Reporter
from .util import IS_WINDOWS, safe_relpath, unique_path


def _read_umask():
    if IS_WINDOWS:
        return None
    m = os.umask(0)
    os.umask(m)
    return m


_UMASK = _read_umask()


class NullSink:
    """Discards data; used by Test."""

    def __init__(self, reporter: Reporter | None = None):
        self.reporter = reporter
        self.extracted: list[str] = []

    def open(self, entry: Entry):
        if self.reporter:
            self.reporter.begin(entry.name)
        return _NullWriter(self.reporter)

    def close(self, entry: Entry, f, ok: bool = True):
        pass

    def make_dir(self, entry: Entry):
        pass

    def finish(self):
        pass


class _NullWriter:
    def __init__(self, reporter):
        self.r = reporter
        self.n = 0

    def write(self, b):
        self.n += len(b)
        if self.r:
            self.r.advance(len(b))
        return len(b)

    def close(self):
        pass


class _CountingWriter:
    def __init__(self, f, reporter):
        self.f = f
        self.r = reporter

    def write(self, b):
        n = self.f.write(b)
        if self.r:
            self.r.advance(len(b))
        return n

    def flush(self):
        self.f.flush()

    def close(self):
        self.f.close()


class DiskSink:
    """Writes entries to disk honouring path mode, overwrite policy and timestamps."""

    def __init__(self, opts: ExtractOptions, interaction: Interaction | None = None,
                 reporter: Reporter | None = None):
        self.opts = opts
        self.ui = interaction or Interaction()
        self.reporter = reporter
        self.policy = opts.overwrite          # may become always/never after "all" answers
        self.extracted: list[str] = []
        self.skipped: list[str] = []
        self._paths: dict[int, str] = {}
        self._dirs: list = []
        os.makedirs(opts.dest, exist_ok=True)

    def target_path(self, entry: Entry) -> str | None:
        name = entry.name
        prefix = self.opts.strip_prefix.strip("/")
        if prefix:
            if name == prefix or not name.startswith(prefix + "/"):
                return None if name != prefix else None
            name = name[len(prefix) + 1:]
        rel = safe_relpath(os.path.basename(name) if self.opts.paths == "none" else name)
        if not rel:
            return None
        return os.path.join(self.opts.dest, rel)

    def make_dir(self, entry: Entry):
        if self.opts.paths == "none":
            return
        p = self.target_path(entry)
        if p:
            os.makedirs(p, exist_ok=True)
            if entry.mtime:
                self._dirs.append((p, entry.mtime))

    def finish(self):
        """Folder times are set last; writing files inside a folder changes its time."""
        if not self.opts.set_times:
            return
        for p, mt in sorted(self._dirs, key=lambda d: -len(d[0])):
            try:
                os.utime(p, (time.time(), mt.timestamp()))
            except (OSError, OverflowError, ValueError):
                pass

    def _resolve_conflict(self, path: str, entry: Entry) -> str | None:
        if not os.path.exists(path):
            return path
        pol = self.policy
        if pol == "newer":
            if entry.mtime and os.path.getmtime(path) >= entry.mtime.timestamp() - 1:
                return None
            return path
        if pol == "always":
            return path
        if pol == "never":
            return None
        if pol == "rename":
            return unique_path(path)
        ans = self.ui.ask_overwrite(path, entry)
        if ans == "yes":
            return path
        if ans == "yes_all":
            self.policy = "always"
            return path
        if ans == "no_all":
            self.policy = "never"
            return None
        if ans == "rename":
            return unique_path(path)
        if ans == "cancel":
            raise Cancelled("Operation cancelled")
        return None

    def open(self, entry: Entry):
        if self.reporter:
            self.reporter.begin(entry.name)
        path = self.target_path(entry)
        if not path:
            return None
        path = self._resolve_conflict(path, entry)
        if path is None:
            self.skipped.append(entry.name)
            if self.reporter:
                self.reporter.advance(entry.size)
            return None
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.exists(path):
            try:
                os.chmod(path, 0o666)
            except OSError:
                pass
        f = open(path, "wb")
        w = _CountingWriter(f, self.reporter)
        self._paths[id(w)] = path
        return w

    def close(self, entry: Entry, f, ok: bool = True):
        if f is None:
            return
        path = self._paths.pop(id(f), None)
        f.close()
        if path is None:
            return
        if not ok and not self.opts.keep_broken:
            try:
                os.remove(path)
            except OSError:
                pass
            return
        if _UMASK is not None and entry.mode & 0o777:
            try:                               # keep executable bits from ZIP/TAR made on Unix
                os.chmod(path, entry.mode & 0o777 & ~_UMASK | 0o600)
            except OSError:
                pass
        if self.opts.set_times and entry.mtime:
            try:
                t = entry.mtime.timestamp()
                os.utime(path, (time.time(), t))
            except (OSError, OverflowError, ValueError):
                pass
        self.extracted.append(path)
