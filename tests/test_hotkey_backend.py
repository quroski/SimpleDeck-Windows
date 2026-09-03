"""Testy platform/hotkey.py — env-aware backend selection i bool return.

V4: ``simulate_combo`` zwraca bool. ``LinuxAutoBackend`` pomija xdotool na
Wayland (``$XDG_SESSION_TYPE == "wayland"``). ``_run_capture`` loguje stderr.

Scenariusze:
  - LinuxAutoBackend na Wayland → tylko wtype + ydotool (bez xdotool).
  - LinuxAutoBackend na X11 → wtype + ydotool + xdotool.
  - simulate_combo: brak backendu → False.
  - simulate_combo: sukces → True; błąd returncode → False + log warning.
"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch


from simple_deck.platform.hotkey import (
    LinuxAutoBackend,
    LinuxWtypeBackend,
    LinuxYdotoolBackend,
    LinuxXdotoolBackend,
    NullHotkeyBackend,
    _run_capture,
)


class TestNullBackend:
    def test_simulate_returns_false(self):
        assert NullHotkeyBackend().simulate_combo("Ctrl+D") is False

    def test_not_available(self):
        assert NullHotkeyBackend().available() is False


class TestAutoBackendWaylandAware:
    """LinuxAutoBackend pomija xdotool na Wayland."""

    def _make_auto(self, which_map: dict[str, bool]):
        """Stwórz LinuxAutoBackend z zmockowanym which()."""
        with patch("simple_deck.platform.hotkey.shutil.which",
                   lambda b: "/usr/bin/" + b if which_map.get(b, False) else None):
            return LinuxAutoBackend()

    def test_wayland_skips_xdotool(self, monkeypatch):
        """Na Wayland, nawet z xdotool w PATH, _pick nie wybiera xdotool."""
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        with patch("simple_deck.platform.hotkey.shutil.which",
                   lambda b: "/usr/bin/" + b if b == "xdotool" else None):
            auto = LinuxAutoBackend()
            picked = auto._pick()
            assert picked is None  # xdotool pominięty, nic innego nie ma
            assert auto.available() is False

    def test_wayland_picks_wtype(self, monkeypatch):
        """Na Wayland z wtype w PATH → wybiera wtype, nie xdotool."""
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        with patch("simple_deck.platform.hotkey.shutil.which",
                   lambda b: "/usr/bin/" + b if b in ("wtype", "xdotool") else None):
            auto = LinuxAutoBackend()
            picked = auto._pick()
            assert isinstance(picked, LinuxWtypeBackend)

    def test_x11_session_picks_xdotool_as_fallback(self, monkeypatch):
        """Na X11 bez wtype/ydotool → xdotool jest wybierany."""
        monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        with patch("simple_deck.platform.hotkey.shutil.which",
                   lambda b: "/usr/bin/" + b if b == "xdotool" else None):
            auto = LinuxAutoBackend()
            picked = auto._pick()
            assert isinstance(picked, LinuxXdotoolBackend)

    def test_wayland_display_alone_triggers_skip(self, monkeypatch):
        """Brak XDG_SESSION_TYPE ale WAYLAND_DISPLAY ustawione → Wayland."""
        monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        with patch("simple_deck.platform.hotkey.shutil.which",
                   lambda b: "/usr/bin/" + b if b == "xdotool" else None):
            auto = LinuxAutoBackend()
            assert auto._pick() is None

    def test_ydotool_works_on_wayland(self, monkeypatch):
        """ydotool jest uinput-based → działa na Wayland."""
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        with patch("simple_deck.platform.hotkey.shutil.which",
                   lambda b: "/usr/bin/" + b if b == "ydotool" else None):
            auto = LinuxAutoBackend()
            picked = auto._pick()
            assert isinstance(picked, LinuxYdotoolBackend)


class TestRunCapture:
    """_run_capture zwraca True przy sukcesie, False przy błędzie + loguje."""

    def test_success_returns_true(self):
        cp = MagicMock(returncode=0, stdout=b"", stderr=b"")
        with patch("simple_deck.platform.hotkey.subprocess.run", return_value=cp):
            assert _run_capture(["echo", "hi"], "test") is True

    def test_nonzero_returncode_returns_false(self):
        cp = MagicMock(returncode=1, stdout=b"",
                       stderr=b"compositor rejected protocol")
        with patch("simple_deck.platform.hotkey.subprocess.run", return_value=cp):
            assert _run_capture(["wtype", "-k", "a"], "wtype") is False

    def test_timeout_returns_false(self):
        with patch("simple_deck.platform.hotkey.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="wtype", timeout=2)):
            assert _run_capture(["wtype", "-k", "a"], "wtype") is False

    def test_file_not_found_returns_false(self):
        with patch("simple_deck.platform.hotkey.subprocess.run",
                   side_effect=FileNotFoundError("wtype")):
            assert _run_capture(["wtype", "-k", "a"], "wtype") is False


class TestSimulateComboBool:
    """Konkretne backendy zwracają bool z simulate_combo."""

    def test_wtype_returns_true_on_success(self):
        backend = LinuxWtypeBackend()
        backend._have = True
        cp = MagicMock(returncode=0, stdout=b"", stderr=b"")
        with patch("simple_deck.platform.hotkey.subprocess.run", return_value=cp):
            assert backend.simulate_combo("Ctrl+D") is True

    def test_wtype_returns_false_when_not_installed(self):
        backend = LinuxWtypeBackend()
        backend._have = False
        assert backend.simulate_combo("Ctrl+D") is False

    def test_wtype_returns_false_on_nonzero_exit(self):
        backend = LinuxWtypeBackend()
        backend._have = True
        cp = MagicMock(returncode=1, stdout=b"", stderr=b"failed")
        with patch("simple_deck.platform.hotkey.subprocess.run", return_value=cp):
            assert backend.simulate_combo("Ctrl+D") is False

    def test_xdotool_returns_false_on_nonzero_exit(self):
        backend = LinuxXdotoolBackend()
        backend._have = True
        cp = MagicMock(returncode=1, stdout=b"", stderr=b"")
        with patch("simple_deck.platform.hotkey.subprocess.run", return_value=cp):
            assert backend.simulate_combo("Ctrl+D") is False

    def test_ydotool_returns_false_on_unknown_key(self):
        backend = LinuxYdotoolBackend()
        backend._have = True
        # Nieznany token → None z _token_to_evdev → return False (bez subprocess)
        assert backend.simulate_combo("BogusKey") is False

    def test_empty_combo_returns_false(self):
        backend = LinuxWtypeBackend()
        backend._have = True
        assert backend.simulate_combo("") is False
