"""Boczny pasek nawigacji - przyciski checkable z ikoną SVG + etykietą.

Aktywny element ma wskaźnik (lewy pasek + gradient) zdefiniowany w glossy.qss
(``QPushButton#navItem:checked``). Ikony są przebarwiane akcentem (aktywny)
lub szarością (nieaktywny) przez ``set_accent``.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QButtonGroup, QFrame, QLabel, QPushButton,
                                QVBoxLayout)

from .icon import icon_pixmap

ACCENT_DEFAULT = "#2DD4FF"
INACTIVE = "#A5ABC0"
ACTIVE_TEXT = "#F5F7FA"


class NavSidebar(QFrame):
    """Sidebar z 5 przyciskami nawigacji (checkable, exclusive)."""

    page_requested = Signal(int)  # indeks strony 0..N-1

    NAV_ITEMS = [
        ("home",     "Overview"),
        ("sliders",  "Potencjometry"),
        ("grid",     "Przyciski"),
        ("lamp",     "LED"),
        ("settings", "Ustawienia"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(230)
        self._accent = ACCENT_DEFAULT
        self._buttons: list[QPushButton] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 18, 14, 18)
        lay.setSpacing(6)

        # Tytuł sekcji
        title = QLabel("NAWIGACJA", objectName="labelMuted")
        title.setContentsMargins(8, 0, 0, 6)
        lay.addWidget(title)

        # Grupa przycisków wykluczających
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        for idx, (icon_name, text) in enumerate(self.NAV_ITEMS):
            btn = QPushButton(f"  {text}", objectName="navItem")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setIconSize(QSize(18, 18))
            btn._icon_name = icon_name
            btn.clicked.connect(lambda _checked=False, i=idx: self._on_click(i))
            self._group.addButton(btn, idx)
            self._buttons.append(btn)
            lay.addWidget(btn)

        lay.addStretch()

        # Pierwszy zaznaczony
        first = self._group.button(0)
        if first is not None:
            first.setChecked(True)
        # V7: Jeden _refresh_icons() na końcu — dawniej wołany dwa razy
        # (przed addStretch i po setChecked). Pierwsze 5 ikon inactive-tinted
        # było od razu zastępowane tym drugim wywołaniem (1 active + 4 inactive).
        self._refresh_icons()

    def _refresh_icons(self) -> None:
        """Odśwież ikony przycisków: aktywny=accent, nieaktywny=szary."""
        for btn in self._buttons:
            color = self._accent if btn.isChecked() else INACTIVE
            btn.setIcon(QIcon(icon_pixmap(btn._icon_name, color=color, size=18)))

    def _on_click(self, idx: int) -> None:
        self._refresh_icons()
        self.page_requested.emit(idx)

    def set_active(self, idx: int) -> None:
        btn = self._group.button(idx)
        if btn is not None:
            btn.setChecked(True)
        self._refresh_icons()

    def set_accent(self, color: str) -> None:
        self._accent = color
        self._refresh_icons()
