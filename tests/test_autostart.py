"""Testy modułu core.autostart (klucz Run + sprzątanie skrótów Autostart)."""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from simple_deck.core import autostart
from simple_deck.core.autostart import (
    VALUE_NAME,
    remove_startup_shortcuts,
    repair_frozen_run_value,
    set_autostart,
)


class _FakeRegKey:
    """Minimalna atrapa klucza rejestru (context manager + dict)."""

    def __init__(self, values: dict | None = None):
        self.values = dict(values or {})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def Close(self) -> None:
        pass

    def SetValueEx(self, name, _reserved, _type, data):
        self.values[name] = data

    def QueryValueEx(self, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], None

    def DeleteValue(self, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


@pytest.fixture
def startup_dirs(tmp_path, monkeypatch):
    """Atrapy obu folderów Autostart (user + common) — pełny układ ścieżek."""
    user_root = tmp_path / "user"
    common_root = tmp_path / "common"
    user = user_root / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    common = (common_root / "Microsoft" / "Windows" / "Start Menu"
              / "Programs" / "StartUp")
    user.mkdir(parents=True)
    common.mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(user_root))
    monkeypatch.setenv("ProgramData", str(common_root))
    return user, common


class TestSetAutostart:
    """Klucz Run HKCU: włącz/wyłącz + sprzątanie legacy GREJEMOS."""

    def test_enable_writes_value(self):
        key = _FakeRegKey()
        with patch.object(autostart, "winreg", None, create=True), \
             patch.dict(sys.modules, {"winreg": _fake_winreg(key)}):
            assert set_autostart(True) is True
        assert VALUE_NAME in key.values

    def test_enable_deletes_legacy_grejemos(self):
        key = _FakeRegKey({VALUE_NAME: "old", "GREJEMOS": "legacy"})
        with patch.dict(sys.modules, {"winreg": _fake_winreg(key)}):
            set_autostart(True)
        assert "GREJEMOS" not in key.values
        assert VALUE_NAME in key.values

    def test_disable_removes_both(self):
        key = _FakeRegKey({VALUE_NAME: "x", "GREJEMOS": "legacy"})
        with patch.dict(sys.modules, {"winreg": _fake_winreg(key)}):
            assert set_autostart(False) is True
        assert VALUE_NAME not in key.values
        assert "GREJEMOS" not in key.values

    def test_frozen_command_is_bare_exe(self):
        key = _FakeRegKey()
        with patch.dict(sys.modules, {"winreg": _fake_winreg(key)}), \
             patch.object(sys, "frozen", True, create=True):
            set_autostart(True)
        cmd = key.values[VALUE_NAME]
        assert "-m simple_deck" not in cmd
        assert f'"{sys.executable}"' == cmd

    def test_dev_command_has_module_flag(self):
        key = _FakeRegKey()
        with patch.dict(sys.modules, {"winreg": _fake_winreg(key)}), \
             patch.object(sys, "frozen", False, create=True):
            set_autostart(True)
        assert "-m simple_deck" in key.values[VALUE_NAME]


class TestRepairFrozenRunValue:
    """V8 fix: nadpisz zepsuty wpis zawierający '-m simple_deck'."""

    def test_repairs_broken_entry(self):
        broken = f'"{sys.executable}" -m simple_deck'
        key = _FakeRegKey({VALUE_NAME: broken})
        with patch.dict(sys.modules, {"winreg": _fake_winreg(key)}), \
             patch.object(sys, "frozen", True, create=True):
            repair_frozen_run_value()
        assert "-m simple_deck" not in key.values[VALUE_NAME]

    def test_leaves_healthy_entry(self):
        healthy = '"C:/Program Files/Simple Deck/Simple-Deck.exe"'
        key = _FakeRegKey({VALUE_NAME: healthy})
        with patch.dict(sys.modules, {"winreg": _fake_winreg(key)}), \
             patch.object(sys, "frozen", True, create=True):
            repair_frozen_run_value()
        assert key.values[VALUE_NAME] == healthy

    def test_noop_when_value_missing(self):
        key = _FakeRegKey()
        with patch.dict(sys.modules, {"winreg": _fake_winreg(key)}), \
             patch.object(sys, "frozen", True, create=True):
            repair_frozen_run_value()  # nie powinno podnieść wyjątku
        assert VALUE_NAME not in key.values


class TestRemoveStartupShortcuts:
    """Migracja: usuwanie skrótów 'Simple Deck' z folderów Autostart."""

    def test_removes_shortcuts_from_both_dirs(self, startup_dirs):
        user, common = startup_dirs
        (user / "Simple Deck.lnk").write_bytes(b"")
        (common / "Simple Deck.lnk").write_bytes(b"")
        assert remove_startup_shortcuts() == 2
        assert not (user / "Simple Deck.lnk").exists()
        assert not (common / "Simple Deck.lnk").exists()

    def test_counts_only_existing(self, startup_dirs):
        user, _ = startup_dirs
        (user / "Simple Deck.lnk").write_bytes(b"")
        assert remove_startup_shortcuts() == 1

    def test_removes_nospace_variant(self, startup_dirs):
        common, _ = startup_dirs
        (common / "SimpleDeck.lnk").write_bytes(b"")
        assert remove_startup_shortcuts() == 1

    def test_noop_when_missing(self, startup_dirs):
        assert remove_startup_shortcuts() == 0

    def test_survives_removal_error(self, startup_dirs):
        """Plik zablokowany przez inny proces — nie podnosi wyjątku."""
        user, _ = startup_dirs
        lnk = user / "Simple Deck.lnk"
        lnk.write_bytes(b"")
        with patch.object(os, "remove", side_effect=OSError("locked")):
            assert remove_startup_shortcuts() == 0


def _fake_winreg(key: _FakeRegKey) -> MagicMock:
    """Atrapa modułu winreg: funkcje modułowe (key, ...) → metody klucza."""
    wr = MagicMock()
    wr.HKEY_CURRENT_USER = 0x80000001
    wr.KEY_SET_VALUE = 0x0002
    wr.KEY_QUERY_VALUE = 0x0001
    wr.REG_SZ = 1
    wr.OpenKey.return_value = key
    wr.SetValueEx.side_effect = (
        lambda _key, name, reserved, typ, data:
            key.SetValueEx(name, reserved, typ, data))
    wr.QueryValueEx.side_effect = (
        lambda _key, name: key.QueryValueEx(name))
    wr.DeleteValue.side_effect = (
        lambda _key, name: key.DeleteValue(name))
    wr.FileNotFoundError = FileNotFoundError
    return wr
