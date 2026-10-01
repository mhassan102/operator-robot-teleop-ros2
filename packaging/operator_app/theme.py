"""Dark operator chrome matching teleoperation-prototype/web/operate.css.

Colors and button shapes follow that console. This does not style the
page loaded in the embedded console view.
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import QApplication, QLabel, QWidget

_ARROW = (Path(__file__).resolve().parent / "down-arrow.png").as_posix()

# operate.css :root
BG = "#07090c"
PANEL = "#10141a"
PANEL_2 = "#0c1015"
TEXT = "#e7eaee"
MUTED = "#8b939d"
FAINT = "#5c656e"
ACCENT = "#4aa3ff"
STOP = "#c45454"
LINE = "#1d242d"
# .rail button.stop
STOP_TEXT = "#f0d0d0"
STOP_BG = "#1a1010"
KEY = "#1a212b"

OPERATOR_QSS = f"""
#operator_window {{
  background-color: {BG};
  color: {TEXT};
  font-family: "Segoe UI", "Ubuntu", sans-serif;
  font-size: 14px;
}}
#pages, #form_page, #waiting_page, #config_page, #config_main {{
  background-color: {BG};
  color: {TEXT};
}}
#panel {{
  background-color: {PANEL_2};
  border: 1px solid {LINE};
  border-radius: 8px;
}}
#session_bar {{
  background-color: {PANEL_2};
  border-bottom: 1px solid {LINE};
  min-height: 48px;
}}
#rail {{
  background-color: {PANEL_2};
  border-left: 1px solid {LINE};
}}
#dot {{
  background-color: {ACCENT};
  border-radius: 3px;
}}
QLabel {{
  background: transparent;
  color: {TEXT};
}}
QLabel#brand, QLabel#hostname, QLabel#config_hostname {{
  color: {TEXT};
  font-size: 15px;
  font-weight: 600;
}}
QLabel#section {{
  color: {MUTED};
  font-size: 11px;
  font-weight: 600;
}}
QLabel#field, QLabel#status, QLabel#waiting, QLabel#registry,
QLabel#link_help, QLabel#operator_network, QLabel#review_status,
QLabel#session_status, QLabel#bar_status {{
  color: {MUTED};
}}
QLabel#link_help {{
  font-size: 11px;
}}
QLabel#review_status, QLabel#session_status, QLabel#bar_status {{
  font-size: 12px;
}}
QLabel#bar_status {{
  background: transparent;
  border: none;
  padding: 0px;
}}
QLabel#bar_status[filled="true"] {{
  color: {MUTED};
  border: 1px solid {LINE};
  border-radius: 10px;
  padding: 4px 10px;
}}
QLineEdit, QComboBox {{
  background-color: {PANEL};
  color: {TEXT};
  border: 1px solid {LINE};
  border-radius: 6px;
  padding: 6px 28px 6px 10px;
  selection-background-color: {KEY};
  selection-color: {TEXT};
}}
QLineEdit {{
  padding: 6px 10px;
  lineedit-password-character: 9679;
}}
QLineEdit#robot_id[text=""],
QLineEdit#password[text=""] {{
  color: {FAINT};
}}
QLineEdit:focus, QComboBox:focus {{
  border: 1px solid {ACCENT};
}}
QComboBox::drop-down {{
  subcontrol-origin: padding;
  subcontrol-position: top right;
  border: none;
  width: 22px;
  background: transparent;
}}
QComboBox::down-arrow {{
  image: url({_ARROW});
  width: 12px;
  height: 8px;
}}
QComboBox QAbstractItemView {{
  background-color: {PANEL};
  color: {TEXT};
  border: 1px solid {LINE};
  selection-background-color: {KEY};
  selection-color: {TEXT};
  outline: 0;
}}
QComboBox QAbstractItemView::item {{
  min-height: 24px;
  padding: 4px 8px;
  background-color: {PANEL};
  color: {TEXT};
}}
QComboBox QAbstractItemView::item:selected {{
  background-color: {KEY};
  color: {TEXT};
}}
QPushButton {{
  background-color: {PANEL};
  color: {TEXT};
  border: 1px solid {LINE};
  border-radius: 6px;
  padding: 6px 10px;
  text-align: left;
}}
QPushButton:hover, QPushButton:focus {{
  border: 1px solid {ACCENT};
}}
QPushButton:disabled {{
  color: {MUTED};
  background-color: {PANEL_2};
  border: 1px solid {LINE};
}}
#session_bar QPushButton {{
  text-align: center;
  padding: 4px 12px;
  min-width: 72px;
  max-height: 32px;
}}
QPushButton#stop {{
  color: {STOP_TEXT};
  background-color: {STOP_BG};
  border: 1px solid {STOP};
  text-align: center;
}}
QPushButton#stop:hover, QPushButton#stop:focus {{
  border: 1px solid {STOP};
}}
QPushButton#stop:disabled {{
  color: {MUTED};
  background-color: {STOP_BG};
  border: 1px solid {STOP};
}}
QPushButton#login {{
  background-color: {ACCENT};
  color: {BG};
  border: 1px solid {ACCENT};
  border-radius: 6px;
  padding: 8px 18px;
  text-align: center;
  font-weight: 600;
}}
QPushButton#login:hover, QPushButton#login:focus {{
  background-color: #79baff;
  color: {BG};
  border: 1px solid #79baff;
}}
QPushButton#login:disabled {{
  background-color: #1a3348;
  color: {MUTED};
  border: 1px solid #1a3348;
}}
QRadioButton {{
  color: {TEXT};
  background: transparent;
  spacing: 8px;
}}
QRadioButton::indicator {{
  width: 14px;
  height: 14px;
  border: 1px solid {LINE};
  border-radius: 7px;
  background-color: {PANEL};
}}
QRadioButton::indicator:checked {{
  background-color: {ACCENT};
  border: 1px solid {ACCENT};
}}
QToolTip {{
  background-color: {PANEL};
  color: {TEXT};
  border: 1px solid {LINE};
}}
"""


def field_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("field")
    label.setTextFormat(Qt.PlainText)
    return label


def section_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("section")
    label.setTextFormat(Qt.PlainText)
    return label


def accent_dot() -> QWidget:
    dot = QWidget()
    dot.setObjectName("dot")
    dot.setFixedSize(7, 7)
    return dot


def install_theme(widget: QWidget) -> None:
    """Paint the operator window like the console. Safe to call once."""
    app = QApplication.instance()
    if app is not None:
        app.setStyle("Fusion")
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor(BG))
        palette.setColor(QPalette.WindowText, QColor(TEXT))
        palette.setColor(QPalette.Base, QColor(PANEL))
        palette.setColor(QPalette.AlternateBase, QColor(PANEL_2))
        palette.setColor(QPalette.Text, QColor(TEXT))
        palette.setColor(QPalette.Button, QColor(PANEL))
        palette.setColor(QPalette.ButtonText, QColor(TEXT))
        palette.setColor(QPalette.Highlight, QColor(KEY))
        palette.setColor(QPalette.HighlightedText, QColor(TEXT))
        palette.setColor(QPalette.PlaceholderText, QColor(MUTED))
        palette.setColor(QPalette.Link, QColor(ACCENT))
        app.setPalette(palette)
    widget.setObjectName("operator_window")
    widget.setStyleSheet(OPERATOR_QSS)
