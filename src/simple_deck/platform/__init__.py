"""Abstrakcje platformowe.

Moduł dostarcza backendy (Windows-only build):
  - audio          : Windows WASAPI (pycaw)
  - hotkey         : Windows SendInput
  - window_detector: Windows GetForegroundWindow

Każdy backend ma klasę ``Null*`` jako fallback gdy zależności nie są dostępne.
Funkcje fabryki (``make_*()``) zawsze zwracają działający obiekt.
"""
from .audio import AudioBackend, NullAudioBackend, make_audio_backend
from .hotkey import HotkeyBackend, NullHotkeyBackend, make_hotkey_backend
from .window_detector import (NullBackend, WindowDetector,
                              WindowDetectorBackend, WindowsBackend, make_backend)

__all__ = [
    "AudioBackend", "NullAudioBackend", "make_audio_backend",
    "HotkeyBackend", "NullHotkeyBackend", "make_hotkey_backend",
    "WindowDetectorBackend", "WindowDetector", "NullBackend",
    "WindowsBackend", "make_backend",
]
