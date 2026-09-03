"""Testy VolumeBar (V6: setProperty zamiast setStyleSheet) i Overview throttle."""
from __future__ import annotations

from unittest.mock import MagicMock


from simple_deck.ui.widgets.deck_map import _VolumeBar
from simple_deck.ui.pages.overview import OverviewPage
from simple_deck.core.event_bus import EventBus


class TestVolumeBarProperty:
    def test_construction_sets_off_state(self, qapp):
        bar = _VolumeBar(n_segments=3)
        for seg in bar._segments:
            assert seg.property("state") == "off"

    def test_set_level_full(self, qapp):
        bar = _VolumeBar(n_segments=3)
        bar.show()  # V7: isVisible() guard wymaga show()
        bar.set_level(0, 1.0)  # pełna — wszystkie segmenty "on"
        for state in bar._seg_states:
            assert state == "on"

    def test_set_level_half(self, qapp):
        bar = _VolumeBar(n_segments=3)
        bar.show()  # V7: isVisible() guard wymaga show()
        bar.set_level(0, 0.5)  # 1.5 segmentu → 1 "on" + 1 "half" + 1 "off"
        assert bar._seg_states[0] == "on"
        assert bar._seg_states[1] == "half"
        assert bar._seg_states[2] == "off"

    def test_set_level_zero(self, qapp):
        bar = _VolumeBar(n_segments=3)
        bar.show()  # V7: isVisible() guard wymaga show()
        bar.set_level(0, 1.0)
        bar.set_level(0, 0.0)
        for state in bar._seg_states:
            assert state == "off"

    def test_clamp_outside_range(self, qapp):
        bar = _VolumeBar(n_segments=3)
        bar.show()  # V7: isVisible() guard wymaga show()
        bar.set_level(0, 2.0)  # > 1.0 → clamp do 1.0
        for state in bar._seg_states:
            assert state == "on"
        bar.set_level(0, -1.0)  # < 0.0 → clamp do 0.0
        for state in bar._seg_states:
            assert state == "off"

    def test_no_redundant_polish_on_same_state(self, qapp):
        """Ustawienie tego samego stanu nie powinno wołać polish."""
        bar = _VolumeBar(n_segments=3)
        bar.show()  # V7: isVisible() guard wymaga show()
        bar.set_level(0, 1.0)
        # Teraz wszystkie są "on". Ustawienie 1.0 ponownie — brak zmiany.
        # Sprawdź że _seg_states się nie zmieniły (no-op).
        states_before = list(bar._seg_states)
        bar.set_level(0, 1.0)
        assert bar._seg_states == states_before

    def test_invisible_skips_polish(self, qapp):
        """V7: Gdy widget niewidoczny, set_level nie ruszy segmentami.
        Strona ukryta w tray lub user patrzy na inną kartę — skip = oszczędność CPU."""
        bar = _VolumeBar(n_segments=3)
        # bez show() — simuluje ukryty widget
        bar.set_level(0, 1.0)
        for state in bar._seg_states:
            assert state == "off"  # bez zmian, work skipped


class TestOverviewThrottle:
    def test_deque_maxlen(self, qapp):
        """Overview używa deque(maxlen=6) — historię dłuższa niż 6 odrzuca."""
        bus = EventBus()
        conn = MagicMock()
        conn.state = MagicMock()
        conn.state_changed = MagicMock()
        conn.heartbeat_received = MagicMock()
        page = OverviewPage(bus=bus, connection=conn)
        page.show()  # V7: isVisible() guard wymaga show()
        # Wyślij 10 zdarzeń pot
        for i in range(10):
            bus.pot_event.emit(0, i * 100)
        # deque powinno mieć max 6 elementów
        assert len(page._events_history) <= 6

    def test_timer_coalesces(self, qapp):
        """Wiele zdarzeń w krótkim czasie → timer coalesc'uje do ~10 Hz."""
        bus = EventBus()
        conn = MagicMock()
        conn.state = MagicMock()
        conn.state_changed = MagicMock()
        conn.heartbeat_received = MagicMock()
        page = OverviewPage(bus=bus, connection=conn)
        page.show()  # V7: isVisible() guard wymaga show()
        # Wyślij 20 zdarzeń szybko
        for i in range(20):
            bus.pot_event.emit(0, i)
        # Timer powinien być aktywny (armed)
        assert page._event_timer.isActive() or len(page._events_history) > 0

    def test_invisible_skips_event_processing(self, qapp):
        """V7: Gdy strona niewidoczna, _on_event nie rusza deque ani timerem.
        Symuluje tray lub inną aktywną kartę — eliminuje ~100 deque ops/s."""
        bus = EventBus()
        conn = MagicMock()
        conn.state = MagicMock()
        conn.state_changed = MagicMock()
        conn.heartbeat_received = MagicMock()
        page = OverviewPage(bus=bus, connection=conn)
        # bez show() — symuluje ukrytą stronę
        for i in range(20):
            bus.pot_event.emit(0, i)
        assert len(page._events_history) == 0
        assert not page._event_timer.isActive()
