"""Original line icons (24x24 SVG) tinted to the current palette."""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_P = {
    "add": '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M2 4h20v3H2z"/><path d="M12 10v7M8.5 13.5h7"/>',
    "extract": '<rect x="3" y="9" width="18" height="11" rx="2"/><path d="M2 6h20v3H2z"/><path d="M12 2v10M8.5 8.5 12 12l3.5-3.5"/>',
    "test": '<path d="M12 3l8 3v5c0 5-3.4 8.6-8 10-4.6-1.4-8-5-8-10V6z"/><path d="M8.5 12l2.5 2.5 4.5-5"/>',
    "view": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "delete": '<path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14"/><path d="M10 10v7M14 10v7"/>',
    "find": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5 21 21"/>',
    "wizard": '<path d="M4 20 16 8M14 6l4 4"/><path d="M18 2v3M21.5 5.5h-3M6 3v2M7 4H5M20 14v2M21 15h-2"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
    "repair": '<path d="M14.5 4.5a4.5 4.5 0 0 0 5 6l-9 9a2.1 2.1 0 0 1-3-3l9-9a4.5 4.5 0 0 1-2-3z"/>',
    "up": '<path d="M12 19V5M6 11l6-6 6 6"/>',
    "back": '<path d="M19 12H5M11 6l-6 6 6 6"/>',
    "comment": '<path d="M4 5h16v11H9l-5 4z"/><path d="M8 9h8M8 12h5"/>',
    "lock": '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    "unlock": '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 7.6-1.7"/>',
    "sfx": '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M2 4h20v3H2z"/><path d="M13 9l-3 5h4l-3 5"/>',
    "convert": '<path d="M4 9a8 8 0 0 1 14-3l2 2M20 15a8 8 0 0 1-14 3l-2-2"/><path d="M20 3v5h-5M4 21v-5h5"/>',
    "benchmark": '<path d="M4 18a8 8 0 1 1 16 0"/><path d="M12 18l4-6"/><circle cx="12" cy="18" r="1.2"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M2 12h3M19 12h3M4.9 19.1 7 17M17 7l2.1-2.1"/>',
    "folder": '<path d="M3 6a1 1 0 0 1 1-1h5l2 2h9a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/>',
    "archive": '<rect x="4" y="7" width="16" height="13" rx="2"/><path d="M3 4h18v3H3z"/><path d="M12 9v1M12 12v1M12 15v1"/>',
    "protect": '<path d="M12 3l8 3v5c0 5-3.4 8.6-8 10-4.6-1.4-8-5-8-10V6z"/><path d="M12 8v8M8 12h8"/>',
    "virus": '<circle cx="12" cy="12" r="5"/><path d="M12 2v5M12 17v5M2 12h5M17 12h5M5 5l3.5 3.5M15.5 15.5 19 19M5 19l3.5-3.5M15.5 8.5 19 5"/>',
    "report": '<path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M9 12h7M9 16h7"/>',
    "star": '<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.5 2.9 1-6.1L3.2 9.5l6.1-.9z"/>',
    "drive": '<rect x="3" y="13" width="18" height="7" rx="2"/><path d="M5 13l2-8h10l2 8"/><circle cx="17" cy="16.5" r=".6"/>',
    "refresh": '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
    "newfolder": '<path d="M3 6a1 1 0 0 1 1-1h5l2 2h9a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/><path d="M12 10v6M9 13h6"/>',
    "tree": '<path d="M4 4h6v4H4zM14 10h6v4h-6zM14 17h6v4h-6z"/><path d="M7 8v11h7M7 12h7"/>',
    "key": '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M17 6l3 3M15 8l2 2"/>',
    "rename": '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13 7l4 4"/>',
    "copy": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
    "open": '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6V14M12 17v.5"/>',
    "theme": '<path d="M12 3a9 9 0 1 0 9 9 7 7 0 0 1-9-9z"/>',
    "pause": '<path d="M8 5v14M16 5v14"/>',
    "play": '<path d="M7 4l13 8-13 8z"/>',
}

_COLORS = {"default": "#3f4a5a", "dark": "#d5dbe4"}
_ACCENTS = {"add": "#4f46e5", "extract": "#0284c7", "test": "#16a34a", "delete": "#dc2626", "repair": "#d97706",
            "protect": "#16a34a", "lock": "#d97706", "sfx": "#ea580c", "virus": "#dc2626", "star": "#eab308",
            "folder": "#e0a526", "archive": "#6366f1"}

_dark = False


def set_dark(dark: bool):
    global _dark
    _dark = dark
    icon.cache_clear()


@lru_cache(maxsize=None)
def icon(name: str, accent: bool = True) -> QIcon:
    body = _P.get(name, _P["info"])
    base = _COLORS["dark" if _dark else "default"]
    color = _ACCENTS.get(name, base) if accent else base
    if _dark and accent and name in _ACCENTS:
        color = _lighten(color)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
    if name == "folder":
        svg = svg.replace("<path", f'<path fill="{color}" fill-opacity="0.25"', 1)
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    ic = QIcon()
    for size in (16, 20, 24, 32, 48, 64):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        renderer.render(p, QRectF(0, 0, size, size))
        p.end()
        ic.addPixmap(pm)
    return ic


def _lighten(hex_color: str) -> str:
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    r, g, b = (int(c + (255 - c) * 0.3) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"
