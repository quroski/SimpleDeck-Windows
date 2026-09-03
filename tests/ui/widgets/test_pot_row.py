"""Testy PotRow — V4: przyciski zmiany kolejności potencjometrów.

Weryfikuje:
  1. Przycisk ▲ emituje move_requested(idx, -1)
  2. Przycisk ▼ emituje move_requested(idx, +1)
  3. Pierwszy wiersz ma ▲ wyłączony, ostatni ma ▼ wyłączony
  4. Wiersz w środku ma oba przyciski włączone
"""
from __future__ import annotations

from unittest.mock import MagicMock

from simple_deck.core.profile import PotConfig
from simple_deck.ui.widgets.config_rows import PotRow


class TestPotRowReorder:
    """V4: Sygnał move_requested i stany przycisków."""

    def test_move_up_emits_signal(self, qapp):
        cfg = PotConfig(idx=2)
        row = PotRow(cfg)
        emitted = MagicMock()
        row.move_requested.connect(emitted)

        row._up_btn.click()
        assert emitted.called
        idx, direction = emitted.call_args[0]
        assert idx == 2
        assert direction == -1

    def test_move_down_emits_signal(self, qapp):
        cfg = PotConfig(idx=1)
        row = PotRow(cfg)
        emitted = MagicMock()
        row.move_requested.connect(emitted)

        row._down_btn.click()
        assert emitted.called
        idx, direction = emitted.call_args[0]
        assert idx == 1
        assert direction == +1

    def test_first_row_up_disabled(self, qapp):
        cfg = PotConfig(idx=0)
        row = PotRow(cfg, first=True, last=False)
        assert row._up_btn.isEnabled() is False
        assert row._down_btn.isEnabled() is True

    def test_last_row_down_disabled(self, qapp):
        cfg = PotConfig(idx=4)
        row = PotRow(cfg, first=False, last=True)
        assert row._up_btn.isEnabled() is True
        assert row._down_btn.isEnabled() is False

    def test_middle_row_both_enabled(self, qapp):
        cfg = PotConfig(idx=2)
        row = PotRow(cfg, first=False, last=False)
        assert row._up_btn.isEnabled() is True
        assert row._down_btn.isEnabled() is True

    def test_default_both_enabled(self, qapp):
        """Bez parametrów first/last oba przyciski są włączone."""
        cfg = PotConfig(idx=2)
        row = PotRow(cfg)
        assert row._up_btn.isEnabled() is True
        assert row._down_btn.isEnabled() is True
