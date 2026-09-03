"""Entrypoint Simple Deck.

Uruchomienie:
    python -m simple_deck            # normalny tryb
    python -m simple_deck --demo     # bez łączenia z MCU (do testów UI)
    python -m simple_deck --verbose  # debug logging
"""
from __future__ import annotations

import sys


def main() -> int:
    # Najpierw migruj stary katalog konfiguracji (grejem-os → simple-deck).
    # Musi to nastąpić przed acquire_single_instance() / Settings.load(), bo one
    # odwołują się do settings_dir() — tutaj zostaje ona przesunięta na nową nazwę.
    from .core.migration import migrate_legacy_config_dir
    migrate_legacy_config_dir()

    # V8 fix: Napraw popsuty wpis autostartu w rejestrze Windows.
    # Poprzednie wersje zapisywały: "Simple-Deck.exe" -m simple_deck
    # co powoduje "Failed to launch python embedded interface" przy starcie
    # Windows (bootloader PyInstallera nie obsługuje -m dla frozen bundle).
    # Nadpisz wpis jeśli zawiera "-m simple_deck" (gwarantowany znak zepsutego
    # wpisu z tej wersji) — tylko dla frozen buildów, dev (python -m) zostaje.
    if sys.platform.startswith("win") and getattr(sys, "frozen", False):
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
            )
            try:
                try:
                    existing, _ = winreg.QueryValueEx(key, "SIMPLEDECK")
                    if isinstance(existing, str) and "-m simple_deck" in existing:
                        winreg.SetValueEx(
                            key, "SIMPLEDECK", 0, winreg.REG_SZ,
                            f'"{sys.executable}"',
                        )
                except FileNotFoundError:
                    pass
            finally:
                key.Close()
        except OSError:
            # Brak dostępu do rejestru / brak klucza — ignoruj (autostart off)
            pass

    args = sys.argv[1:]
    demo_mode = "--demo" in args
    if demo_mode:
        args.remove("--demo")
    # Przekaż resztę args dalej (Qt parsuje swoje opcje)
    sys.argv = [sys.argv[0]] + args
    # V6: Single-instance guard — druga instancja kończy po cichu.
    # --demo i testy pomijają blokadę (wiele instancji UI dozwolone).
    # V8: Druga instancja ping'uje IPC serwer pierwszej by ta przywołała okno
    # (z tray'a / minimalizacji) zamiast milczeć i sprawiać wrażenie "nic się
    # nie dzieje". Lock to wciąż QLockFile; IPC jest warstwą dodaną nad nim.
    lock = None
    if not demo_mode:
        from .core.single_instance import (
            acquire_single_instance,
            notify_existing_instance,
        )
        lock = acquire_single_instance()
        if lock is None:
            # Druga instancja: poproś pierwszą o raise, potem wyjdź niezależnie.
            notify_existing_instance()
            return 0
    from .app import run
    return run(demo_mode=demo_mode, lock=lock)


if __name__ == "__main__":
    raise SystemExit(main())
