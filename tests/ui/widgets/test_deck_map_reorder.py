"""Testy DeckMap — V4: przestawianie komórek potencjometrów wg kolejności.

Weryfikuje:
  1. set_profile z pot_display_order przestawia komórki w gridzie
  2. Sygnał bus.pot_order_changed przestawia komórki na żywo
  3. Komórki pozostają indeksowane kanałem fizycznym (event handling nietknięty)
"""
from __future__ import annotations

from simple_deck.core.event_bus import EventBus
from simple_deck.core.profile import Profile
from simple_deck.ui.widgets.deck_map import DeckMap


def _pot_at_position(deck: DeckMap, col: int) -> int:
    """Zwróć fizyczny indeks (_idx) potencjometru w danej kolumnie gridu."""
    item = deck._pot_grid.itemAtPosition(0, col)
    assert item is not None, f"Brak widgetu na pozycji (0,{col})"
    return item.widget()._idx


class TestDeckMapReorder:

    def test_default_order_identity(self, qapp):
        """Bez ustawienia profilu komórki są w kolejności 0,1,2,3,4."""
        bus = EventBus()
        settings = None
        deck = DeckMap(bus=bus, settings=settings)
        for col in range(5):
            assert _pot_at_position(deck, col) == col

    def test_arrange_on_set_profile(self, qapp):
        """set_profile z custom order → komórki przestawione."""
        bus = EventBus()
        deck = DeckMap(bus=bus, settings=None)
        profile = Profile(name="T", pot_display_order=[0, 2, 4, 1, 3])
        deck.set_profile(profile)

        assert _pot_at_position(deck, 0) == 0
        assert _pot_at_position(deck, 1) == 2
        assert _pot_at_position(deck, 2) == 4
        assert _pot_at_position(deck, 3) == 1
        assert _pot_at_position(deck, 4) == 3

    def test_rearrange_on_signal(self, qapp):
        """bus.pot_order_changed → grid przestawia się na żywo."""
        bus = EventBus()
        deck = DeckMap(bus=bus, settings=None)
        profile = Profile(name="T")
        deck.set_profile(profile)

        # Początkowo identyczność
        assert _pot_at_position(deck, 1) == 1

        # Zmień kolejność i emituj sygnał
        profile.pot_display_order = [0, 3, 1, 4, 2]
        bus.pot_order_changed.emit()

        assert _pot_at_position(deck, 0) == 0
        assert _pot_at_position(deck, 1) == 3
        assert _pot_at_position(deck, 2) == 1
        assert _pot_at_position(deck, 3) == 4
        assert _pot_at_position(deck, 4) == 2

    def test_events_still_use_physical_index(self, qapp):
        """pot_event z fizycznym idx aktualizuje właściwą komórkę po reorder."""
        bus = EventBus()
        deck = DeckMap(bus=bus, settings=None)
        deck.show()  # V7: isVisible() guard
        profile = Profile(name="T", pot_display_order=[4, 3, 2, 1, 0])
        deck.set_profile(profile)

        # Fizyczny pot 4 jest teraz na pozycji 0, ale _on_pot(4, ...) nadal
        # aktualizuje self._pots[4] (nie _pots[0]).
        bus.pot_event.emit(4, 2000)
        assert deck._pots[4]._last_value >= 0
        assert deck._pots[0]._last_value < 0  # pot 0 nie otrzymał zdarzenia
