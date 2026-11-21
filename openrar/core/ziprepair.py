"""Salvage files from a damaged ZIP that has no recovery record.

Scans for local file headers, keeps every entry whose data is intact
(verified by CRC for unencrypted entries) and writes a fresh, valid archive.
Encrypted entries are copied verbatim (they can't be verified without the
password).
"""
from __future__ import annotations

import bz2
import lzma
import os
import re
import struct
import zlib

from .model import Reporter

LOCAL = b"PK\x03\x04"
DD = b"PK\x07\x08"


def _inflate(method, data):
    if method == 0:
        return data
    if method == 8:
        d = zlib.decompressobj(-15)
        out = d.decompress(data)
        if not d.eof:
            raise ValueError("truncated deflate stream")
        return out
    if method == 12:
        return bz2.decompress(data)
    if method == 14:
        # zip LZMA: 2 bytes version, 2 bytes props size, props, raw lzma1 stream
        psize = struct.unpack_from("<H", data, 2)[0]
        props = data[4:4 + psize]
        filt = lzma._decode_filter_properties(lzma.FILTER_LZMA1, props)
        return lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=[filt]).decompress(data[4 + psize:])
    raise ValueError(f"unsupported method {method}")


def salvage(src: str, dst: str, reporter: Reporter | None = None) -> str:
    with open(src, "rb") as f:
        blob = f.read()
    if reporter:
        reporter.begin("Scanning for files")
        reporter.set_total(len(blob))
    offsets = [m.start() for m in re.finditer(re.escape(LOCAL), blob)]
    recovered, lost = [], []
    out = bytearray()
    central = bytearray()
    for n, off in enumerate(offsets):
        if reporter:
            reporter.done = off
            reporter.advance(0)
        if off + 30 > len(blob):
            break
        (ver, flags, method, mtime, mdate, crc, csize, usize, nlen, elen) = \
            struct.unpack_from("<HHHHHIIIHH", blob, off + 4)
        name = blob[off + 30: off + 30 + nlen]
        extra = blob[off + 30 + nlen: off + 30 + nlen + elen]
        data_start = off + 30 + nlen + elen
        dd = b""
        if flags & 0x08 or csize in (0, 0xFFFFFFFF) and not name.endswith(b"/"):
            # sizes live in a data descriptor; data ends before the next header
            nxt = offsets[n + 1] if n + 1 < len(offsets) else blob.find(b"PK\x01\x02", data_start)
            if nxt < 0:
                nxt = len(blob)
            seg = blob[data_start:nxt]
            p = seg.rfind(DD)
            if p >= 0 and len(seg) - p in (16, 24):
                crc, csize, usize = struct.unpack_from("<III", seg, p + 4)
                dd = seg[p:]
            elif len(seg) >= 12 and flags & 0x08:
                crc, csize, usize = struct.unpack_from("<III", seg, len(seg) - 12)
                dd = seg[-12:]
            else:
                csize = len(seg)
        data = blob[data_start:data_start + csize]
        uname = name.decode("utf-8" if flags & 0x800 else "cp437", "replace")
        if len(data) < csize:
            lost.append(uname)
            continue
        encrypted = flags & 1
        if not encrypted and method != 99:
            try:
                raw = _inflate(method, data)
                if zlib.crc32(raw) != crc or len(raw) != usize:
                    lost.append(uname)
                    continue
            except Exception:
                lost.append(uname)
                continue
        new_off = len(out)
        out += blob[off:off + 30] + name + extra + data + dd
        central += b"PK\x01\x02" + struct.pack("<HHHHHHIIIHHHHHII", 0x031E, ver, flags, method, mtime, mdate,
                                               crc, csize, usize, len(name), len(extra), 0, 0, 0,
                                               (0o644 << 16) if not name.endswith(b"/") else (0o40755 << 16) | 0x10,
                                               new_off) + name + extra
        recovered.append(uname)
    cd_off = len(out)
    out += central
    out += b"PK\x05\x06" + struct.pack("<HHHHIIH", 0, 0, len(recovered), len(recovered), len(central), cd_off, 0)
    with open(dst, "wb") as f:
        f.write(out)
    report = f"Recovered {len(recovered)} file(s) into {os.path.basename(dst)}."
    if lost:
        report += f"\n{len(lost)} file(s) could not be recovered:\n  " + "\n  ".join(lost[:50])
    return report
