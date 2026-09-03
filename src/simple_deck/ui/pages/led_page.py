"""Strona LED - konfiguracja trybu linijki LED (3 diody).

V3: Użytkownik wybiera tryb (VU bar, Solid, Breathing, Chase, Knight Rider,
Strobe, Button indicator, Manual), ustawia jasność/szybkość/duty, a strona
wysyła ramkę LED_CMD do MCU i zapisuje ustawienia w profilu.
"""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout,
                                QLabel, QScrollArea, QSlider, QVBoxLayout,
                                QWidget)

from ...core.event_bus import EventBus
from ...core.profile import LedMode, Profile
from ...core.profile_manager import ProfileManager
from ...transport.connection_manager import ConnectionManager
from ...transport.protocol import (ACTIVE_LED_COUNT, make_led_manual_cmd,
                                    make_led_mode_cmd)

log = logging.getLogger(__name__)

# Tryby dostępne w UI (nazwa wyświetlana, wartość LedMode)
LED_MODES_UI = [
    ("VU Bar (głośność)",      LedMode.VU_BAR),
    ("Stałe (Solid)",          LedMode.SOLID),
    ("Oddychanie (Breathing)", LedMode.BREATHING),
    ("Pościg (Chase)",         LedMode.CHASE),
    ("Knight Rider",           LedMode.KNIGHT_RIDER),
    ("Stroboskop (Strobe)",    LedMode.STROBE_BAR),
    ("Przyciski (Buttons)",    LedMode.BUTTONS),
    ("Ręczny (Manual)",        LedMode.MANUAL),
]

# Domyślne okresy animacji per tryb (ms)
DEFAULT_SPEED = {
    LedMode.BREATHING: 3000,
    LedMode.CHASE: 450,
    LedMode.KNIGHT_RIDER: 600,
    LedMode.STROBE_BAR: 120,
}


class LedPage(QWidget):
    """V3: Strona konfiguracji trybu linijki LED."""

    SAVE_DEBOUNCE_MS = 500

    def __init__(self, bus: EventBus, connection: ConnectionManager,
                 profile_mgr: Optional[ProfileManager] = None, parent=None):
        super().__init__(parent)
        self._bus = bus
        self._conn = connection
        self._profile_mgr = profile_mgr
        self._profile: Optional[Profile] = None
        self._suspend_signals = False

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
        title_lbl = QLabel("LED", objectName="sectionTitle")
        title_lbl.setStyleSheet("font-size: 22px; font-weight: 700; background: transparent;")
        sub_lbl = QLabel(f"{ACTIVE_LED_COUNT} diody  ·  8 trybów", objectName="sectionSubtitle")
        head.addWidget(title_lbl)
        head.addStretch()
        head.addWidget(sub_lbl, alignment=Qt.AlignBottom)
        self._content.addLayout(head)

        # === Tryb ===
        self._mode_combo = QComboBox()
        for display_name, mode_val in LED_MODES_UI:
            self._mode_combo.addItem(display_name, mode_val.value)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self._add_row("Tryb", self._mode_combo)

        # === Jasność ===
        self._brightness_slider = QSlider(Qt.Horizontal)
        self._brightness_slider.setRange(0, 255)
        self._brightness_slider.setValue(255)
        self._brightness_slider.valueChanged.connect(self._on_brightness_changed)
        self._brightness_label = QLabel("255")
        self._brightness_label.setFixedWidth(40)
        self._brightness_row = self._add_row("Jasność", self._brightness_slider, self._brightness_label)

        # === Szybkość animacji ===
        self._speed_slider = QSlider(Qt.Horizontal)
        self._speed_slider.setRange(50, 5000)
        self._speed_slider.setValue(1000)
        self._speed_slider.valueChanged.connect(self._on_speed_changed)
        self._speed_label = QLabel("1000 ms")
        self._speed_label.setFixedWidth(70)
        self._speed_row = self._add_row("Szybkość", self._speed_slider, self._speed_label)

        # === Duty cycle (tylko Strobe) ===
        self._duty_slider = QSlider(Qt.Horizontal)
        self._duty_slider.setRange(10, 90)
        self._duty_slider.setValue(50)
        self._duty_slider.valueChanged.connect(self._on_duty_changed)
        self._duty_label = QLabel("50 %")
        self._duty_label.setFixedWidth(50)
        self._duty_row = self._add_row("Duty (Strobe)", self._duty_slider, self._duty_label)

        # === Per-LED (tylko Manual) — Master + individual ===
        self._manual_widget = QWidget()
        manual_lay = QVBoxLayout(self._manual_widget)
        manual_lay.setContentsMargins(0, 0, 0, 0)
        manual_lay.setSpacing(8)

        # Master slider — ustaw wszystkie LEDy jednocześnie
        master_row = QHBoxLayout()
        master_lbl = QLabel("WSZYSTKIE")
        master_lbl.setFixedWidth(80)
        master_lbl.setStyleSheet("font-weight: 700; color: #2DD4FF;")
        self._master_slider = QSlider(Qt.Horizontal)
        self._master_slider.setRange(0, 255)
        self._master_slider.setValue(0)
        self._master_label = QLabel("0")
        self._master_label.setFixedWidth(40)
        self._master_label.setStyleSheet("color: #2DD4FF; font-weight: 600;")
        self._master_slider.valueChanged.connect(self._on_master_changed)
        master_row.addWidget(master_lbl)
        master_row.addWidget(self._master_slider)
        master_row.addWidget(self._master_label)
        manual_lay.addLayout(master_row)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: rgba(255,255,255,20); background: rgba(255,255,255,20); max-height: 1px;")
        manual_lay.addWidget(sep)

        self._manual_sliders: list[QSlider] = []
        for i in range(ACTIVE_LED_COUNT):
            row = QHBoxLayout()
            lbl = QLabel(f"LED {i + 1}")
            lbl.setFixedWidth(80)
            sld = QSlider(Qt.Horizontal)
            sld.setRange(0, 255)
            sld.setValue(0)
            sld.valueChanged.connect(lambda v, idx=i: self._on_manual_changed(idx, v))
            val_lbl = QLabel("0")
            val_lbl.setFixedWidth(40)
            sld.valueChanged.connect(lambda v, lbl=val_lbl: lbl.setText(str(v)))
            row.addWidget(lbl)
            row.addWidget(sld)
            row.addWidget(val_lbl)
            manual_lay.addLayout(row)
            self._manual_sliders.append(sld)
        self._content.addWidget(self._manual_widget)

        # === Info label ===
        self._info_label = QLabel("")
        self._info_label.setStyleSheet("color: rgba(255,255,255,128); font-size: 12px;")
        self._info_label.setWordWrap(True)
        self._content.addWidget(self._info_label)

        self._content.addStretch()

        # Debounced save
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(self.SAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self._flush_save)

        # Visibility init
        self._update_visibility()

    def _add_row(self, label_text: str, *widgets) -> QWidget:
        """Dodaj wiersz: etykieta + widget(y).

        Każdy wiersz jest owinięty własnym QWidget'em aby można było
        go ukryć (setVisible(False)) bez wpływu na inne wiersze.
        Zwraca wrapper widget.
        """
        wrapper = QWidget()
        wrapper.setStyleSheet("background: transparent;")
        row = QHBoxLayout(wrapper)
        row.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(label_text)
        lbl.setFixedWidth(130)
        row.addWidget(lbl)
        for w in widgets:
            row.addWidget(w)
        self._content.addWidget(wrapper)
        return wrapper

    def set_profile(self, profile: Profile) -> None:
        """Załaduj ustawienia z profilu."""
        self._profile = profile
        self._suspend_signals = True

        # Znajdź indeks trybu w combo
        mode_val = profile.led_mode
        for i in range(self._mode_combo.count()):
            if self._mode_combo.itemData(i) == mode_val:
                self._mode_combo.setCurrentIndex(i)
                break

        self._brightness_slider.setValue(profile.led_brightness)
        self._speed_slider.setValue(profile.led_speed_ms)
        for i, sld in enumerate(self._manual_sliders):
            if i < len(profile.led_per_led):
                sld.setValue(profile.led_per_led[i])
        vals = [profile.led_per_led[i] if i < len(profile.led_per_led) else 0
                for i in range(len(self._manual_sliders))]
        if vals and all(v == vals[0] for v in vals):
            self._master_slider.setValue(vals[0])
            self._master_label.setText(str(vals[0]))

        self._suspend_signals = False
        self._update_visibility()
        self._send_led_cmd()

    def _get_current_mode(self) -> int:
        return self._mode_combo.currentData()

    def _on_mode_changed(self) -> None:
        if self._suspend_signals:
            return
        mode = self._get_current_mode()

        # Jeśli tryb animacji ma domyślną szybkość, zastosuj ją
        led_mode = LedMode(mode)
        if led_mode in DEFAULT_SPEED and self._profile is not None:
            self._suspend_signals = True
            self._speed_slider.setValue(DEFAULT_SPEED[led_mode])
            self._suspend_signals = False

        self._update_visibility()
        self._send_led_cmd()
        self._save_profile_field("led_mode", mode)

    def _on_brightness_changed(self, value: int) -> None:
        if self._suspend_signals:
            return
        self._brightness_label.setText(str(value))
        self._send_led_cmd()
        self._save_profile_field("led_brightness", value)

    def _on_speed_changed(self, value: int) -> None:
        if self._suspend_signals:
            return
        self._speed_label.setText(f"{value} ms")
        self._send_led_cmd()
        self._save_profile_field("led_speed_ms", value)

    def _on_duty_changed(self, value: int) -> None:
        if self._suspend_signals:
            return
        self._duty_label.setText(f"{value} %")
        self._send_led_cmd()

    def _on_master_changed(self, value: int) -> None:
        if self._suspend_signals:
            return
        self._master_label.setText(str(value))
        self._suspend_signals = True
        for sld in self._manual_sliders:
            sld.setValue(value)
        self._suspend_signals = False
        if self._profile is not None:
            for i in range(len(self._manual_sliders)):
                while len(self._profile.led_per_led) <= i:
                    self._profile.led_per_led.append(0)
                self._profile.led_per_led[i] = value
            self._schedule_save()
        self._send_led_cmd()

    def _on_manual_changed(self, idx: int, value: int) -> None:
        if self._suspend_signals:
            return
        self._send_led_cmd()
        if self._profile is not None:
            while len(self._profile.led_per_led) <= idx:
                self._profile.led_per_led.append(0)
            self._profile.led_per_led[idx] = value
            self._schedule_save()

    def _update_visibility(self) -> None:
        """Pokaż/ukryj kontrolki zależnie od trybu."""
        mode = LedMode(self._get_current_mode())

        # Speed slider: tylko dla animacji
        animated = mode in (LedMode.BREATHING, LedMode.CHASE,
                            LedMode.KNIGHT_RIDER, LedMode.STROBE_BAR)
        self._speed_row.setVisible(animated)

        # Duty slider: tylko dla Strobe
        self._duty_row.setVisible(mode == LedMode.STROBE_BAR)

        # Manual sliders: tylko dla Manual
        self._manual_widget.setVisible(mode == LedMode.MANUAL)

        # Info text per mode
        info_map = {
            LedMode.VU_BAR: "Linijka pokazuje poziom głośności — rusz potencjometrem aby aktywować.",
            LedMode.SOLID: "Wszystkie LEDy świecą ciągle z ustawioną jasnością.",
            LedMode.BREATHING: "Wszystkie LEDy pulsują sinusoidalnie.",
            LedMode.CHASE: "Jedna LED biegnie sekwencyjnie w przód.",
            LedMode.KNIGHT_RIDER: "Scanner KITT — pozycja biegnie tam i z powrotem.",
            LedMode.STROBE_BAR: "Wszystkie LEDy migają z regulowanym duty cycle.",
            LedMode.BUTTONS: "Każda LED odzwierciedla stan przycisku (1:1).",
            LedMode.MANUAL: "Ustaw jasność każdej LED niezależnie.",
        }
        self._info_label.setText(info_map.get(mode, ""))

        # Jasność niewidoczna dla VU_BAR (kontrolowana z potencjometrów)
        self._brightness_row.setVisible(mode != LedMode.VU_BAR)

    def _send_led_cmd(self) -> None:
        """Wyślij ramkę LED_CMD do MCU z aktualnymi ustawieniami."""
        mode = self._get_current_mode()

        if mode == LedMode.VU_BAR.value:
            # VU bar nie wymaga statycznej komendy — sterowany z PotDispatchera
            return

        if mode == LedMode.MANUAL.value:
            levels = [sld.value() for sld in self._manual_sliders]
            self._conn.send_frame(make_led_manual_cmd(levels))
        else:
            brightness = self._brightness_slider.value()
            speed = self._speed_slider.value()
            arg = self._duty_slider.value() if mode == LedMode.STROBE_BAR.value else 0
            self._conn.send_frame(make_led_mode_cmd(mode, brightness, speed, arg))

    def _save_profile_field(self, field: str, value) -> None:
        if self._profile is not None:
            setattr(self._profile, field, value)
            self._schedule_save()

    def _schedule_save(self) -> None:
        if self._profile_mgr is not None:
            self._save_timer.start()

    def _flush_save(self) -> None:
        if self._profile_mgr is None or self._profile is None:
            return
        try:
            self._profile_mgr.save(self._profile)
            log.debug("profile '%s' saved (LED settings)", self._profile.name)
        except Exception:
            log.exception("profile save failed")
