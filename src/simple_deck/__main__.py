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

    # V8 fix: Napraw popsuty wpis autostartu w rejestrze Windows (frozen)
    # + usuń skróty "Simple Deck" z folderów Autostart — instalator tworzył
    # skrót, aplikacja klucz Run: razem = DWA wpisy w Ustawieniach Windows.
    # Jedyne źródło autostartu to klucz Run zarządzany przez aplikację.
    from .core.autostart import remove_startup_shortcuts, repair_frozen_run_value
    repair_frozen_run_value()
    remove_startup_shortcuts()

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
