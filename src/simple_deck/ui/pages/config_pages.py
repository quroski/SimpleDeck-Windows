"""Strony POTS / BUTTONS - edycja konfiguracji per kontrolka.

Każda strona ma listę wierszy konfiguracyjnych (PotRow/ButtonRow).
Zmiana w wierszu emituje sygnał ``changed(idx, cfg)`` który strona przechwytuje,
aktualizuje profil w pamięci i woła ``_schedule_save()`` - debounced zapis
do dysku przez ``ProfileManager.save()`` (co 500ms po ostatniej zmianie).
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFrame,
                                QHBoxLayout, QLabel, QLineEdit,
                                QProgressBar, QPushButton, QScrollArea,
                                QSizePolicy, QSlider,
                                QTabWidget, QVBoxLayout, QWidget)

from ... import __version__
from ...core.event_bus import EventBus
from ...core.profile import (ButtonConfig, PotConfig,
                              Profile)
from ...core.profile_manager import ProfileManager
from ...core.settings import ACCENTS, Settings, settings_path
from ...transport.connection_manager import ConnectionManager
from ...transport.protocol import make_cfg_cmd
from ..widgets.config_rows import ButtonRow, PotRow
from ..widgets.icon import IconLabel
from ..widgets.profile_switcher import ProfileSwitcher

log = logging.getLogger(__name__)


class _Card(QFrame):
    """QFrame karty z doklejonym atrybutem _icon_ref (zapobieganie GC)."""

    _icon_ref: object


class _BaseConfigPage(QWidget):
    """Bazowa strona z listą wierszy konfiguracji + debounced save.

    Subklasy implementują ``_populate_rows`` (tworzy wiersze i podłącza
    ich sygnał ``changed`` do metod ``_on_*_changed``).
    """

    SAVE_DEBOUNCE_MS = 500   # czekaj 500ms po ostatniej zmianie przed zapisem

    def __init__(self, title: str, subtitle: str, bus: EventBus,
                 connection: ConnectionManager,
                 profile_mgr: Optional[ProfileManager] = None,
                 parent=None):
        super().__init__(parent)
        self._bus = bus
        self._conn = connection
        self._profile_mgr = profile_mgr
        self._profile: Optional[Profile] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)

        inner = QWidget()
        scroll.setWidget(inner)

        self._content = QVBoxLayout(inner)
        self._content.setContentsMargins(0, 0, 0, 0)
        self._content.setSpacing(14)

        # Nagłówek
        head = QHBoxLayout()
        title_lbl = QLabel(title, objectName="sectionTitle")
        title_lbl.setStyleSheet("font-size: 22px; font-weight: 700; background: transparent;")
        # V1.0.2: Ignored — subtitle nie rozpycha strony przy wąskim oknie.
        sub_lbl = QLabel(subtitle, objectName="sectionSubtitle")
        sub_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        head.addWidget(title_lbl)
        head.addStretch()
        head.addWidget(sub_lbl, alignment=Qt.AlignBottom)
        self._content.addLayout(head)

        # Debounced save - QTimer single-shot. Start opóźnia zapis do czasu
        # gdy użytkownik przez SAVE_DEBOUNCE_MS nic nie zmienił.
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(self.SAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self._flush_save)

    def set_profile(self, profile: Profile) -> None:
        self._profile = profile
        self._rebuild_rows()

    def _rebuild_rows(self) -> None:
        # Czyść stare wiersze (poza nagłówkiem na pozycji 0)
        while self._content.count() > 1:
            item = self._content.takeAt(1)
            w = item.widget() if item is not None else None
            if w is not None:
                w.deleteLater()
        self._populate_rows()
        # Stretch na końcu
        self._content.addStretch()

    def _populate_rows(self) -> None:
        raise NotImplementedError

    # ---- Save helpery ----
    def _schedule_save(self) -> None:
        """Odstaw zapis profilu o SAVE_DEBOUNCE_MS (debounce).

        Jeśli kolejna zmiana przyjdzie w tym czasie, timer się restartuje -
        zapis nastąpi dopiero 500ms po OSTATNIEJ zmianie.
        """
        if self._profile_mgr is not None:
            self._save_timer.start()

    def _flush_save(self) -> None:
        """Zapisz profil do dysku. Wywoływane przez _save_timer."""
        if self._profile_mgr is None or self._profile is None:
            return
        try:
            self._profile_mgr.save(self._profile)
            log.debug("profile '%s' saved", self._profile.name)
        except Exception:
            log.exception("profile save failed")


# ============================================================
class CalibrationDialog(QDialog):
    """V3: Modalny dialog kalibracji min/max potencjometru.

    Zamiast przechwytywać „pierwsze zdarzenie pota" (bug: przechwytywał
    szum albo ruch który już trwał), dialog pokazuje NA ŻYWO aktualną wartość
    ADC i pozwala użytkownikowi zapisać min/max gdy jest gotowy.

    UX:
      1. Użytkownik kręci potem na minimum → widzi wartość na pasku
      2. Klika „Zapisz minimum" → wartość zapisana
      3. Kręci na maximum → widzi wartość
      4. Klika „Zapisz maximum" → wartość zapisana
      5. „Zastosuj" zamyka dialog i aplikuje nowy zakres do profilu.
      ESC / „Anuluj" przywraca poprzedni zakres.

    Wymaga połączonego urządzenia (POT_EVT musi płynąć). Jeśli nie ma
    połączenia, dialog pokazuje placeholder „—" i przyciski zapisu są
    wyłączone.
    """

    def __init__(self, idx: int, bus: EventBus, current_min: float,
                 current_max: float, label: str = "", parent=None):
        super().__init__(parent)
        # V1.0.3b: tytuł używa własnej nazwy potencjometru (spójność z kartami).
        display = label.strip() or f"POT {idx + 1}"
        self.setWindowTitle(f"Kalibracja — {display}")
        self.setModal(True)
        self.setFixedSize(420, 340)
        self._bus = bus
        self._idx = idx
        self._current_adc: int = -1
        # Zapamiętaj oryginalne wartości dla Anuluj
        self._orig_min = float(current_min)
        self._orig_max = float(current_max)
        # Robocze wartości (modyfikowane przez przyciski)
        self._new_min: Optional[float] = None
        self._new_max: Optional[float] = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(12)

        # Instrukcja
        info = QLabel(
            '1. Kręć potencjometrem na <b>minimum</b> i kliknij „Zapisz min".<br>'
            '2. Kręć na <b>maximum</b> i kliknij „Zapisz max".<br>'
            '3. Kliknij „Zastosuj" aby zapisać nowy zakres.')
        info.setWordWrap(True)
        info.setStyleSheet("font-size: 12px; color: rgba(255,255,255,180);")
        lay.addWidget(info)

        # Wartość ADC na żywo
        self._value_lbl = QLabel("—")
        self._value_lbl.setAlignment(Qt.AlignCenter)
        self._value_lbl.setStyleSheet(
            "font-size: 28px; font-weight: 700; padding: 12px;"
            "background: rgba(45,212,255,24); border-radius: 8px;"
            "color: #2DD4FF;")
        lay.addWidget(self._value_lbl)

        # Pasek wartości
        self._bar = QProgressBar()
        self._bar.setRange(0, 4095)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(8)
        self._bar.setStyleSheet("""
            QProgressBar {
                background: rgba(255, 255, 255, 24); border: none; border-radius: 4px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                            stop:0 #2DD4FF, stop:1 #9B5CFF);
                border-radius: 4px;
            }
        """)
        lay.addWidget(self._bar)

        # Aktualny zapisany zakres
        self._range_lbl = QLabel(
            f"Zakres: {current_min:.0%} – {current_max:.0%}")
        self._range_lbl.setAlignment(Qt.AlignCenter)
        self._range_lbl.setStyleSheet(
            "font-size: 12px; color: rgba(255,255,255,140);")
        lay.addWidget(self._range_lbl)

        # Przyciski zapisu min/max
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._btn_min = QPushButton("▾  Zapisz min")
        self._btn_min.setCursor(Qt.PointingHandCursor)
        self._btn_min.clicked.connect(self._save_min)
        self._btn_max = QPushButton("▴  Zapisz max")
        self._btn_max.setCursor(Qt.PointingHandCursor)
        self._btn_max.clicked.connect(self._save_max)
        btn_row.addWidget(self._btn_min)
        btn_row.addWidget(self._btn_max)
        lay.addLayout(btn_row)

        # Przyciski Apply / Cancel
        action_row = QHBoxLayout()
        action_row.addStretch()
        self._btn_apply = QPushButton("✓  Zastosuj")
        self._btn_apply.setCursor(Qt.PointingHandCursor)
        self._btn_apply.setEnabled(False)
        self._btn_apply.clicked.connect(self._apply)
        cancel_btn = QPushButton("Anuluj")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        action_row.addWidget(self._btn_apply)
        action_row.addWidget(cancel_btn)
        lay.addLayout(action_row)

        # Subskrybuj zdarzenia potencjometru
        bus.pot_event.connect(self._on_pot)

    def _on_pot(self, idx: int, adc: int) -> None:
        if idx != self._idx:
            return
        self._current_adc = int(adc)
        pct = adc * 100 // 4095
        self._value_lbl.setText(f"{adc}   ({pct}%)")
        self._bar.setValue(adc)

    def _save_min(self) -> None:
        if self._current_adc < 0:
            return
        norm = max(0.0, min(1.0, self._current_adc / 4095.0))
        self._new_min = norm
        self._update_range_label()
        self._check_apply_ready()

    def _save_max(self) -> None:
        if self._current_adc < 0:
            return
        norm = max(0.0, min(1.0, self._current_adc / 4095.0))
        self._new_max = norm
        self._update_range_label()
        self._check_apply_ready()

    def _update_range_label(self) -> None:
        lo = self._new_min if self._new_min is not None else self._orig_min
        hi = self._new_max if self._new_max is not None else self._orig_max
        if hi < lo:
            hi = lo
        self._range_lbl.setText(f"Zakres: {lo:.0%} – {hi:.0%}")

    def _check_apply_ready(self) -> None:
        self._btn_apply.setEnabled(
            self._new_min is not None and self._new_max is not None)

    def _apply(self) -> None:
        """Zaakceptuj dialog — _get_calibrated_values zwróci wynik."""
        if self._new_min is None or self._new_max is None:
            return
        # Zamień jeśli użytkownik zapisał w odwrotnej kolejności
        lo, hi = self._new_min, self._new_max
        if hi < lo:
            lo, hi = hi, lo
        self._result_min = lo
        self._result_max = hi
        self._bus.pot_event.disconnect(self._on_pot)
        self.accept()

    def get_result(self) -> tuple[float, float]:
        """Zwraca (min, max) po udanym accept()."""
        return (getattr(self, "_result_min", self._orig_min),
                getattr(self, "_result_max", self._orig_max))


# ============================================================
class PotsPage(_BaseConfigPage):
    """Lista 5 potencjometrów."""

    def __init__(self, bus, connection, audio_backend=None,
                 profile_mgr=None, settings=None, parent=None):
        # Uwaga: atrybut instancji _audio_backend musi być ustawiony PRZED
        # super().__init__ bo _populate_rows (wołane z set_profile) go używa.
        # Python na to pozwala; Qt nie ma tu nic do gadania bo to zwykły atrybut.
        self._audio_backend = audio_backend
        self._settings = settings
        super().__init__("POTENCJOMETRY", "Przypisz regulatory do źródeł audio",
                         bus, connection, profile_mgr, parent)

    def _populate_rows(self) -> None:
        if self._profile is None: return
        # V4: Iteruj w kolejności zdefiniowanej przez pot_display_order.
        # cfg.idx (fizyczny kanał) nie ulega zmianie — zmienia się tylko
        # pozycja karty na stronie. first/last kontrolują dostępność przycisków.
        order = self._profile.pot_display_order
        for pos, physical_idx in enumerate(order):
            if physical_idx >= len(self._profile.pots):
                continue
            cfg = self._profile.pots[physical_idx]
            row = PotRow(cfg, audio_backend=self._audio_backend,
                         settings=self._settings,
                         first=(pos == 0), last=(pos == len(order) - 1))
            # KRYTYCZNE: podłącz sygnał changed, inaczej zmiany giną
            row.changed.connect(self._on_pot_changed)
            row.calibrate_requested.connect(self._on_calibrate)
            row.move_requested.connect(self._on_move_requested)
            # V1.0.3: żywy wskaźnik aktywności — dot gaśnie przy ruchu pota.
            self._bus.pot_event.connect(row.on_pot_event)
            self._content.addWidget(row)

    def _rebuild_rows(self) -> None:
        """Nadpisane — odłącz żywe wskaźniki starych wierszy przed usunięciem."""
        # V1.0.3: przy rebuildzie stare PotRow są deleteLater() — Qt rozłącza
        # sloty automatycznie PO zniszczeniu, ale między rebuild a destroy
        # sloty potrafią wpaść na martwych widgetach. Odłącz jawnie.
        for i in range(1, self._content.count()):
            item = self._content.itemAt(i)
            w = item.widget() if item is not None else None
            if isinstance(w, PotRow):
                try:
                    self._bus.pot_event.disconnect(w.on_pot_event)
                except (RuntimeError, TypeError):
                    pass
        super()._rebuild_rows()

    def refresh_labels(self) -> None:
        """V1.0.3b: Synchronizuj tytuły kart z profilem (bez rebuildu).

        Wywoływane po zmianie nazwy kontrolki w Overview — karty na tej stronie
        muszą pokazywać TE SAME nazwy. Bez rebuildu: nie traci się stanu pól
        edycji ani nie miga cała strona.
        """
        if self._profile is None:
            return
        for i in range(1, self._content.count()):
            item = self._content.itemAt(i)
            w = item.widget() if item is not None else None
            if isinstance(w, PotRow) and 0 <= w._config.idx < len(self._profile.pots):
                w.update_config(self._profile.pots[w._config.idx])

    def _on_pot_changed(self, idx: int, cfg: PotConfig) -> None:
        """Aktualizuj profil w pamięci + debounced zapis."""
        if self._profile is not None and 0 <= idx < len(self._profile.pots):
            self._profile.pots[idx] = cfg
            self._schedule_save()

    def _on_move_requested(self, physical_idx: int, direction: int) -> None:
        """V4: Przesuń potencjometr w kolejności wyświetlania.

        Zamienia pozycje w ``pot_display_order``. Fizyczne mapowanie kanałów
        (cfg.idx) pozostaje nietknięte — zmienia się tylko układ kart na stronie.
        """
        if self._profile is None:
            return
        order = self._profile.pot_display_order
        try:
            pos = order.index(physical_idx)
        except ValueError:
            return
        new_pos = pos + direction
        if new_pos < 0 or new_pos >= len(order):
            return
        order[pos], order[new_pos] = order[new_pos], order[pos]
        self._schedule_save()
        self._rebuild_rows()
        # V4: Powiadom DeckMap by przestawił komórki w gridzie overview.
        self._bus.pot_order_changed.emit()

    def _on_calibrate(self, idx: int) -> None:
        """V3: Otwórz modalny dialog kalibracji min/max.

        Dialog pokazuje wartość ADC na żywo i pozwala użytkownikowi zapisać
        min/max gdy jest gotowy — eliminuje race condition ze starym podejściem
        „przechwyć pierwsze zdarzenie pota".
        """
        if self._profile is None or idx >= len(self._profile.pots):
            return
        cfg = self._profile.pots[idx]
        dlg = CalibrationDialog(
            idx=idx, bus=self._bus,
            current_min=cfg.min_volume, current_max=cfg.max_volume,
            label=cfg.label, parent=self.window())
        if dlg.exec() == QDialog.Accepted:
            new_min, new_max = dlg.get_result()
            cfg.min_volume = new_min
            cfg.max_volume = new_max
            self._schedule_save()
            self._rebuild_rows()
            self._bus.notify.emit(
                "success",
                f"POT {idx+1}: zakres {new_min:.0%} – {new_max:.0%}")


# ============================================================
class ButtonsPage(_BaseConfigPage):
    """Lista 4 przycisków."""

    def __init__(self, bus, connection, profile_mgr=None, parent=None):
        super().__init__("PRZYCISKI", "Skróty klawiszowe i akcje",
                         bus, connection, profile_mgr, parent)

    def _populate_rows(self) -> None:
        if self._profile is None: return
        for cfg in self._profile.buttons:
            row = ButtonRow(cfg)
            # KRYTYCZNE: podłącz sygnał changed
            row.changed.connect(self._on_button_changed)
            # V3: Test button — symuluj wciśnięcie przez EventBus
            row.test_clicked.connect(self._on_test_button)
            self._content.addWidget(row)

    def _on_test_button(self, idx: int) -> None:
        """V3: Symuluj wciśnięcie przycisku (press + release)."""
        self._bus.button_event.emit(idx, 1)
        QTimer.singleShot(150, lambda: self._bus.button_event.emit(idx, 0))

    def _on_button_changed(self, idx: int, cfg: ButtonConfig) -> None:
        """Aktualizuj profil w pamięci + debounced zapis."""
        if self._profile is not None and 0 <= idx < len(self._profile.buttons):
            self._profile.buttons[idx] = cfg
            self._schedule_save()

    def refresh_labels(self) -> None:
        """V1.0.3b: Synchronizuj tytuły kart z profilem (bez rebuildu)."""
        if self._profile is None:
            return
        for i in range(1, self._content.count()):
            item = self._content.itemAt(i)
            w = item.widget() if item is not None else None
            if isinstance(w, ButtonRow) and 0 <= w._config.idx < len(self._profile.buttons):
                w.update_config(self._profile.buttons[w._config.idx])


# ============================================================
class SettingsPage(QWidget):
    """Strona ustawień - profile, tuning MCU, auto-switch, wygląd, autostart,
    urządzenie audio, diagnostyka połączenia, odinstalowanie.

    Pełna wersja zbudowana z kart (``_card`` helper). Sygnał ``accent_changed``
    pozwala MainWindow na żywo przebarwić aplikację.
    """

    accent_changed = Signal(str)   # kolor akcentu (hex)

    def __init__(self, bus: EventBus, connection: ConnectionManager,
                 profile_mgr=None, audio_backend=None, settings=None,
                 parent=None):
        super().__init__(parent)
        self._bus = bus
        self._conn = connection
        self._profile_mgr = profile_mgr
        self._audio = audio_backend
        self._settings = settings if settings is not None else Settings()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        title = QLabel("USTAWIENIA", objectName="sectionTitle")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        outer.addWidget(title)

        # V1.3.4: Zakładki zamiast jednego długiego scrolla. Wcześniej 11 kart
        # w jednym QScrollArea (~2000+ px) — karta „Gry" była 5. i przy DPI
        # 125-150% jej przycisk Zapisz lądował poza ekranem (niewidoczny
        # scrollbar nie pomagał). Teraz każda zakładka to krótki scroll.
        # V8: Lazy tab infrastructure — each tab has a builder method.
        # Currently all tabs are built eagerly in __init__ (tests access
        # private attributes like _game_input directly). To enable lazy
        # loading later, replace the eager loop below with:
        #   for i, label in enumerate(self._tab_labels):
        #       self._tabs.addTab(QWidget(), label)
        #   self._build_tab(0)  # build first tab only
        self._tabs = QTabWidget()
        self._tab_built: dict[int, bool] = {i: False for i in range(5)}
        self._tabs.currentChanged.connect(self._on_tab_changed)
        outer.addWidget(self._tabs, stretch=1)

        # Tab labels and builder methods
        self._tab_labels = ["Profile", "Gry", "Sterowanie", "System", "Info"]
        self._tab_builders = [
            self._build_profile_tab,
            self._build_games_tab,
            self._build_control_tab,
            self._build_system_tab,
            self._build_info_tab,
        ]

        # Build all tabs eagerly (tests access private attributes directly)
        for i, label in enumerate(self._tab_labels):
            tab_widget = self._tab_builders[i]()
            self._tabs.addTab(tab_widget, label)
            self._tab_built[i] = True

        # V6: Debounced settings save — eliminuje 30-60 zapisów settings.json
        # na sekundę podczas przeciągania suwaków CFG/audio device/etc.
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)  # 500 ms debounce
        self._save_timer.timeout.connect(self._flush_save)

    def _on_tab_changed(self, index: int) -> None:
        """V8: Build tab content on first visit."""
        self._build_tab(index)

    def _build_tab(self, index: int) -> None:
        """V8: Build tab content if not yet built."""
        if self._tab_built.get(index, False):
            return
        if index < 0 or index >= len(self._tab_builders):
            return
        # Block signals during tab replacement to prevent recursion
        # (removeTab/insertTab triggers currentChanged again)
        self._tabs.blockSignals(True)
        try:
            builder = self._tab_builders[index]
            tab_widget = builder()
            # Replace placeholder with built content
            self._tabs.removeTab(index)
            self._tabs.insertTab(index, tab_widget, self._tab_labels[index])
            self._tabs.setCurrentIndex(index)
            self._tab_built[index] = True
        finally:
            self._tabs.blockSignals(False)

    def _build_profile_tab(self) -> QWidget:
        """V8: Profile tab — built lazily on first visit."""
        profile_cards = []
        if self._profile_mgr is not None:
            profile_cards.append(self._card_profile())
        profile_cards.append(self._card_auto_switch())
        return self._tab_page(profile_cards)

    def _build_games_tab(self) -> QWidget:
        """V8: Games tab — built lazily on first visit."""
        return self._tab_page([self._card_game_apps()])

    def _build_control_tab(self) -> QWidget:
        """V8: Control tab — built lazily on first visit."""
        return self._tab_page([self._card_filter_tuning(), self._card_pot_invert()])

    def _build_system_tab(self) -> QWidget:
        """V8: System tab — built lazily on first visit."""
        return self._tab_page([
            self._card_autostart(), self._card_appearance(),
            self._card_tray(), self._card_audio_device(),
        ])

    def _build_info_tab(self) -> QWidget:
        """V8: Info tab — built lazily on first visit."""
        return self._tab_page([self._card_about(), self._card_connection()])

    def _tab_page(self, cards: list) -> QWidget:
        """Zbuduj stronę zakładki: scroll area z kartami (każda osobno)."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        inner = QWidget()
        scroll.setWidget(inner)
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        for card in cards:
            lay.addWidget(card)
        lay.addStretch()
        return scroll

    # ============================================================
    # Helper budujący kartę
    # ============================================================
    def _card(self, title: str, icon: str = "settings") -> tuple[QFrame, QVBoxLayout]:
        card = _Card(objectName="card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(24, 20, 24, 20)
        cl.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(10)
        ic = IconLabel(icon, color=self._settings.accent_color, size=20)
        card._icon_ref = ic  # zapobiega GC
        head.addWidget(ic)
        lbl = QLabel(title.upper(), objectName="labelMuted")
        lbl.setStyleSheet("font-size: 13px; letter-spacing: 1px;")
        head.addWidget(lbl)
        head.addStretch()
        cl.addLayout(head)
        return card, cl

    def _row(self, label: str, widget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(label.upper(), objectName="labelMuted")
        lbl.setFixedWidth(150)
        row.addWidget(lbl)
        row.addWidget(widget, stretch=1)
        return row

    def _save_settings(self) -> None:
        """V6: Debounced — wystartuj timer, zapis nastąpi 500 ms po ostatniej
        zmianie. Eliminuje 30-60 zapisów/s podczas przeciągania suwaków."""
        self._save_timer.start()

    def _flush_save(self) -> None:
        """Zapisz settings.json do dysku (debounced)."""
        try:
            self._settings.to_json(settings_path())
        except Exception:
            log.exception("settings save failed")

    def _notify(self, level: str, msg: str) -> None:
        try:
            self._bus.notify.emit(level, msg)
        except Exception:
            pass

    # ============================================================
    # Karty
    # ============================================================
    def _card_profile(self) -> QFrame:
        card, cl = self._card("Zarządzanie profilami", "copy")
        if self._profile_mgr is not None:
            sw = ProfileSwitcher(self._profile_mgr,
                                 accent=self._settings.accent_color)
            self._profile_switcher = sw
            cl.addWidget(sw)
            cl.addWidget(QLabel(
                "Przełącz aktywny profil lub utwórz/zmień nazwę/duplikuj/"
                "usuń/importuj/eksportuj.",
                objectName="sectionSubtitle"))
        return card

    def _card_filter_tuning(self) -> QFrame:
        card, cl = self._card("Filtr ADC (MCU — CFG_CMD)", "filter")
        cl.addWidget(QLabel(
            "Dostrój filtr potencjometrów w urządzeniu. Wartości wysyłane "
            "komendą CFG_CMD (wymaga wsparcia firmware'u).",
            objectName="sectionSubtitle"))

        cfg = self._settings.cfg_tuning
        self._cfg_sliders: dict[str, QSlider] = {}
        self._cfg_labels: dict[str, QLabel] = {}
        for key, label, hi in [
            ("deadband", "Deadband (szum)", 64),
            ("alpha_slow", "α wolny (Q8)", 255),
            ("alpha_fast", "α szybki (Q8)", 255),
            ("send_thr", "Próg wysyłki", 128),
        ]:
            sld = QSlider(Qt.Horizontal)
            sld.setRange(0, hi)
            val = int(getattr(cfg, key))
            sld.setValue(val)
            val_lbl = QLabel(str(val))
            val_lbl.setFixedWidth(36)
            val_lbl.setStyleSheet("color: #2DD4FF; font-weight: 600;")
            sld.valueChanged.connect(lambda v, k=key, l=val_lbl: self._on_cfg_changed(k, v, l))
            self._cfg_sliders[key] = sld
            self._cfg_labels[key] = val_lbl
            row = self._row(label, sld)
            row.addWidget(val_lbl)
            cl.addLayout(row)

        btn_row = QHBoxLayout()
        send_btn = QPushButton("Wyślij do urządzenia", objectName="primaryBtn")
        send_btn.setCursor(Qt.PointingHandCursor)
        send_btn.clicked.connect(self._send_cfg)
        btn_row.addWidget(send_btn)
        btn_row.addStretch()
        cl.addLayout(btn_row)
        return card

    def _on_cfg_changed(self, key: str, value: int, lbl: QLabel) -> None:
        lbl.setText(str(value))
        setattr(self._settings.cfg_tuning, key, value)
        self._save_settings()

    def _send_cfg(self) -> None:
        c = self._settings.cfg_tuning
        ok = self._conn.send_frame(
            make_cfg_cmd(c.deadband, c.alpha_slow, c.alpha_fast, c.send_thr))
        self._notify("success" if ok else "warning",
                     "CFG_CMD wysłany" if ok else "CFG_CMD nie wysłany (brak połączenia)")

    def _card_pot_invert(self) -> QFrame:
        """Globalne odwrócenie kierunku wszystkich potencjometrów (hw wiring fix).

        XOR'uje się z per-pot 'invert' checkbox w profilu — oba razem dają
        brak odwrócenia. Zapewnia szybkie rozwiązanie gdy pota są podłączone
        odwrotnie na poziomie sprzętowym (CCW = max zamiast min).
        """
        card, cl = self._card("Potencjometry", "sliders")
        cl.addWidget(QLabel(
            "Odwróć kierunek WSZYSTKICH potencjometrów. Przydatne gdy są "
            "podłączone odwrotnie sprzętowo (kręcenie w prawo zmniejsza zamiast "
            "zwiększać). Działa niezależnie od per-pot 'Odwróć kierunek' w "
            "ustawieniach zaawansowanych (oba się XOR'ują).",
            objectName="sectionSubtitle"))
        cb = QCheckBox("Odwróć kierunek wszystkich potencjometrów")
        cb.setChecked(bool(getattr(self._settings, "invert_all_pots", False)))
        cb.toggled.connect(self._on_pot_invert_toggled)
        cl.addWidget(cb)
        return card

    def _on_pot_invert_toggled(self, checked: bool) -> None:
        self._settings.invert_all_pots = bool(checked)
        self._save_settings()
        self._notify("info",
                     "Globalne odwrócenie potencjometrów: WŁĄCZONE" if checked
                     else "Globalne odwrócenie potencjometrów: wyłączone")

    def _card_auto_switch(self) -> QFrame:
        card, cl = self._card("Auto-przełączanie profili", "branch")
        cl.addWidget(QLabel(
            "Powiąż nazwę procesu aktywnego okna z profilem. Gdy okno staje "
            "się aktywne, profil przełączy się automatycznie.",
            objectName="sectionSubtitle"))
        self._rules_widget = QWidget()
        self._rules_lay = QVBoxLayout(self._rules_widget)
        self._rules_lay.setContentsMargins(0, 0, 0, 0)
        self._rules_lay.setSpacing(6)
        cl.addWidget(self._rules_widget)

        add_row = QHBoxLayout()
        self._rule_proc = QLineEdit()
        self._rule_proc.setPlaceholderText("np. discord / spotify.exe")
        self._rule_profile = QComboBox()
        if self._profile_mgr is not None:
            self._rule_profile.addItems(self._profile_mgr.list_profiles())
        add_btn = QPushButton("+ Dodaj regułę")
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(self._add_rule)
        add_row.addWidget(self._rule_proc, stretch=1)
        add_row.addWidget(self._rule_profile, stretch=1)
        add_row.addWidget(add_btn)
        cl.addLayout(add_row)

        self._reload_rules()
        return card

    def _reload_rules(self) -> None:
        # Wyczyść listę
        while self._rules_lay.count():
            it = self._rules_lay.takeAt(0)
            w = it.widget() if it is not None else None
            if w is not None:
                w.deleteLater()
        for proc, prof in sorted(self._settings.auto_switch_rules.items()):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(QLabel(f"🎧  {proc}", objectName="sectionSubtitle"))
            row.addWidget(QLabel("→", objectName="labelMuted"))
            row.addWidget(QLabel(prof, objectName="sectionSubtitle"))
            btn = QPushButton("✕")
            btn.setFixedSize(26, 26)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, p=proc: self._remove_rule(p))
            row.addWidget(btn)
            row.addStretch()
            container = QWidget()
            container.setLayout(row)
            self._rules_lay.addWidget(container)

    def _add_rule(self) -> None:
        proc = self._rule_proc.text().strip()
        prof = self._rule_profile.currentText().strip()
        if not proc or not prof:
            return
        self._settings.set_rule(proc, prof)
        self._save_settings()
        if self._profile_mgr is not None:
            self._profile_mgr.set_rules(self._settings.auto_switch_rules)
        self._rule_proc.clear()
        self._reload_rules()
        self._notify("success", f"Reguła dodana: {proc} → {prof}")

    def _remove_rule(self, proc: str) -> None:
        self._settings.set_rule(proc, "")
        self._save_settings()
        if self._profile_mgr is not None:
            self._profile_mgr.set_rules(self._settings.auto_switch_rules)
        self._reload_rules()

    def _card_game_apps(self) -> QFrame:
        """Lista procesów oznaczonych jako gry (dla PotAction.GAME_VOLUME)."""
        card, cl = self._card("Gry", "gamepad")
        cl.addWidget(QLabel(
            "Wpisz nazwy procesów gier (można wiele, rozdzielone przecinkiem) "
            "i kliknij Zapisz. Potencjometr z akcją „Gra (auto)„ automatycznie "
            "wykryje która gra jest aktywna i steruje jej głośnością.",
            objectName="sectionSubtitle"))

        # V1.3.4: Wiersz input+Zapisz NA GÓRZE karty (nad listą) — przycisk
        # widoczny natychmiast po wejściu w zakładkę, bez scrollowania.
        add_row = QHBoxLayout()
        self._game_input = QLineEdit()
        self._game_input.setPlaceholderText("np. cs2.exe, witcher3.exe")
        game_save_btn = QPushButton("💾  Zapisz")
        game_save_btn.setCursor(Qt.PointingHandCursor)
        game_save_btn.clicked.connect(self._save_games)
        self._game_input.returnPressed.connect(self._save_games)
        add_row.addWidget(self._game_input, stretch=1)
        add_row.addWidget(game_save_btn)
        cl.addLayout(add_row)

        self._games_widget = QWidget()
        self._games_lay = QVBoxLayout(self._games_widget)
        self._games_lay.setContentsMargins(0, 0, 0, 0)
        self._games_lay.setSpacing(6)
        cl.addWidget(self._games_widget)

        self._reload_games()
        return card

    def _reload_games(self) -> None:
        while self._games_lay.count():
            it = self._games_lay.takeAt(0)
            w = it.widget() if it is not None else None
            if w is not None:
                w.deleteLater()
        for app in sorted(self._settings.game_apps):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(QLabel(f"🎮  {app}", objectName="sectionSubtitle"))
            btn = QPushButton("✕")
            btn.setFixedSize(26, 26)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, a=app: self._remove_game(a))
            row.addWidget(btn)
            row.addStretch()
            container = QWidget()
            container.setLayout(row)
            self._games_lay.addWidget(container)

    @staticmethod
    def _parse_game_names(raw: str) -> list[str]:
        """Sparsuj input użytkownika na listę nazw procesów.

        Akceptuje wiele nazw rozdzielonych przecinkiem, średnikiem lub białymi
        znakami. Trim + lowercase + deduplikacja (zachowując kolejność wpisania).
        """
        tokens = re.split(r"[,;\s]+", (raw or "").strip())
        seen: list[str] = []
        for t in tokens:
            name = t.strip().lower()
            if name and name not in seen:
                seen.append(name)
        return seen

    def _save_games(self) -> None:
        """Zapisz nazwy procesów z pola wejściowego do settings (natychmiast, bez debounce).

        V1.3.2: Zastąpił natychmiastowe „+ Dodaj". Natychmiastowy flush na dysk
        (bez 500 ms debouncera) eliminuje utratę wpisu przy szybkim zamknięciu
        aplikacji po zapisie.
        """
        names = self._parse_game_names(self._game_input.text())
        if not names:
            self._notify("info", "Nic do zapisania — wpisz nazwę procesu gry.")
            return
        added = []
        for name in names:
            if name not in self._settings.game_apps:
                self._settings.game_apps.append(name)
                added.append(name)
        self._game_input.clear()
        self._reload_games()
        if added:
            self._flush_save()
            self._notify("success", f"Zapisano: {', '.join(added)}")
        else:
            self._notify("info", "Wszystkie wpisane gry są już na liście.")

    def _remove_game(self, name: str) -> None:
        try:
            self._settings.game_apps.remove(name)
        except ValueError:
            pass
        self._flush_save()
        self._reload_games()

    def _card_appearance(self) -> QFrame:
        card, cl = self._card("Wygląd — akcent", "palette")
        cl.addWidget(QLabel("Wybierz kolor akcentu aplikacji.",
                            objectName="sectionSubtitle"))
        swatches = QHBoxLayout()
        swatches.setSpacing(10)
        self._accent_buttons: dict[str, QPushButton] = {}
        for key, color in ACCENTS.items():
            b = QPushButton()
            b.setFixedSize(34, 34)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(key)
            b.setStyleSheet(
                f"QPushButton {{ background: {color}; border-radius: 17px; "
                f"border: 3px solid rgba(255,255,255,40); }}"
                f"QPushButton:hover {{ border: 3px solid #F5F7FA; }}"
            )
            if key == self._settings.accent:
                b.setStyleSheet(
                    f"QPushButton {{ background: {color}; border-radius: 17px; "
                    f"border: 3px solid #F5F7FA; }}"
                )
            b.clicked.connect(lambda _=False, k=key: self._set_accent(k))
            self._accent_buttons[key] = b
            swatches.addWidget(b)
        swatches.addStretch()
        cl.addLayout(swatches)
        return card

    def _set_accent(self, key: str) -> None:
        self._settings.accent = key
        self._save_settings()
        color = self._settings.accent_color
        self.accent_changed.emit(color)
        # Odśwież podświetlenie swatch'y
        for k, b in self._accent_buttons.items():
            sel = (k == key)
            c = ACCENTS[k]
            b.setStyleSheet(
                f"QPushButton {{ background: {c}; border-radius: 17px; "
                f"border: 3px solid {'#F5F7FA' if sel else 'rgba(255,255,255,40)'}; }}"
                f"QPushButton:hover {{ border: 3px solid #F5F7FA; }}"
            )
        self._notify("info", f"Akcent: {key}")

    def _card_autostart(self) -> QFrame:
        card, cl = self._card("Autostart z systemem", "power")
        cb = QCheckBox("Uruchom Simple Deck przy logowaniu do systemu")
        cb.setChecked(self._settings.autostart)
        cb.toggled.connect(self._on_autostart)
        cl.addWidget(cb)
        return card

    def _on_autostart(self, checked: bool) -> None:
        self._settings.autostart = checked
        self._save_settings()
        ok = self._apply_autostart(checked)
        self._notify("success" if ok else "warning",
                     "Autostart zaktualizowany" if ok else "Nie udało się ustawić autostartu")

    def _card_tray(self) -> QFrame:
        """V6: Karta ustawień ikony w zasobniku systemowym (tray)."""
        card, cl = self._card("Zasobnik systemowy (tray)", "monitor")
        cl.addWidget(QLabel(
            "Ikona w zasobniku systemowym pokazuje stan połączenia i pozwala "
            "szybko pokazać/ukryć okno. Kiedy włączone, zamykanie okna może "
            "je ukrywać do tray'a zamiast kończyć aplikację.",
            objectName="sectionSubtitle"))
        cb_tray = QCheckBox("Pokaż ikonę w zasobniku systemowym")
        cb_tray.setChecked(bool(getattr(self._settings, "show_tray_icon", False)))
        cb_tray.toggled.connect(self._on_tray_toggled)
        cl.addWidget(cb_tray)
        cb_minimize = QCheckBox("Zamykanie okna = ukryj do tray'a (zamiast zakończ)")
        cb_minimize.setChecked(bool(getattr(self._settings, "minimize_to_tray_on_close", False)))
        cb_minimize.toggled.connect(self._on_minimize_to_tray_toggled)
        cl.addWidget(cb_minimize)

        cb_start_min = QCheckBox("Uruchom zminimalizowane przy starcie")
        cb_start_min.setChecked(
            bool(getattr(self._settings, "start_minimized_to_tray", False)))
        cb_start_min.toggled.connect(self._on_start_minimized_toggled)
        cl.addWidget(cb_start_min)

        cb_notif = QCheckBox("Pokazuj powiadomienia aplikacji")
        cb_notif.setChecked(bool(getattr(self._settings, "notifications_enabled", True)))
        cb_notif.toggled.connect(self._on_notifications_toggled)
        cl.addWidget(cb_notif)
        return card

    def _on_tray_toggled(self, checked: bool) -> None:
        self._settings.show_tray_icon = bool(checked)
        self._save_settings()
        self._notify("info",
                     "Tray: WŁĄCZONY — restartuj aplikację by zastosować" if checked
                     else "Tray: wyłączony — restartuj aplikację by zastosować")

    def _on_minimize_to_tray_toggled(self, checked: bool) -> None:
        self._settings.minimize_to_tray_on_close = bool(checked)
        self._save_settings()

    def _on_start_minimized_toggled(self, checked: bool) -> None:
        self._settings.start_minimized_to_tray = bool(checked)
        self._save_settings()

    def _on_notifications_toggled(self, checked: bool) -> None:
        self._settings.notifications_enabled = bool(checked)
        self._save_settings()

    def _apply_autostart(self, enable: bool) -> bool:
        try:
            if sys.platform.startswith("win"):
                import winreg
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                     r"Software\Microsoft\Windows\CurrentVersion\Run",
                                     0, winreg.KEY_SET_VALUE)
                try:
                    if enable:
                        # V8 fix: For PyInstaller-frozen bundles, sys.executable
                        # is the Simple-Deck.exe launcher. The "-m simple_deck"
                        # argument is Python-only and causes the bootloader to
                        # fail with "Failed to launch python embedded interface"
                        # during Windows autostart (which runs before the user's
                        # shell is fully up). Detect frozen state and use just
                        # the executable path - PyInstaller's bootloader handles
                        # the rest.
                        if getattr(sys, "frozen", False):
                            cmd = f'"{sys.executable}"'
                        else:
                            cmd = f'"{sys.executable}" -m simple_deck'
                        winreg.SetValueEx(key, "SIMPLEDECK", 0, winreg.REG_SZ, cmd)
                        # Wyczyść legacy wpis po aktualizacji z grejem-os.
                        try:
                            winreg.DeleteValue(key, "GREJEMOS")
                        except FileNotFoundError:
                            pass
                    else:
                        for name in ("SIMPLEDECK", "GREJEMOS"):
                            try:
                                winreg.DeleteValue(key, name)
                            except FileNotFoundError:
                                pass
                finally:
                    key.Close()
                return True
            # Linux/macOS: ~/.config/autostart/simple-deck.desktop
            auto_dir = Path.home() / ".config" / "autostart"
            entry = auto_dir / "simple-deck.desktop"
            legacy_entry = auto_dir / "grejem-os.desktop"
            if enable:
                auto_dir.mkdir(parents=True, exist_ok=True)
                launcher = Path.home() / ".local" / "bin" / "simple-deck"
                entry.write_text(
                    "[Desktop Entry]\nType=Application\nName=Simple Deck\n"
                    f"Exec={launcher}\nTerminal=false\nX-GNOME-Autostart-enabled=true\n",
                    encoding="utf-8")
                # Wyczyść legacy wpis po aktualizacji z grejem-os.
                if legacy_entry.exists():
                    try:
                        legacy_entry.unlink()
                    except OSError:
                        log.warning("nie udało się usunąć legacy autostart: %s",
                                    legacy_entry)
            else:
                if entry.exists():
                    entry.unlink()
                if legacy_entry.exists():
                    try:
                        legacy_entry.unlink()
                    except OSError:
                        log.warning("nie udało się usunąć legacy autostart: %s",
                                    legacy_entry)
            return True
        except Exception:
            log.exception("autostart apply failed")
            return False

    def _card_audio_device(self) -> QFrame:
        card, cl = self._card("Urządzenie wyjściowe audio", "volume")
        cl.addWidget(QLabel(
            "Wybierz domyślne urządzenie wyjściowe (regulacja głośności "
            "systemowej dotyczy tego urządzenia).",
            objectName="sectionSubtitle"))
        self._device_combo = QComboBox()
        self._device_combo.addItem("Domyślne urządzenie", "")
        if self._audio is not None:
            try:
                for name, desc in self._audio.list_output_devices():
                    self._device_combo.addItem(desc, name)
            except Exception:
                log.exception("list_output_devices failed")
        # Wybierz zapisane
        if self._settings.audio_output_device:
            i = self._device_combo.findData(self._settings.audio_output_device)
            if i >= 0:
                self._device_combo.setCurrentIndex(i)
        self._device_combo.currentIndexChanged.connect(self._on_device_changed)
        cl.addLayout(self._row("Urządzenie", self._device_combo))
        return card

    def _on_device_changed(self, idx: int) -> None:
        name = self._device_combo.itemData(idx) or ""
        self._settings.audio_output_device = name
        self._save_settings()
        if self._audio is not None:
            if hasattr(self._audio, "set_output_device"):
                self._audio.set_output_device(name)
                self._notify("success", "Urządzenie audio zmienione")
            elif name:
                self._notify("warning", "Zmiana urządzenia nie jest obsługiwana na tej platformie")

    def _card_about(self) -> QFrame:
        card, cl = self._card("O aplikacji", "home")
        cl.addWidget(QLabel(f"Simple Deck  ·  v{__version__}", objectName="labelLarge"))
        cl.addWidget(QLabel("by GREJEM INDUSTRIES", objectName="sectionSubtitle"))
        line = QFrame(objectName="hLine")
        cl.addWidget(line)
        cl.addWidget(QLabel(
            "Aplikacja kontrolna dla urządzenia GREJEM Stream Deck.\n"
            "Firmware: STM32F103C6T6 (libopencm3, USB Custom HID).",
            objectName="sectionSubtitle"))
        return card

    def _card_connection(self) -> QFrame:
        card, cl = self._card("Połączenie USB", "refresh")
        cl.addWidget(QLabel("VID:PID  0x1209:0xDE10  (pid.codes)",
                            objectName="sectionSubtitle"))
        cl.addWidget(QLabel("Protokół: binarny, CRC16-CCITT, 64 B raport HID",
                            objectName="sectionSubtitle"))
        cl.addWidget(QLabel("Heartbeat: 1500 ms (timeout 4500 ms)",
                            objectName="sectionSubtitle"))
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_reconnect = QPushButton("🔄  Wymuś reconnect", objectName="primaryBtn")
        btn_reconnect.setCursor(Qt.PointingHandCursor)
        btn_reconnect.clicked.connect(self._force_reconnect)
        btn_row.addWidget(btn_reconnect)
        btn_uninstall = QPushButton("Odinstaluj Simple Deck")
        btn_uninstall.setCursor(Qt.PointingHandCursor)
        btn_uninstall.setStyleSheet(
            "QPushButton { background: rgba(255,92,108,40); border: 1px solid #FF5C6C;"
            " border-radius: 8px; color: #FF5C6C; padding: 6px 12px; }"
            "QPushButton:hover { background: rgba(255,92,108,80); }")
        btn_uninstall.clicked.connect(self._uninstall)
        btn_row.addWidget(btn_uninstall)
        btn_row.addStretch()
        cl.addLayout(btn_row)
        return card

    # ============================================================
    # Akcje
    # ============================================================
    def _force_reconnect(self) -> None:
        self._conn.stop()
        self._conn.start()
        self._notify("info", "Ponowne łączenie…")

    def _uninstall(self) -> None:
        """Uruchom deinstalator platformowy i zamknij aplikację."""
        from PySide6.QtWidgets import QApplication, QMessageBox
        btn = QMessageBox.question(
            self, "Odinstaluj Simple Deck",
            "Uruchomić deinstalator? Aplikacja zostanie zamknięta.\n"
            "(Twoje profile pozostaną zachowane).",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if btn != QMessageBox.Yes:
            return
        import subprocess
        try:
            if sys.platform.startswith("win"):
                # Inno Setup uninstaller w katalogu aplikacji
                from pathlib import Path as _P
                app_dir = _P(sys.executable).parent
                unins = app_dir / "unins000.exe"
                if unins.exists():
                    subprocess.Popen([str(unins)], close_fds=True)
                else:
                    self._notify("error", "Nie znaleziono unins000.exe")
                    return
            else:
                # Linux: install.sh --uninstall z katalogu instalatora
                # Szukaj względem źródła lub w PATH
                candidates = [
                    Path(__file__).resolve().parents[5] / "installer" / "linux" / "install.sh",
                    Path.home() / ".local" / "share" / "grejem-os" / "install.sh",
                ]
                inst = next((c for c in candidates if c.exists()), None)
                if inst is None:
                    self._notify("error", "Nie znaleziono install.sh")
                    return
                subprocess.Popen(["sh", str(inst), "--uninstall"], close_fds=True)
            QApplication.quit()
        except Exception:
            log.exception("uninstall launch failed")
            self._notify("error", "Nie udało się uruchomić deinstalatora")
