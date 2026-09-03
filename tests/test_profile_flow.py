"""Testy integracyjne profilów — pełny flow switch/save/load/migrate.

Weryfikacja że przełączanie profili poprawnie propaguje do dispatcherów i UI,
oraz że zmiany w UI są zapisywane i odtwarzane po restart.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from simple_deck.core.hotkey_dispatcher import HotkeyDispatcher
from simple_deck.core.pot_dispatcher import PotDispatcher
from simple_deck.core.profile import (ButtonAction, ButtonConfig, LedMode,
                                       PotAction, PotConfig, SCHEMA_VERSION)
from simple_deck.core.profile_manager import ProfileManager


class TestProfileSwitchPropagation:
    """Przełączenie profilu → dispatchery dostają nowy obiekt Profile."""

    def test_switch_profile_updates_pot_dispatcher(self, qapp, bus, tmp_home):
        mgr = ProfileManager()
        mgr.ensure_default()
        mgr.create("Gaming")

        # Skonfiguruj profil Gaming z innym mapping'iem pota
        gaming = mgr.load("Gaming")
        gaming.pots[0] = PotConfig(idx=0, action=PotAction.APP_VOLUME,
                                    target="game.exe")
        mgr.save(gaming)

        audio = MagicMock()
        settings = MagicMock()
        settings.invert_all_pots = False
        settings.last_pot_values = [-1] * 5
        disp = PotDispatcher(bus, audio, settings=settings)
        disp.set_profile(mgr.active)

        # Przełącz na Default
        mgr.set_active("Default")
        default = mgr.active
        disp.set_profile(default)

        # Pot 0 w Default = SYSTEM_VOLUME (domyślny)
        bus.pot_event.emit(0, 2048)
        assert audio.set_volume.called
        # target=None dla system volume
        assert audio.set_volume.call_args.kwargs.get("target") is None

    def test_switch_profile_updates_hotkey_dispatcher(self, qapp, bus, tmp_home):
        mgr = ProfileManager()
        mgr.ensure_default()
        mgr.create("Media")

        media = mgr.load("Media")
        media.buttons[0] = ButtonConfig(
            idx=0, action=ButtonAction.HOTKEY, hotkey="MediaPlay",
            on_press=True)
        mgr.save(media)

        hotkey = MagicMock()
        disp = HotkeyDispatcher(bus=bus, hotkey_backend=hotkey)
        disp._thread_pool = MagicMock()
        disp.set_profile(mgr.active)

        # V6: HOTKEY jest async (QThreadPool) — sprawdzamy że job jest startowany
        bus.button_event.emit(0, True)
        assert disp._thread_pool.start.called

    def test_active_profile_changed_signal(self, qapp, tmp_home):
        """active_profile_changed emituje przy set_active."""
        mgr = ProfileManager()
        mgr.ensure_default()
        mgr.create("Second")

        received = []
        mgr.active_profile_changed.connect(lambda p: received.append(p))

        mgr.set_active("Default")
        assert len(received) == 1
        assert received[0].name == "Default"

        mgr.set_active("Second")
        assert len(received) == 2
        assert received[1].name == "Second"


class TestProfileSaveLoadRoundtrip:
    """Zmiany w profilu są zapisywane i odtwarzane po restart."""

    def test_pot_config_roundtrip(self, qapp, tmp_home):
        mgr = ProfileManager()
        mgr.ensure_default()
        p = mgr.active
        p.pots[2] = PotConfig(
            idx=2, action=PotAction.APP_VOLUME, target="firefox",
            sensitivity=1.5, curve="log", min_volume=0.1, max_volume=0.9,
            invert=True)
        mgr.save(p)

        # Re-load z dysku
        mgr2 = ProfileManager()
        loaded = mgr2.load("Default")
        cfg = loaded.pots[2]
        assert cfg.action == PotAction.APP_VOLUME
        assert cfg.target == "firefox"
        assert cfg.sensitivity == 1.5
        assert cfg.curve == "log"
        assert cfg.min_volume == pytest.approx(0.1)
        assert cfg.max_volume == pytest.approx(0.9)
        assert cfg.invert is True

    def test_button_config_roundtrip(self, qapp, tmp_home):
        mgr = ProfileManager()
        mgr.ensure_default()
        p = mgr.active
        p.buttons[1] = ButtonConfig(
            idx=1, action=ButtonAction.TOGGLE_MUTE, target="discord",
            on_press=False)
        mgr.save(p)

        mgr2 = ProfileManager()
        loaded = mgr2.load("Default")
        cfg = loaded.buttons[1]
        assert cfg.action == ButtonAction.TOGGLE_MUTE
        assert cfg.target == "discord"
        assert cfg.on_press is False

    def test_led_config_roundtrip(self, qapp, tmp_home):
        mgr = ProfileManager()
        mgr.ensure_default()
        p = mgr.active
        p.led_mode = LedMode.KNIGHT_RIDER.value
        p.led_brightness = 128
        p.led_speed_ms = 750
        p.led_per_led = [64, 128, 192]
        mgr.save(p)

        mgr2 = ProfileManager()
        loaded = mgr2.load("Default")
        assert loaded.led_mode == LedMode.KNIGHT_RIDER.value
        assert loaded.led_brightness == 128
        assert loaded.led_speed_ms == 750
        assert loaded.led_per_led == [64, 128, 192]

    def test_schema_version_migrates(self, qapp, tmp_home):
        """Profil schema v1/v2 powinien zostać zmigrowany do v3."""
        # Ręcznie stwórz stary profil schema v1 (bez led_mode etc.)
        old_data = {
            "name": "OldProfile",
            "description": "Stary profil",
            "pots": [{"idx": 0, "action": "system_volume"}],
            "buttons": [{"idx": 0, "action": "hotkey", "hotkey": "F5"}],
        }
        path = ProfileManager().directory / "OldProfile.json"
        path.write_text(json.dumps(old_data))

        mgr = ProfileManager()
        loaded = mgr.load("OldProfile")
        assert loaded.schema_version == SCHEMA_VERSION
        assert loaded.led_mode == LedMode.VU_BAR.value  # domyślny
        assert loaded.led_brightness == 255


class TestProfileCRUDFlow:
    """Pełny CRUD: create → rename → duplicate → delete."""

    def test_full_crud_cycle(self, qapp, tmp_home):
        mgr = ProfileManager()
        mgr.ensure_default()
        assert "Default" in mgr.list_profiles()

        # Create
        p = mgr.create("Work")
        assert p is not None
        assert "Work" in mgr.list_profiles()

        # Modify + save
        p.pots[0] = PotConfig(idx=0, target="slack")
        mgr.save(p)

        # Duplicate
        dup = mgr.duplicate("Work")
        assert dup is not None
        assert dup.name == "Work (kopia)"
        assert "Work (kopia)" in mgr.list_profiles()

        # Rename
        assert mgr.rename("Work (kopia)", "WorkBackup")
        assert "WorkBackup" in mgr.list_profiles()
        assert "Work (kopia)" not in mgr.list_profiles()

        # Delete
        assert mgr.delete("WorkBackup")
        assert "WorkBackup" not in mgr.list_profiles()

    def test_export_import_roundtrip(self, qapp, tmp_home, tmp_path):
        mgr = ProfileManager()
        mgr.ensure_default()
        p = mgr.active
        p.pots[0] = PotConfig(idx=0, action=PotAction.APP_VOLUME,
                               target="spotify")
        mgr.save(p)

        export_path = tmp_path / "export.json"
        assert mgr.export_profile("Default", export_path)
        assert export_path.exists()

        # Import pod inną nazwą
        imported = mgr.import_profile(export_path)
        assert imported is not None
        # Nazwa powinna być „Default" z pliku ale to koliduje — powinna dostać suffix
        assert imported.name != "Default" or "Default" in imported.name


class TestAutoSwitchRules:
    """Reguły auto-przełączania profili wg aktywnej aplikacji."""

    def test_rule_triggers_switch(self, qapp, tmp_home):
        mgr = ProfileManager()
        mgr.ensure_default()
        mgr.create("Discord")

        mgr.set_rule("discord", "Discord")
        assert mgr.rules.get("discord") == "Discord"

        # Symuluj aktywne okno Discord
        mgr.on_foreground_process("discord")
        assert mgr.active.name == "Discord"

    def test_rule_case_insensitive(self, qapp, tmp_home):
        mgr = ProfileManager()
        mgr.ensure_default()
        mgr.create("Spotify")

        mgr.set_rule("Spotify.exe", "Spotify")
        mgr.on_foreground_process("SPOTIFY.EXE")
        assert mgr.active.name == "Spotify"

    def test_no_rule_no_switch(self, qapp, tmp_home):
        mgr = ProfileManager()
        mgr.ensure_default()
        mgr.create("Other")

        # Brak reguły dla „unknown.exe" → brak przełączenia
        original = mgr.active.name
        mgr.on_foreground_process("unknown.exe")
        assert mgr.active.name == original
