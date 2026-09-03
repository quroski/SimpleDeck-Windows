"""Testy CalibrationDialog - modalny dialog kalibracji min/max potencjometru.

Regresja: wcześniej kalibracja przechwytywała „pierwsze zdarzenie pota" co
łowiło szum/ruch. Teraz dialog pokazuje wartość ADC na żywo i użytkownik
klika „Zapisz min/max" gdy jest gotowy.
"""
from __future__ import annotations


import pytest

from simple_deck.core.event_bus import EventBus
from simple_deck.ui.pages.config_pages import CalibrationDialog


@pytest.fixture
def calib_dlg(qapp):
    bus = EventBus()
    dlg = CalibrationDialog(idx=2, bus=bus, current_min=0.0, current_max=1.0)
    return dlg, bus


class TestCalibrationDialog:
    def test_pot_event_updates_live_display(self, calib_dlg):
        dlg, bus = calib_dlg
        bus.pot_event.emit(2, 2048)
        assert dlg._current_adc == 2048
        assert "2048" in dlg._value_lbl.text()

    def test_pot_event_ignores_other_idx(self, calib_dlg):
        dlg, bus = calib_dlg
        bus.pot_event.emit(0, 4095)
        assert dlg._current_adc == -1   # bez zmian

    def test_save_min_without_pot_event_noop(self, calib_dlg):
        dlg, bus = calib_dlg
        dlg._save_min()
        assert dlg._new_min is None   # brak ADC → nie zapisuje
        assert not dlg._btn_apply.isEnabled()

    def test_save_min_after_pot_event(self, calib_dlg):
        dlg, bus = calib_dlg
        bus.pot_event.emit(2, 1024)   # 25% of 4095
        dlg._save_min()
        assert dlg._new_min == pytest.approx(1024 / 4095, abs=0.001)

    def test_save_max_after_pot_event(self, calib_dlg):
        dlg, bus = calib_dlg
        bus.pot_event.emit(2, 3072)   # 75% of 4095
        dlg._save_max()
        assert dlg._new_max == pytest.approx(3072 / 4095, abs=0.001)

    def test_apply_enabled_only_when_both_saved(self, calib_dlg):
        dlg, bus = calib_dlg
        bus.pot_event.emit(2, 0)
        dlg._save_min()
        assert not dlg._btn_apply.isEnabled()
        bus.pot_event.emit(2, 4095)
        dlg._save_max()
        assert dlg._btn_apply.isEnabled()

    def test_apply_returns_correct_range(self, calib_dlg):
        dlg, bus = calib_dlg
        bus.pot_event.emit(2, 1000)
        dlg._save_min()
        bus.pot_event.emit(2, 3000)
        dlg._save_max()
        dlg._apply()
        lo, hi = dlg.get_result()
        assert lo == pytest.approx(1000 / 4095, abs=0.01)
        assert hi == pytest.approx(3000 / 4095, abs=0.01)

    def test_apply_swaps_if_max_lt_min(self, calib_dlg):
        """Jeśli użytkownik zapisał min > max (np. odwrotna kolejność),
        dialog powinien zamienić wartości."""
        dlg, bus = calib_dlg
        bus.pot_event.emit(2, 3000)   # „min" = 73%
        dlg._save_min()
        bus.pot_event.emit(2, 1000)   # „max" = 24% — mniejsze!
        dlg._save_max()
        dlg._apply()
        lo, hi = dlg.get_result()
        assert lo < hi
        assert lo == pytest.approx(1000 / 4095, abs=0.01)
        assert hi == pytest.approx(3000 / 4095, abs=0.01)

    def test_cancel_preserves_original(self, calib_dlg):
        dlg, bus = calib_dlg
        bus.pot_event.emit(2, 1000)
        dlg._save_min()
        # Symuluj „Anuluj" — reject, bez _apply
        dlg.reject()
        # get_result zwraca oryginał gdy nie było _apply
        lo, hi = dlg.get_result()
        assert lo == 0.0
        assert hi == 1.0
