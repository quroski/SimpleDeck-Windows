"""Window Detector - wykrywanie aktywnej aplikacji (Foreground Window).

Backendi:
  - Windows: GetForegroundWindow + GetWindowText + QueryFullProcessImageName
  - Linux X11:  EWMH _NET_ACTIVE_WINDOW przez python-xlib
  - Linux Wayland: best-effort - brak uniwersalnego API (wymaga wtyczki per compositor)

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
        """Zwraca (process_name, window_title) w jednym wywołaniu.

        V6: Domyślnie woła obie metody osobno, ale backendy które potrafią
        (np. LinuxX11Backend) nadpisują to by uniknąć podwójnego RPC.
        """
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
# Linux X11 (EWMH)
# ============================================================
class LinuxX11Backend(WindowDetectorBackend):
    """Backend Linux/X11 przez python-xlib (EWMH _NET_ACTIVE_WINDOW).

    V6: Atomy są cache'owane w ``__init__`` (intern_atom to RPC do serwera X —
    dawniej wołane 4× na każdą sekundę). ``active_window_info`` łączy proc +
    title w jedno zapytanie (dawniej 2× X round-trip / s).
    """

    def __init__(self) -> None:
        # V7: ``from Xlib.display import Display`` odroczone do _ensure_display
        # — oszczędność ~30-60 ms cold-start + ~1 MB RSS do pierwszego pollingu.
        # Aplikacja bez auto-switch profili (większość userów) nie ładuje Xlib
        # w ogóle (window_det.start() jest pominięte gdy brak reguł).
        self._display = None
        self._root = None
        self._atom_active = None
        self._atom_pid = None
        self._atom_name = None
        self._atom_utf8 = None
        # V7: Cache po wid — gdy aktywne okno się nie zmieni, pomiń rund-trip
        # o PID i odczyt /proc. Steady-state: 3 X round-trips/s → 1/s.
        self._last_wid: Optional[int] = None
        self._last_pid: Optional[int] = None
        self._last_proc: str = ""
        self._last_title: str = ""
        self._init_failed = False

    def _ensure_display(self):
        """V7: Leniwa inicjalizacja połączenia X11 + cache atomów.

        Pierwsze wywołanie importuje python-xlib i otwiera socket do serwera X.
        Kolejne wywołania zwracają cached obiekt. Błędy (X unavailable na
        natywnym Wayland) ustawiają ``_init_failed`` i backend staje się no-op.
        """
        if self._init_failed:
            return None
        if self._display is None:
            try:
                from Xlib.display import Display
                self._display = Display()
                self._root = self._display.screen().root
                # V6: Cache atomów — nigdy się nie zmieniają, a intern_atom to RPC.
                self._atom_active = self._display.intern_atom("_NET_ACTIVE_WINDOW")
                self._atom_pid = self._display.intern_atom("_NET_WM_PID")
                self._atom_name = self._display.intern_atom("_NET_WM_NAME")
                self._atom_utf8 = self._display.intern_atom("UTF8_STRING")
            except Exception:
                log.warning("Xlib unavailable — LinuxX11Backend becomes no-op")
                self._init_failed = True
                return None
        return self._display

    def _active_window(self):
        if self._ensure_display() is None:
            return None
        try:
            reply = self._root.get_full_property(self._atom_active, 0)
            if not reply or not reply.value:
                return None
            wid = reply.value[0]
            win = self._display.create_resource_object("window", wid)

            # V7: Krótki obieg gdy to samo okno — oszczędza 2 RPC (PID + /proc).
            if wid == self._last_wid and self._last_pid is not None:
                return (win, self._last_pid, self._last_proc, self._last_title, True)

            # PID
            pid_reply = win.get_full_property(self._atom_pid, 0)
            pid = pid_reply.value[0] if pid_reply and pid_reply.value else None
            return (win, pid, None, None, False)
        except Exception:
            log.exception("X11 active window query failed")
            return None

    def active_process_name(self) -> str:
        info = self._active_window()
        if not info:
            return ""
        _, pid, cached_proc, _, is_cached = info
        if is_cached:
            return cached_proc
        if not pid:
            return ""
        try:
            with open(f"/proc/{pid}/comm", "r") as f:
                return f.read().strip().lower()
        except (FileNotFoundError, ProcessLookupError):
            return ""

    def active_window_title(self) -> str:
        info = self._active_window()
        if not info:
            return ""
        win, _, _, cached_title, is_cached = info
        if is_cached:
            return cached_title
        try:
            r = win.get_full_property(self._atom_name, self._atom_utf8)
            return r.value.decode("utf-8", errors="replace") if r and r.value else ""
        except Exception:
            return ""

    def active_window_info(self) -> tuple[str, str]:
        """V6: Jeden X round-trip zamiast dwóch — proc + title razem.

        V7: Gdy to samo okno co poprzednio — zwróć zcache'owane wartości
        bez dodatkowych RPC. Steady-state z 3 RPC/s na 1 RPC/s.
        """
        info = self._active_window()
        if not info:
            self._last_wid = None
            return ("", "")
        win, pid, cached_proc, cached_title, is_cached = info
        if is_cached:
            return (cached_proc or "", cached_title or "")

        # Proces
        proc = ""
        if pid:
            try:
                with open(f"/proc/{pid}/comm", "r") as f:
                    proc = f.read().strip().lower()
            except (FileNotFoundError, ProcessLookupError):
                pass
        # Tytuł
        title = ""
        try:
            r = win.get_full_property(self._atom_name, self._atom_utf8)
            title = r.value.decode("utf-8", errors="replace") if r and r.value else ""
        except Exception:
            pass

        # V7: Zaktualizuj cache
        try:
            self._last_wid = int(win.id)
        except Exception:
            pass
        self._last_pid = pid
        self._last_proc = proc
        self._last_title = title
        return (proc, title)


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
        elif sys.platform.startswith("linux"):
            # Spróbuj X11 - LinuxX11Backend.__init__ sam rzuci wyjątek jeśli
            # X11 niedostępny (Wayland bez XWayland) - złapane przez outer try/except
            return LinuxX11Backend()
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
            # V6: Jeden call do backendu (zamiast dwóch) gdy wspiera
            # active_window_info — LinuxX11Backend robi 1 X round-trip.
            proc, title = self._backend.active_window_info()
            if proc != self._last_proc or title != self._last_title:
                self._last_proc = proc
                self._last_title = title
                self.active_app_changed.emit(proc, title)
        except Exception:
            log.exception("window detector poll failed")
