"""Split ("multi-volume") archives: name.ext.001, name.ext.002, ...

The splitting is format independent and byte-compatible with 7-Zip / WinRAR's
"split into volumes" for non-RAR formats, so volumes can be joined by any tool
(or with `copy /b`).
"""
from __future__ import annotations

import io
import os
import re

_VOL_RE = re.compile(r"^(.*)\.(\d{3,})$")


def volume_name(base: str, index: int) -> str:
    return f"{base}.{index + 1:03d}"


def is_volume(path: str) -> bool:
    return bool(_VOL_RE.match(path))


def first_volume(path: str) -> str:
    m = _VOL_RE.match(path)
    if not m:
        return path
    return f"{m.group(1)}.{1:0{len(m.group(2))}d}"


def list_volumes(path: str) -> list[str]:
    m = _VOL_RE.match(path)
    if not m:
        return [path]
    base, width = m.group(1), len(m.group(2))
    vols = []
    i = 1
    while True:
        p = f"{base}.{i:0{width}d}"
        if not os.path.exists(p):
            break
        vols.append(p)
        i += 1
    return vols


class VolumeWriter(io.RawIOBase):
    """Seekable writable file that spreads its bytes across fixed-size volumes."""

    def __init__(self, base: str, volume_size: int):
        super().__init__()
        if volume_size < 1024:
            raise ValueError("Volume size must be at least 1 KB")
        self.base = base
        self.vsize = volume_size
        self.pos = 0
        self.length = 0
        self._handles: dict[int, io.BufferedRandom] = {}
        # Remove stale volumes from a previous run so they are not mistaken as ours.
        i = 0
        while os.path.exists(volume_name(base, i)):
            os.remove(volume_name(base, i))
            i += 1

    def _h(self, idx: int):
        h = self._handles.get(idx)
        if h is None:
            p = volume_name(self.base, idx)
            h = open(p, "r+b" if os.path.exists(p) else "w+b")
            self._handles[idx] = h
            if len(self._handles) > 8:          # keep the number of open handles small
                for k in list(self._handles)[:-8]:
                    if k != idx:
                        self._handles.pop(k).close()
        return h

    def writable(self):
        return True

    def seekable(self):
        return True

    def readable(self):
        return True

    def write(self, b) -> int:
        mv = memoryview(b).cast("B")
        total = len(mv)
        while len(mv):
            idx, off = divmod(self.pos, self.vsize)
            n = min(len(mv), self.vsize - off)
            h = self._h(idx)
            h.seek(off)
            h.write(mv[:n])
            mv = mv[n:]
            self.pos += n
        self.length = max(self.length, self.pos)
        return total

    def read(self, n=-1) -> bytes:
        if n is None or n < 0:
            n = self.length - self.pos
        out = bytearray()
        while n > 0 and self.pos < self.length:
            idx, off = divmod(self.pos, self.vsize)
            k = min(n, self.vsize - off, self.length - self.pos)
            h = self._h(idx)
            h.seek(off)
            chunk = h.read(k)
            if not chunk:
                break
            out += chunk
            self.pos += len(chunk)
            n -= len(chunk)
        return bytes(out)

    def readinto(self, b):
        data = self.read(len(b))
        b[: len(data)] = data
        return len(data)

    def seek(self, offset, whence=0):
        if whence == 0:
            self.pos = offset
        elif whence == 1:
            self.pos += offset
        else:
            self.pos = self.length + offset
        return self.pos

    def tell(self):
        return self.pos

    def truncate(self, size=None):
        size = self.pos if size is None else size
        self.length = size
        return size

    def flush(self):
        for h in self._handles.values():
            h.flush()

    def close(self):
        if self.closed:
            return
        for h in self._handles.values():
            h.close()
        self._handles.clear()
        # trim the final volume and drop any volumes beyond the logical length
        count = max(1, (self.length + self.vsize - 1) // self.vsize)
        for i in range(count):
            p = volume_name(self.base, i)
            want = min(self.vsize, self.length - i * self.vsize)
            if not os.path.exists(p):
                open(p, "wb").close()
            if os.path.getsize(p) != want:
                with open(p, "r+b") as f:
                    f.truncate(want)
        i = count
        while os.path.exists(volume_name(self.base, i)):
            os.remove(volume_name(self.base, i))
            i += 1
        self.paths = [volume_name(self.base, i) for i in range(count)]
        super().close()


class VolumeReader(io.RawIOBase):
    """Read-only seekable view of name.001 + name.002 + ... as one stream."""

    def __init__(self, path: str):
        super().__init__()
        self.paths = list_volumes(path)
        if not self.paths:
            raise FileNotFoundError(path)
        self.sizes = [os.path.getsize(p) for p in self.paths]
        self.starts = []
        s = 0
        for sz in self.sizes:
            self.starts.append(s)
            s += sz
        self.length = s
        self.pos = 0
        self._idx = -1
        self._h = None
        self.name = path

    def readable(self):
        return True

    def seekable(self):
        return True

    def _open(self, idx):
        if idx != self._idx:
            if self._h:
                self._h.close()
            self._h = open(self.paths[idx], "rb")
            self._idx = idx

    def read(self, n=-1) -> bytes:
        if n is None or n < 0:
            n = self.length - self.pos
        out = bytearray()
        while n > 0 and self.pos < self.length:
            idx = self._locate(self.pos)
            off = self.pos - self.starts[idx]
            k = min(n, self.sizes[idx] - off)
            self._open(idx)
            self._h.seek(off)
            chunk = self._h.read(k)
            if not chunk:
                break
            out += chunk
            self.pos += len(chunk)
            n -= len(chunk)
        return bytes(out)

    def readinto(self, b):
        data = self.read(len(b))
        b[: len(data)] = data
        return len(data)

    def _locate(self, pos):
        lo, hi = 0, len(self.starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.starts[mid] <= pos:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def seek(self, offset, whence=0):
        if whence == 0:
            self.pos = offset
        elif whence == 1:
            self.pos += offset
        else:
            self.pos = self.length + offset
        return self.pos

    def tell(self):
        return self.pos

    def close(self):
        if self._h:
            self._h.close()
            self._h = None
        super().close()
