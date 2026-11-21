"""Self-extracting archives: a small native Windows stub followed by a ZIP.

SFX options are stored as a script in the ZIP comment (between marker lines),
which the stub reads at start-up:

    Title=My Setup
    Text=Welcome!\\nThis installs ...
    Path=%ProgramFiles%\\MyApp       default destination
    Setup=setup.exe /quiet           run after extraction (relative to dest)
    Silent=1                         0 = show dialog, 1 = no UI
    Overwrite=1                      0 = ask, 1 = overwrite all, 2 = skip existing
    TempMode=1                       extract to temp, run Setup, then delete
    License=...text the user must accept...
"""
from __future__ import annotations

import os
import sys

from .formats import SFX_BEGIN, SFX_END

SFX_KEYS = ["Title", "Text", "Path", "Setup", "Silent", "Overwrite", "TempMode", "License"]


def stub_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [os.path.join(here, "..", "resources", "sfx.exe")]
    mei = getattr(sys, "_MEIPASS", None)
    if mei:
        cands.insert(0, os.path.join(mei, "openrar", "resources", "sfx.exe"))
    for c in cands:
        if os.path.isfile(c):
            return os.path.normpath(c)
    raise FileNotFoundError("SFX module (resources/sfx.exe) is missing from this installation")


def build_script(options: dict) -> str:
    lines = [SFX_BEGIN]
    for k in SFX_KEYS:
        v = options.get(k)
        if v in (None, "", False):
            continue
        if v is True:
            v = "1"
        v = str(v).replace("\\n", "\n").replace("\r\n", "\n").replace("\n", "\\n")
        lines.append(f"{k}={v}")
    lines.append(SFX_END)
    return "\n".join(lines)


def parse_script(block: str) -> dict:
    out = {}
    for line in block.splitlines():
        if "=" in line and not line.startswith(";"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out
