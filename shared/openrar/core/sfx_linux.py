"""Self-extracting archives for Linux / Unix: a POSIX sh script followed by a .tar.gz payload.

Layout:  <script ending in "exit">  <tar.gz bytes>
The script stores the payload's byte offset in a fixed-width OPENRAR_OFFSET=... line, so OpenRAR (on any
platform) and the script itself can find the archive. SFX options are kept twice: as shell variables the
script uses, and as "#: Key=value" comment lines OpenRAR reads back (same keys as the Windows module).
"""
from __future__ import annotations

import os
import re
import sys

from .formats import SFX_BEGIN, SFX_END
from .sfx import SFX_KEYS, parse_script

MAGIC = b"#!/bin/sh\n# OpenRAR self-extracting archive"
OFFSET_WIDTH = 12
_OFFSET_RE = re.compile(rb"\nOPENRAR_OFFSET=(\d+) *\n")   # space-padded: sh reads 0123 as octal


def template_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [os.path.join(here, "..", "resources", "sfx.sh")]
    mei = getattr(sys, "_MEIPASS", None)
    if mei:
        cands.insert(0, os.path.join(mei, "openrar", "resources", "sfx.sh"))
    for c in cands:
        if os.path.isfile(c):
            return os.path.normpath(c)
    raise FileNotFoundError("Linux SFX module (resources/sfx.sh) is missing from this installation")


def _q(v: str) -> str:
    """Single-quote for sh."""
    return "'" + str(v).replace("'", "'\\''") + "'"


def _text(v) -> str:
    return str(v or "").replace("\r\n", "\n").replace("\\n", "\n")


def build_header(options: dict, name: str = "archive.run") -> bytes:
    o = {k: options.get(k) for k in SFX_KEYS}
    lines = ["#: " + SFX_BEGIN]
    for k in SFX_KEYS:
        v = o[k]
        if v in (None, "", False):
            continue
        v = "1" if v is True else _text(v).replace("\n", "\\n")
        lines.append(f"#: {k}={v}")
    lines.append("#: " + SFX_END)
    flag = lambda k: "1" if str(o[k] or "") in ("1", "True", "true") or o[k] is True else "0"  # noqa: E731
    overwrite = str(o["Overwrite"] or "0")
    settings = "\n".join([
        f"TITLE={_q(_text(o['Title']))}",
        f"TEXT={_q(_text(o['Text']))}",
        f"LICENSE={_q(_text(o['License']))}",
        f"DEFAULT_PATH={_q(_text(o['Path']))}",
        f"SETUP={_q(_text(o['Setup']))}",
        f"SILENT={flag('Silent')}",
        f"OVERWRITE={overwrite if overwrite in ('0', '1', '2') else '0'}",
        f"TEMPMODE={flag('TempMode')}",
    ])
    with open(template_path(), encoding="utf-8") as f:
        tpl = f.read()
    safe_name = re.sub(r"[^\w.+-]", "_", os.path.basename(name))
    script = (tpl.replace("@NAME@", safe_name).replace("@OPTIONS@", "\n".join(lines))
              .replace("@SETTINGS@", settings).replace("\r\n", "\n"))
    placeholder = "OPENRAR_OFFSET=@OFFSET@"
    assert placeholder in script
    if not script.endswith("\n"):
        script += "\n"
    data = script.encode("utf-8")
    size = len(data) - len("@OFFSET@") + OFFSET_WIDTH
    return data.replace(b"@OFFSET@", str(size).ljust(OFFSET_WIDTH).encode())


def is_linux_sfx(head: bytes) -> bool:
    return head.startswith(MAGIC)


def payload_offset(path: str) -> int:
    with open(path, "rb") as f:
        head = f.read(64 * 1024)
    m = _OFFSET_RE.search(head)
    if not head.startswith(MAGIC) or not m:
        raise ValueError("Not an OpenRAR Linux self-extracting archive")
    return int(m.group(1))


def read_options(path: str) -> dict:
    off = payload_offset(path)
    with open(path, "rb") as f:
        header = f.read(off).decode("utf-8", "replace")
    block = "\n".join(line[3:] for line in header.splitlines() if line.startswith("#: "))
    return parse_script(block.replace(SFX_BEGIN, "").replace(SFX_END, ""))
