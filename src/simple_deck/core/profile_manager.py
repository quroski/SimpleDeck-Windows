"""Profile Manager - ładuje/zapisuje profile i wybiera aktywny.

Profile są przechowywane jako pliki JSON w ``~/.config/simple-deck/profiles/``.
Aktywny profil może być przełączany:
  - ręcznie z UI
  - automatycznie przez WindowDetector (gdy detekcja okien aktywna)

Zmiana profilu emituje sygnał ``active_profile_changed``.
"""
from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QFileSystemWatcher, Signal

from .profile import Profile

log = logging.getLogger(__name__)


def _sanitize_name(name: str) -> str:
    """Zamień nazwę profilu na bezpieczną nazwę pliku (slug).

    Usuwa znaki ścieżkowe i kontrolne, zachowując litery/cyfry/spacje/-_.
    Zapobiega path-traversal (np. nazwa '../../../etc/x').
    """
    name = (name or "").strip()
    # Usuń separatatory ścieżek i znaki kontrolne
    name = re.sub(r"[\\/\x00-\x1f]", "", name)
    # Zamień ciągi białych znaków na pojedynczą spację, odetnij kropki z obu końców
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    return name or "Profile"


def profiles_dir() -> Path:
    """Katalog na profile (tworzony jeśli nie istnieje)."""
    p = Path.home() / ".config" / "simple-deck" / "profiles"
    p.mkdir(parents=True, exist_ok=True)
    return p


class ProfileManager(QObject):
    """Zarządca profili mapowania + auto-przełączanie wg aktywnej aplikacji."""

    active_profile_changed = Signal(object)  # Profile
    profile_list_changed = Signal()

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._dir = profiles_dir()
        self._active: Optional[Profile] = None
        # Reguły automatycznego przełączania: process_name → profile_filename
        # Najprostsza postać; docelowo konfigurowalne w UI.
        self._rules: dict[str, str] = {}
        # Filesystem watcher - przeładuj listę profili gdy katalog się zmienia
        self._watcher = QFileSystemWatcher(self)
        if self._dir.exists():
            self._watcher.addPath(str(self._dir))
        self._watcher.directoryChanged.connect(lambda *_: self.profile_list_changed.emit())

    # ---- API publiczne ----
    @property
    def directory(self) -> Path:
        return self._dir

    @property
    def active(self) -> Optional[Profile]:
        return self._active

    def list_profiles(self) -> list[str]:
        """Zwraca listę nazw profili (bez rozszerzeń)"""
        if not self._dir.exists():
            return []
        return sorted(p.stem for p in self._dir.glob("*.json"))

    def load(self, name: str) -> Optional[Profile]:
        """Wczytaj profil wg nazwy (bez .json)."""
        path = self._dir / f"{name}.json"
        if not path.exists():
            log.warning("profile not found: %s", path)
            return None
        try:
            p = Profile.from_json(path)
            self._active = p
            self.active_profile_changed.emit(p)
            return p
        except Exception as e:
            log.exception("failed to load profile %s: %s", path, e)
            return None

    def save(self, profile: Profile) -> None:
        """Zapisz profil do pliku (nadpisuje). Nazwa pliku jest sanityzowana."""
        safe = _sanitize_name(profile.name)
        path = self._dir / f"{safe}.json"
        # Utrzymaj spójność: jeśli sanityzacja zmieniła nazwę, zaktualizuj obiekt
        if safe != profile.name:
            profile.name = safe
        try:
            profile.to_json(path)
            self.profile_list_changed.emit()
        except Exception:
            log.exception("failed to save profile %s", path)

    def set_active(self, name: str) -> bool:
        p = self.load(name)
        return p is not None

    # ---- Zarządzanie profilami (CRUD) ----
    def create(self, name: str, description: str = "") -> Optional[Profile]:
        """Utwórz nowy pusty profil i uczyń go aktywnym."""
        safe = _sanitize_name(name)
        if safe in self.list_profiles():
            log.warning("profile '%s' already exists", safe)
            return None
        p = Profile(name=safe, description=description)
        self.save(p)
        self.load(safe)
        return self._active

    def rename(self, old: str, new: str) -> bool:
        """Zmień nazwę profilu (pliku). Zwraca True jeśli się udało."""
        old_safe = _sanitize_name(old)
        new_safe = _sanitize_name(new)
        old_path = self._dir / f"{old_safe}.json"
        new_path = self._dir / f"{new_safe}.json"
        if not old_path.exists():
            log.warning("rename: source not found: %s", old_path)
            return False
        if new_path.exists() and new_path != old_path:
            log.warning("rename: target exists: %s", new_path)
            return False
        try:
            p = Profile.from_json(old_path)
            p.name = new_safe
            p.to_json(new_path)
            if new_path != old_path:
                old_path.unlink()
            # Aktualizuj aktywny jeśli to on był przemianowany
            if self._active is not None and self._active.name == old_safe:
                self._active = p
                self.active_profile_changed.emit(p)
            self.profile_list_changed.emit()
            return True
        except Exception:
            log.exception("rename failed %s -> %s", old_safe, new_safe)
            return False

    def duplicate(self, name: str, new_name: Optional[str] = None) -> Optional[Profile]:
        """Skopiuj profil pod nową nazwą (domyślnie '<name> (kopia)')."""
        src = self._dir / f"{_sanitize_name(name)}.json"
        if not src.exists():
            return None
        new_safe = _sanitize_name(new_name) if new_name else f"{_sanitize_name(name)} (kopia)"
        # Uniknij kolizji
        base = new_safe
        i = 2
        while new_safe in self.list_profiles():
            new_safe = f"{base} {i}"
            i += 1
        try:
            p = Profile.from_json(src)
            p.name = new_safe
            self.save(p)
            return p
        except Exception:
            log.exception("duplicate failed: %s", src)
            return None

    def delete(self, name: str) -> bool:
        """Skasuj profil. Odmów jeśli to ostatni profil (zawsze musi być 1+)."""
        safe = _sanitize_name(name)
        path = self._dir / f"{safe}.json"
        if not path.exists():
            return False
        if len(self.list_profiles()) <= 1:
            log.warning("delete: refusing to remove the last profile '%s'", safe)
            return False
        try:
            path.unlink()
            # Jeśli skasowano aktywny - przełącz na pierwszy dostępny
            if self._active is not None and self._active.name == safe:
                remaining = self.list_profiles()
                if remaining:
                    self.load(remaining[0])
            self.profile_list_changed.emit()
            return True
        except Exception:
            log.exception("delete failed: %s", path)
            return False

    def export_profile(self, name: str, dst: Path) -> bool:
        """Zapisz profil do dowolnej ścieżki (eksport)."""
        src = self._dir / f"{_sanitize_name(name)}.json"
        if not src.exists():
            return False
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            return True
        except Exception:
            log.exception("export failed: %s -> %s", src, dst)
            return False

    def import_profile(self, src: Path, overwrite: bool = False) -> Optional[Profile]:
        """Wczytaj profil z dowolnej ścieżki do katalogu profili."""
        try:
            p = Profile.from_json(src)
            safe = _sanitize_name(p.name)
            p.name = safe
            dst = self._dir / f"{safe}.json"
            if dst.exists() and not overwrite:
                # Wygeneruj unikalną nazwę
                base, i = safe, 2
                while dst.exists():
                    p.name = f"{base} {i}"
                    dst = self._dir / f"{p.name}.json"
                    i += 1
            p.to_json(dst)
            self.profile_list_changed.emit()
            return p
        except Exception:
            log.exception("import failed: %s", src)
            return None

    # ---- Auto-przełączanie (Window Detector hook) ----
    @property
    def rules(self) -> dict[str, str]:
        """Bieżące reguły auto-przełączania (process_lower → profile_name)."""
        return dict(self._rules)

    def set_rules(self, rules: dict[str, str]) -> None:
        """Zastąp wszystkie reguły auto-przełączania."""
        self._rules = {str(k).lower(): str(v) for k, v in (rules or {}).items() if v}

    def set_rule(self, process_name: str, profile_name: str) -> None:
        """Powiąż nazwę procesu (np. 'discord') z profilem."""
        if profile_name:
            self._rules[process_name.lower()] = profile_name
        else:
            self._rules.pop(process_name.lower(), None)

    def on_foreground_process(self, process_name: str) -> None:
        """Wołane przez WindowDetector gdy aktywne okno się zmieni."""
        name = process_name.lower()
        profile = self._rules.get(name)
        if profile and (self._active is None or self._active.name != profile):
            log.info("auto-switching to profile '%s' for '%s'", profile, name)
            self.load(profile)

    def ensure_default(self) -> None:
        """Utwórz domyślny profil jeśli żaden nie istnieje."""
        if not self.list_profiles():
            default = Profile(name="Default", description="Domyślny profil")
            self.save(default)
        if self._active is None:
            self.load("Default")
