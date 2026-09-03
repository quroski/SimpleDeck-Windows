"""System tray icon dla Simple Deck (opt-in).

Kiedy ``settings.show_tray_icon == True``, w zasobniku systemowym pojawia się
ikona pokazująca stan połączenia z urządzeniem. Menu kontekstowe (right-click)
pozwala pokazać/ukryć okno, wymusić reconnect lub zamknąć aplikację.

Gdy urządzenie zostanie rozłączone na >5 s, tray pokazuje powiadomienie
(``QSystemTrayIcon.showMessage``). Symetryczne "Połączono" po odzyskaniu.

Zależności:
  - ``settings.show_tray_icon`` (domyślnie False — opt-in)
  - ``settings.minimize_to_tray_on_close`` — zamykanie okna = hide to tray
  - ``QApplication.setQuitOnLastWindowClosed(False)`` gdy tray aktywny
"""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from ...core.event_bus import EventBus
from ...core.settings import Settings
from ...transport.connection_manager import ConnectionState
from .icon import icon_pixmap

log = logging.getLogger(__name__)


class TrayController(QObject):
    """Kontroler ikony w zasobniku systemowym.

    Signals:
        show_window_requested: użytkownik kliknął "Pokaż okno" / dwuklik na tray.
        hide_window_requested: użytkownik kliknął "Ukryj okno".
        reconnect_requested: użytkownik kliknął "Połącz ponownie".
        quit_requested: użytkownik kliknął "Zakończ".
    """

    show_window_requested = Signal()
    hide_window_requested = Signal()
    reconnect_requested = Signal()
    quit_requested = Signal()

    DISCONNECT_NOTIFY_MS = 5000  # 5 s bez urządzenia → toast

    def __init__(self, app: QApplication,
                 connection=None,
                 bus: Optional[EventBus] = None,
                 settings: Optional[Settings] = None,
                 parent=None):
        super().__init__(parent)
        self._app = app
        self._connection = connection
        self._bus = bus
        self._settings = settings
        self._accent = "#2DD4FF"
        if settings is not None:
            self._accent = settings.accent_color

        # Tray icon
        self._tray = QSystemTrayIcon(self)
        self._tray.setToolTip("Simple Deck")
        self._refresh_icon(ConnectionState.DISCONNECTED)

        # Menu
        self._menu = QMenu()
        self._build_menu()
        self._tray.setContextMenu(self._menu)

        # Aktywacja (dwuklik = pokaż okno)
        self._tray.activated.connect(self._on_activated)

        # Status header action (disabled, pokazuje stan)
        self._status_action: Optional[QAction] = None

        # Timer dla disconnect notification (>5s → toast)
        self._disconnect_timer = QTimer(self)
        self._disconnect_timer.setSingleShot(True)
        self._disconnect_timer.setInterval(self.DISCONNECT_NOTIFY_MS)
        self._disconnect_timer.timeout.connect(self._on_disconnect_too_long)
        self._was_connected = False
        self._showed_disconnect = False

        # Podłącz sygnał stanu połączenia
        if connection is not None:
            connection.state_changed.connect(self._on_state_changed)
            self._on_state_changed(connection.state)

        # Quit on tray — też przez quit_requested (wołające woła QApplication.quit)
        # setQuitOnLastWindowClosed(False) jest wołane w app.py gdy tray aktywny
        self._tray.show()

    def _build_menu(self) -> None:
        self._menu.clear()

        # Status header (disabled)
        self._status_action = QAction("Simple Deck — Inicjalizacja…", self._menu)
        self._status_action.setEnabled(False)
        self._menu.addAction(self._status_action)
        self._menu.addSeparator()

        act_show = QAction("Pokaż okno", self._menu)
        act_show.triggered.connect(self.show_window_requested.emit)
        self._menu.addAction(act_show)

        act_hide = QAction("Ukryj okno", self._menu)
        act_hide.triggered.connect(self.hide_window_requested.emit)
        self._menu.addAction(act_hide)

        act_reconnect = QAction("Połącz ponownie", self._menu)
        act_reconnect.triggered.connect(self.reconnect_requested.emit)
        self._menu.addAction(act_reconnect)

        self._menu.addSeparator()

        act_quit = QAction("Zakończ", self._menu)
        act_quit.triggered.connect(self.quit_requested.emit)
        self._menu.addAction(act_quit)

    def _on_activated(self, reason) -> None:
        """Dwuklik (i pojedynczy klik na niektórych DE) → pokaż okno."""
        if reason == QSystemTrayIcon.DoubleClick or reason == QSystemTrayIcon.Trigger:
            self.show_window_requested.emit()

    @Slot(object)
    def _on_state_changed(self, state: ConnectionState) -> None:
        """Aktualizuj tooltip, ikonę i menu przy zmianie stanu."""
        self._refresh_icon(state)
        labels = {
            ConnectionState.DISCONNECTED: "Nieaktywny",
            ConnectionState.CONNECTING: "Łączenie…",
            ConnectionState.CONNECTED: "Połączony",
            ConnectionState.RECONNECTING: "Ponowne łączenie…",
        }
        if self._status_action is not None:
            self._status_action.setText(f"Simple Deck — {labels.get(state, '?')}")
        self._tray.setToolTip(f"Simple Deck — {labels.get(state, '?')}")

        # Disconnect notification timer
        if state == ConnectionState.CONNECTED:
            self._was_connected = True
            self._disconnect_timer.stop()
            # Toast o reconnect TYLKO jeśli wcześniej był disconnect notification
            if self._showed_disconnect:
                self._showed_disconnect = False
                self._tray.showMessage(
                    "Simple Deck", "Urządzenie połączone ponownie ✓",
                    QSystemTrayIcon.Information, 2000)
        elif state in (ConnectionState.DISCONNECTED, ConnectionState.RECONNECTING):
            if self._was_connected and not self._disconnect_timer.isActive():
                self._disconnect_timer.start()
        else:
            self._disconnect_timer.stop()

    def _on_disconnect_too_long(self) -> None:
        """Urządzenie rozłączone >5 s — pokaż toast."""
        self._showed_disconnect = True
        self._tray.showMessage(
            "Simple Deck", "Urządzenie rozłączone — oczekiwanie na ponowne połączenie",
            QSystemTrayIcon.Warning, 3000)

    def _refresh_icon(self, state: ConnectionState) -> None:
        """Odśwież ikonę tray'a (akcent + kolor statusu)."""
        # Użyj istniejącego SVG "home" przebarwionego na akcent + status dot.
        # Prosty approach: pixmap z icon_pixmap + overlay status color.
        pm = icon_pixmap("home", self._accent, 22)
        # Overlay: zielona/żółta/czerwona kropka w rogu
        from PySide6.QtCore import Qt, QRect
        from PySide6.QtGui import QColor, QPainter
        overlay_colors = {
            ConnectionState.CONNECTED: QColor("#3CFFB0"),
            ConnectionState.CONNECTING: QColor("#FFB13C"),
            ConnectionState.RECONNECTING: QColor("#9B5CFF"),
            ConnectionState.DISCONNECTED: QColor("#FF5C6C"),
        }
        color = overlay_colors.get(state, QColor("#6A7080"))
        painter = QPainter(pm)
        painter.setBrush(color)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QRect(pm.width() - 8, pm.height() - 8, 7, 7))
        painter.end()
        self._tray.setIcon(QIcon(pm))

    @Slot(str)
    def set_accent(self, color: str) -> None:
        """Przebarwij ikonę tray'a po zmianie akcentu."""
        self._accent = color
        if self._connection is not None:
            self._refresh_icon(self._connection.state)

    def cleanup(self) -> None:
        """Ukryj tray i zwolnij zasoby."""
        self._disconnect_timer.stop()
        self._tray.hide()
