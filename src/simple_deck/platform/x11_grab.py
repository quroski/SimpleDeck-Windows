"""X11 keyboard grab — przechwytuje WSZYSTKIE klawisze na poziomie serwera X.

Używane przez HotkeyCaptureDialog aby zapobiec przeciekowi skrótów do WM/OS.
Bez tego np. Super+E otwiera menedżer plików zamiast dać się przechwycić
jako skrót w aplikacji.

Działa TYLKO na Linux/X11. Na Wayland nie ma odpowiednika (sandboxing WM).
Jeśli Xlib niedostępny lub nie ma połączenia z X server, funkcje są no-op
(abort silently — fallback do Qt grabKeyboard).

Zastosowanie:
    grabber = X11KeyboardGrabber()
    if grabber.grab():
        # ... przechwytywanie ...
        grabber.release()   # ważne! bez tego klawiatura zostaje zablokowana

Implementacja: XGrabKey na root window dla wszystkich keycode'ów × modyfikatorów.
To przechwytuje klawisze globalnie — serwer X wysyła je do naszego okna zamiast
do WM. XUngrabKey zwalnia wszystkie grab'y.

UWAGA: grab jest per-display-connection. Qt ma własne połączenie z X server.
Otwieramy OSOBNE połączenie przez Xlib.display.Display() — grab'y na nim
działają globalnie (X grab jest per-server, nie per-connection).
"""
from __future__ import annotations

import logging
import sys
from typing import Optional

log = logging.getLogger(__name__)

# Cache: czy próbowaliśmy już załadować Xlib i czy się udało
_xlib_available: Optional[bool] = None


def _try_import_xlib():
    """Spróbuj zaimportować Xlib. Zwraca moduł lub None."""
    global _xlib_available
    if _xlib_available is False:
        return None
    try:
        from Xlib import X, display, XK  # noqa: F401
        _xlib_available = True
        return X, display, XK
    except Exception:
        _xlib_available = False
        return None


class X11KeyboardGrabber:
    """Przechwytuje wszystkie klawisze na poziomie X server.

    Usage:
        g = X11KeyboardGrabber()
        if g.grab():
            try:
                # ... przechwytywanie ...
            finally:
                g.release()

    grab() otwiera własne połączenie do X server (osobne od Qt), woła
    XGrabKey dla wszystkich keycode'ów × 8 modyfikatorów (none, shift, lock,
    control, mod1-mod5). release() woła XUngrabKey dla każdego i zamyka
    połączenie.

    Jeśli cokolwiek zawiedzie (brak X, brak uprawnień, Wayland), grab()
    zwraca False i aplikacja działa bez globalnego grab'a (fallback do
    Qt grabKeyboard + manual text input).
    """

    def __init__(self):
        self._disp = None
        self._root = None
        self._grabbed = False
        self._mods = []

    def grab(self) -> bool:
        """Przechwyć klawiaturę. Zwraca True jeśli się udało."""
        if not sys.platform.startswith("linux"):
            return False
        mods = _try_import_xlib()
        if mods is None:
            return False
        V_mod, display_mod, XK_mod = mods
        try:
            self._V = V_mod
            self._XK = XK_mod
            self._disp = display_mod.Display()
            self._root = self._disp.screen().root
            # 8 modyfikatorów: Shift, Lock, Control, Mod1-Mod5
            self._mods = [
                0,  # brak modyfikatora
                V_mod.ShiftMask, V_mod.LockMask, V_mod.ControlMask,
                V_mod.Mod1Mask, V_mod.Mod2Mask, V_mod.Mod3Mask,
                V_mod.Mod4Mask, V_mod.Mod5Mask,
                # Kombinacje z Control (dla skrótów Ctrl+...)
                V_mod.ControlMask | V_mod.Mod1Mask,
                V_mod.ControlMask | V_mod.ShiftMask,
                V_mod.ControlMask | V_mod.Mod4Mask,
                V_mod.Mod4Mask | V_mod.ShiftMask,  # Super+Shift
                V_mod.Mod4Mask | V_mod.Mod1Mask,   # Super+Alt
            ]
            # Pobierz range keycode'ów (zwykle 8-255)
            min_key = self._disp.display.info.min_keycode
            max_key = self._disp.display.info.max_keycode
            grabbed_count = 0
            for keycode in range(min_key, max_key + 1):
                for mod in self._mods:
                    try:
                        self._root.grab_key(keycode, mod,
                                            True,
                                            V_mod.GrabModeAsync,
                                            V_mod.GrabModeAsync)
                        grabbed_count += 1
                    except Exception:
                        pass   # niektóre kombinacje mogą być już zajęte
            self._grabbed = True
            log.debug("X11 keyboard grab: %d key/mod combos", grabbed_count)
            return True
        except Exception:
            log.debug("X11 keyboard grab failed", exc_info=True)
            self.release()
            return False

    def release(self) -> None:
        """Zwolnij wszystkie grab'y i zamknij połączenie."""
        if self._disp is None or self._root is None:
            self._disp = None
            self._root = None
            self._grabbed = False
            return
        try:
            if self._grabbed and hasattr(self._root, "ungrab_key"):
                V = getattr(self, "_V", None)
                if V is not None:
                    min_key = self._disp.display.info.min_keycode
                    max_key = self._disp.display.info.max_keycode
                    for keycode in range(min_key, max_key + 1):
                        for mod in self._mods:
                            try:
                                self._root.ungrab_key(keycode, mod)
                            except Exception:
                                pass
            self._disp.flush()
            self._disp.close()
        except Exception:
            log.debug("X11 keyboard ungrab failed", exc_info=True)
        finally:
            self._disp = None
            self._root = None
            self._grabbed = False
            self._mods = []
