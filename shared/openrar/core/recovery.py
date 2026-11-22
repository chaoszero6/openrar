"""Recovery records: parity data that lets a damaged archive be repaired.

A record is stored next to the archive as `<archive>.orr` so it works with
every format and never changes the archive bytes that other tools read.

Layout: the archive is split into sectors. With N parity columns, parity
sector c is the XOR of every sector whose index i satisfies i % N == c.
Per-sector CRC32 values locate damage. Any damaged sectors can be rebuilt
as long as no two of them share a column, so a contiguous burst of damage
up to the record size (N sectors) is always repairable. Truncated archives
are also handled.
"""
from __future__ import annotations

import hashlib
import os
import struct
import zlib

from .model import ArchiveError, Reporter

MAGIC = b"OPENRAR-RR\x00\x01"
HDR = struct.Struct("<12sQIIf32s")      # magic, file size, sector size, columns, percent, sha256


def record_path(archive: str) -> str:
    return archive + ".orr"


def _sector_size(size: int) -> int:
    s = 512
    while size / s > 262144:        # cap the CRC table at ~1 MB
        s *= 2
    return s


def create_record(archive: str, percent: float, reporter: Reporter | None = None) -> str:
    percent = max(0.1, min(float(percent), 100.0))
    size = os.path.getsize(archive)
    ss = _sector_size(size)
    nsec = max(1, (size + ss - 1) // ss)
    cols = max(1, min(nsec, int(nsec * percent / 100.0 + 0.999)))
    row_bytes = cols * ss
    parity = 0
    crcs = bytearray()
    sha = hashlib.sha256()
    if reporter:
        reporter.begin(f"Adding recovery record to {os.path.basename(archive)}")
        reporter.set_total(size)
    with open(archive, "rb") as f:
        while True:
            row = f.read(row_bytes)
            if not row:
                break
            sha.update(row)
            if len(row) < row_bytes:
                row = row + b"\0" * (row_bytes - len(row))
            mv = memoryview(row)
            for o in range(0, row_bytes, ss):
                crcs += struct.pack("<I", zlib.crc32(mv[o:o + ss]))
            parity ^= int.from_bytes(row, "little")
            if reporter:
                reporter.advance(len(row))
    crcs = crcs[: nsec * 4]
    out = record_path(archive)
    body = HDR.pack(MAGIC, size, ss, cols, float(percent), sha.digest()) + bytes(crcs) + \
        parity.to_bytes(row_bytes, "little")
    with open(out, "wb") as f:
        f.write(body)
        f.write(struct.pack("<I", zlib.crc32(body)))
    return out


def read_header(path: str) -> dict:
    with open(path, "rb") as f:
        magic, size, ss, cols, pct, digest = HDR.unpack(f.read(HDR.size))
    if magic != MAGIC:
        raise ArchiveError("Not an OpenRAR recovery record")
    return {"size": size, "sector": ss, "cols": cols, "percent": pct, "sha256": digest}


def _load(path: str):
    with open(path, "rb") as f:
        data = f.read()
    body, tail = data[:-4], data[-4:]
    if struct.unpack("<I", tail)[0] != zlib.crc32(body):
        raise ArchiveError("The recovery record itself is damaged")
    magic, size, ss, cols, pct, digest = HDR.unpack_from(body)
    nsec = max(1, (size + ss - 1) // ss)
    o = HDR.size
    crcs = struct.unpack_from(f"<{nsec}I", body, o)
    o += nsec * 4
    parity = body[o:o + cols * ss]
    return size, ss, cols, digest, crcs, parity


def verify(archive: str, reporter: Reporter | None = None) -> tuple[bool, list[int]]:
    """Return (intact, damaged_sector_indexes)."""
    size, ss, cols, digest, crcs, _ = _load(record_path(archive))
    bad = _scan(archive, size, ss, crcs, reporter)
    return (not bad and os.path.getsize(archive) == size), bad


def _scan(archive, size, ss, crcs, reporter):
    bad = []
    with open(archive, "rb") as f:
        for i, want in enumerate(crcs):
            sec = f.read(ss)
            if i == len(crcs) - 1 or len(sec) < ss:
                sec = sec + b"\0" * (ss - len(sec))
            if zlib.crc32(sec) != want:
                bad.append(i)
            if reporter:
                reporter.advance(ss)
    return bad


def repair(archive: str, output: str, reporter: Reporter | None = None) -> str:
    """Rebuild a damaged archive into `output`. Returns a human-readable report."""
    size, ss, cols, digest, crcs, parity = _load(record_path(archive))
    nsec = len(crcs)
    if reporter:
        reporter.begin("Scanning for damaged sectors")
        reporter.set_total(size * 2)
    bad = _scan(archive, size, ss, crcs, reporter)
    if not bad and os.path.getsize(archive) == size:
        return "No damage found; the archive is intact."
    bycol: dict[int, list[int]] = {}
    for b in bad:
        bycol.setdefault(b % cols, []).append(b)
    unrecoverable = [c for c, v in bycol.items() if len(v) > 1]
    if unrecoverable:
        raise ArchiveError(f"{len(bad)} damaged sectors found, too much damage in {len(unrecoverable)} "
                           f"parity groups for the recovery record to fix.")
    badset = set(bad)
    row_bytes = cols * ss
    acc = int.from_bytes(parity, "little")
    if reporter:
        reporter.begin("Reconstructing data")
    with open(archive, "rb") as f:
        idx = 0
        while idx < nsec:
            row = bytearray(f.read(row_bytes))
            if len(row) < row_bytes:
                row += b"\0" * (row_bytes - len(row))
            for c in range(cols):
                if idx + c in badset:
                    row[c * ss:(c + 1) * ss] = b"\0" * ss
            acc ^= int.from_bytes(row, "little")
            idx += cols
            if reporter:
                reporter.advance(row_bytes)
    fixed = acc.to_bytes(row_bytes, "little")
    tmp = output + ".tmp"
    with open(archive, "rb") as src, open(tmp, "wb") as dst:
        for i in range(nsec):
            sec = src.read(ss)
            if i in badset:
                c = i % cols
                sec = fixed[c * ss:(c + 1) * ss]
            dst.write(sec)
        dst.truncate(size)
    h = hashlib.sha256()
    with open(tmp, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    if h.digest() != digest:
        os.remove(tmp)
        raise ArchiveError("Reconstruction failed: the repaired data does not match the original checksum.")
    os.replace(tmp, output)
    return f"Repaired {len(bad)} damaged sector(s) ({len(bad) * ss:,} bytes). Saved as {output}"
