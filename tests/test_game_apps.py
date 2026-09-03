"""Testy panelu „Gry" (game_apps) — zapis, usuwanie, persistencja.

Regresja v1.3.2: „+ Dodaj" zastąpiono „💾 Zapisz" z natychmiastowym flushem
(pominięty 500 ms debouncer eliminuje utratę wpisu przy szybkim zamknięciu
aplikacji). Dodatkowo regresja v1.3.1: ``Settings.load()`` musi kopiować
``game_apps`` (v1.3.0 gubiło pole → nazwy ginęły po restarcie).
"""
from __future__ import annotations

import json

import pytest

from PySide6.QtWidgets import QLabel

from simple_deck.core.settings import Settings
from simple_deck.ui.pages import config_pages


# ============================================================================
#  Parsowanie inputu użytkownika (statyczne, bez Qt)
# ============================================================================

class TestParseGameNames:
    def test_single_name(self):
        assert SettingsPage_parse("cs2.exe") == ["cs2.exe"]

    def test_comma_separated(self):
        assert SettingsPage_parse("cs2.exe, witcher3.exe") == \
            ["cs2.exe", "witcher3.exe"]

    def test_semicolon_and_whitespace_separated(self):
        assert SettingsPage_parse("cs2.exe; witcher3.exe   dota2.exe") == \
            ["cs2.exe", "witcher3.exe", "dota2.exe"]

    def test_lowercase_normalization(self):
        assert SettingsPage_parse("CS2.EXE") == ["cs2.exe"]

    def test_dedup_preserving_order(self):
        assert SettingsPage_parse("b.exe, a.exe, b.exe, a.exe") == \
            ["b.exe", "a.exe"]

    def test_empty_and_garbage(self):
        assert SettingsPage_parse("") == []
        assert SettingsPage_parse("   ") == []
        assert SettingsPage_parse(" , ; ,") == []


def SettingsPage_parse(raw: str) -> list[str]:
    """Wrapper na SettingsPage._parse_game_names (staticmethod, bez widgetów)."""
    return config_pages.SettingsPage._parse_game_names(raw)


# ============================================================================
#  Persistencja (regresja v1.3.0 — load() gubił game_apps)
# ============================================================================

class TestGameAppsPersistence:
    def test_json_roundtrip(self, tmp_path):
        s = Settings()
        s.game_apps = ["cs2.exe", "witcher3.exe"]
        p = tmp_path / "settings.json"
        s.to_json(p)

        s2 = Settings.from_json(p)
        assert s2.game_apps == ["cs2.exe", "witcher3.exe"]

    def test_load_in_place_keeps_game_apps(self, tmp_path):
        """Regresja v1.3.0: load() nie kopiował game_apps → restart gubił listę."""
        s_saved = Settings()
        s_saved.game_apps = ["dota2.exe"]
        p = tmp_path / "settings.json"
        s_saved.to_json(p)

        s_live = Settings()
        s_live.game_apps = []           # stan domyślny przed load
        s_live.load(p)
        assert s_live.game_apps == ["dota2.exe"]

    def test_lowercased_on_load(self, tmp_path):
        p = tmp_path / "settings.json"
        p.write_text(json.dumps({"game_apps": ["CS2.EXE", "Witcher3.Exe"]}),
                     encoding="utf-8")
        assert Settings.from_json(p).game_apps == ["cs2.exe", "witcher3.exe"]


# ============================================================================
#  UI — SettingsPage panel „Gry" (zapisz / usuń; offscreen Qt)
# ============================================================================

@pytest.fixture
def game_page(qapp, bus, mock_connection, monkeypatch, tmp_path):
    """SettingsPage z przekierowanym settings_path na tmp_path.

    Monkeypatchuje ``settings_path`` w namespace modułu config_pages (tam gdzie
    został zaimportowany ``from ...core.settings import settings_path``), by
    natychmiastowy flush nie nadpisał prawdziwego settings.json usera.
    """
    target = tmp_path / "settings.json"
    monkeypatch.setattr(config_pages, "settings_path", lambda: target)

    settings = Settings()
    page = config_pages.SettingsPage(
        bus=bus, connection=mock_connection,
        profile_mgr=None, audio_backend=None, settings=settings)
    yield page, settings, target


class TestSaveGames:
    def test_save_multiple_names_immediate_flush(self, game_page):
        page, settings, target = game_page
        page._game_input.setText("cs2.exe, witcher3.exe")
        page._save_games()

        # Natychmiast na dysku (bez 500 ms debouncera)
        assert target.exists()
        data = json.loads(target.read_text(encoding="utf-8"))
        assert data["game_apps"] == ["cs2.exe", "witcher3.exe"]
        assert settings.game_apps == ["cs2.exe", "witcher3.exe"]
        # Pole wyczyszczone po zapisie
        assert page._game_input.text() == ""

    def test_save_appends_not_replaces(self, game_page):
        page, settings, target = game_page
        settings.game_apps = ["existing.exe"]
        page._game_input.setText("new.exe")
        page._save_games()
        assert settings.game_apps == ["existing.exe", "new.exe"]

    def test_save_duplicates_only_is_noop_no_write(self, game_page):
        page, settings, target = game_page
        settings.game_apps = ["cs2.exe"]
        page._game_input.setText("cs2.exe")
        page._save_games()
        assert settings.game_apps == ["cs2.exe"]
        assert not target.exists()      # nic nie zapisano

    def test_save_empty_input_is_noop(self, game_page):
        page, settings, target = game_page
        page._game_input.setText("   ")
        page._save_games()
        assert settings.game_apps == []
        assert not target.exists()

    def test_list_refreshed_after_save(self, game_page):
        page, settings, _ = game_page
        page._game_input.setText("cs2.exe")
        page._save_games()
        labels = [w.text() for w in page._games_widget.findChildren(QLabel)]
        assert any("cs2.exe" in t for t in labels)


class TestRemoveGame:
    def test_remove_single_flushes_immediately(self, game_page):
        page, settings, target = game_page
        settings.game_apps = ["cs2.exe", "witcher3.exe"]
        page._reload_games()

        page._remove_game("cs2.exe")

        assert settings.game_apps == ["witcher3.exe"]
        # Natychmiastowy zapis na dysk
        assert target.exists()
        data = json.loads(target.read_text(encoding="utf-8"))
        assert data["game_apps"] == ["witcher3.exe"]

    def test_remove_nonexistent_is_safe(self, game_page):
        page, settings, target = game_page
        settings.game_apps = ["a.exe"]
        page._remove_game("nope.exe")   # nie rzuca
        assert settings.game_apps == ["a.exe"]


class TestGamesSurviveRestart:
    def test_save_then_reload_new_settings_object(self, game_page):
        """E2E: zapisz w UI → wczytaj świeży Settings z tego samego pliku."""
        page, settings, target = game_page
        page._game_input.setText("cs2.exe, dota2.exe")
        page._save_games()

        reloaded = Settings.from_json(target)
        assert reloaded.game_apps == ["cs2.exe", "dota2.exe"]
