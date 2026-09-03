"""Testy migracji katalogu konfiguracji (grejem-os → simple-deck)."""
from __future__ import annotations

from unittest import mock

from simple_deck.core.migration import (
    CURRENT_DIR_NAME,
    LEGACY_DIR_NAME,
    migrate_legacy_config_dir,
)


class TestMigrateLegacyConfigDir:
    def test_migrates_legacy_to_current(self, tmp_path):
        # Stary katalog istnieje z danymi, nowy nie istnieje → przenieś.
        root = tmp_path / ".config"
        legacy = root / LEGACY_DIR_NAME
        current = root / CURRENT_DIR_NAME
        legacy.mkdir(parents=True)
        (legacy / "settings.json").write_text('{"accent": "magenta"}')
        (legacy / "profiles").mkdir()
        (legacy / "profiles" / "Default.json").write_text("{}")
        (legacy / "grejem-os.lock").write_text("stale")

        with mock.patch("simple_deck.core.migration.Path.home", return_value=tmp_path):
            result = migrate_legacy_config_dir()

        assert result is True
        assert not legacy.exists(), "stary katalog powinien zostać przeniesiony"
        assert current.exists()
        assert (current / "settings.json").read_text() == '{"accent": "magenta"}'
        assert (current / "profiles" / "Default.json").exists()
        assert (current / "grejem-os.lock").exists()  # plik przeniesiony razem

    def test_noop_when_current_already_exists(self, tmp_path):
        # Nowy katalog już istnieje (user już zmigrowany lub fresh install) → no-op.
        root = tmp_path / ".config"
        legacy = root / LEGACY_DIR_NAME
        current = root / CURRENT_DIR_NAME
        current.mkdir(parents=True)
        (current / "settings.json").write_text('{"accent": "cyan"}')
        legacy.mkdir(parents=True)
        (legacy / "stale.json").write_text("{}")

        with mock.patch("simple_deck.core.migration.Path.home", return_value=tmp_path):
            result = migrate_legacy_config_dir()

        assert result is False
        # Oba katalogi nietknięte
        assert current.exists()
        assert legacy.exists()
        assert (current / "settings.json").read_text() == '{"accent": "cyan"}'

    def test_noop_when_neither_exists(self, tmp_path):
        # Fresh install — żaden katalog nie istnieje → no-op, nie rzuca błędem.
        with mock.patch("simple_deck.core.migration.Path.home", return_value=tmp_path):
            result = migrate_legacy_config_dir()

        assert result is False
        assert not (tmp_path / ".config" / LEGACY_DIR_NAME).exists()
        assert not (tmp_path / ".config" / CURRENT_DIR_NAME).exists()

    def test_idempotent(self, tmp_path):
        # Wołanie dwa razy: pierwsze migruje, drugie to no-op.
        root = tmp_path / ".config"
        legacy = root / LEGACY_DIR_NAME
        legacy.mkdir(parents=True)
        (legacy / "settings.json").write_text("{}")

        with mock.patch("simple_deck.core.migration.Path.home", return_value=tmp_path):
            first = migrate_legacy_config_dir()
            second = migrate_legacy_config_dir()

        assert first is True
        assert second is False
