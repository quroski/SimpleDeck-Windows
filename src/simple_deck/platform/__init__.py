"""Abstrakcje platformowe.

Moduł dostarcza backendy:
  - audio          : Windows WASAPI / Linux PulseAudio-PipeWire
  - hotkey         : Windows SendInput / Linux xdotool
  - window_detector: Windows GetForegroundWindow / Linux X11 EWMH

Każdy backend ma klasę ``Null*`` jako fallback gdy zależności nie są dostępne.
Funkcje fabryki (``make_*()``) zawsze zwracają działający obiekt.
"""
from .audio import AudioBackend, NullAudioBackend, make_audio_backend
from .hotkey import HotkeyBackend, NullHotkeyBackend, make_hotkey_backend
from .window_detector import (LinuxX11Backend, NullBackend, WindowDetector,
                              WindowDetectorBackend, WindowsBackend, make_backend)

__all__ = [
    "AudioBackend", "NullAudioBackend", "make_audio_backend",
    "HotkeyBackend", "NullHotkeyBackend", "make_hotkey_backend",
    "WindowDetectorBackend", "WindowDetector", "NullBackend",
    "WindowsBackend", "LinuxX11Backend", "make_backend",
]
