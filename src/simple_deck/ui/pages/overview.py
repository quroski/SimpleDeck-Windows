"""Strona Overview - dashboard główny z wizualizacją urządzenia + status."""
from __future__ import annotations

from collections import deque

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QScrollArea,
                                QSizePolicy, QVBoxLayout, QWidget)

from ...core.event_bus import EventBus
from ...transport.connection_manager import ConnectionManager, ConnectionState
from ..widgets.deck_map import DeckMap


class OverviewPage(QWidget):
    """Strona główna - dashboard."""

    pot_clicked = Signal(int)  # klik na potencjometr → MainWindow nawiguje do PotsPage
    # V1.0.3: klik na komórce przycisku → MainWindow nawiguje do ButtonsPage.
    button_clicked = Signal(int)
    # V1.0.3: (kind: "pot"|"btn", idx, nazwa) — edycja nazwy w komórce DeckMap.
    label_renamed = Signal(str, int, str)

    def __init__(self, bus: EventBus, connection: ConnectionManager,
                 settings=None, parent=None):
        super().__init__(parent)
        self._bus = bus
        self._conn = connection
        self._settings = settings

        # Scroll area (gdy małe okno)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        inner = QWidget()
        scroll.setWidget(inner)

        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)

        # Nagłówek strony
        head_box = QHBoxLayout()
        title = QLabel("OVERVIEW", objectName="sectionTitle")
        title.setStyleSheet("font-size: 22px; font-weight: 700; color: #F5F7FA; background: transparent;")
        # V1.0.2: Ignored — subtitle (~456 px hint) rozpychał stronę; bez tego
        # overview nie dawał się zwęzić poniżej szerokości tekstu nagłówka.
        subtitle = QLabel("Stan urządzenia i wizualizacja na żywo",
                          objectName="sectionSubtitle")
        subtitle.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        head_box.addWidget(title)
        head_box.addStretch()
        head_box.addWidget(subtitle, alignment=Qt.AlignBottom)
        lay.addLayout(head_box)

        # Status połączenia - karta
        self._status_card = self._build_status_card()
        lay.addWidget(self._status_card)

        # DeckMap - wizualizacja
        self._deck_map = DeckMap(bus=bus, settings=self._settings)
        self._deck_map.pot_clicked.connect(self.pot_clicked)
        self._deck_map.button_clicked.connect(self.button_clicked)
        self._deck_map.label_renamed.connect(self.label_renamed)
        lay.addWidget(self._deck_map)

        # Ostatnie zdarzenia - karta
        self._events_card = self._build_events_card()
        lay.addWidget(self._events_card)

        lay.addStretch()

        # Outer layout
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        # Aktualizacja statusu
        connection.state_changed.connect(self._on_state_changed)
        connection.heartbeat_received.connect(self._on_heartbeat)
        connection.fw_version_received.connect(self._on_fw_version)
        bus.pot_event.connect(self._on_event)
        bus.button_event.connect(self._on_event)
        self._on_state_changed(connection.state)
        
        # V11: Pełna wersja FW (major.minor.patch) z ramki VERSION
        self._fw_version = (0, 0, 0)

    def set_profile(self, profile) -> None:
        self._deck_map.set_profile(profile)

    @property
    def deck_map(self) -> DeckMap:
        """V1.0.3: dostęp do mapy urządzenia (testy + rename wiring w MainWindow)."""
        return self._deck_map

    # --- Budowa kart ---
    def _build_status_card(self) -> QFrame:
        card = QFrame(objectName="card")
        lay = QHBoxLayout(card)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(32)

        # Stan
        col1 = QVBoxLayout(); col1.setSpacing(4)
        col1.addWidget(QLabel("STAN", objectName="labelMuted"))
        self._status_value = QLabel("Łączenie…", objectName="labelValue")
        col1.addWidget(self._status_value)
        lay.addLayout(col1)

        # Uptime MCU
        col2 = QVBoxLayout(); col2.setSpacing(4)
        col2.addWidget(QLabel("UPTIME MCU", objectName="labelMuted"))
        self._uptime_value = QLabel("—", objectName="labelValue")
        col2.addWidget(self._uptime_value)
        lay.addLayout(col2)

        # Wersja FW
        col3 = QVBoxLayout(); col3.setSpacing(4)
        col3.addWidget(QLabel("WERSJA FW", objectName="labelMuted"))
        self._fw_value = QLabel("—", objectName="labelValue")
        col3.addWidget(self._fw_value)
        lay.addLayout(col3)

        lay.addStretch()
        return card

    def _build_events_card(self) -> QFrame:
        card = QFrame(objectName="card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(8)

        head = QLabel("OSTATNIE ZDARZENIA", objectName="sectionTitle")
        head.setStyleSheet("font-size: 13px;")
        lay.addWidget(head)

        self._events_log = QLabel("Brak zdarzeń",
                                   objectName="sectionSubtitle")
        self._events_log.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._events_log.setWordWrap(True)
        self._events_log.setStyleSheet(
            "font-family: 'JetBrains Mono', 'Consolas', monospace;"
            "font-size: 11px; color: #A5ABC0; background: transparent;"
        )
        lay.addWidget(self._events_log)
        # V6: Throttle log updates — deque + coalescing QTimer (~10 Hz)
        # zamiast setText na każdym z ~250 zdarzeń/s przy wiggle 5 potów.
        self._events_history: deque[str] = deque(maxlen=6)
        self._events_dirty = False
        self._event_timer = QTimer(self)
        self._event_timer.setSingleShot(True)
        self._event_timer.setInterval(100)  # 10 Hz max repaint
        self._event_timer.timeout.connect(self._flush_events)
        return card

    # --- Sloty ---
    @Slot(object)
    def _on_state_changed(self, state: ConnectionState) -> None:
        labels = {
            ConnectionState.DISCONNECTED: "Nieaktywny",
            ConnectionState.CONNECTING: "Łączenie…",
            ConnectionState.CONNECTED: "Połączony",
            ConnectionState.RECONNECTING: "Ponowne łączenie…",
        }
        colors = {
            ConnectionState.DISCONNECTED: "#FF5C6C",
            ConnectionState.CONNECTING: "#FFB13C",
            ConnectionState.CONNECTED: "#3CFFB0",
            ConnectionState.RECONNECTING: "#9B5CFF",
        }
        self._status_value.setText(labels.get(state, "?"))
        self._status_value.setStyleSheet(
            f"font-size: 16px; font-weight: 700; color: {colors.get(state, '#F5F7FA')};"
            "background: transparent;"
        )
        if state != ConnectionState.CONNECTED:
            self._uptime_value.setText("—")
            self._fw_value.setText("—")
            self._fw_version = (0, 0, 0)

    @Slot(int, int)
    def _on_heartbeat(self, uptime_ms: int, fw_packed: int) -> None:
        seconds = uptime_ms // 1000
        mins, secs = divmod(seconds, 60)
        hours, mins = divmod(mins, 60)
        self._uptime_value.setText(f"{hours:02d}:{mins:02d}:{secs:02d}")
        # V11: Użyj cached wersji FW z ramki VERSION (major.minor.patch)
        # Heartbeat zawiera tylko major.minor w fw_packed, ale pełna wersja
        # pochodzi z dedykowanej ramki VERSION (3 bajty: major, minor, patch).
        major, minor, patch = self._fw_version
        if major or minor or patch:
            self._fw_value.setText(f"v{major}.{minor}.{patch}")
        else:
            # Fallback: heartbeat nie ma patch byte, pokaż major.minor.x
            major_hb = (fw_packed >> 4) & 0x0F
            minor_hb = fw_packed & 0x0F
            self._fw_value.setText(f"v{major_hb}.{minor_hb}.x")

    @Slot(int, int, int)
    def _on_fw_version(self, major: int, minor: int, patch: int) -> None:
        """V11: Odbierz pełną wersję firmware (major.minor.patch) z ramki VERSION."""
        self._fw_version = (major, minor, patch)
        self._fw_value.setText(f"v{major}.{minor}.{patch}")

    @Slot(int, int)
    def _on_event(self, *args) -> None:
        """Log ostatnich zdarzeń (pot / button). Throttle ~10 Hz.

        V1.0.3b: używa WŁASNEJ nazwy kontrolki z profilu (spójnie z kartami
        Device map) zamiast "POT N"/"BTN N" — spójność nazw między kartami.
        V6: append do deque + arm timer; setText następuje w ``_flush_events``
        max co 100 ms — eliminuje 250 setText/s przy wiggle wszystkich potów.
        V7:早期 return gdy strona niewidoczna — eliminuje ~100 deque ops/s +
        timer armów gdy user jest na innej karcie lub zminimalizowany do tray.
        """
        if not self.isVisible():
            return
        if len(args) == 2:
            # V1.0.3b: własna nazwa kontrolki (spójnie z kartami Device map).
            kind = "btn" if isinstance(args[1], bool) else "pot"
            name = self._control_name(kind, args[0])
            if isinstance(args[1], bool):
                idx, pressed = args
                line = f"{name}  {'▼ WCIŚNIĘTY' if pressed else '▲ PUSZCZONY'}"
            else:
                idx, val = args
                pct = val * 100 // 4095
                line = f"{name}  →  {val:4d}  ({pct:3d}%)"
            self._events_history.appendleft(line)
            if not self._event_timer.isActive():
                self._event_timer.start()

    def _control_name(self, kind: str, idx: int) -> str:
        """V1.0.3b: nazwa kontrolki do logu zdarzeń (własna lub default)."""
        try:
            if kind == "pot":
                label = self._deck_map._pots[idx]._label
                return label.strip() or f"POT {idx + 1}"
            label = self._deck_map._buttons[idx]._label
            return label.strip() or f"BTN {idx + 1}"
        except (IndexError, TypeError, AttributeError):
            return f"{kind.upper()} {idx + 1}"

    def _flush_events(self) -> None:
        """Repaint log z deque — wołane przez coalescing timer (max 10 Hz)."""
        if self._events_history:
            self._events_log.setText("\n".join(self._events_history))
