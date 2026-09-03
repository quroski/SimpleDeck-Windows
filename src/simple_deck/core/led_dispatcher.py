"""LedDispatcher - V3: steruje linijką LED na podstawie trybu i potencjometrów.

V2: Gdy użytkownik poruszy potencjometrem przypisanym do głośności,
dispatcher wysyła ramkę ``LED_CMD(mode=VU_BAR)`` z poziomem 0..255 do MCU.

V3: Dispatcher wysyła VU bar TYLKO gdy ``profile.led_mode == LedMode.VU_BAR``.
Dla innych trybów (Solid, Breathing, itp.) LEDy są sterowane z LedPage,
nie z potencjometrów.

V4: VU bar jest decoupled od akcji potencjometru. Każdy włączony potencjometr
(``cfg.enabled == True``) steruje linijką gdy jest ruszany, niezależnie czy
reguluje głośność (SYSTEM_VOLUME / APP_VOLUME) czy ma action=NONE. Dzięki
temu potencjometry bez akcji nadal pokazują swoją pozycję na LED bar.

V7: Koalescencja wysyłki do ~30 Hz (MIN_INTERVAL_S = 33 ms). MCU i tak próbuje
LED scheduler z 50 Hz, dawniej dispatcher wysyłał jedną ramkę USB OUT na każdy
pot_level emit (do ~100/s podczas szybkiego wiggle) — większość była odrzucana
przez MCU. Pierwsza ramka po okresie spoczynku leci natychmiast; kolejne są
buforowane i flush'owane timerem 33 ms, końcowa wartość zawsze trafia.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Slot

from .event_bus import EventBus
from .profile import LedMode, Profile
from ..transport.connection_manager import ConnectionManager
from ..transport.protocol import make_vu_cmd

log = logging.getLogger(__name__)

# MCU LED scheduler = 50 Hz; 33 ms ≈ 30 Hz host cap = bezpieczny margines.
MIN_INTERVAL_S = 0.033


class LedDispatcher(QObject):
    """V2: Wysyła poziom VU bar do MCU gdy potencjometr głośności się zmienia."""

    def __init__(self, bus: EventBus, connection: ConnectionManager,
                 profile_mgr=None, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._bus = bus
        self._conn = connection
        self._profile: Optional[Profile] = None

        # V7: Koalescencja USB OUT — ostatnia wartość per-pot, flush co 33 ms.
        self._pending: dict[int, int] = {}
        self._last_flush = 0.0
        self._coalesce = QTimer(self)
        self._coalesce.setSingleShot(True)
        self._coalesce.setInterval(int(MIN_INTERVAL_S * 1000))
        self._coalesce.timeout.connect(self._flush)

        # Subskrypcja: poziom potencjometru zmieniony przez PotDispatcher
        bus.pot_level.connect(self._on_pot_level)

        if profile_mgr is not None:
            profile_mgr.active_profile_changed.connect(self.set_profile)

    def set_profile(self, profile: Profile) -> None:
        self._profile = profile

    @Slot(int, float)
    def _on_pot_level(self, pot_idx: int, level: float) -> None:
        """Gdy potencjometr się ruszy, wyślij poziom VU do MCU.

        V3: Aktywne tylko gdy led_mode == VU_BAR.
        V4: Decoupled od akcji — każdy włączony potencjometr steruje
        linijką, niezależnie czy reguluje głośność czy ma action=NONE.
        V7: Koalescencja — pierwsza ramka od razu, kolejne co 33 ms.
        """
        if self._profile is None:
            return

        # V3: Only send VU when in VU_BAR mode
        if self._profile.led_mode != LedMode.VU_BAR.value:
            return

        if not self._profile.vu_bar_enabled:
            return

        if pot_idx >= len(self._profile.pots):
            return
        cfg = self._profile.pots[pot_idx]
        if not cfg.enabled:
            return

        level_clamped = max(0.0, min(1.0, level))
        brightness = int(level_clamped * 255)

        # V7: koalescencja — zbuforuj i ew. wyślij od razu
        self._pending[pot_idx] = brightness
        now = time.monotonic()
        if now - self._last_flush >= MIN_INTERVAL_S:
            self._flush()
        elif not self._coalesce.isActive():
            self._coalesce.start()

    def _flush(self) -> None:
        """Wyślij najnowszą zbuforowaną wartość per-pot do MCU."""
        if not self._pending:
            return
        # Jeśli w buforze jest wiele potów (np. równoczesny wiggle 2 potów),
        # wyślij każdą ramkę osobno — to tego oczekuje protokół (1 ramka = 1 LED_CMD).
        for pot_idx, brightness in self._pending.items():
            try:
                self._conn.send_frame(make_vu_cmd(pot_idx, brightness))
            except Exception:
                log.exception("LedDispatcher send_frame failed for pot %d", pot_idx)
        self._pending.clear()
        self._last_flush = time.monotonic()
