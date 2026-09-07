"""Autostart — pojedyncze źródło prawdy dla "uruchom przy logowaniu".

Wszystkie operacje autostartu przechodzą przez ten moduł:
  - wpis w rejestrze HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
    (value name: ``SIMPLEDECK``; legacy ``GREJEMOS`` sprzed rebrandingu
    jest sprzątany),
  - skróty w folderach Autostart (``shell:startup`` oraz wspólny
    ``{commonstartup}``) — tworzone dawniej przez instalator Inno Setup
    (task "Uruchom przy starcie systemu"). Windows pokazuje wpis Run-key
    i skrót z folderu Autostart jako DWA osobne wpisy "Simple Deck" w
    Ustawienia → Aplikacje → Uruchamianie, więc skróty są usuwane, a
    jedynym mechanizmem pozostaje klucz Run zarządzany przez aplikację.

Wszystkie funkcje są bezpieczne na platformach innych niż Windows
(zwracają False / 0 bez działania) i nie podnoszą wyjątków.
"""
from __future__ import annotations

import logging
import os
import sys

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "SIMPLEDECK"
_LEGACY_NAMES = ("GREJEMOS",)
# Nazwy skrótów w folderach Autostart (instalator + ewentualne warianty).
_SHORTCUT_NAMES = ("Simple Deck.lnk", "SimpleDeck.lnk")


def _run_command() -> str:
    """Komenda zapisywana w kluczu Run.

    PyInstaller-frozen: sam launcher ``Simple-Deck.exe`` (bootloader nie
    obsługuje ``-m simple_deck`` — "Failed to launch python embedded
    interface" przy starcie Windows). Dev: ``python.exe -m simple_deck``.
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return f'"{sys.executable}" -m simple_deck'


def _startup_dirs() -> list[str]:
    """Foldery Autostart (użytkownika + wspólny), jeśli istnieją."""
    dirs: list[str] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        dirs.append(os.path.join(
            appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup"))
    programdata = os.environ.get("ProgramData")
    if programdata:
        dirs.append(os.path.join(
            programdata, "Microsoft", "Windows", "Start Menu", "Programs",
            "StartUp"))
    return [d for d in dirs if os.path.isdir(d)]


def set_autostart(enable: bool) -> bool:
    """Włącz/wyłącz autostart przez klucz Run HKCU.

    Enable: zapisuje ``SIMPLEDECK`` i usuwa legacy ``GREJEMOS``.
    Disable: usuwa oba. Zwraca True gdy operacja się powiodła.
    """
    if not sys.platform.startswith("win"):
        return False
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY,
                             0, winreg.KEY_SET_VALUE)
        try:
            if enable:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ,
                                  _run_command())
                for name in _LEGACY_NAMES:
                    try:
                        winreg.DeleteValue(key, name)
                    except FileNotFoundError:
                        pass
            else:
                for name in (VALUE_NAME, *_LEGACY_NAMES):
                    try:
                        winreg.DeleteValue(key, name)
                    except FileNotFoundError:
                        pass
        finally:
            key.Close()
        return True
    except Exception:
        log.exception("autostart apply failed (enable=%s)", enable)
        return False


def repair_frozen_run_value() -> None:
    """V8 fix: napraw zepsuty wpis autostartu (frozen buildy).

    Poprzednie wersje zapisywały ``"Simple-Deck.exe" -m simple_deck`` co
    psuło start z autostartu. Nadpisz ``SIMPLEDECK`` gdy zawiera
    ``-m simple_deck`` — tylko dla frozen buildów (dev zostaje).
    """
    if not sys.platform.startswith("win") or not getattr(sys, "frozen", False):
        return
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY,
            0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
        )
        try:
            try:
                existing, _ = winreg.QueryValueEx(key, VALUE_NAME)
                if isinstance(existing, str) and "-m simple_deck" in existing:
                    winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ,
                                      _run_command())
            except FileNotFoundError:
                pass
        finally:
            key.Close()
    except OSError:
        # Brak dostępu do rejestru / brak klucza — ignoruj (autostart off)
        pass


def remove_startup_shortcuts() -> int:
    """Usuń skróty "Simple Deck" z folderów Autostart (migracja z instalatora).

    Instalator Inno tworzył ``{commonstartup}\\Simple Deck.lnk`` (task
    ``startupicon``). Skrót + klucz Run = dwa wpisy "Simple Deck" w
    Ustawieniach Windows, więc skróty są sprzątane przy starcie aplikacji.

    Zwraca liczbę usuniętych plików.
    """
    if not sys.platform.startswith("win"):
        return 0
    removed = 0
    for directory in _startup_dirs():
        for name in _SHORTCUT_NAMES:
            path = os.path.join(directory, name)
            try:
                if os.path.isfile(path):
                    os.remove(path)
                    removed += 1
                    log.info("usunięto skrót autostartu: %s", path)
            except OSError:
                log.exception("nie udało się usunąć skrótu: %s", path)
    return removed
