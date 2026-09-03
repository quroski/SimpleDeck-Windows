"""Shared AppListCache — jedna enumeracja sesji audio dla wszystkich AppPickerów.

Bez tego każde z 5 instancji AppPicker miało własny QTimer 5 s wołający
``audio_backend.list_apps()`` (pełny RPC PulseAudio/WASAPI). To 5 RPC przy
starcie + 1/s sustained. AppListCache robi JEDEN timer i broadcast'uje wynik
do wszystkich subskrybentów.
"""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal

log = logging.getLogger(__name__)

# Globalny singleton — jeden timer dla całej aplikacji.
_global_cache: Optional["AppListCache"] = None


class AppListCache(QObject):
    """Cache listy aplikacji audio z pojedynczym timerem odświeżania.

    AppPicker subskrybuje ``apps_changed`` i aktualizuje się gdy lista się
    zmieni. Eliminuje N niezależnych timerów i N RPC co 5 s.
    """

    apps_changed = Signal(list)  # list[str]

    REFRESH_INTERVAL_MS = 5000
    MAX_APPS = 50  # V8: Limit cache size to reduce memory usage

    def __init__(self, audio_backend=None, parent=None):
        super().__init__(parent)
        self._audio = audio_backend
        self._timer = QTimer(self)
        self._timer.setInterval(self.REFRESH_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)
        # Pierwsze ładowanie natychmiast
        self._last_apps: list[str] = []
        self.refresh()

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def refresh(self) -> None:
        """Pobierz listę aplikacji z backendu i emituj jeśli się zmieniła.

        V7: Emitujemy TYLKO gdy lista się różni. Dawniej emit zawsze powodował,
        że każdy AppPicker wołał własne ``list_apps()`` w ``refresh()``
        i ``_update_hint()`` — czyli 1 enumeracja PulseAudio tutaj + 5×2 w
        pickerach = 11 RPC co 5 s. Teraz pickery konsumują listę przekazaną
        w sygnale, więc liczy się tylko ta jedna enumeracja.
        ``last_apps`` jest zawsze aktualne — subskrybent może pytać przez
        ``cached_apps()`` by zweryfikować czy skonfigurowany cel żyje.
        
        V8: Ogranicz rozmiar listy do MAX_APPS (50) by zmniejszyć zużycie pamięci.
        Sortujemy alfabetycznie i bierzemy pierwsze N.
        """
        if self._audio is None:
            return
        try:
            apps = self._audio.list_apps()
        except Exception:
            log.exception("AppListCache refresh failed")
            return
        # V8: Limit cache size to MAX_APPS
        if len(apps) > self.MAX_APPS:
            apps = sorted(apps)[:self.MAX_APPS]
        if apps != self._last_apps:
            self._last_apps = list(apps)
            self.apps_changed.emit(list(apps))

    def cached_apps(self) -> list[str]:
        """Zwróć ostatnio pobraną listę aplikacji (bez RPC)."""
        return list(self._last_apps)


def get_app_list_cache(audio_backend=None, parent=None) -> AppListCache:
    """Zwróć (lub utwórz) globalny singleton AppListCache.

    Pierwsze wywołenie tworzy cache z ``audio_backend``. Kolejne wywołania
    ignorują argumenty i zwracają istniejący singleton.
    """
    global _global_cache
    if _global_cache is None:
        _global_cache = AppListCache(audio_backend=audio_backend, parent=parent)
        _global_cache.start()
    return _global_cache


def reset_app_list_cache() -> None:
    """Reset singletonu — używane w testach."""
    global _global_cache
    if _global_cache is not None:
        _global_cache.stop()
        _global_cache.deleteLater()
        _global_cache = None


def peek_app_list_cache() -> Optional[AppListCache]:
    """V7: Zwróć istniejący singleton lub None — bez tworzenia.

    Używane przez MainWindow.hideEvent/showEvent by zatrzymać timer gdy okno
    ukryte (tray) i wznowić gdy widoczne. Nie chcemy tworzyć cache'u tylko
    po to by go zatrzymać.
    """
    return _global_cache
