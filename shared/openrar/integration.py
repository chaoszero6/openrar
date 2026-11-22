"""What the file-manager integration offers. Shared by the Windows (registry) and Linux (.desktop,
service menus) implementations so both show the same commands."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Verb:
    setting: str          # key in settings["shell"]
    key: str              # stable id, also sorts the menu
    label: str
    icon: str
    args: tuple           # arguments before the selected file(s)
    archives_only: bool   # only shown on archive files


VERBS = [
    Verb("add", "a_add", "Add to archive...", "add", ("--add-dialog",), False),
    Verb("add_quick", "a_quick", "Add to .zip archive", "add", ("--add-quick",), False),
    Verb("extract", "b_extract", "Extract files...", "extract", ("--extract-dialog",), True),
    Verb("extract_here", "c_here", "Extract Here", "extract", ("--extract-here",), True),
    Verb("extract_to", "d_to", "Extract to subfolder", "extract", ("--extract-subfolder",), True),
    Verb("test", "e_test", "Test archive", "test", ("--test-archives",), True),
]

# extension -> MIME type (shared-mime-info names; first one is the canonical type)
ARCHIVE_TYPES = {
    ".zip": ["application/zip", "application/x-zip-compressed"],
    ".7z": ["application/x-7z-compressed"],
    ".rar": ["application/vnd.rar", "application/x-rar", "application/x-rar-compressed"],
    ".tar": ["application/x-tar"],
    ".gz": ["application/gzip", "application/x-gzip"],
    ".tgz": ["application/x-compressed-tar"],
    ".bz2": ["application/x-bzip2", "application/x-bzip"],
    ".tbz2": ["application/x-bzip-compressed-tar", "application/x-bzip2-compressed-tar"],
    ".xz": ["application/x-xz"],
    ".txz": ["application/x-xz-compressed-tar"],
    ".zst": ["application/zstd"],
    ".tzst": ["application/x-zstd-compressed-tar"],
    ".iso": ["application/x-cd-image", "application/vnd.efi.iso"],
    ".cab": ["application/vnd.ms-cab-compressed"],
    ".arj": ["application/x-arj"],
    ".lzh": ["application/x-lha"],
    ".lha": ["application/x-lha"],
    ".001": ["application/x-7z-compressed-split"],
    ".cbz": ["application/vnd.comicbook+zip"],
    ".cbr": ["application/vnd.comicbook-rar"],
    ".cb7": ["application/x-cb7"],
    ".wim": ["application/x-ms-wim"],
    ".z": ["application/x-compress"],
    ".run": ["application/x-openrar-sfx"],
}
DEFAULT_ASSOCIATIONS = [".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".001"]


def verbs(cfg: dict, archives: bool) -> list[Verb]:
    return [v for v in VERBS if cfg.get(v.setting) and (archives or not v.archives_only)]
