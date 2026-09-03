"""AppPicker - wybór celu regulacji audio (system vs dowolna aplikacja).

Combo jest EDYTOWALNE: użytkownik może wpisać dowolną nazwę procesu
(np. ``spotify``, ``discord.exe``) nawet jeśli aplikacja nie jest w tej chwili
uruchomiona. Skonfigurowany cel jest zapamiętany i zacznie działać gdy aplikacja
się uruchomi. Aktualnie uruchomione aplikacje audio są pokazywane jako
sugestie (odświeżane co 5s), a ostatnio używane - jako dodatkowe sugestie.

Wybór użytkownika jest zachowywany przez refresh (M9 fix zachowany).
"""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QLabel, QVBoxLayout, QWidget

from ...core.app_list_cache import get_app_list_cache

log = logging.getLogger(__name__)


class AppPicker(QWidget):
    """Edytowalne combo wyboru celu audio.

    Zapewnia stałą opcję "Głośność systemowa" + dynamiczne sugestie aplikacji
    audio (uruchomione + ostatnio używane). Dowolny inny ciąg można wpisać
    ręcznie - zostanie zapamiętany nawet gdy aplikacja nie działa.

    V6: Subskrybuje globalny AppListCache (jeden timer dla wszystkich pickerów)
    zamiast własnego QTimer'a — eliminuje 5 RPC PulseAudio co 5 s.
    """

    SYSTEM_KEY = "__system__"
    SYSTEM_LABEL = "🔊  Głośność systemowa"

    selection_changed = Signal(str)  # wybrany target ('' = system, inaczej proces)

    def __init__(self, audio_backend=None, label: str = "Źródło audio",
                 recent_apps: Optional[list[str]] = None, parent=None):
        super().__init__(parent)
        self._audio = audio_backend
        self._recent: list[str] = list(recent_apps or [])
        # Wartość skonfigurowana przez set_target - zapamiętana nawet gdy
        # aplikacja nie jest (jeszcze) na liście uruchomionych.
        self._configured_target: str = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        lbl = QLabel(label.upper(), objectName="labelMuted")
        lay.addWidget(lbl)

        self._combo = QComboBox()
        # EDYTOWALNE - dowolna nazwa procesu. NoInsert => wpisanany tekst nie
        # dodaje trwałej pozycji do listy (lista to tylko sugestie).
        self._combo.setEditable(True)
        self._combo.setInsertPolicy(QComboBox.NoInsert)
        lay.addWidget(self._combo)

        # Hint pokazywany gdy cel nie jest obecnie uruchomiony
        self._hint = QLabel("", objectName="labelMuted")
        self._hint.setStyleSheet(
            "color: #FFB13C; background: transparent; font-size: 11px;"
        )
        self._hint.setWordWrap(True)
        self._hint.setVisible(False)
        lay.addWidget(self._hint)

        # Sygnały
        self._combo.currentIndexChanged.connect(self._on_idx_changed)
        # Dla tekstu wpisywanego ręcznie (currentText zmienia się bez zmiany idx)
        self._combo.editTextChanged.connect(self._on_text_changed)

        # V6: Subskrybuj globalny AppListCache (jeden timer dla wszystkich).
        # Pierwsze ładowanie listy nastąpi po subskrypcji (cache odświeża przy
        # tworzeniu). Zapewnia to od razu aktualną listę.
        self._cached_apps: list[str] = []
        if self._audio is not None:
            try:
                cache = get_app_list_cache(self._audio)
                self._cached_apps = cache.cached_apps()
                cache.apps_changed.connect(self._on_apps_changed)
            except Exception:
                log.debug("AppListCache unavailable, picker without refresh")

        # Pierwsze ładowanie listy (synchroniczne — by combo nie było puste)
        self.refresh()

    # ---- Obsługa sygnałów ----
    def _on_idx_changed(self, idx: int) -> None:
        key = self._combo.itemData(idx)
        # Wybór z listy (system lub uruchomiona/ostatnia aplikacja)
        if key == self.SYSTEM_KEY:
            self._configured_target = ""
        elif key:
            self._configured_target = key
        # aktualizuj _configured_target też z tekstu (gdy zmiana pozycji listy)
        self._update_hint()
        self.selection_changed.emit(self._configured_target)

    def _on_text_changed(self, text: str) -> None:
        # Ręcznie wpisany tekst (nie pasujący do żadnej pozycji listy)
        # Ignoruj gdy tekst to po prostu etykieta wybranej pozycji listy
        idx = self._combo.currentIndex()
        if idx >= 0:
            item_text = self._combo.itemText(idx)
            if text == item_text:
                return
        cleaned = text.strip()
        if cleaned == self.SYSTEM_LABEL:
            self._configured_target = ""
        else:
            self._configured_target = cleaned
        self._update_hint()
        self.selection_changed.emit(self._configured_target)

    def _update_hint(self) -> None:
        """Pokaż ostrzeżenie gdy cel nie jest obecnie wśród uruchomionych aplikacji.

        V7: Korzysta z ``self._cached_apps`` zamiast wołać ``list_apps()`` —
        dawniej każda zmiana tekstu / refresh picker'a powodowała dodatkowy
        RPC PulseAudio tutaj. Teraz zużywa dane z AppListCache (odświeżane
        raz na 5 s singletonem, nie per-picker).
        """
        target = self._configured_target
        if not target:
            self._hint.setVisible(False)
            self._hint.setText("")
            return
        running = {a.lower() for a in self._cached_apps}
        if target.lower() in running:
            self._hint.setVisible(False)
            self._hint.setText("")
        else:
            self._hint.setText(
                "⚠ aplikacja nie jest obecnie uruchomiona — regulacja "
                "rozpocznie się po jej starcie."
            )
            self._hint.setVisible(True)

    def _on_apps_changed(self, apps: list) -> None:
        """V6/V7: Callback z globalnego AppListCache — zużyj przekazaną listę.

        V6 wołał ``self.refresh()`` który ignorował argument i ponownie wołał
        ``list_apps()``. V7 buforuje listę i używa jej w ``refresh()``
        oraz ``_update_hint()`` — eliminuje 5× redundowaną enumerację PA.
        """
        self._cached_apps = list(apps) if apps else []
        self.refresh()

    def refresh(self) -> None:
        """Odśwież listę sugestii + ostatnio używane.

        V7: Korzysta z ``self._cached_apps`` (z AppListCache). Nie woła już
        ``audio_backend.list_apps()`` — eliminuje redundowaną enumerację PA.
        Gdy picker powstał zanim cache miał dane (np. audio_backend=None
        w testach), lista jest po prostu pusta.

        M9 fix (zachowany): obecny wybór jest zapamiętany przez refresh.
        Dodatkowo: jeśli skonfigurowany cel NIE jest na liście uruchomionych,
        NIE kasujemy go (użytkownik może regulować aplikację, która nie działa).
        """
        prev_target = self._configured_target

        self._combo.blockSignals(True)
        self._combo.clear()
        # Stała pozycja: system
        self._combo.addItem(self.SYSTEM_LABEL, self.SYSTEM_KEY)
        # Uruchomione aplikacje audio (sortowane) — z cache
        running: set[str] = set()
        for name in sorted(self._cached_apps):
            self._combo.addItem(f"🎧  {name}", name)
            running.add(name.lower())
        # Ostatnio używane aplikacje (jako sugestie, jeśli nie są uruchomione)
        for name in self._recent:
            if name.lower() in running:
                continue
            self._combo.addItem(f"🕘  {name}  (ostatnio)", name)

        # Przywróć wybór
        restored = False
        if prev_target:
            # System?
            if prev_target in ("", "system"):
                self._combo.setCurrentIndex(0)
                restored = True
            else:
                for i in range(1, self._combo.count()):
                    if self._combo.itemData(i) == prev_target:
                        self._combo.setCurrentIndex(i)
                        restored = True
                        break
                if not restored:
                    # Cel nie na liście (nie uruchomiony) - zostaw jako tekst
                    self._combo.setCurrentIndex(-1)
                    self._combo.setEditText(prev_target)
                    restored = True
        else:
            self._combo.setCurrentIndex(0)
        self._combo.blockSignals(False)

        self._update_hint()

    def set_target(self, target: str) -> None:
        """Ustaw wartość: '' / 'system' → system; inna → nazwa procesu."""
        self._configured_target = "" if (not target or target == "system") else target
        self._combo.blockSignals(True)
        if not self._configured_target:
            self._combo.setCurrentIndex(0)
        else:
            # Szukaj na liście
            found = False
            for i in range(1, self._combo.count()):
                if self._combo.itemData(i) == self._configured_target:
                    self._combo.setCurrentIndex(i)
                    found = True
                    break
            if not found:
                # Nie na liście - wpisz jako tekst (aplikacja może nie działać)
                self._combo.setCurrentIndex(-1)
                self._combo.setEditText(self._configured_target)
        self._combo.blockSignals(False)
        self._update_hint()

    def get_target(self) -> str:
        """Zwraca wybrany target ('' dla system, nazwa procesu w p.p.)."""
        return self._configured_target

    def set_recent_apps(self, recent: list[str]) -> None:
        self._recent = list(recent or [])
        self.refresh()
