"""Migracja katalogu konfiguracji ze starej nazwy (grejem-os) do nowej (simple-deck).

Po zmianie nazwy aplikacji (GREJEM OS → Simple Deck) katalog konfiguracji usera
przeszedł z ``~/.config/grejem-os/`` na ``~/.config/simple-deck/``. Aby użytkownicy
aktualizujący aplikację nie stracili profili i ustawień, ta funkcja przenosi
stary katalog w nowe miejsce przy pierwszym uruchomieniu po aktualizacji.

Idempotentna i bezpieczna: nigdy nie rzuca wyjątku (najwyżej loguje warning).
Musi być wołana ZANIM cokolwiek innego odwoła się do ``settings_dir()`` —
w szczególności przed ``acquire_single_instance()`` (lockfile żyje w tym katalogu),
``Settings.load()`` oraz ``ProfileManager()``.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

LEGACY_DIR_NAME = "grejem-os"
CURRENT_DIR_NAME = "simple-deck"


def _config_root() -> Path:
    """``~/.config`` (bez tworzenia)."""
    return Path.home() / ".config"


def migrate_legacy_config_dir() -> bool:
    """Przenieś ``~/.config/grejem-os/`` → ``~/.config/simple-deck/`` jeśli trzeba.

    Zwraca True jeśli migracja została wykonana, False jeśli nic nie było do
    zrobienia (nowy katalog już istnieje, lub stary nigdy nie istniał).
    Bezpieczna do wołania wielokrotnie; nigdy nie rzuca wyjątku.
    """
    root = _config_root()
    current = root / CURRENT_DIR_NAME
    legacy = root / LEGACY_DIR_NAME

    # 1) Nowy katalog już istnieje — nic nie rób (user już zmigrowany lub fresh install).
    if current.exists():
        return False

    # 2) Stary katalog nie istnieje — fresh install, nie ma czego migrować.
    if not legacy.exists():
        return False

    # 3) Migruj. ``shutil.move`` na tym samym filesystemie to原子 rename katalogu
    #    (POSIX) — szybkie i bezpieczne. Gdyby coś poszło nie tak, loguj warning
    #    i zostaw stary katalog w spokoju (user może migrować ręcznie).
    try:
        root.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy), str(current))
        log.info("migrated config dir: %s → %s", legacy, current)
        return True
    except Exception:
        log.warning("config dir migration %s → %s failed; leaving legacy in place",
                    legacy, current, exc_info=True)
        return False
