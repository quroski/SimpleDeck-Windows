"""Hotkey backend - symulacja wciśnięć klawiszy.

Backend (Windows-only build):
  - Windows: SendInput przez ctypes (user32)

Format "combo string":
    "Ctrl+Shift+D"     → wciska Ctrl, Shift, D, pakuSC-EE, D ↑, Shift ↑, Ctrl ↑
    "MediaPlay"        → specjalne klawisze multimedialne
    "F5"               → klawisz funkcyjny

V4: ``simulate_combo`` zwraca ``bool`` (True = wstrzyknięcie zlecone, False =
brak backendu / nieznany klawisz / błąd).
"""
from __future__ import annotations

import logging
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

    def backend_name(self) -> str:
        """Nazwa backendu (do logów). Domyślnie nazwa klasy."""
        return type(self).__name__


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
        # V1.0.5: rozszerzone klawisze funkcyjne (poza normalnym zakresem)
        "f13": 0x7C, "f14": 0x7D, "f15": 0x7E, "f16": 0x7F,
        "f17": 0x80, "f18": 0x81, "f19": 0x82, "f20": 0x83,
        "f21": 0x84, "f22": 0x85, "f23": 0x86, "f24": 0x87,
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
# Fabryka
# ============================================================
def make_hotkey_backend() -> HotkeyBackend:
    try:
        if sys.platform.startswith("win"):
            return WindowsHotkeyBackend()
        else:
            return NullHotkeyBackend()
    except Exception:
        log.exception("hotkey backend creation failed")
        return NullHotkeyBackend()
