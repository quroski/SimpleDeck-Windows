"""Testy ProfileManager - CRUD profili + sanityzacja nazw + reguły."""
from __future__ import annotations

import json

from simple_deck.core.profile import Profile, SCHEMA_VERSION
from simple_deck.core.profile_manager import ProfileManager, _sanitize_name


class TestSanitize:
    def test_strips_path_separators(self):
        assert _sanitize_name("../../etc/x") == "etcx"
        assert _sanitize_name("a/b\\c") == "abc"

    def test_collapses_whitespace(self):
        assert _sanitize_name("My   Profile") == "My Profile"

    def test_strips_trailing_dots(self):
        assert _sanitize_name("Profile...") == "Profile"

    def test_empty_falls_back(self):
        assert _sanitize_name("") == "Profile"
        assert _sanitize_name("   ") == "Profile"

    def test_strips_control_chars(self):
        assert _sanitize_name("a\x00b\x01c") == "abc"


class TestCRUD:
    def test_create_and_list(self, qapp, tmp_home):
        mgr = ProfileManager()
        mgr.ensure_default()
        assert "Default" in mgr.list_profiles()
        p = mgr.create("Gaming", "opis")
        assert p is not None
        assert "Gaming" in mgr.list_profiles()
        assert mgr.active.name == "Gaming"

    def test_create_duplicate_returns_none(self, qapp, tmp_home):
        mgr = ProfileManager()
        mgr.ensure_default()
        assert mgr.create("Default") is None

    def test_rename(self, qapp, tmp_home):
        mgr = ProfileManager()
        mgr.ensure_default()
        assert mgr.rename("Default", "Renamed") is True
        assert "Renamed" in mgr.list_profiles()
        assert "Default" not in mgr.list_profiles()
        assert mgr.active.name == "Renamed"

    def test_rename_collision_fails(self, qapp, tmp_home):
        mgr = ProfileManager()
        mgr.ensure_default()
        mgr.create("Second")
        assert mgr.rename("Default", "Second") is False

    def test_duplicate(self, qapp, tmp_home):
        mgr = ProfileManager()
        mgr.ensure_default()
        dup = mgr.duplicate("Default")
        assert dup is not None
        assert dup.name == "Default (kopia)"
        assert "Default (kopia)" in mgr.list_profiles()

    def test_delete_refuses_last_profile(self, qapp, tmp_home):
        mgr = ProfileManager()
        mgr.ensure_default()
        assert mgr.delete("Default") is False   # jedyny profil

    def test_delete_switches_active(self, qapp, tmp_home):
        mgr = ProfileManager()
        mgr.ensure_default()
        mgr.create("Second")
        mgr.set_active("Second")
        assert mgr.delete("Second") is True
        # aktywny przełączył się na Default (pozostały)
        assert mgr.active.name == "Default"


class TestImportExport:
    def test_export_then_import(self, qapp, tmp_home, tmp_path):
        mgr = ProfileManager()
        mgr.ensure_default()
        mgr.create("Src", "do eksportu")
        out = tmp_path / "exp.json"
        assert mgr.export_profile("Src", out) is True
        assert out.exists()
        # Usuń oryginał, potem importuj → powinien wrócić pod tą samą nazwą
        assert mgr.delete("Src") is True
        p = mgr.import_profile(out)
        assert p is not None
        assert p.name == "Src"
        assert "Src" in mgr.list_profiles()
        # Ponowny import → uniknięcie kolizji (sufiks " 2")
        p2 = mgr.import_profile(out)
        assert p2 is not None
        assert p2.name == "Src 2"

    def test_import_nonexistent_returns_none(self, qapp, tmp_home, tmp_path):
        mgr = ProfileManager()
        assert mgr.import_profile(tmp_path / "nope.json") is None


class TestRules:
    def test_set_and_sync_rules(self, qapp, tmp_home):
        mgr = ProfileManager()
        mgr.set_rule("discord", "Gaming")
        assert mgr.rules == {"discord": "Gaming"}
        mgr.set_rules({"spotify": "Music", "discord": "Gaming2"})
        assert mgr.rules == {"spotify": "Music", "discord": "Gaming2"}

    def test_clear_rule_with_empty(self, qapp, tmp_home):
        mgr = ProfileManager()
        mgr.set_rule("discord", "Gaming")
        mgr.set_rule("discord", "")
        assert mgr.rules == {}


class TestV2Migration:
    """V2: migracja profilu v1 (z leds) → v2 (vu_bar_enabled)."""

    def test_v1_leds_silently_dropped(self, qapp, tmp_home, tmp_path):
        """Stary profil v1 z polem 'leds' ładuje się bez błędu, leds ignorowane."""
        legacy = {
            "name": "OldV1",
            "description": "stary profil",
            "pots": [{"idx": 0, "action": "system_volume"}],
            "buttons": [],
            "leds": [
                {"idx": 0, "mode": 2, "label": "Status"},
                {"idx": 1, "mode": 1},
            ],
        }
        path = tmp_path / "legacy.json"
        path.write_text(json.dumps(legacy))
        p = Profile.from_json(path)
        assert p.name == "OldV1"
        assert p.schema_version == SCHEMA_VERSION
        assert p.vu_bar_enabled is True
        assert not hasattr(p, "leds") or not getattr(p, "leds", None)

    def test_v2_round_trip(self, qapp, tmp_home, tmp_path):
        """Pełna serializacja v2 z vu_bar_enabled."""
        p = Profile(name="V2Test", vu_bar_enabled=False)
        path = tmp_path / "v2.json"
        p.to_json(path)
        loaded = Profile.from_json(path)
        assert loaded.name == "V2Test"
        assert loaded.vu_bar_enabled is False
        assert loaded.schema_version == SCHEMA_VERSION

    def test_default_vu_bar_enabled(self, qapp, tmp_home):
        p = Profile(name="Default")
        assert p.vu_bar_enabled is True
        assert p.schema_version == SCHEMA_VERSION


class TestPotDisplayOrder:
    """V4: Kolejność wyświetlania potencjometrów na PotsPage."""

    def test_default_order_is_identity(self, qapp, tmp_home):
        p = Profile(name="Default")
        assert p.pot_display_order == [0, 1, 2, 3, 4]

    def test_custom_order_roundtrip(self, qapp, tmp_home, tmp_path):
        p = Profile(name="Reorder", pot_display_order=[0, 2, 4, 1, 3])
        path = tmp_path / "reorder.json"
        p.to_json(path)
        loaded = Profile.from_json(path)
        assert loaded.pot_display_order == [0, 2, 4, 1, 3]

    def test_v3_migration_defaults_identity(self, qapp, tmp_home, tmp_path):
        """Profil zapisany w schema v3 (bez pot_display_order) → identyczność."""
        import json
        old = {
            "name": "OldV3",
            "pots": [{"idx": i} for i in range(5)],
            "buttons": [{"idx": i} for i in range(4)],
            "schema_version": 3,
        }
        path = tmp_path / "v3.json"
        path.write_text(json.dumps(old))
        loaded = Profile.from_json(path)
        assert loaded.pot_display_order == [0, 1, 2, 3, 4]

    def test_invalid_order_duplicates_repaired(self, qapp, tmp_home):
        """Duplikaty w pot_display_order → fallback do identyczności."""
        p = Profile(name="Bad", pot_display_order=[0, 0, 1, 2, 3])
        assert p.pot_display_order == [0, 1, 2, 3, 4]

    def test_invalid_order_wrong_length_repaired(self, qapp, tmp_home):
        """Zła długość → fallback do identyczności."""
        p = Profile(name="Bad", pot_display_order=[0, 1, 2, 3])
        assert p.pot_display_order == [0, 1, 2, 3, 4]

    def test_invalid_order_out_of_range_repaired(self, qapp, tmp_home):
        """Wartość spoza zakresu → fallback do identyczności."""
        p = Profile(name="Bad", pot_display_order=[0, 1, 2, 3, 5])
        assert p.pot_display_order == [0, 1, 2, 3, 4]
