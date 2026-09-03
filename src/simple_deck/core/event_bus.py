"""Centralny event bus - dystrybucja ramek do subskrybentów (UI, hotkey, audio).

Opieramy się o Qt signals/slots - sygnały są thread-safe (automatyczna kolejka
gdy emitowane z innego wątku). EventBus rozpakowuje surowe ramki i emituje
sygnały typowane per typ zdarzenia.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal

from ..transport.protocol import Frame, FrameType, parse_pot_payload

log = logging.getLogger(__name__)


class EventBus(QObject):
    """Hub dystrybuujący zdarzenia z MCU do subskrybentów UI."""

    # --- Surowe zdarzenia MCU → PC ---
    button_event = Signal(int, bool)   # (idx, pressed)
    pot_event = Signal(int, int)       # (idx, value 0..4095)
    # V7: heartbeat/version/ack/nak usunięte — subskrybenci używają sygnałów
    # z ConnectionManager (heartbeat_received, fw_version_received). Zero
    # .connect() w całym codebase (weryfikowane grepem). emit w route() był
    # no-opem (0 odbiorców) — każdy HEARTBEAT 1× na 1.5 s to czysty waste.

    # --- Zdarzenia aplikacji ---
    # V7: profile_changed / led_changed / vu_level usunięte — zadeklarowane
    # ale nigdy niewyemitowane (grep .emit = 0) i nigdy niesubskrybowane
    # (grep .connect = 0). Martwy kod.
    # C10: Sygnały dla sprzężenia LED (LedDispatcher)
    # Poziom potencjometru 0..1 (dla LED follows pot)
    pot_level = Signal(int, float)     # (pot_idx, level 0..1)
    # Powiadomienia toast (level, message). level: info/success/warning/error.
    notify = Signal(str, str)
    # V4: Kolejność wyświetlania potencjometrów zmieniła się na PotsPage.
    # DeckMap nasłuchuje by przestawić komórki w gridzie.
    pot_order_changed = Signal()

    def route(self, frame: Frame) -> None:
        """Rozpakuj ramkę i emituj odpowiedni sygnał."""
        t = frame.type
        if t == FrameType.BUTTON_EVT:
            if frame.payload:
                self.button_event.emit(frame.ch, bool(frame.payload[0]))
        elif t == FrameType.POT_EVT:
            self.pot_event.emit(frame.ch, parse_pot_payload(frame.payload))
        elif t == FrameType.HEARTBEAT and len(frame.payload) >= 5:
            # V7: ConnectionManager.emit('heartbeat_received') jest kanonicznym
            # źródłem — bus.heartbeat.emit miał zero subskrybentów.
            pass
        elif t == FrameType.VERSION and len(frame.payload) >= 3:
            # V7: ConnectionManager.emit('fw_version_received') jest kanoniczne.
            pass
        elif t == FrameType.ACK and frame.payload:
            # V7: zero subskrybentów bus.ack; tylko log debug.
            log.debug("ACK for type=0x%02X", frame.payload[0])
        elif t == FrameType.NAK and frame.payload:
            # V7: zero subskrybentów bus.nak; tylko log warning dla diagnostyki.
            log.warning("NAK err=0x%02X", frame.payload[0])
        else:
            log.debug("unhandled frame type: %s", t)
