"""Regression: strony konfiguracyjne muszą się dać zbudować bez NameError.

V1.0.2: dodanie ``sub_lbl.setSizePolicy(...)`` do nagłówka _BaseConfigPage
bez zaimportowania QSizePolicy — PotsPage/ButtonsPage wywalały NameError przy
pierwszym otwarciu (Overview nie dotykał tej ścieżki, więc testy nie łapały).
"""
from __future__ import annotations

import pytest

from simple_deck.core.event_bus import EventBus
from simple_deck.core.profile import ButtonConfig, PotConfig, Profile
from simple_deck.ui.pages.config_pages import ButtonsPage, PotsPage
from simple_deck.ui.pages.led_page import LedPage


def _make_connection():
    from PySide6.QtCore import QObject, Signal as QSignal
    from simple_deck.transport.connection_manager import ConnectionState

    class Conn(QObject):
        state_changed = QSignal(object)
        heartbeat_received = QSignal(object)
        fw_version_received = QSignal(int, int, int)
        state = ConnectionState.DISCONNECTED

    return Conn()


@pytest.fixture
def conn(qapp):
    return _make_connection()


@pytest.fixture
def profile():
    p = Profile(name="Test")
    p.pots = [PotConfig(idx=i) for i in range(5)]
    p.buttons = [ButtonConfig(idx=i) for i in range(4)]
    return p


class TestConfigPagesConstructible:
    """Wszystkie strony muszą się zbudować (regresja NameError: QSizePolicy)."""

    def test_pots_page_constructs(self, qapp, conn):
        page = PotsPage(bus=EventBus(), connection=conn)
        assert page is not None

    def test_pots_page_with_profile(self, qapp, conn, profile):
        page = PotsPage(bus=EventBus(), connection=conn)
        page.set_profile(profile)
        page.show()
        qapp.processEvents()
        page.close()

    def test_buttons_page_constructs(self, qapp, conn, profile):
        page = ButtonsPage(bus=EventBus(), connection=conn)
        page.set_profile(profile)
        assert page is not None

    def test_led_page_constructs(self, qapp, conn, profile):
        page = LedPage(bus=EventBus(), connection=conn)
        page.set_profile(profile)
        page.show()
        qapp.processEvents()
        page.close()
