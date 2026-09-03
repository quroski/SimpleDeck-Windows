"""Heartbeat watchdog - wykrywa utratę połączenia z MCU.

Działa w wątku głównym Qt (QTimer). Po czasie HEARTBEAT_TIMEOUT bez
odebrania HEARTBEAT emituje sygnał ``timeout`` który ConnectionManager
interpretuje jako rozłączenie.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal


class HeartbeatWatchdog(QObject):
    """Periodyczny timer sprawdzający czy MCU żyje.

    Po starcie (:meth:`start`) uruchamia single-shot timer długości
    ``timeout_ms``. Każde odebranie HEARTBEAT resetuje timer (:meth:`heartbeat`).
    Jeśli timer wygaśnie bez resetu → emit ``timeout``.
    """

    timeout = Signal()

    def __init__(self, timeout_ms: int = 4500, parent=None):
        super().__init__(parent)
        self._timeout_ms = int(timeout_ms)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self._timeout_ms)
        self._timer.timeout.connect(self.timeout.emit)

    def start(self) -> None:
        """Uruchom watchdoga (po połączeniu z MCU)."""
        self._timer.start()

    def stop(self) -> None:
        """Zatrzymaj watchdoga (przed rozłączeniem / reconnectem)."""
        self._timer.stop()

    def heartbeat(self) -> None:
        """Zresetuj timer - wołać po każdym odebranym HEARTBEAT."""
        if self._timer.isActive():
            self._timer.start()  # restart = reset

    @property
    def timeout_ms(self) -> int:
        return self._timeout_ms
