"""Low-level ZIP structure access: raw entry copying (fast delete/rename/comment
without recompression or passwords) and central-directory parsing."""
from __future__ import annotations

import struct
from dataclasses import dataclass

LOCAL_SIG = b"PK\x03\x04"
CENTRAL_SIG = b"PK\x01\x02"
EOCD_SIG = b"PK\x05\x06"
EOCD64_SIG = b"PK\x06\x06"
EOCD64_LOC_SIG = b"PK\x06\x07"
DD_SIG = b"PK\x07\x08"


@dataclass
class CDRecord:
    raw: bytes            # complete central directory record
    name: bytes
    flags: int
    method: int
    crc: int
    csize: int
    usize: int
    offset: int           # absolute file offset of local header
    zip64_offset_pos: int  # index into raw where a zip64 offset lives, or -1


def _parse_zip64_extra(extra: bytes, usize, csize, offset):
    """Return (usize, csize, offset, offset_pos_in_extra)."""
    i = 0
    off_pos = -1
    while i + 4 <= len(extra):
        hid, ln = struct.unpack_from("<HH", extra, i)
        if hid == 1:
            j = i + 4
            if usize == 0xFFFFFFFF:
                usize = struct.unpack_from("<Q", extra, j)[0]
                j += 8
            if csize == 0xFFFFFFFF:
                csize = struct.unpack_from("<Q", extra, j)[0]
                j += 8
            if offset == 0xFFFFFFFF:
                offset = struct.unpack_from("<Q", extra, j)[0]
                off_pos = j
            break
        i += 4 + ln
    return usize, csize, offset, off_pos


def read_central_directory(f):
    """Parse EOCD and the central directory. Returns (records, comment, concat)."""
    f.seek(0, 2)
    size = f.tell()
    tail_len = min(size, 65536 + 22)
    f.seek(size - tail_len)
    tail = f.read(tail_len)
    pos = tail.rfind(EOCD_SIG)
    if pos < 0:
        raise ValueError("End of central directory not found")
    eocd_abs = size - tail_len + pos
    (_, _, _, _, count, cd_size, cd_off, clen) = struct.unpack_from("<4sHHHHIIH", tail, pos)
    comment = tail[pos + 22: pos + 22 + clen]
    cd_abs = eocd_abs - cd_size
    if cd_off == 0xFFFFFFFF or count == 0xFFFF or cd_size == 0xFFFFFFFF:
        loc = eocd_abs - 20
        f.seek(loc)
        locb = f.read(20)
        if locb[:4] == EOCD64_LOC_SIG:
            eocd64 = struct.unpack_from("<Q", locb, 8)[0]
            f.seek(eocd64)
            rec = f.read(56)
            # if prepended data exists the stored eocd64 pointer is relative; search back
            if rec[:4] != EOCD64_SIG:
                f.seek(loc - 56)
                rec = f.read(56)
                eocd64_abs = loc - 56
            else:
                eocd64_abs = eocd64
            count, cd_size, cd_off = struct.unpack_from("<QQQ", rec, 32)
            cd_abs = eocd64_abs - cd_size
    concat = cd_abs - cd_off
    f.seek(cd_abs)
    cd = f.read(cd_size)
    records = []
    i = 0
    while i + 46 <= len(cd) and cd[i:i + 4] == CENTRAL_SIG:
        (flags, method, _t, _d, crc, csize, usize, nlen, elen, clen2, _disk, _ia, _ea, offset) = \
            struct.unpack_from("<HHHHIIIHHHHHII", cd, i + 8)
        rec_len = 46 + nlen + elen + clen2
        raw = cd[i:i + rec_len]
        name = raw[46:46 + nlen]
        extra = raw[46 + nlen:46 + nlen + elen]
        usize, csize, offset, opos = _parse_zip64_extra(extra, usize, csize, offset)
        records.append(CDRecord(raw, name, flags, method, crc, csize, usize, offset + concat,
                                (46 + nlen + opos) if opos >= 0 else -1))
        i += rec_len
    return records, comment, concat


def _local_extent(f, rec: CDRecord):
    """Return (header_bytes, data_start, data_len, descriptor_len) for a record."""
    f.seek(rec.offset)
    hdr = f.read(30)
    if hdr[:4] != LOCAL_SIG:
        raise ValueError(f"Bad local header for {rec.name!r}")
    nlen, elen = struct.unpack_from("<HH", hdr, 26)
    rest = f.read(nlen + elen)
    data_start = rec.offset + 30 + nlen + elen
    dd_len = 0
    if rec.flags & 0x08:
        f.seek(data_start + rec.csize)
        peek = f.read(4)
        is64 = rec.csize >= 0xFFFFFFFF or rec.usize >= 0xFFFFFFFF or b"\x01\x00" in rest[nlen:nlen + 2]
        body = 20 if is64 else 12
        dd_len = body + (4 if peek == DD_SIG else 0)
    return hdr + rest, data_start, rec.csize, dd_len


def rewrite(src_path: str, dst, keep: list[tuple[CDRecord, bytes]], comment: bytes, prefix: bytes = b"",
            reporter=None):
    """Write a new zip to file object `dst` containing `keep` = [(record, new_name_bytes)],
    copying compressed data verbatim. Returns nothing; dst is left positioned at end."""
    out_records = []
    with open(src_path, "rb") as f:
        dst.write(prefix)
        for rec, new_name in keep:
            hdr, data_start, dlen, ddlen = _local_extent(f, rec)
            nlen, elen = struct.unpack_from("<HH", hdr, 26)
            new_off = dst.tell()
            flags = rec.flags
            if new_name != rec.name:
                flags |= 0x800  # names we write are UTF-8
            lh = bytearray(hdr[:30])
            struct.pack_into("<H", lh, 6, flags)
            struct.pack_into("<H", lh, 26, len(new_name))
            dst.write(bytes(lh) + new_name + hdr[30 + nlen:30 + nlen + elen])
            f.seek(data_start)
            remaining = dlen + ddlen
            while remaining:
                chunk = f.read(min(remaining, 1 << 20))
                if not chunk:
                    raise ValueError("Unexpected end of archive")
                dst.write(chunk)
                remaining -= len(chunk)
                if reporter:
                    reporter.advance(len(chunk))
            # central record with new name / offset
            raw = bytearray(rec.raw)
            onlen = struct.unpack_from("<H", raw, 28)[0]
            struct.pack_into("<H", raw, 8, flags)
            if rec.zip64_offset_pos >= 0:
                struct.pack_into("<Q", raw, rec.zip64_offset_pos, new_off)
            else:
                if new_off >= 0xFFFFFFFF:
                    raise OverflowError("zip64 offset needed")
                struct.pack_into("<I", raw, 42, new_off)
            struct.pack_into("<H", raw, 28, len(new_name))
            out_records.append(bytes(raw[:46]) + new_name + bytes(raw[46 + onlen:]))
        cd_start = dst.tell()
        for r in out_records:
            dst.write(r)
        cd_end = dst.tell()
        n = len(out_records)
        cd_size = cd_end - cd_start
        if n >= 0xFFFF or cd_size >= 0xFFFFFFFF or cd_start >= 0xFFFFFFFF:
            dst.write(EOCD64_SIG + struct.pack("<QHHIIQQQQ", 44, 45, 45, 0, 0, n, n, cd_size, cd_start))
            dst.write(EOCD64_LOC_SIG + struct.pack("<IQI", 0, cd_end, 1))
            dst.write(EOCD_SIG + struct.pack("<HHHHIIH", 0, 0, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF,
                                             len(comment)) + comment)
        else:
            # absolute offsets: readers locate the zip start from the EOCD, so SFX stubs keep working
            dst.write(EOCD_SIG + struct.pack("<HHHHIIH", 0, 0, n, n, cd_size, cd_start, len(comment)) + comment)
