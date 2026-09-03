"""Testy globalnego store'u ustawień (settings.json)."""
from __future__ import annotations


from simple_deck.core.settings import (ACCENTS, CFG_DEFAULT_ALPHA_FAST,
                                      CFG_DEFAULT_DEADBAND, CfgTuning, Settings)


class TestCfgTuning:
    def test_defaults_match_firmware(self):
        c = CfgTuning()
        assert c.deadband == CFG_DEFAULT_DEADBAND == 8
        assert c.alpha_slow == 13
        assert c.alpha_fast == CFG_DEFAULT_ALPHA_FAST == 205
        assert c.send_thr == 16

    def test_clamp_on_load(self):
        c = CfgTuning.from_dict({"deadband": 9999, "alpha_slow": -5,
                                 "alpha_fast": "abc", "send_thr": 50})
        assert c.deadband == 255
        assert c.alpha_slow == 0
        assert c.alpha_fast == 0     # nieprawidłowy → lo
        assert c.send_thr == 50

    def test_roundtrip(self):
        c = CfgTuning(deadband=20, alpha_slow=30, alpha_fast=200, send_thr=64)
        c2 = CfgTuning.from_dict(c.to_dict())
        assert c2 == c


class TestSettingsRoundtrip:
    def test_defaults(self):
        s = Settings()
        assert s.accent == "cyan"
        assert s.accent_color == ACCENTS["cyan"]
        assert s.autostart is False
        assert s.audio_output_device == ""
        assert s.auto_switch_rules == {}
        assert s.recent_apps == []

    def test_roundtrip_json(self, tmp_path):
        s = Settings()
        s.accent = "magenta"
        s.autostart = True
        s.audio_output_device = "alsa_output.pci"
        s.cfg_tuning = CfgTuning(deadband=12, alpha_slow=20, alpha_fast=180,
                                 send_thr=32)
        s.set_rule("Discord", "Gaming")
        s.set_rule("Spotify", "Music")
        s.remember_app("firefox")
        s.remember_app("vlc")
        p = tmp_path / "settings.json"
        s.to_json(p)

        s2 = Settings.from_json(p)
        assert s2.accent == "magenta"
        assert s2.autostart is True
        assert s2.audio_output_device == "alsa_output.pci"
        assert s2.cfg_tuning.deadband == 12
        assert s2.cfg_tuning.alpha_fast == 180
        # reguły zapisane lowercased
        assert s2.auto_switch_rules == {"discord": "Gaming", "spotify": "Music"}
        assert s2.recent_apps == ["vlc", "firefox"]   # LRU kolejność

    def test_corrupt_file_falls_back_to_defaults(self, tmp_path):
        p = tmp_path / "settings.json"
        p.write_text("{ this is not valid json", encoding="utf-8")
        s = Settings.from_json(p)
        assert s.accent == "cyan"   # domyślny

    def test_missing_file_falls_back_to_defaults(self, tmp_path):
        s = Settings.from_json(tmp_path / "nope.json")
        assert s.accent == "cyan"


class TestAccent:
    def test_invalid_accent_falls_back(self):
        s = Settings(accent="hotpink")   # nie istnieje
        d = s.to_dict()
        assert d["accent"] == "cyan"
        assert Settings.from_dict({"accent": "weird"}).accent == "cyan"

    def test_accent_color_property(self):
        assert Settings(accent="green").accent_color == ACCENTS["green"]
        assert Settings(accent="purple").accent_color == ACCENTS["purple"]


class TestRulesAndRecent:
    def test_set_rule_empty_removes(self):
        s = Settings()
        s.set_rule("discord", "Default")
        assert "discord" in s.auto_switch_rules
        s.set_rule("discord", "")     # pusty profil = usuń
        assert "discord" not in s.auto_switch_rules

    def test_profile_for_process_case_insensitive(self):
        s = Settings()
        s.set_rule("Discord", "Gaming")
        assert s.profile_for_process("DISCORD") == "Gaming"
        assert s.profile_for_process("discord") == "Gaming"
        assert s.profile_for_process("spotify") is None

    def test_remember_app_dedup_and_cap(self):
        s = Settings()
        for _ in range(60):
            s.remember_app("spotify")
        s.remember_app("vlc")
        s.remember_app("spotify")     # przenies na przód
        assert s.recent_apps[0] == "spotify"
        assert s.recent_apps[1] == "vlc"
        assert len(s.recent_apps) <= 50
        assert s.recent_apps.count("spotify") == 1

    def test_remember_empty_ignored(self):
        s = Settings()
        s.remember_app("")
        s.remember_app("   ")
        assert s.recent_apps == []
