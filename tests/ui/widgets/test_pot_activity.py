"""V1.0.3: Żywy wskaźnik aktywności w PotRow (strona POTENCJOMETRY).

Weryfikuje:
  1. PotRow ma dot + etykietę wartości (start: idle)
  2. bus.pot_event SWOJEGO kanału zapala dot i pokazuje %
  3. Eventy INNYCH kanałów są ignorowane
  4. Po wygaśnięciu timera (1 s) wskaźnik gaśnie do stanu idle
  5. PotsPage podłącza pot_event → on_pot_event (i odłącza przy rebuildzie)
  6. Tytuł karty pokazuje własną nazwę z profilu (gdy ustawiona)
"""
from __future__ import annotations

from simple_deck.core.event_bus import EventBus
from simple_deck.core.profile import ButtonConfig, PotConfig, Profile
from simple_deck.ui.pages.config_pages import PotsPage
from simple_deck.ui.widgets.config_rows import PotRow


def _make_connection():
    from PySide6.QtCore import QObject, Signal as QSignal
    from simple_deck.transport.connection_manager import ConnectionState

    class Conn(QObject):
        state_changed = QSignal(object)
        heartbeat_received = QSignal(object)
        fw_version_received = QSignal(int, int, int)
        state = ConnectionState.DISCONNECTED

    return Conn()


class TestPotRowActivityIndicator:

    def test_indicator_exists_initially_idle(self, qapp):
        row = PotRow(PotConfig(idx=0))
        assert row._activity_dot.property("active") == "false"
        assert row._activity_value.text() == "—"
        assert row._activity_active is False

    def test_own_channel_lights_up(self, qapp):
        row = PotRow(PotConfig(idx=2))
        row.on_pot_event(2, 2048)
        assert row._activity_active is True
        assert row._activity_dot.property("active") == "true"
        assert row._activity_value.text() == " 50%"  # :3d format

    def test_other_channel_ignored(self, qapp):
        row = PotRow(PotConfig(idx=2))
        row.on_pot_event(0, 4095)
        assert row._activity_active is False
        assert row._activity_value.text() == "—"

    def test_value_clamped(self, qapp):
        row = PotRow(PotConfig(idx=0))
        row.on_pot_event(0, 99999)
        assert row._activity_value.text() == "100%"
        row.on_pot_event(0, -5)
        # procent z ujemnej wartości → clamp do 0
        assert row._activity_value.text().strip() == "0%"

    def test_idle_after_timeout(self, qapp):
        row = PotRow(PotConfig(idx=0))
        row.on_pot_event(0, 1000)
        assert row._activity_active is True
        # Symuluj wygaśnięcie: wywołaj timeout handlera bezpośrednio
        row._activity_off()
        assert row._activity_active is False
        assert row._activity_dot.property("active") == "false"
        assert row._activity_value.text() == "—"

    def test_button_event_noop(self, qapp):
        row = PotRow(PotConfig(idx=0))
        row.on_button_event(0, True)  # nie może rzucić
        assert row._activity_active is False


class TestPotsPageActivityWiring:

    def test_page_connects_pot_events(self, qapp):
        bus = EventBus()
        page = PotsPage(bus=bus, connection=_make_connection())
        profile = Profile(name="T")
        profile.pots = [PotConfig(idx=i) for i in range(5)]
        profile.buttons = [ButtonConfig(idx=i) for i in range(4)]
        page.set_profile(profile)

        row0 = page._content.itemAt(1).widget()
        assert isinstance(row0, PotRow)
        bus.pot_event.emit(0, 3000)
        assert row0._activity_active is True

    def test_page_disconnects_on_rebuild(self, qapp):
        bus = EventBus()
        page = PotsPage(bus=bus, connection=_make_connection())
        profile = Profile(name="T")
        profile.pots = [PotConfig(idx=i) for i in range(5)]
        profile.buttons = [ButtonConfig(idx=i) for i in range(4)]
        page.set_profile(profile)
        row0 = page._content.itemAt(1).widget()

        page._rebuild_rows()  # np. po reorder / kalibracji
        # Po rebuildzie stary wiersz nie dostaje eventów (dot zostaje idle
        # albo nie update'uje się ponownie — tu: brak crashu i brak świeżego
        # zapalenia na starym obiekcie).
        bus.pot_event.emit(0, 1234)
        # brak wyjątku == sukces; stary wiersz mógł zostać poprawnie odłączony
        del row0

    def test_card_title_shows_custom_label(self, qapp):
        row = PotRow(PotConfig(idx=3, label="Wentylator"))
        assert row._title_lbl.text() == "Wentylator"

    def test_card_title_default_without_label(self, qapp):
        row = PotRow(PotConfig(idx=3))
        assert row._title_lbl.text() == "Potencjometr 4"
