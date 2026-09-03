"""Hotkey backend - symulacja wciśnięć klawiszy.

Backendi (auto-detected):
  - Windows: SendInput przez ctypes (user32)
  - Linux/Wayland: wtype (Wayland-native virtual keyboard)
  - Linux/any: ydotool (działa wszędzie przez uinput, wymaga ydotoold)
  - Linux/X11: xdotool (X11-only, nie działa na Wayland)

Format "combo string":
    "Ctrl+Shift+D"     → wciska Ctrl, Shift, D, pakuSC-EE, D ↑, Shift ↑, Ctrl ↑
    "MediaPlay"        → specjalne klawisze multimedialne
    "F5"               → klawisz funkcyjny

V4: ``simulate_combo`` zwraca ``bool`` (True = wstrzyknięcie zlecone, False =
brak backendu / nieznany klawisz / błąd). Błędy są logowane ze stderr zamiast
trafiać do /dev/null. ``LinuxAutoBackend`` pomija xdotool gdy
``$XDG_SESSION_TYPE == "wayland"`` (bo i tak nie zadziała).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from abc import ABC, abstractmethod
from typing import Optional

log = logging.getLogger(__name__)


class HotkeyBackend(ABC):
    """Abstrakcyjny backend symulacji klawiszy.

    Wszystkie implementacje ``simulate_combo`` zwracają ``bool``:
      True  = wstrzyknięcie zlecone pomyślnie,
      False = backend niedostępny, nieznany klawisz, lub błąd wykonania
              (stderr jest wtedy logowany na poziomie WARNING).
    """

    @abstractmethod
    def simulate_combo(self, combo: str) -> bool:
        ...

    @abstractmethod
    def available(self) -> bool:
        ...


class NullHotkeyBackend(HotkeyBackend):
    def simulate_combo(self, combo: str) -> bool:
        log.debug("[null-hotkey] would send: %s", combo)
        return False
    def available(self) -> bool: return False


# ============================================================
# Windows: SendInput
# ============================================================
class WindowsHotkeyBackend(HotkeyBackend):
    VK_MAP = {
        "ctrl": 0x11, "shift": 0x10, "alt": 0x12, "menu": 0x12,
        "win": 0x5B, "super": 0x5B, "meta": 0x5B,
        "tab": 0x09, "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B,
        "backspace": 0x08, "delete": 0x2E, "insert": 0x2D,
        "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
        "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
        "space": 0x20,
        "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
        "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79,
        "f11": 0x7A, "f12": 0x7B,
        "mediaplay": 0xB3, "mediapause": 0xB3,
        "medianext": 0xB0, "mediaprev": 0xB1,
        "volup": 0xAF, "voldown": 0xAE, "volmute": 0xAD,
    }

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes
        self._ctypes = ctypes
        self._wintypes = wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._user32 = user32

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]
        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_void_p)]
        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                        ("wParamH", wintypes.WORD)]
        class INPUT_UNION(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]
        class INPUT(ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", wintypes.DWORD), ("u", INPUT_UNION)]

        self._INPUT = INPUT
        user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), wintypes.INT]
        user32.SendInput.restype = wintypes.UINT
        user32.VkKeyScanW.argtypes = [wintypes.WCHAR]
        user32.VkKeyScanW.restype = wintypes.SHORT
        self._KEYEVENTF_KEYUP = 0x0002
        self._KEYEVENTF_UNICODE = 0x0004
        self._INPUT_KEYBOARD = 1

    def _vk_for(self, name: str) -> Optional[int]:
        n = name.lower()
        if n in self.VK_MAP:
            return self.VK_MAP[n]
        if len(name) == 1:
            code = self._user32.VkKeyScanW(name)
            if code != -1:
                return code & 0xFF
        return None

    def _send(self, vk: int, up: bool = False) -> None:
        flags = self._KEYEVENTF_KEYUP if up else 0
        inp = self._INPUT()
        inp.type = self._INPUT_KEYBOARD
        inp.ki = (vk, 0, flags, 0, None)  # type: ignore
        arr = (self._INPUT * 1)(inp)
        self._user32.SendInput(1, arr, self._ctypes.sizeof(self._INPUT))

    def simulate_combo(self, combo: str) -> bool:
        tokens = [t.strip() for t in combo.split("+") if t.strip()]
        if not tokens:
            return False
        vks = []
        for t in tokens:
            vk = self._vk_for(t)
            if vk is None:
                log.warning("unknown key: %s", t)
                return False
            vks.append(vk)
        for vk in vks: self._send(vk, up=False)
        for vk in reversed(vks): self._send(vk, up=True)
        return True

    def available(self) -> bool: return True


# ============================================================
# Shared token → evdev/wtype key name mapping
# ============================================================

_EVDEV_CODES = {
    "ctrl": 29, "control": 29,
    "shift": 42,
    "alt": 56,
    "super": 125, "win": 125, "meta": 125, "cmd": 125,
    "tab": 15, "enter": 28, "return": 28, "esc": 1, "escape": 1,
    "backspace": 14, "delete": 111, "insert": 110,
    "home": 102, "end": 107, "pageup": 104, "pagedown": 109,
    "up": 103, "down": 108, "left": 105, "right": 106,
    "space": 57,
    "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63,
    "f6": 64, "f7": 65, "f8": 66, "f9": 67, "f10": 68,
    "f11": 87, "f12": 88,
    "mediaplay": 164, "mediapause": 164, "mediastop": 166,
    "medianext": 163, "mediaprev": 165,
    "volup": 115, "voldown": 114, "volmute": 113,
}
for _c, _v in zip("abcdefghijklmnopqrstuvwxyz", [30,48,46,32,18,33,34,35,23,36,37,38,50,49,24,25,16,19,31,20,22,47,17,45,21,44]):
    _EVDEV_CODES[_c] = _v
for _i, _d in enumerate("0123456789"):
    _EVDEV_CODES[_d] = 11 + _i if _d != "0" else 11

_WTYPE_NAMES = {
    "ctrl": "ctrl", "control": "ctrl",
    "shift": "shift",
    "alt": "alt",
    "super": "super", "win": "super", "meta": "super", "cmd": "super",
    "tab": "tab", "enter": "enter", "return": "enter",
    "esc": "escape", "escape": "escape",
    "backspace": "backspace", "delete": "delete", "insert": "insert",
    "home": "home", "end": "end",
    "pageup": "pageup", "pagedown": "pagedown",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "space": "space",
    "mediaplay": "XF86AudioPlay", "mediapause": "XF86AudioPause",
    "mediastop": "XF86AudioStop",
    "medianext": "XF86AudioNext", "mediaprev": "XF86AudioPrev",
    "volup": "XF86AudioRaiseVolume",
    "voldown": "XF86AudioLowerVolume", "volmute": "XF86AudioMute",
}


def _token_to_evdev(token: str) -> Optional[int]:
    t = token.lower()
    if t in _EVDEV_CODES:
        return _EVDEV_CODES[t]
    if len(token) == 1 and token.upper() in _EVDEV_CODES:
        return _EVDEV_CODES[token.upper()]
    return None


def _token_to_wtype(token: str) -> str:
    t = token.lower()
    if t in _WTYPE_NAMES:
        return _WTYPE_NAMES[t]
    if len(token) == 1:
        return token.lower()
    if t[0] == "f" and t[1:].isdigit():
        return token.upper()
    return token


# ============================================================
# Linux: wtype (Wayland-native virtual keyboard)
# ============================================================
class LinuxWtypeBackend(HotkeyBackend):
    """wtype — Wayland-native virtual keyboard via ext-keyboard-unstable-v1.

    Działa na większości kompozytorów Wayland (Sway, GNOME, KDE, wlroots).
    Wymaga zainstalowanego 'wtype' w PATH.
    """

    def __init__(self) -> None:
        self._have = shutil.which("wtype") is not None

    def available(self) -> bool: return self._have

    def simulate_combo(self, combo: str) -> bool:
        if not self._have:
            log.warning("wtype not installed - cannot simulate: %s", combo)
            return False
        tokens = [t.strip() for t in combo.split("+") if t.strip()]
        if not tokens:
            return False
        mods = [_token_to_wtype(t) for t in tokens[:-1]]
        key = _token_to_wtype(tokens[-1])
        args = ["wtype"]
        for m in mods:
            args += ["-M", m]
        args += ["-k", key]
        for m in reversed(mods):
            args += ["-m", m]
        return _run_capture(args, "wtype")


# ============================================================
# Linux: ydotool (uinput-based, works on both X11 and Wayland)
# ============================================================
class LinuxYdotoolBackend(HotkeyBackend):
    """ydotool — input injection przez /dev/uinput (evdev).

    Działa na X11 i Wayland, ale wymaga:
      1. 'ydotool' w PATH
      2. 'ydotoold' daemon uruchomiony
      3. Uprawnienia do /dev/uinput (lub grupa 'input')
    """

    def __init__(self) -> None:
        self._have = shutil.which("ydotool") is not None

    def available(self) -> bool: return self._have

    def simulate_combo(self, combo: str) -> bool:
        if not self._have:
            log.warning("ydotool not installed - cannot simulate: %s", combo)
            return False
        tokens = [t.strip() for t in combo.split("+") if t.strip()]
        if not tokens:
            return False
        codes = []
        for t in tokens:
            c = _token_to_evdev(t)
            if c is None:
                log.warning("ydotool: unknown key '%s' in combo '%s'", t, combo)
                return False
            codes.append(c)
        args = ["ydotool", "key"]
        for c in codes:
            args.append(f"{c}:1")
        for c in reversed(codes):
            args.append(f"{c}:0")
        return _run_capture(args, "ydotool")


# ============================================================
# Linux: xdotool (X11-only, nie działa na natywnym Wayland)
# ============================================================
class LinuxXdotoolBackend(HotkeyBackend):
    """xdotool — klasyczny backend X11. Nie działa na natywnym Wayland."""

    def __init__(self) -> None:
        self._have = shutil.which("xdotool") is not None

    def available(self) -> bool: return self._have

    def simulate_combo(self, combo: str) -> bool:
        if not self._have:
            log.warning("xdotool not installed - cannot simulate: %s", combo)
            return False
        tokens = [t.strip() for t in combo.split("+") if t.strip()]
        if not tokens:
            return False
        arg = "+".join(t if len(t) == 1 else t.lower() for t in tokens)
        return _run_capture(["xdotool", "key", "--clearmodifiers", arg], "xdotool")


# ============================================================
# Shared subprocess runner — captures stderr for diagnostics
# ============================================================
def _run_capture(args: list[str], tag: str, timeout: float = 2.0) -> bool:
    """Uruchom komendę, przechwyć stdout/stderr, zwróć True przy sukcesie.

    ``subprocess.run`` zamiast ``Popen``: krótki timeout (2 s) i capture_output
    dają nam diagnostykę gdy backend istnieje w PATH ale np. compositor
    odrzuca protokół. Wcześniej stderr trafiał do /dev/null i błąd był
    niewidoczny. Błędy są logowane z tagiem backendu.
    """
    try:
        cp = subprocess.run(args, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        log.warning("%s binary vanished mid-run", tag)
        return False
    except subprocess.TimeoutExpired:
        log.warning("%s timed out after %.1fs: %s", tag, timeout, " ".join(args))
        return False
    except Exception:
        log.exception("%s unexpected error", tag)
        return False
    if cp.returncode != 0:
        err = cp.stderr.decode("utf-8", "replace").strip()
        log.warning("%s exited %d: %s", tag, cp.returncode, err or "(no stderr)")
        return False
    return True


# ============================================================
# Auto-detect wrapper (probes backends lazily)
# ============================================================
class LinuxAutoBackend(HotkeyBackend):
    """Auto-detekcja najlepszego dostępnego backendu na Linuksie.

    Kolejność preferencji:
      1. wtype (Wayland-native, najprostszy)
      2. ydotool (works everywhere via uinput)
      3. xdotool (X11-only fallback)

    V4: Na natywnym Wayland (``$XDG_SESSION_TYPE == "wayland"``) xdotool jest
    celowo pomijany — to narzędzie X11, które na Wayland kompiluje się i startuje
    ale nie ma jak dotrzeć do compositora. Wcześniej auto-detekcja wybierała
    xdotool gdy brak wtype/ydotool, simulate_combo wołało binarkę,
    ``returncode != 0`` trafiał do /dev/null i użytkownik widział ciszę.

    Po pierwszym udanym simulate_combo, zapamiętuje działający backend.
    """

    def __init__(self) -> None:
        self._wtype = LinuxWtypeBackend()
        self._ydotool = LinuxYdotoolBackend()
        self._xdotool = LinuxXdotoolBackend()
        self._preferred: Optional[HotkeyBackend] = None
        self._warned = False

    def _candidates(self) -> list[HotkeyBackend]:
        """Lista backendów do sprawdzenia, uwzględniająca typ sesji."""
        session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
        on_wayland = session_type == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))
        if on_wayland:
            # xdotool jest narzędziem X11 — na Wayland nie działa.
            return [self._wtype, self._ydotool]
        return [self._wtype, self._ydotool, self._xdotool]

    def _pick(self) -> Optional[HotkeyBackend]:
        if self._preferred is not None:
            return self._preferred
        for backend in self._candidates():
            if backend.available():
                self._preferred = backend
                log.info("hotkey backend: %s", type(backend).__name__)
                return backend
        return None

    def simulate_combo(self, combo: str) -> bool:
        backend = self._pick()
        if backend is not None:
            return backend.simulate_combo(combo)
        if not self._warned:
            self._warned = True
            log.error(
                "No hotkey backend available! Install one of: "
                "wtype (dnf install wtype), "
                "ydotool (dnf install ydotool), "
                "xdotool (dnf install xdotool)")
        return False

    def available(self) -> bool:
        return self._pick() is not None

    def backend_name(self) -> str:
        b = self._pick()
        return type(b).__name__ if b else "none"


# ============================================================
# Fabryka
# ============================================================
def make_hotkey_backend() -> HotkeyBackend:
    try:
        if sys.platform.startswith("win"):
            return WindowsHotkeyBackend()
        elif sys.platform.startswith("linux"):
            return LinuxAutoBackend()
        else:
            return NullHotkeyBackend()
    except Exception:
        log.exception("hotkey backend creation failed")
        return NullHotkeyBackend()
