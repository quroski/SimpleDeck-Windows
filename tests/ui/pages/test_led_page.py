"""Testy LedPage - rendering poszczególnych trybów + visibility bug regression.

Regresja: wcześniej _update_visibility() wołało slider.parentWidget().setVisible()
co ukrywało CAŁĄ stronę (parentem slidera był root content widget). Teraz każdy
wiersz ma własny wrapper QWidget i setVisible wpływa tylko na ten wiersz.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from simple_deck.core.profile import LedMode
from simple_deck.ui.pages.led_page import LedPage


@pytest.fixture
def led_page(qapp, mock_connection):
    bus = MagicMock()
    mgr = MagicMock()
    page = LedPage(bus=bus, connection=mock_connection, profile_mgr=mgr)
    return page


def _set_mode(page: LedPage, mode: LedMode) -> None:
    """Ustaw tryb w combo i wyzwól _on_mode_changed."""
    for i in range(page._mode_combo.count()):
        if page._mode_combo.itemData(i) == mode.value:
            page._mode_combo.setCurrentIndex(i)
            return
    raise AssertionError(f"mode {mode} not in combo")


class TestVisibilityRegression:
    """Krytyczne: cała strona musi być widoczna niezależnie od trybu.

    Używamy isHidden() (a nie isVisible()) ponieważ w trybie offscreen
    top-level window nigdy nie jest pokazany — isVisible() zawsze False.
    isHidden() zwraca True tylko gdy widget był JAWNIE ukryty przez
    setVisible(False) / hide().
    """

    def test_vu_bar_mode_shows_page(self, led_page):
        """VU_BAR (domyślny) ukrywa tylko specyficzne wiersze, NIE całą stronę."""
        _set_mode(led_page, LedMode.VU_BAR)
        assert led_page._speed_row.isHidden() is True
        assert led_page._duty_row.isHidden() is True
        assert led_page._brightness_row.isHidden() is True
        assert led_page._manual_widget.isHidden() is True
        # KRYTYCZNE: tryb combo i info NIE mogą być ukryte
        assert led_page._mode_combo.isHidden() is False
        assert led_page._info_label.isHidden() is False

    def test_solid_mode_shows_brightness(self, led_page):
        _set_mode(led_page, LedMode.SOLID)
        assert led_page._brightness_row.isHidden() is False
        assert led_page._speed_row.isHidden() is True

    def test_breathing_shows_speed(self, led_page):
        _set_mode(led_page, LedMode.BREATHING)
        assert led_page._brightness_row.isHidden() is False
        assert led_page._speed_row.isHidden() is False

    def test_strobe_shows_duty(self, led_page):
        _set_mode(led_page, LedMode.STROBE_BAR)
        assert led_page._speed_row.isHidden() is False
        assert led_page._duty_row.isHidden() is False

    def test_manual_shows_sliders(self, led_page):
        _set_mode(led_page, LedMode.MANUAL)
        assert led_page._manual_widget.isHidden() is False
        assert led_page._brightness_row.isHidden() is False


class TestSendsCommandOnModeChange:
    """Zmiana trybu wysyła ramkę LED_CMD do MCU (poza VU_BAR)."""

    def test_solid_mode_sends_cmd(self, led_page, mock_connection):
        mock_connection.send_frame.reset_mock()
        _set_mode(led_page, LedMode.SOLID)
        assert mock_connection.send_frame.called

    def test_vu_bar_mode_does_not_send_static_cmd(self, led_page, mock_connection):
        """VU_BAR sterowany z PotDispatchera — strona nie wysyła statycznej komendy."""
        mock_connection.send_frame.reset_mock()
        _set_mode(led_page, LedMode.VU_BAR)
        assert not mock_connection.send_frame.called
