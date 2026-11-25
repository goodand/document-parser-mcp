"""Design tokens of the Windows app (Theme.cs, Astryx-based) as one Qt style sheet.

Buttons carry a dynamic property `kind` (primary | secondary | ghost | tab) and tabs `active` (bool);
after changing a property call `repolish(widget)`.
"""

from __future__ import annotations

from PySide6.QtGui import QFont, QFontDatabase

CANVAS = "#F4F7F9"
SURFACE = "#FFFFFF"
BORDER = "#E3E8EE"
TEXT = "#1F2A37"
MUTED = "#63707E"
PRIMARY = "#00646E"
PRIMARY_PRESSED = "#004A52"
SELECTED = "#EEF5F5"
WARN = "#B25400"
WARN_BG = "#FFF7EB"
OK = "#198100"
DISABLED = "#A0A8B2"

# Windows list first (same as Theme.cs), then macOS and Linux Korean fonts.
FONTS = ["KoPubWorld돋움체 Medium", "KoPubWorld돋움체", "KoPubWorldDotum Medium", "Pretendard", "맑은 고딕", "Malgun Gothic",
         "Apple SD Gothic Neo", "Noto Sans CJK KR", "Noto Sans KR", "NanumGothic"]


def font_family() -> str | None:
    installed = set(QFontDatabase.families())
    return next((f for f in FONTS if f in installed), None)


QSS = f"""
QMainWindow, QDialog {{ background: {SURFACE}; color: {TEXT}; }}
QWidget {{ color: {TEXT}; }}
QLabel#brand {{ font-weight: 700; font-size: 12pt; color: {TEXT}; padding-right: 8px; }}
QLabel#lblFileName {{ padding-left: 8px; }}

QPushButton {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px;
    padding: 6px 14px; min-height: 22px;
}}
QPushButton:hover {{ background: {CANVAS}; }}
QPushButton:pressed {{ background: {SELECTED}; }}
QPushButton:disabled {{ color: {DISABLED}; background: {SURFACE}; }}
QPushButton:focus {{ border: 2px solid {PRIMARY}; }}
QPushButton[kind="primary"] {{ background: {PRIMARY}; color: white; border: none; font-weight: 700; }}
QPushButton[kind="primary"]:hover {{ background: {PRIMARY_PRESSED}; }}
QPushButton[kind="primary"]:disabled {{ background: #AAC6C9; color: white; }}
QPushButton[kind="primary"]:focus {{ border: 2px solid {TEXT}; }}
QPushButton[kind="ghost"], QPushButton[kind="tab"] {{ background: transparent; border: none; }}
QPushButton[kind="ghost"]:hover, QPushButton[kind="tab"]:hover {{ background: {CANVAS}; }}
QPushButton[kind="tab"][active="true"] {{ background: {SELECTED}; color: {PRIMARY}; font-weight: 700; }}

QLineEdit, QComboBox, QPlainTextEdit, QTextBrowser {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px; padding: 4px 6px;
    selection-background-color: {SELECTED}; selection-color: {TEXT};
}}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border: 1px solid {PRIMARY}; }}

QTreeWidget, QListWidget {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px;
    selection-background-color: {SELECTED}; selection-color: {TEXT}; outline: 0;
}}
QTreeWidget::item, QListWidget::item {{ padding: 3px 2px; }}
QTreeWidget::item:selected, QListWidget::item:selected {{ background: {SELECTED}; color: {TEXT}; }}
QHeaderView::section {{
    background: {CANVAS}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER}; padding: 5px 6px; font-weight: 600;
}}

QGroupBox {{ border: 1px solid {BORDER}; border-radius: 10px; margin-top: 14px; padding: 10px 8px 8px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {PRIMARY}; font-weight: 700; }}

QScrollArea#pageScroll, QScrollArea#pageScroll > QWidget > QWidget {{ background: {CANVAS}; border: none; }}
QWidget#topBar, QWidget#bottomBar {{ background: {SURFACE}; }}
QWidget#strip {{ background: {CANVAS}; border-top: 1px solid {BORDER}; border-bottom: 1px solid {BORDER}; }}
QWidget#sidePanel, QWidget#figurePanel {{ background: {SURFACE}; }}
QSplitter::handle {{ background: {BORDER}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QProgressBar {{ border: none; background: {BORDER}; border-radius: 3px; max-height: 6px; }}
QProgressBar::chunk {{ background: {PRIMARY}; border-radius: 3px; }}
QMenu {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px; }}
QMenu::item {{ padding: 8px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {SELECTED}; color: {PRIMARY}; }}
QMenu::item:disabled {{ color: {DISABLED}; }}
QRadioButton {{ font-weight: 700; spacing: 8px; }}
"""


def apply(app) -> None:
    family = font_family()
    f = QFont(family) if family else app.font()
    f.setPointSizeF(10.5)
    app.setFont(f)
    app.setStyleSheet(QSS)


def kind(widget, k: str) -> None:
    widget.setProperty("kind", k)
    repolish(widget)


def repolish(widget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()
