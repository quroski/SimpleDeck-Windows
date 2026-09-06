"""Simple Deck - desktop controller for GREJEM Stream Deck.

Moduł główny aplikacji. Pakiety:
  - transport : HID + protokół binarny + auto-reconnect
  - core      : event bus, profile, hotkey dispatcher
  - platform  : abstrakcje WASAPI/PulseAudio, GetForegroundWindow, SendInput
  - ui        : widgety i strony PySide6 (styl Glassmorphism)
"""
__version__ = "1.0.3a"
__all__ = ["__version__"]
