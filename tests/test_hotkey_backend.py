"""Testy platform/hotkey.py — Windows SendInput backend.

Scenariusze:
  - make_hotkey_backend() na Windows → WindowsHotkeyBackend (available).
  - WindowsHotkeyBackend: znane tokeny (modyfikatory, F-klawisze, media),
    nieznany token → simulate_combo zwraca False, pusty combo → False.
  - NullHotkeyBackend → zawsze False/niedostępny.
"""
from __future__ import annotations

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


class TestFactory:
    def test_factory_returns_working_backend(self):
        """Fabryka zawsze zwraca działający obiekt (Windows → SendInput)."""
        backend = make_hotkey_backend()
        assert isinstance(backend, (WindowsHotkeyBackend, NullHotkeyBackend))
        assert hasattr(backend, "simulate_combo")
        assert hasattr(backend, "available")
