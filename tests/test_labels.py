"""V1.0.3: Własne nazwy kontrolek (label) — profil + Overview rename flow.

Weryfikuje:
  1. PotConfig/ButtonConfig mają pole ``label`` (puste = domyślna nazwa)
  2. Round-trip JSON (to_dict/from_dict) zachowuje label
  3. Stare profile (schema < 5, bez pola label) ładują się z label=""
  4. DeckMap label_renamed emituje (kind, idx, label) przy edycji w komórce
  5. set_label aktualizuje tytuł komórki ("" wraca do "POT N"/"BTN N")
"""
from __future__ import annotations

import json

from simple_deck.core.profile import (ButtonConfig, PotConfig, Profile,
                                      SCHEMA_VERSION)
from simple_deck.core.event_bus import EventBus
from simple_deck.ui.widgets.deck_map import DeckMap


class TestLabelDataModel:

    def test_default_label_empty(self, qapp):
        assert PotConfig(idx=0).label == ""
        assert ButtonConfig(idx=0).label == ""

    def test_label_roundtrip_json(self, qapp, tmp_home, tmp_path):
        p = Profile(name="L")
        p.pots[1].label = "Głośność muzyki"
        p.buttons[0].label = "Push-To-Talk"
        path = tmp_path / "labels.json"
        p.to_json(path)
        loaded = Profile.from_json(path)
        assert loaded.pots[1].label == "Głośność muzyki"
        assert loaded.buttons[0].label == "Push-To-Talk"

    def test_to_dict_contains_label(self, qapp):
        p = Profile(name="D")
        p.pots[0].label = "Master"
        d = p.to_dict()
        assert d["pots"][0]["label"] == "Master"

    def test_legacy_schema_without_label_loads_empty(self, qapp, tmp_path):
        """Profil schema 4 (przed V1.0.3) nie ma pola label → label=\"\"."""
        old = {
            "name": "OldV4",
            "pots": [{"idx": i} for i in range(5)],
            "buttons": [{"idx": i} for i in range(4)],
            "schema_version": 4,
        }
        path = tmp_path / "v4.json"
        path.write_text(json.dumps(old), encoding="utf-8")
        loaded = Profile.from_json(path)
        assert loaded.schema_version == SCHEMA_VERSION
        assert all(p.label == "" for p in loaded.pots)
        assert all(b.label == "" for b in loaded.buttons)


class TestDeckMapRename:

    def _emit(self, cell, signal_name, *args):
        """Wywołaj prywatny slot committa bez symulacji fokusa (offscreen)."""
        getattr(cell, signal_name)(*args)

    def test_set_label_updates_title(self, qapp):
        deck = DeckMap(bus=__import__("simple_deck.core.event_bus",
                                     fromlist=["EventBus"]).EventBus(),
                       settings=None)
        cell = deck._pots[2]
        cell.set_label("Bas")
        assert cell._title.text() == "Bas"

    def test_empty_label_restores_default(self, qapp):
        from simple_deck.core.event_bus import EventBus
        deck = DeckMap(bus=EventBus(), settings=None)
        cell = deck._pots[0]
        cell.set_label("X")
        cell.set_label("")
        assert cell._title.text() == "POT 1"

    def test_button_cell_set_label(self, qapp):
        from simple_deck.core.event_bus import EventBus
        deck = DeckMap(bus=EventBus(), settings=None)
        cell = deck._buttons[3]
        cell.set_label("Mute mic")
        assert cell._title.text() == "Mute mic"
        cell.set_label("")
        assert cell._title.text() == "BTN 4"

    def test_pot_rename_emits_kind_idx_label(self, qapp):
        from simple_deck.core.event_bus import EventBus
        deck = DeckMap(bus=EventBus(), settings=None)
        deck.show()
        try:
            got = []
            deck.label_renamed.connect(lambda k, i, s: got.append((k, i, s)))
            cell = deck._pots[1]
            cell._begin_edit()
            cell._edit.setText("Subwoofer")
            cell._commit_edit()
            assert got == [("pot", 1, "Subwoofer")]
        finally:
            deck.close()

    def test_button_rename_emits_kind_idx_label(self, qapp):
        from simple_deck.core.event_bus import EventBus
        deck = DeckMap(bus=EventBus(), settings=None)
        deck.show()
        try:
            got = []
            deck.label_renamed.connect(lambda k, i, s: got.append((k, i, s)))
            cell = deck._buttons[0]
            cell._begin_edit()
            cell._edit.setText("PTT")
            cell._commit_edit()
            assert got == [("btn", 0, "PTT")]
        finally:
            deck.close()

    def test_rename_same_value_no_emit(self, qapp):
        from simple_deck.core.event_bus import EventBus
        deck = DeckMap(bus=EventBus(), settings=None)
        deck.show()
        try:
            got = []
            deck.label_renamed.connect(lambda k, i, s: got.append((k, i, s)))
            cell = deck._pots[0]
            cell._begin_edit()
            cell._edit.setText("")
            cell._commit_edit()  # "" == obecny label → brak sygnału
            assert got == []
        finally:
            deck.close()

    def test_whitespace_stripped(self, qapp):
        from simple_deck.core.event_bus import EventBus
        deck = DeckMap(bus=EventBus(), settings=None)
        cell = deck._pots[0]
        cell.set_label("  Pad  ")
        assert cell._label == "Pad"
        assert cell._title.text() == "Pad"

    def test_set_profile_loads_labels(self, qapp):
        from simple_deck.core.event_bus import EventBus
        deck = DeckMap(bus=EventBus(), settings=None)
        profile = Profile(name="T")
        profile.pots[4].label = "Game"
        profile.buttons[2].label = "Screenshots"
        deck.set_profile(profile)
        assert deck._pots[4]._title.text() == "Game"
        assert deck._buttons[2]._title.text() == "Screenshots"

    def test_default_title_keeps_subtle_style(self, qapp):
        """Brak nazwy własnej → deckCellTitle (mała, przygaszona)."""
        from simple_deck.core.event_bus import EventBus
        deck = DeckMap(bus=EventBus(), settings=None)
        cell = deck._pots[0]
        assert cell._title.objectName() == "deckCellTitle"

    def test_custom_name_gets_prominent_style(self, qapp):
        """Własna nazwa → deckCellName (duża, jasna) — V1.0.3b."""
        from simple_deck.core.event_bus import EventBus
        deck = DeckMap(bus=EventBus(), settings=None)
        cell = deck._pots[0]
        cell.set_label("Master")
        assert cell._title.objectName() == "deckCellName"
        assert cell._title.text() == "Master"

    def test_clearing_label_reverts_style(self, qapp):
        """Wyczyszczenie nazwy → powrót do subtelnego deckCellTitle."""
        from simple_deck.core.event_bus import EventBus
        deck = DeckMap(bus=EventBus(), settings=None)
        cell = deck._pots[0]
        cell.set_label("Master")
        cell.set_label("")
        assert cell._title.objectName() == "deckCellTitle"
        assert cell._title.text() == "POT 1"


class TestCrossTabSync:
    """V1.0.3b: Nazwy synchronizowane między Overview a POTS/PRZYCISKI."""

    def _make_connection(self):
        from PySide6.QtCore import QObject, Signal as QSignal
        from simple_deck.transport.connection_manager import ConnectionState

        class Conn(QObject):
            state_changed = QSignal(object)
            heartbeat_received = QSignal(object)
            fw_version_received = QSignal(int, int, int)
            state = ConnectionState.DISCONNECTED

        return Conn()

    def test_potrow_update_config_refreshes_title(self, qapp):
        from simple_deck.ui.widgets.config_rows import PotRow
        row = PotRow(PotConfig(idx=1))
        assert row._title_lbl.text() == "Potencjometr 2"
        row.update_config(PotConfig(idx=1, label="Muzyka"))
        assert row._title_lbl.text() == "Muzyka"
        # get_config zwraca zaktualizowany config (label nie ginie)
        assert row.get_config().label == "Muzyka"

    def test_buttonrow_update_config_refreshes_title(self, qapp):
        from simple_deck.ui.widgets.config_rows import ButtonRow
        row = ButtonRow(ButtonConfig(idx=2))
        assert row._title_lbl.text() == "Przycisk 3"
        row.update_config(ButtonConfig(idx=2, label="PTT"))
        assert row._title_lbl.text() == "PTT"

    def test_pots_page_refresh_labels(self, qapp):
        from simple_deck.ui.pages.config_pages import PotsPage
        from simple_deck.ui.widgets.config_rows import PotRow
        bus = EventBus()
        page = PotsPage(bus=bus, connection=self._make_connection())
        profile = Profile(name="T")
        profile.pots = [PotConfig(idx=i) for i in range(5)]
        profile.buttons = [ButtonConfig(idx=i) for i in range(4)]
        page.set_profile(profile)

        # Symuluj rename w Overview: profil się zmienia, strona się odświeża
        profile.pots[3].label = "Wentylator"
        page.refresh_labels()

        # Znajdź wiersz pota 3 (kolejność = pot_display_order, domyślnie identyczność)
        row3 = None
        for i in range(1, page._content.count()):
            w = page._content.itemAt(i).widget()
            if isinstance(w, PotRow) and w._config.idx == 3:
                row3 = w
                break
        assert row3 is not None
        assert row3._title_lbl.text() == "Wentylator"

    def test_buttons_page_refresh_labels(self, qapp):
        from simple_deck.ui.pages.config_pages import ButtonsPage
        bus = EventBus()
        page = ButtonsPage(bus=bus, connection=self._make_connection())
        profile = Profile(name="T")
        profile.pots = [PotConfig(idx=i) for i in range(5)]
        profile.buttons = [ButtonConfig(idx=i) for i in range(4)]
        page.set_profile(profile)

        profile.buttons[0].label = "Push-To-Talk"
        page.refresh_labels()
        row0 = page._content.itemAt(1).widget()
        assert row0._title_lbl.text() == "Push-To-Talk"

    def test_mainwindow_rename_updates_pots_page(self, qapp, bus, mock_connection):
        """Pełny flow: rename w Overview → karta na POTS pokazuje tę samą nazwę."""
        from simple_deck.core.settings import Settings
        from simple_deck.ui.main_window import MainWindow
        from simple_deck.ui.widgets.config_rows import PotRow
        win = MainWindow(bus=bus, connection=mock_connection,
                         settings=Settings())
        try:
            profile = Profile(name="T")
            profile.pots = [PotConfig(idx=i) for i in range(5)]
            profile.buttons = [ButtonConfig(idx=i) for i in range(4)]
            win.set_profile(profile)

            # Rename pota 2 przez sygnał z Overview
            win._page_overview.label_renamed.emit("pot", 2, "Game audio")

            # Karta na niezbudowanej stronie POTS pobierze nazwę przy budowie
            win._set_page(1)  # buduje i pokazuje PotsPage
            row2 = None
            for i in range(1, win._page_pots._content.count()):
                w = win._page_pots._content.itemAt(i).widget()
                if isinstance(w, PotRow) and w._config.idx == 2:
                    row2 = w
                    break
            assert row2 is not None
            assert row2._title_lbl.text() == "Game audio"

            # Rename z istniejącą stroną POTS → natychmiastowa synchronizacja
            win._page_overview.label_renamed.emit("pot", 2, "Loot")
            assert row2._title_lbl.text() == "Loot"
        finally:
            win.close()

    def test_events_log_uses_custom_names(self, qapp):
        """Log zdarzeń w Overview używa własnych nazw (spójność między kartami)."""
        from simple_deck.ui.pages.overview import OverviewPage
        bus = EventBus()
        conn = self._make_connection()
        page = OverviewPage(bus=bus, connection=conn)
        try:
            profile = Profile(name="T")
            profile.pots = [PotConfig(idx=i) for i in range(5)]
            profile.buttons = [ButtonConfig(idx=i) for i in range(4)]
            profile.pots[1].label = "Music"
            page.set_profile(profile)
            page.show()

            bus.pot_event.emit(1, 2000)
            bus.button_event.emit(0, True)
            page._flush_events()
            text = page._events_log.text()
            assert "Music" in text
            assert "BTN 1" in text  # przycisk bez nazwy → default
            assert "POT 2" not in text  # pot 2 ma nazwę własną
        finally:
            page.close()
