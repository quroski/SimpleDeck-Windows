"""Hotkey dispatcher - wykonuje akcje zdefiniowane w profilu.

Reaguje na zdarzenia z MCU (button_event) i wykonuje powiązane akcje:
  - HOTKEY: symulacja sekwencji klawiszy przez platform.hotkey.simulate()
  - TOGGLE_MUTE: wycisz/odmutuj audio przez platform.audio
  - RUN_COMMAND: uruchom proces systemowy

V6: HOTKEY akcje są uruchamiane w QThreadPool (off-main-thread) — eliminuje
do 2 s UI stall gdy ``wtype``/``ydotool`` jest powolny. Toast info o sukcesie
i toast warning o błędzie są emitowane po powrocie do głównego wątku.
"""
from __future__ import annotations

import logging
import subprocess
from typing import Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Slot

from .event_bus import EventBus
from .profile import ButtonAction, Profile
from ..platform.hotkey import HotkeyBackend

log = logging.getLogger(__name__)


class _HotkeyJob(QRunnable):
    """QRunnable wołający ``simulate_combo`` w wątku puli.

    Wynik (bool) przekazywany z powrotem przez signal QObject (auto-queued).
    """

    class _SignalsBridge(QObject):
        from PySide6.QtCore import Signal
        done = Signal(bool, str, bool)  # (success, combo, is_press)

    def __init__(self, hotkey_backend: HotkeyBackend, combo: str,
                 is_press: bool, dispatcher: "HotkeyDispatcher"):
        super().__init__()
        self._backend = hotkey_backend
        self._combo = combo
        self._is_press = is_press
        self._dispatcher = dispatcher
        self._signals = self._SignalsBridge()
        self._signals.done.connect(dispatcher._on_hotkey_done)

    @Slot()
    def run(self) -> None:
        try:
            ok = self._backend.simulate_combo(self._combo)
        except Exception:
            log.exception("hotkey job failed: %s", self._combo)
            ok = False
        self._signals.done.emit(bool(ok), self._combo, self._is_press)


class HotkeyDispatcher(QObject):
    """Słucha button_event z EventBus i wykonuje akcje profilu."""

    def __init__(self, bus: EventBus, hotkey_backend: HotkeyBackend,
                 audio_backend=None, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._bus = bus
        self._hotkey = hotkey_backend
        self._audio = audio_backend
        self._profile: Optional[Profile] = None
        self._thread_pool = QThreadPool.globalInstance()

        # Subskrybuj zdarzenia przycisków
        bus.button_event.connect(self._on_button)

    def set_profile(self, profile: Profile) -> None:
        self._profile = profile

    @Slot(int, bool)
    def _on_button(self, idx: int, pressed: bool) -> None:
        if self._profile is None or idx < 0 or idx >= len(self._profile.buttons):
            return
        cfg = self._profile.buttons[idx]
        if cfg.on_press != pressed:
            return

        trigger = "press" if pressed else "release"
        try:
            ok = self._dispatch(cfg, idx, trigger)
            # V4: Toast daje użytkownikowi wizualne info KIEDY akcja odpaliła
            # (press vs release) — pomaga zauważyć różnicę gdy checkbox
            # „Reaguj przy wciśnięciu" jest odznaczony.
            # Uwaga: dla HOTKEY, toast jest emitowany przez _on_hotkey_done
            # (async) — nie tutaj.
            if cfg.action != ButtonAction.HOTKEY and cfg.action != ButtonAction.NONE:
                if ok:
                    self._bus.notify.emit(
                        "info",
                        f"BTN {idx + 1} → {cfg.action.value} ({trigger})")
        except Exception:
            log.exception("button %d action failed", idx)

    def _dispatch(self, cfg, idx: int, trigger: str = "press") -> bool:
        """Wykonaj akcję przycisku. Zwraca True jeśli akcja zadziałała."""
        action = cfg.action
        if action == ButtonAction.HOTKEY:
            if not cfg.hotkey:
                log.debug("hotkey empty, skipping")
                self._bus.notify.emit(
                    "warning",
                    "Skrót klawiszowy nie jest ustawiony — przypisz kombinację.")
                return False
            log.debug("hotkey: %s", cfg.hotkey)
            # V6: Uruchom w wątku puli — ``simulate_combo`` robi subprocess.run
            # z timeout=2s; bez tego główny wątek Qt by stall'ował.
            job = _HotkeyJob(self._hotkey, cfg.hotkey,
                             trigger == "press", self)
            self._thread_pool.start(job)
            return True  # wynik przyjdzie async w _on_hotkey_done
        elif action == ButtonAction.TOGGLE_MUTE:
            if self._audio is not None:
                log.debug("toggle mute: target=%s", cfg.target or "<system>")
                self._audio.toggle_mute(cfg.target or None)
                return True
            self._bus.notify.emit("warning", "Brak backendu audio — wyciszanie niedostępne.")
            return False
        elif action == ButtonAction.RUN_COMMAND:
            cmd = cfg.target.strip() if cfg.target else ""
            if cmd:
                log.debug("run: %s", cmd)
                try:
                    subprocess.Popen(cmd, shell=True,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return True
                except Exception:
                    log.exception("run_command failed: %s", cmd)
                    self._bus.notify.emit("warning", f"Nie udało się uruchomić: {cmd}")
                    return False
            self._bus.notify.emit("warning", "Polecenie nie jest ustawione.")
            return False
        elif action == ButtonAction.PASTE_TEXT:
            text = cfg.target if cfg.target else ""
            if not text.strip():
                self._bus.notify.emit("warning", "Tekst do wklejenia nie jest ustawiony.")
                return False
            try:
                self._set_clipboard(text)
                import time
                time.sleep(0.05)
                self._hotkey.simulate_combo("Ctrl+V")
                if getattr(cfg, "paste_enter", False):
                    time.sleep(0.05)
                    self._hotkey.simulate_combo("Enter")
                return True
            except Exception:
                log.exception("paste_text failed")
                self._bus.notify.emit("warning", "Nie udało się wkleić tekstu.")
                return False
        elif action == ButtonAction.NONE:
            return True
        log.debug("unhandled button action: %s", action)
        return False

    @Slot(bool, str, bool)
    def _on_hotkey_done(self, success: bool, combo: str, is_press: bool) -> None:
        """Callback z QThreadPool po wykonaniu ``simulate_combo``."""
        trigger = "press" if is_press else "release"
        if success:
            self._bus.notify.emit(
                "info",
                f"→ {combo} ({trigger})")
        else:
            self._bus.notify.emit(
                "warning",
                f"Skrót nie zadziałał: „{combo}".rstrip() + "”. "
                "Sprawdź czy wtype/ydotool są zainstalowane (dnf install wtype).")

    @staticmethod
    def _set_clipboard(text: str) -> None:
        """Ustaw tekst w schowku systemowym (Windows ctypes / Linux xclip)."""
        import sys as _sys
        if _sys.platform.startswith("win"):
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            CF_UNICODETEXT = 13
            GMEM_MOVEABLE = 0x0002
            user32.OpenClipboard.argtypes = [wintypes.HWND]
            user32.OpenClipboard.restype = wintypes.BOOL
            user32.EmptyClipboard.restype = wintypes.BOOL
            user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
            user32.SetClipboardData.restype = wintypes.HANDLE
            user32.CloseClipboard.restype = wintypes.BOOL
            kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
            kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
            kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
            kernel32.GlobalLock.restype = ctypes.c_void_p
            kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
            kernel32.GlobalUnlock.restype = wintypes.BOOL
            data = text + "\0"
            buf = data.encode("utf-16-le")
            h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(buf))
            if not h:
                raise ctypes.WinError(ctypes.get_last_error())
            ptr = kernel32.GlobalLock(h)
            if not ptr:
                raise ctypes.WinError(ctypes.get_last_error())
            ctypes.memmove(ptr, buf, len(buf))
            kernel32.GlobalUnlock(h)
            if not user32.OpenClipboard(None):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                user32.EmptyClipboard()
                user32.SetClipboardData(CF_UNICODETEXT, h)
            finally:
                user32.CloseClipboard()
        else:
            import subprocess
            subprocess.run(["xclip", "-selection", "clipboard"],
                           input=text.encode("utf-8"), check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
