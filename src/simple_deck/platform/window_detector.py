"""Window Detector - wykrywanie aktywnej aplikacji (Foreground Window).

Backend (Windows-only build):
  - Windows: GetForegroundWindow + GetWindowText + QueryFullProcessImageName

Jeśli backend nie jest dostępny, działa fallback "no-op" (zawsze zwraca pusty
string) - aplikacja działa, ale auto-switch profili nie.
"""
from __future__ import annotations

import logging
import sys
from abc import ABC, abstractmethod
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal

log = logging.getLogger(__name__)


class WindowDetectorBackend(ABC):
    """Abstrakcyjny backend detekcji aktywnej aplikacji."""

    @abstractmethod
    def active_process_name(self) -> str:
        """Zwraca nazwę procesu aktywnej aplikacji (np. 'discord') lub ''."""
        ...

    @abstractmethod
    def active_window_title(self) -> str:
        """Zwraca tytuł okna aktywnej aplikacji lub ''."""
        ...

    def active_window_info(self) -> tuple[str, str]:
        """Zwraca (process_name, window_title) w jednym wywołaniu."""
        return (self.active_process_name(), self.active_window_title())


class NullBackend(WindowDetectorBackend):
    """No-op - gdy platforma nie wspiera detekcji."""
    def active_process_name(self) -> str: return ""
    def active_window_title(self) -> str: return ""
    def active_window_info(self) -> tuple[str, str]: return ("", "")


# ============================================================
# Windows
# ============================================================
class WindowsBackend(WindowDetectorBackend):
    """Implementacja Windows API przez ctypes (bez jawnej zależności)."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes
        self._ctypes = ctypes
        self._wintypes = wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._user32 = user32
        self._kernel32 = kernel32

        # Setup signatures
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD

        # QueryFullProcessImageNameW needs PROCESS_QUERY_LIMITED_INFORMATION
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

    def active_process_name(self) -> str:
        hwnd = self._user32.GetForegroundWindow()
        if not hwnd:
            return ""
        pid = self._wintypes.DWORD(0)
        self._user32.GetWindowThreadProcessId(hwnd, self._ctypes.byref(pid))
        if not pid.value:
            return ""
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = self._kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not h:
            return ""
        try:
            buf = self._ctypes.create_unicode_buffer(1024)
            size = self._wintypes.DWORD(1024)
            if self._kernel32.QueryFullProcessImageNameW(
                    h, 0, buf, self._ctypes.byref(size)):
                import os
                return os.path.basename(buf.value).lower()
        finally:
            self._kernel32.CloseHandle(h)
        return ""

    def active_window_title(self) -> str:
        hwnd = self._user32.GetForegroundWindow()
        if not hwnd:
            return ""
        buf = self._ctypes.create_unicode_buffer(512)
        n = self._user32.GetWindowTextW(hwnd, buf, 512)
        return buf.value[:n] if n else ""


# ============================================================
# Fabryka
# ============================================================
def make_backend() -> WindowDetectorBackend:
    """Tworzy odpowiedni backend dla bieżącej platformy.

    Zwraca NullBackend jeśli żaden nie jest dostępny - aplikacja nadal działa.
    """
    try:
        if sys.platform.startswith("win"):
            return WindowsBackend()
        else:
            log.warning("unsupported platform: %s", sys.platform)
            return NullBackend()
    except Exception:
        log.warning("window detector backend unavailable - using Null")
        return NullBackend()


# ============================================================
# QTimer-driven poller
# ============================================================
class WindowDetector(QObject):
    """Cyklicznie pyta backend o aktywne okno i emituje sygnał przy zmianie.

    Polling co ~1 s - tanie CPU.

    V7: ``set_idle(True)`` zwalnia interwał do ``IDLE_INTERVAL_MS`` (3 s)
    gdy okno aplikacji jest ukryte (tray). Auto-switch profili nadal działa
    ale z większym opóźnieniem — akceptowalne bo użytkownik nie patrzy.
    """
    active_app_changed = Signal(str, str)  # (process_name, window_title)

    POLL_INTERVAL_MS = 1000
    IDLE_INTERVAL_MS = 5000  # V8: Increased from 3000 - less CPU when window hidden (tray)

    def __init__(self, backend: Optional[WindowDetectorBackend] = None,
                 parent=None):
        super().__init__(parent)
        self._backend = backend or make_backend()
        self._last_proc = ""
        self._last_title = ""
        self._idle = False
        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def set_idle(self, idle: bool) -> None:
        """V7: Zmień interwał pollingu gdy okno jest ukryte (tray)."""
        idle = bool(idle)
        if idle == self._idle:
            return
        self._idle = idle
        was_active = self._timer.isActive()
        new_interval = self.IDLE_INTERVAL_MS if idle else self.POLL_INTERVAL_MS
        self._timer.setInterval(new_interval)
        if was_active:
            self._timer.start()  # restart z nowym interwałem

    def _poll(self) -> None:
        try:
            proc, title = self._backend.active_window_info()
            if proc != self._last_proc or title != self._last_title:
                self._last_proc = proc
                self._last_title = title
                self.active_app_changed.emit(proc, title)
        except Exception:
            log.exception("window detector poll failed")
