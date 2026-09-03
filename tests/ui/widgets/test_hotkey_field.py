"""Testy HotkeyField - ręczne wpisywanie skrótu + normalizacja combo.

V3 fix: Dodano „Wpisz ręcznie…" jako fallback gdy OS przechwytuje kombinację
(np. Super+E otwiera menedżer plików zamiast dać się przechwycić).
Normalizacja zamienia synonimy (win→Super, ctrl→Ctrl) i formatuje klawisze.
"""
from __future__ import annotations

import pytest

from simple_deck.ui.widgets.hotkey_field import (
    HotkeyField, _normalize_combo_token,
)


class TestNormalizeToken:
    """Normalizacja pojedynczego tokena z ręcznego wpisu."""

    @pytest.mark.parametrize("raw,expected", [
        # Modyfikatory — synonimy
        ("ctrl", "Ctrl"),
        ("CTRL", "Ctrl"),
        ("control", "Ctrl"),
        ("shift", "Shift"),
        ("alt", "Alt"),
        ("Alt", "Alt"),
        ("win", "Super"),
        ("WIN", "Super"),
        ("windows", "Super"),
        ("meta", "Super"),
        ("cmd", "Super"),
        ("super", "Super"),
        # AltGr
        ("altgr", "AltGr"),
        # Pojedyncze znaki
        ("d", "D"),
        ("D", "D"),
        ("1", "1"),
        (",", ","),
        # Funkcyjne
        ("f5", "F5"),
        ("F12", "F12"),
        ("f1", "F1"),
        # Multimedia
        ("mediaplay", "MediaPlay"),
        ("volup", "VolUp"),
        ("volmute", "VolMute"),
        ("space", "Space"),
        ("enter", "Enter"),
        ("esc", "Esc"),
        # Puste
        ("", ""),
        ("   ", ""),
    ])
    def test_normalize(self, raw, expected):
        assert _normalize_combo_token(raw) == expected

    def test_unknown_fallback_capitalizes(self):
        # Nieznany token → pierwsza litera upper, reszta jak wpisano
        assert _normalize_combo_token("foobar") == "Foobar"


class TestManualInputFlow:
    """Symulacja: użytkownik klika „Wpisz ręcznie" i wpisuje combo."""

    def test_manual_set_value(self, qapp, monkeypatch):
        """HotkeyField z ręcznie wpisaną wartością przez set_value."""
        from unittest.mock import MagicMock
        field = HotkeyField()
        emitted = MagicMock()
        field.hotkey_changed.connect(emitted)

        # Symuluj wpisanie „super+e" przez użytkownika (bez otwierania dialogu)
        field.set_value("Super+E")
        assert field.value() == "Super+E"
        assert field.text() == "Super+E"

    def test_set_value_empty(self, qapp):
        field = HotkeyField()
        field.set_value("")
        assert field.value() == ""
        assert field.text() == ""

    def test_set_value_none(self, qapp):
        field = HotkeyField()
        field.set_value(None)   # type: ignore[arg-type]
        assert field.value() == ""

    def test_set_value_does_not_emit(self, qapp):
        """set_value jest programowe (ładowanie z profilu) — nie emituje sygnału."""
        from unittest.mock import MagicMock
        field = HotkeyField()
        field.set_value("Ctrl+D")
        emitted = MagicMock()
        field.hotkey_changed.connect(emitted)
        field.set_value("")   # programowe czyszczenie — nie emituje
        emitted.assert_not_called()


class TestManualInputNormalization:
    """Combo wpisane ręcznie powinno być normalizowane przed zapisem.

    Testujemy logikę normalizacji bezpośrednio (bez UI), bo QInputDialog.getText
    jest modalny i trudny do testowania headless.
    """

    def test_super_e_normalizes(self):
        parts = [_normalize_combo_token(p) for p in "super+e".split("+")]
        assert "+".join(parts) == "Super+E"

    def test_ctrl_shift_d(self):
        parts = [_normalize_combo_token(p) for p in "ctrl+shift+d".split("+")]
        assert "+".join(parts) == "Ctrl+Shift+D"

    def test_win_e(self):
        parts = [_normalize_combo_token(p) for p in "win+e".split("+")]
        assert "+".join(parts) == "Super+E"

    def test_mediaplay(self):
        parts = [_normalize_combo_token(p) for p in "mediaplay".split("+")]
        assert "+".join(parts) == "MediaPlay"

    def test_mixed_case_input(self):
        parts = [_normalize_combo_token(p) for p in "CTRL+SHIFT+F5".split("+")]
        assert "+".join(parts) == "Ctrl+Shift+F5"
