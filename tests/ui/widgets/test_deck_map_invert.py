"""Testy DeckMap — V7: reakcja na sygnał pot_invert_changed.

Weryfikuje:
  1. Subskrypcja bus.pot_invert_changed → refresh_invert aktualizuje _invert_all
  2. Widoczne paski przerysowują się wg odwróconych wartości ADC
  3. _display_value uwzględnia globalne odwrócenie po sygnale
"""
from __future__ import annotations

from unittest.mock import MagicMock

from simple_deck.core.event_bus import EventBus
from simple_deck.core.profile import PotConfig, Profile
from simple_deck.ui.widgets.deck_map import DeckMap


def _make_deck(settings) -> tuple[DeckMap, EventBus]:
    bus = EventBus()
    deck = DeckMap(bus=bus, settings=settings)
    deck.set_profile(Profile(name="T"))   # komórki bez per-pot invert
    return deck, bus


class TestDeckMapInvertSignal:

    def test_signal_updates_invert_all(self, qapp):
        settings = MagicMock()
        settings.invert_all_pots = False
        settings.last_pot_values = [-1] * 5
        deck, bus = _make_deck(settings)
        assert deck._invert_all is False

        settings.invert_all_pots = True   # tak robi handler w SettingsPage
        bus.pot_invert_changed.emit()
        assert deck._invert_all is True

    def test_signal_redraws_bars_from_cache(self, qapp):
        """Paski pokazują odwróconą wartość po sygnale (bez czekania na MCU)."""
        settings = MagicMock()
        settings.invert_all_pots = False
        cached = [4095, 2048, 0, 1024, 3072]
        settings.last_pot_values = list(cached)
        deck, bus = _make_deck(settings)
        assert deck._pots[0]._bar.value() == 4095   # stan przed odwróceniem

        settings.invert_all_pots = True
        bus.pot_invert_changed.emit()
        assert deck._pots[0]._bar.value() == 4095 - 4095   # 0
        assert deck._pots[2]._bar.value() == 4095 - 0      # 4095

    def test_signal_without_cached_values_no_crash(self, qapp):
        settings = MagicMock()
        settings.invert_all_pots = False
        settings.last_pot_values = [-1] * 5   # nic nieznane
        deck, bus = _make_deck(settings)
        settings.invert_all_pots = True
        bus.pot_invert_changed.emit()   # nie crashuje, flaga zaktualizowana
        assert deck._invert_all is True

    def test_on_pot_uses_inverted_display(self, qapp):
        """Po sygnale świeże POT_EVT też są wyświetlane odwrócone."""
        settings = MagicMock()
        settings.invert_all_pots = False
        settings.last_pot_values = [-1] * 5
        deck, bus = _make_deck(settings)

        settings.invert_all_pots = True
        bus.pot_invert_changed.emit()
        deck.setVisible(True)
        bus.pot_event.emit(0, 0)   # ADC 0 → wyświetlone 4095
        assert deck._pots[0]._bar.value() == 4095

    def test_per_pot_invert_still_applies(self, qapp):
        """XOR: per-pot invert ⊕ global — per-pot nadal działa po sygnale."""
        settings = MagicMock()
        settings.invert_all_pots = False
        settings.last_pot_values = [-1] * 5
        bus = EventBus()
        deck = DeckMap(bus=bus, settings=settings)
        profile = Profile(name="T")
        profile.pots[0] = PotConfig(idx=0, invert=True)
        deck.set_profile(profile)

        settings.invert_all_pots = True   # XOR z per-pot = brak odwrócenia
        bus.pot_invert_changed.emit()
        deck.setVisible(True)
        bus.pot_event.emit(0, 1024)   # bez odwrócenia (global⊕per-pot)
        assert deck._pots[0]._bar.value() == 1024
