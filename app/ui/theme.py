"""Application stylesheet (dark, high-contrast, readable in a small window)."""

from __future__ import annotations

COLORS = {
    "bg": "#14161a",
    "panel": "#1c1f25",
    "card": "#23272f",
    "border": "#323844",
    "text": "#e7e9ee",
    "muted": "#9aa3b2",
    "accent": "#d97757",  # Claude-ish terracotta
    "accent_hover": "#e58a6b",
    "ok": "#3fb27f",
    "warn": "#e0a43a",
    "error": "#e5534b",
    "rec": "#ff3b30",
}


def stylesheet(font_scale: float = 1.0) -> str:
    c = COLORS
    base = round(10 * font_scale, 1)
    return f"""
    QWidget {{ background: {c['bg']}; color: {c['text']}; font-family: 'Segoe UI'; font-size: {base}pt; }}
    QMainWindow::separator {{ background: {c['border']}; width: 1px; }}
    QToolTip {{ background: {c['card']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 4px; }}
    QLabel, QCheckBox {{ background: transparent; }}
    QWidget#CardsHolder, QWidget#StatusRow, QWidget#qt_scrollarea_viewport {{ background: transparent; }}
    QFrame#Panel, QGroupBox {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 8px; }}
    QGroupBox {{ margin-top: 14px; padding-top: 8px; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {c['muted']}; }}
    QFrame#Card {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 10px; }}
    QFrame#Card[latest="true"] {{ border: 1px solid {c['accent']}; }}
    QLabel#Muted {{ color: {c['muted']}; }}
    QLabel#Heading {{ font-size: {round(base * 1.25, 1)}pt; font-weight: 600; }}
    QLabel#Question {{ font-size: {round(base * 1.15, 1)}pt; font-weight: 600; }}
    QLabel#Answer {{ font-size: {round(base * 1.1, 1)}pt; line-height: 140%; }}
    QLabel#Chip {{ border-radius: 9px; padding: 2px 8px; background: {c['card']}; border: 1px solid {c['border']}; }}
    QLabel#Chip[level="ok"] {{ color: {c['ok']}; border-color: {c['ok']}; }}
    QLabel#Chip[level="warn"] {{ color: {c['warn']}; border-color: {c['warn']}; }}
    QLabel#Chip[level="error"] {{ color: {c['error']}; border-color: {c['error']}; }}
    QLabel#Chip[level="off"] {{ color: {c['muted']}; }}
    QLabel#Banner {{ background: #3a1f1d; color: #ffd7d3; border: 1px solid {c['error']}; border-radius: 6px; padding: 6px 10px; }}
    QLabel#InfoBanner {{ background: #2a2a1c; color: #f3e6b5; border: 1px solid {c['warn']}; border-radius: 6px; padding: 6px 10px; }}
    QPushButton {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 5px 12px; }}
    QPushButton:hover {{ border-color: {c['accent']}; }}
    QPushButton:disabled {{ color: #5d6470; border-color: #2a2f38; }}
    QPushButton#Primary {{ background: {c['accent']}; border-color: {c['accent']}; color: #1a0f0b; font-weight: 600; }}
    QPushButton#Primary:hover {{ background: {c['accent_hover']}; }}
    QPushButton#Danger {{ color: {c['error']}; border-color: {c['error']}; }}
    QPushButton:checked {{ background: #3a2a24; border-color: {c['accent']}; }}
    QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 4px 6px;
        selection-background-color: {c['accent']}; selection-color: #000;
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus {{ border-color: {c['accent']}; }}
    QComboBox QAbstractItemView {{ background: {c['panel']}; border: 1px solid {c['border']}; }}
    QTabWidget::pane {{ border: 1px solid {c['border']}; border-radius: 8px; top: -1px; }}
    QTabBar::tab {{ background: transparent; padding: 6px 14px; color: {c['muted']}; border-bottom: 2px solid transparent; }}
    QTabBar::tab:selected {{ color: {c['text']}; border-bottom: 2px solid {c['accent']}; }}
    QScrollArea {{ border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; }}
    QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 5px; min-height: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QProgressBar {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 4px; height: 8px; text-align: center; }}
    QProgressBar::chunk {{ background: {c['ok']}; border-radius: 3px; }}
    QListWidget {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 6px; }}
    QListWidget::item:selected {{ background: #3a2a24; color: {c['text']}; }}
    QCheckBox::indicator {{ width: 16px; height: 16px; }}
    QStatusBar {{ color: {c['muted']}; }}
    """
