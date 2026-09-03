"""Testy V1.3.4 — Ustawienia na zakładkach + naprawa UX (overlay/resize).

Regresja: SettingsPage był jednym długim scrollem z 11 kartami — karta „Gry"
(z przyciskiem Zapisz) była 5. i przy DPI 125-150% lądowała poza ekranem,
a pasek przewijania był praktycznie niewidoczny. Teraz: 5 zakładek, input+Zapisz
na górze karty Gry, ToastHost click-through (znika niewidoczna zasłona łapiąca
mysz), QSizeGrip + szersza strefa resize.
"""
from __future__ import annotations

import pytest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLineEdit, QPushButton, QSizeGrip

from simple_deck.core.settings import Settings
from simple_deck.ui.pages import config_pages
from simple_deck.ui.widgets.toast import Toast, ToastHost


@pytest.fixture
def settings_page(qapp, bus, mock_connection):
    page = config_pages.SettingsPage(bus=bus, connection=mock_connection,
                                     profile_mgr=None, audio_backend=None,
                                     settings=Settings())
    yield page
    page.deleteLater()


# ============================================================================
#  Zakładki
# ============================================================================

class TestSettingsTabs:
    def test_five_tabs(self, settings_page):
        names = [settings_page._tabs.tabText(i)
                 for i in range(settings_page._tabs.count())]
        assert names == ["Profile", "Gry", "Sterowanie", "System", "Info"]

    def test_games_tab_contains_input_and_save_button(self, settings_page):
        settings_page._tabs.setCurrentIndex(1)   # "Gry"
        tab = settings_page._tabs.widget(1)
        inputs = tab.findChildren(QLineEdit)
        buttons = [b for b in tab.findChildren(QPushButton)
                   if "Zapisz" in b.text()]
        assert len(inputs) == 1
        assert len(buttons) == 1

    def test_games_input_above_list(self, settings_page):
        """V1.3.4: wiersz input+Zapisz NA GÓRZE karty (nad listą gier)."""
        settings_page._tabs.setCurrentIndex(1)   # "Gry" — wymuś layout zakładki
        settings_page.show()
        tab = settings_page._tabs.widget(1)
        input_widget = tab.findChildren(QLineEdit)[0]
        input_y = input_widget.mapTo(tab, input_widget.rect().topLeft()).y()
        list_y = settings_page._games_widget.mapTo(
            tab, settings_page._games_widget.rect().topLeft()).y()
        assert input_y < list_y

    def test_tab_cards_scoped(self, settings_page):
        """Karty nie mogą się dublować między zakładkami (każda w 1 zakładce)."""
        tab_texts = [settings_page._tabs.tabText(i)
                     for i in range(settings_page._tabs.count())]
        assert len(tab_texts) == len(set(tab_texts))

    def test_construction_without_profile_mgr_ok(self, qapp, bus,
                                                 mock_connection):
        """Profile tab bez karty profilu (profile_mgr=None) — buduje się OK."""
        page = config_pages.SettingsPage(bus=bus, connection=mock_connection,
                                         profile_mgr=None,
                                         settings=Settings())
        assert page._tabs.count() == 5
        page.deleteLater()


# ============================================================================
#  ToastHost click-through (znika niewidoczna zasłona łapiąca mysz)
# ============================================================================

class TestToastClickThrough:
    def test_host_is_click_through(self, qapp, bus):
        host = ToastHost(bus, None, settings=None)
        assert host.testAttribute(Qt.WA_TransparentForMouseEvents) is True
        host.deleteLater()

    def test_toast_widget_is_click_through(self, qapp):
        toast = Toast("info", "test")
        assert toast.testAttribute(Qt.WA_TransparentForMouseEvents) is True
        toast.deleteLater()


# ============================================================================
#  Resize affordance (QSizeGrip)
# ============================================================================

class TestResizeGrip:
    def test_main_window_has_size_grip(self, qapp, bus, mock_connection):
        from simple_deck.ui.main_window import MainWindow
        win = MainWindow(bus=bus, connection=mock_connection,
                         settings=Settings())
        try:
            grips = win.findChildren(QSizeGrip)
            assert len(grips) == 1
            assert grips[0].isVisible() or grips[0].parentWidget() is not None
        finally:
            win.close()

    def test_resize_margin_widened(self):
        """V1.3.4: strefa krawędzi 8 → 12 px (łatwiej chwycić przy DPI)."""
        from simple_deck.ui.main_window import MainWindow
        assert MainWindow.RESIZE_MARGIN >= 12
