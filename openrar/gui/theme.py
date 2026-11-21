"""Light / dark / follow-system themes on top of Qt's Fusion style."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

from . import icons

LIGHT = {
    "window": "#f4f6f9", "base": "#ffffff", "alt": "#f7f8fa", "text": "#1d2430", "mid": "#d6dbe3",
    "button": "#ffffff", "highlight": "#4f46e5", "hl_text": "#ffffff", "muted": "#667085", "toolbar": "#ffffff",
    "link": "#4338ca",
}
DARK = {
    "window": "#1b1f27", "base": "#222833", "alt": "#262c38", "text": "#e4e8ef", "mid": "#343c4a",
    "button": "#2a313d", "highlight": "#6366f1", "hl_text": "#ffffff", "muted": "#98a2b3", "toolbar": "#20252e",
    "link": "#a5b4fc",
}


def system_is_dark() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return False


def apply(app: QApplication, mode: str = "system") -> bool:
    dark = system_is_dark() if mode == "system" else mode == "dark"
    c = DARK if dark else LIGHT
    app.setStyle("Fusion")
    pal = QPalette()
    roles = {
        QPalette.Window: c["window"], QPalette.WindowText: c["text"], QPalette.Base: c["base"],
        QPalette.AlternateBase: c["alt"], QPalette.Text: c["text"], QPalette.Button: c["button"],
        QPalette.ButtonText: c["text"], QPalette.Highlight: c["highlight"],
        QPalette.HighlightedText: c["hl_text"], QPalette.ToolTipBase: c["base"], QPalette.ToolTipText: c["text"],
        QPalette.PlaceholderText: c["muted"], QPalette.Link: c["link"], QPalette.Mid: c["mid"],
        QPalette.Midlight: c["mid"], QPalette.Light: c["base"], QPalette.Dark: c["mid"],
    }
    for role, col in roles.items():
        pal.setColor(role, QColor(col))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(c["muted"]))
    app.setPalette(pal)
    app.setStyleSheet(f"""
        QToolBar#mainToolbar {{ background: {c['toolbar']}; border: none; border-bottom: 1px solid {c['mid']};
                                padding: 4px 6px; spacing: 2px; }}
        QToolBar#mainToolbar QToolButton {{ padding: 4px 8px; border-radius: 6px; min-width: 52px; }}
        QToolBar#mainToolbar QToolButton:hover {{ background: {c['alt']}; border: 1px solid {c['mid']}; }}
        QToolBar#mainToolbar QToolButton:pressed {{ background: {c['mid']}; }}
        QTreeView, QListView, QTableView {{ border: 1px solid {c['mid']}; border-radius: 4px; }}
        QTreeView::item {{ padding: 2px 0; }}
        QHeaderView::section {{ background: {c['alt']}; border: none; border-right: 1px solid {c['mid']};
                                border-bottom: 1px solid {c['mid']}; padding: 4px 6px; }}
        QStatusBar {{ border-top: 1px solid {c['mid']}; }}
        QLabel#muted {{ color: {c['muted']}; }}
        QLabel#heading {{ font-size: 13pt; font-weight: 600; }}
        QGroupBox {{ border: 1px solid {c['mid']}; border-radius: 6px; margin-top: 10px; padding-top: 6px; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; }}
        QProgressBar {{ border: 1px solid {c['mid']}; border-radius: 4px; text-align: center; height: 16px;
                        background: {c['base']}; }}
        QProgressBar::chunk {{ background: {c['highlight']}; border-radius: 3px; }}
    """)
    icons.set_dark(dark)
    return dark
