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
