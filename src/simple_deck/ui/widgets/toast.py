"""Toast - nietrwałe powiadomienia (zapisano, błąd, rozłączono).

``ToastHost`` przykleja się do okna (top-right) i układa tosty w pionie.
Każdy toast sam znika po kilku sekundach. Klasa ``Toast`` to pojedyncza karta.

Użycie: ``bus.notify.emit("success", "Profil zapisany")`` -> ToastHost słucha.
"""
from __future__ import annotations


from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect,
                                QHBoxLayout, QLabel, QVBoxLayout, QWidget)

# Kolory poziomów (ramka + akcent)
LEVELS = {
    "info":    ("#2DD4FF", "rgba(45,212,255,40)"),
    "success": ("#3CFFB0", "rgba(60,255,176,40)"),
    "warning": ("#FFB13C", "rgba(255,177,60,40)"),
    "error":   ("#FF5C6C", "rgba(255,92,108,40)"),
}
DEFAULT_DURATION_MS = 3000


class Toast(QFrame):
    """Pojedyncze powiadomienie toast."""

    dismissed = Signal()   # wołane gdy toast znika

    def __init__(self, level: str, message: str,
                 duration_ms: int = DEFAULT_DURATION_MS, parent=None):
        super().__init__(parent)
        self.setObjectName("toast")
        # V1.3.4: Click-through — dziecko nie może łapać myszy skoro host
        # przepuszcza zdarzenia (spójność atrybutu na obu poziomach).
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        accent, bg = LEVELS.get(level, LEVELS["info"])
        self.setFixedWidth(320)
        self.setStyleSheet(
            f"QFrame#toast {{ background: rgba(28,30,42,240); "
            f"border: 1px solid {accent}; border-left: 4px solid {accent}; "
            f"border-radius: 12px; }}"
        )

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(10)

        dot = QLabel("●")
        dot.setStyleSheet(f"color: {accent}; background: transparent; font-size: 14px;")
        lay.addWidget(dot)

        text = QLabel(message)
        text.setWordWrap(True)
        text.setStyleSheet("color: #F5F7FA; background: transparent; font-size: 13px;")
        lay.addWidget(text, stretch=1)

        # Cień
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setColor(QColor(0, 0, 0, 160))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)

        # Auto-dismiss
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(duration_ms)
        self._timer.timeout.connect(self._expire)
        self._timer.start()

    def _expire(self) -> None:
        self.dismissed.emit()
        self.deleteLater()


class ToastHost(QWidget):
    """Kontener toast'ów przyklejony do okna (prawy górny róg).

    ``attach(window)`` pozycjonuje hosta nad oknem i utrzymuje go tam.
    Subskrybuje ``bus.notify``.
    """

    def __init__(self, bus, window: QWidget | None, settings=None, parent=None):
        super().__init__(parent)
        self._window = window
        self._settings = settings
        # V1.3.4: Click-through — ToastHost to osobne okno Qt.Tool nakrywające
        # prawy górny róg okna głównego. Z WA_TransparentForMouseEvents=False
        # (poprzednio) niewidoczny pusty host zjadał kliknięcia i blokował
        # resize krawędzi w swoim obszarze ("resize działa średnio"). Toasty
        # są czysto informacyjne — nie potrzebują myszy.
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.setAlignment(Qt.AlignTop | Qt.AlignRight)
        # rezerwujemy miejsce od góry okna
        lay.addStretch()

        self._bus = bus
        bus.notify.connect(self.show_toast)

        # Referencja trzymana poza self.layout() (które w stubach Qt jest
        # Optional[QLayout]) — pewniejszy dostęp w show_toast/_follow.
        self._lay = lay
        self._follow()

    def show_toast(self, level: str, message: str) -> None:
        if self._settings is not None and not getattr(self._settings, "notifications_enabled", True):
            return
        toast = Toast(level, message, parent=self)
        # Wstaw przed stretch'em (na dole listy - kolejność top-down)
        lay = self._lay
        lay.insertWidget(lay.count() - 1, toast)
        toast.dismissed.connect(lambda: lay.removeWidget(toast))
        self._follow()

    def _follow(self) -> None:
        """Ustaw geometrię hosta w prawym górnym rogu okna."""
        if self._window is None:
            return
        geo = self._window.geometry()
        w, h = 340, min(400, max(120, self._lay.sizeHint().height() + 16))
        # Pozycja względem ekranu (top-right okna z marginesem)
        top_left = self._window.mapToGlobal(geo.topLeft())
        x = top_left.x() + geo.width() - w - 24
        y = top_left.y() + 90
        self.setGeometry(x, y, w, h)
