"""Format writers used to create archives."""
from __future__ import annotations

import os
import pathlib
import tarfile
import zipfile
from datetime import datetime

import py7zr
import pyzipper

from .model import CreateOptions, CountingReader, Reporter, UnsupportedOperation

try:
    import zstandard
except ImportError:  # pragma: no cover
    zstandard = None

CHUNK = 1 << 20

FORMATS = {
    "zip": "ZIP",
    "7z": "7Z",
    "tar": "TAR",
    "tar.gz": "TAR.GZ",
    "tar.bz2": "TAR.BZ2",
    "tar.xz": "TAR.XZ",
    "tar.zst": "TAR.ZST",
}
LEVEL_NAMES = ["Store", "Fastest", "Fast", "Normal", "Good", "Best"]
METHODS = {
    "zip": ["Deflate", "BZip2", "LZMA", "Store"],
    "7z": ["LZMA2", "LZMA", "PPMd", "BZip2", "Deflate", "Zstandard", "Store"],
}


def format_from_path(path: str) -> str:
    low = path.lower()
    for ext in ("tar.gz", "tar.bz2", "tar.xz", "tar.zst"):
        if low.endswith("." + ext):
            return ext
    aliases = {".tgz": "tar.gz", ".tbz2": "tar.bz2", ".txz": "tar.xz", ".tzst": "tar.zst"}
    ext = os.path.splitext(low)[1]
    if ext in aliases:
        return aliases[ext]
    ext = ext.lstrip(".")
    if ext in ("zip", "7z", "tar"):
        return ext
    if ext == "run":
        return "tar.gz"                     # Linux self-extracting archive
    return "zip"


# --------------------------------------------------------------------------- ZIP
class ZipWriter:
    def __init__(self, fileobj, opts: CreateOptions, mode: str = "w"):
        method = (opts.method or "Deflate").lower()
        if opts.level == 0 or method == "store":
            ctype = zipfile.ZIP_STORED
        elif method == "bzip2":
            ctype = zipfile.ZIP_BZIP2
        elif method == "lzma":
            ctype = zipfile.ZIP_LZMA
        else:
            ctype = zipfile.ZIP_DEFLATED
        clevel = {0: None, 1: 1, 2: 3, 3: 6, 4: 8, 5: 9}.get(opts.level, 6)
        if ctype == zipfile.ZIP_LZMA:
            clevel = None
        self.ctype, self.clevel = ctype, clevel
        kw = {}
        if opts.password:
            kw["encryption"] = pyzipper.WZ_AES
        self.zf = pyzipper.AESZipFile(fileobj, mode, compression=ctype, compresslevel=clevel,
                                      allowZip64=True, **kw)
        if opts.password:
            self.zf.setpassword(opts.password.encode("utf-8"))
            self.zf.setencryption(pyzipper.WZ_AES, nbits=256)

    def add_file(self, abs_path: str, arcname: str, reporter: Reporter | None):
        zi = self.zf.zipinfo_cls.from_file(abs_path, arcname)
        zi.compress_type = self.ctype
        size = os.path.getsize(abs_path)
        if self.clevel is not None:
            zi._compresslevel = self.clevel
        with open(abs_path, "rb") as src, self.zf.open(zi, "w", force_zip64=size > 0x7FFF0000) as dst:
            while True:
                chunk = src.read(CHUNK)
                if not chunk:
                    break
                dst.write(chunk)
                if reporter:
                    reporter.advance(len(chunk))

    def add_dir(self, abs_path: str | None, arcname: str, mtime: datetime | None = None):
        if abs_path and os.path.isdir(abs_path):
            zi = self.zf.zipinfo_cls.from_file(abs_path, arcname)
        else:
            zi = self.zf.zipinfo_cls(arcname.rstrip("/") + "/", (mtime or datetime.now()).timetuple()[:6])
            zi.external_attr = (0o40755 << 16) | 0x10
        if not zi.filename.endswith("/"):
            zi.filename += "/"
        zi.compress_type = zipfile.ZIP_STORED
        self.zf.writestr(zi, b"")

    def set_comment(self, text: str):
        self.zf.comment = text.encode("utf-8")[:65535]

    def close(self):
        self.zf.close()


# --------------------------------------------------------------------------- 7z
class _ProgressPath(type(pathlib.Path())):
    """A Path whose open() reports read progress (py7zr reads origin.open('rb'))."""

    _reporter: Reporter | None = None

    def open(self, mode="r", *a, **kw):  # noqa: A003
        f = super().open(mode, *a, **kw)
        if "b" in mode and self._reporter:
            return CountingReader(f, self._reporter)
        return f


def sevenzip_filters(opts: CreateOptions):
    method = (opts.method or "LZMA2").lower()
    preset = {0: 0, 1: 1, 2: 3, 3: 5, 4: 7, 5: 9}.get(opts.level, 5)
    if opts.level == 0 or method == "store":
        f = [{"id": py7zr.FILTER_COPY}]
    elif method == "lzma":
        f = [{"id": py7zr.FILTER_LZMA, "preset": preset}]
    elif method == "ppmd":
        f = [{"id": py7zr.FILTER_PPMD, "order": 6, "mem": 24}]
    elif method == "bzip2":
        f = [{"id": py7zr.FILTER_BZIP2}]
    elif method == "deflate":
        f = [{"id": py7zr.FILTER_DEFLATE}]
    elif method == "zstandard":
        f = [{"id": py7zr.FILTER_ZSTD, "level": max(1, preset * 2)}]
    else:
        f = [{"id": py7zr.FILTER_LZMA2, "preset": preset}]
    if opts.password:
        f.append({"id": py7zr.FILTER_CRYPTO_AES256_SHA256})
    return f


class SevenZipWriter:
    def __init__(self, fileobj, opts: CreateOptions, mode: str = "w"):
        self.sz = py7zr.SevenZipFile(fileobj, mode, filters=sevenzip_filters(opts) if mode == "w" else None,
                                     password=opts.password or None,
                                     header_encryption=bool(opts.password and opts.encrypt_names))

    def add_file(self, abs_path, arcname, reporter):
        p = _ProgressPath(abs_path)
        p._reporter = reporter
        self.sz.write(p, arcname)

    def add_dir(self, abs_path, arcname, mtime=None):
        if abs_path and os.path.isdir(abs_path):
            self.sz.write(pathlib.Path(abs_path), arcname)

    def set_comment(self, text):
        if text:
            raise UnsupportedOperation("7z archives do not support comments")

    def close(self):
        self.sz.close()


# --------------------------------------------------------------------------- TAR
class _ZstdOut:
    def __init__(self, fileobj, level):
        self._raw = fileobj
        self._w = zstandard.ZstdCompressor(level=level, threads=-1).stream_writer(fileobj, closefd=False)

    def write(self, b):
        return self._w.write(b)

    def flush(self):
        pass

    def close(self):
        self._w.flush(zstandard.FLUSH_FRAME)
        self._w.close()


class TarWriter:
    def __init__(self, fileobj, opts: CreateOptions, mode: str = "w"):
        comp = opts.format.split(".", 1)[1] if "." in opts.format else ""
        lvl = max(1, {0: 1, 1: 1, 2: 3, 3: 6, 4: 8, 5: 9}.get(opts.level, 6))
        self._zst = None
        if comp == "gz":
            self.tf = tarfile.open(fileobj=fileobj, mode="w:gz", compresslevel=lvl, format=tarfile.PAX_FORMAT)
        elif comp == "bz2":
            self.tf = tarfile.open(fileobj=fileobj, mode="w:bz2", compresslevel=lvl, format=tarfile.PAX_FORMAT)
        elif comp == "xz":
            self.tf = tarfile.open(fileobj=fileobj, mode="w:xz", preset=lvl, format=tarfile.PAX_FORMAT)
        elif comp == "zst":
            if zstandard is None:
                raise UnsupportedOperation("zstandard module is not installed")
            self._zst = _ZstdOut(fileobj, {1: 1, 3: 3, 6: 6, 8: 12, 9: 19}.get(lvl, 3))
            self.tf = tarfile.open(fileobj=self._zst, mode="w|", format=tarfile.PAX_FORMAT)
        else:
            self.tf = tarfile.open(fileobj=fileobj, mode="w", format=tarfile.PAX_FORMAT)
        if opts.password:
            raise UnsupportedOperation("TAR archives cannot be encrypted. Use ZIP or 7Z for password protection.")

    def add_file(self, abs_path, arcname, reporter):
        ti = self.tf.gettarinfo(abs_path, arcname)
        if ti.isfile():
            with open(abs_path, "rb") as f:
                self.tf.addfile(ti, CountingReader(f, reporter))
        else:
            self.tf.addfile(ti)

    def add_dir(self, abs_path, arcname, mtime=None):
        if abs_path and os.path.isdir(abs_path):
            self.tf.addfile(self.tf.gettarinfo(abs_path, arcname))
        else:
            ti = tarfile.TarInfo(arcname)
            ti.type = tarfile.DIRTYPE
            ti.mode = 0o755
            ti.mtime = int((mtime or datetime.now()).timestamp())
            self.tf.addfile(ti)

    def add_member(self, member: tarfile.TarInfo, stream):
        self.tf.addfile(member, stream)

    def set_comment(self, text):
        if text:
            raise UnsupportedOperation("TAR archives do not support comments")

    def close(self):
        self.tf.close()
        if self._zst:
            self._zst.close()


def make_writer(fileobj, opts: CreateOptions, mode: str = "w"):
    fmt = opts.format
    if fmt == "zip":
        return ZipWriter(fileobj, opts, mode)
    if fmt == "7z":
        return SevenZipWriter(fileobj, opts, mode)
    if fmt in FORMATS:
        return TarWriter(fileobj, opts, mode)
    raise UnsupportedOperation(f"Cannot create {fmt} archives")
