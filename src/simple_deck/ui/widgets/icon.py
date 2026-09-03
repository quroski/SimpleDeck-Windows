"""Ikony SVG dla Simple Deck (zestaw Lucide-style w assets/icons/).

Ikony używają ``stroke="currentColor"`` - w runtime podmieniamy na kolor akcentu
przed renderowaniem przez ``QSvgRenderer``. Dzięki temu zmiana akcentu w
Ustawieniach natychmiast przekolorowuje całą aplikację (Task 4 + Appearance).

Public API:
  - ``icon_pixmap(name, color, size)`` -> QPixmap (cached)
  - ``IconLabel`` - QLabel wyświetlający ikonę, przekolorowywalny
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QLabel

log = logging.getLogger(__name__)


def _icons_dir() -> Path:
    """Katalog ikon (zgodny z dev i PyInstaller frozen)."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return base / "assets" / "icons"
    # desktop/src/simple_deck/ui/widgets/icon.py -> parents[4] = desktop/
    return Path(__file__).resolve().parents[4] / "assets" / "icons"


_CACHE: dict[tuple[str, str, int], QPixmap] = {}
_SVG_CACHE: dict[str, str] = {}  # V8: Cache surowych SVG (niezależny od koloru)


def icon_pixmap(name: str, color: str = "#2DD4FF", size: int = 22) -> QPixmap:
    """Zwróć pixmapę ikony ``name`` pokolorowaną ``color`` o rozmiarze ``size``.

    Wynik jest cache'owany (name, color, size). Jeśli ikona nie istnieje,
    zwraca pustą pixmapę (nie rzuca wyjątku).
    """
    key = (name, color, size)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    path = _icons_dir() / f"{name}.svg"
    
    # V8: Sprawdź cache SVG (niezależny od koloru)
    svg = _SVG_CACHE.get(name)
    if svg is None:
        try:
            svg = path.read_text(encoding="utf-8")
            _SVG_CACHE[name] = svg
        except Exception:
            log.warning("icon not found: %s", path)
            pm = QPixmap(QSize(size, size))
            pm.fill(Qt.transparent)
            _CACHE[key] = pm
            return pm

    # Podmień currentColor na żądany kolor (prosty string-replace wystarcza
    # bo nasze SVG używają jedynie stroke="currentColor").
    svg_colored = svg.replace('stroke="currentColor"', f'stroke="{color}"')

    # V6: Lazy import QtSvg — moduł ładuje się dopiero przy pierwszym
    # wywołaniu icon_pixmap, nie przy starcie aplikacji.
    from PySide6.QtSvg import QSvgRenderer
    renderer = QSvgRenderer(QByteArray(svg_colored.encode("utf-8")))
    pm = QPixmap(QSize(size, size))
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    renderer.render(painter)
    painter.end()

    _CACHE[key] = pm
    return pm


def clear_icon_cache() -> None:
    """Wyczyść cache ikon (np. po zmianie akcentu).
    
    V8: Czyści tylko cache pixmap, zachowuje cache SVG (niezależny od koloru).
    """
    _CACHE.clear()
    # Nie czyścimy _SVG_CACHE - SVG są niezależne od koloru


class IconLabel(QLabel):
    """QLabel wyświetlający ikonę SVG, przekolorowywalny w runtime."""

    def __init__(self, name: str, color: str = "#2DD4FF", size: int = 22,
                 parent=None):
        super().__init__(parent)
        self._name = name
        self._color = color
        self._size = size
        self.setFixedSize(size, size)
        self._render()

    def _render(self) -> None:
        self.setPixmap(icon_pixmap(self._name, self._color, self._size))

    def set_color(self, color: str) -> None:
        if color != self._color:
            self._color = color
            self._render()

    def set_icon(self, name: str, color: Optional[str] = None,
                 size: Optional[int] = None) -> None:
        self._name = name
        if color is not None:
            self._color = color
        if size is not None:
            self._size = size
            self.setFixedSize(size, size)
        self._render()
