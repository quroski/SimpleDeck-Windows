"""Testy platform/hotkey.py — Windows SendInput backend.

Scenariusze:
  - make_hotkey_backend() na Windows → WindowsHotkeyBackend (available).
  - WindowsHotkeyBackend: znane tokeny (modyfikatory, F-klawisze, media),
    nieznany token → simulate_combo zwraca False, pusty combo → False.
  - NullHotkeyBackend → zawsze False/niedostępny.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch

from simple_deck.platform.hotkey import (
    NullHotkeyBackend,
    WindowsHotkeyBackend,
    make_hotkey_backend,
)


class TestNullBackend:
    def test_simulate_returns_false(self):
        assert NullHotkeyBackend().simulate_combo("Ctrl+D") is False

    def test_not_available(self):
        assert NullHotkeyBackend().available() is False


class TestWindowsHotkeyBackend:
    """WindowsHotkeyBackend — VkKeyScanW/SendInput przez ctypes."""

    def test_vk_for_known_modifier(self):
        backend = WindowsHotkeyBackend()
        assert backend._vk_for("ctrl") == 0x11
        assert backend._vk_for("shift") == 0x10
        assert backend._vk_for("alt") == 0x12

    def test_vk_for_function_key(self):
        backend = WindowsHotkeyBackend()
        assert backend._vk_for("f5") == 0x74

    def test_vk_for_single_char(self):
        backend = WindowsHotkeyBackend()
        # VkKeyScanW('a') może zwrócić kod zależny od układu klawiatury,
        # ale nie może być -1 (nieznany) dla litery ASCII.
        vk = backend._vk_for("a")
        assert vk is not None
        assert vk != 0xFF

    def test_vk_for_unknown_token(self):
        backend = WindowsHotkeyBackend()
        assert backend._vk_for("boguskey") is None

    def test_simulate_unknown_key_returns_false(self):
        backend = WindowsHotkeyBackend()
        assert backend.simulate_combo("BogusKey") is False

    def test_simulate_empty_combo_returns_false(self):
        backend = WindowsHotkeyBackend()
        assert backend.simulate_combo("") is False

    def test_simulate_combo_sends_keydown_keyup(self):
        """Poprawny combo → SendInput wołany 2× na klawisz (down + up)."""
        backend = WindowsHotkeyBackend()
        with patch.object(backend, "_send") as send:
            assert backend.simulate_combo("Ctrl+D") is True
            # Ctrl down, D down, D up, Ctrl up
            assert send.call_count == 4

    def test_backend_name(self):
        assert WindowsHotkeyBackend().backend_name() == "WindowsHotkeyBackend"


class TestExtendedFunctionKeys:
    """V1.0.5: klawisze funkcyjne F13–F24 (spoza normalnego zakresu)."""

    @pytest.mark.parametrize("name,vk", [
        ("f13", 0x7C), ("f14", 0x7D), ("f15", 0x7E), ("f16", 0x7F),
        ("f17", 0x80), ("f18", 0x81), ("f19", 0x82), ("f20", 0x83),
        ("f21", 0x84), ("f22", 0x85), ("f23", 0x86), ("f24", 0x87),
    ])
    def test_vk_for_extended_fkeys(self, name, vk):
        backend = WindowsHotkeyBackend()
        assert backend._vk_for(name) == vk

    @pytest.mark.parametrize("combo", ["F13", "Ctrl+F13", "Ctrl+Shift+F24",
                                       "Alt+F18"])
    def test_simulate_extended_fkey(self, combo):
        backend = WindowsHotkeyBackend()
        with patch.object(backend, "_send") as send:
            assert backend.simulate_combo(combo) is True
            # N klawiszy → down ×N + up ×N
            assert send.call_count == 2 * len(combo.split("+"))

    def test_simulate_fkey_unknown_modifier_still_false(self):
        backend = WindowsHotkeyBackend()
        assert backend.simulate_combo("Ctrl+Bogus+F13") is False


class TestFactory:
    def test_factory_returns_working_backend(self):
        """Fabryka zawsze zwraca działający obiekt (Windows → SendInput)."""
        backend = make_hotkey_backend()
        assert isinstance(backend, (WindowsHotkeyBackend, NullHotkeyBackend))
        assert hasattr(backend, "simulate_combo")
        assert hasattr(backend, "available")
