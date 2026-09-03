"""Testy X11KeyboardGrabber — globalny keyboard grab na Linux/X11.

Testy weryfikują że:
  - grabber graceful-fallback'uje gdy nie ma X (Wayland, Windows, brak DISPLAY)
  - grab() + release() są idempotentne (bezpieczne wielokrotne wywołanie)
  - na środowisku testowym (headless/offscreen) nie crashuje

Pełny test funkcjonalny (czy WM nie widzi klawiszy) wymaga live X session
i nie jest możliwy headless — tu testujemy tylko kontrakt API.
"""
from __future__ import annotations

import pytest

from simple_deck.platform.x11_grab import X11KeyboardGrabber, _try_import_xlib


class TestX11Grabber:
    def test_release_without_grab_is_safe(self):
        """release() bez uprzedniego grab() nie crashuje."""
        g = X11KeyboardGrabber()
        g.release()   # powinno być no-op

    def test_double_release_safe(self):
        g = X11KeyboardGrabber()
        g.release()
        g.release()   # drugi raz — też bezpieczne

    def test_double_grab_releases_first(self):
        """Ponowny grab() powinien działać po release() pierwszego."""
        g = X11KeyboardGrabber()
        result1 = g.grab()
        g.release()
        result2 = g.grab()
        g.release()
        # Obie próby powinny zakończyć się bez crasha
        assert isinstance(result1, bool)
        assert isinstance(result2, bool)

    def test_grab_returns_bool(self):
        g = X11KeyboardGrabber()
        result = g.grab()
        assert isinstance(result, bool)
        g.release()

    @pytest.mark.skipif(
        not __import__("sys").platform.startswith("linux"),
        reason="X11 grab test tylko na Linux")
    def test_xlib_import_doesnt_crash(self):
        """_try_import_xlib zwraca tuple lub None, nigdy nie rzuca."""
        result = _try_import_xlib()
        assert result is None or isinstance(result, tuple)
