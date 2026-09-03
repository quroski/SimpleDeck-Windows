"""Wiersze konfiguracji (potencjometr / przycisk).

Każdy wiersz to karta QFrame#card z layoutem: ikona + tytuł + kontrolka.
Zawiera listę pól specyficznych dla danego typu kontrolki.
"""
from __future__ import annotations


from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout,
                                QLabel, QLineEdit, QPushButton, QSizePolicy,
                                QSlider, QVBoxLayout,
                                QWidget)

from ...core.profile import (ButtonAction, ButtonConfig,
                              PotAction, PotConfig)
from .app_picker import AppPicker
from .hotkey_field import HotkeyField


class _ConfigRow(QFrame):
    """Bazowa karta wiersza konfiguracji."""

    def __init__(self, title: str, glyph: str, badge: int | None = None,
                 parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(16)

        # Lewa kolumna: badge/ikona + tytuł (subklasy mogą dodawać widgety)
        self._left_col = QVBoxLayout()
        self._left_col.setSpacing(4)

        if badge is not None:
            # V4: Cyfrowy badge (koło z numerem kanału) zamiast glifu tekstowego.
            glyph_lbl = QLabel(str(badge), objectName="potBadge")
            glyph_lbl.setAlignment(Qt.AlignCenter)
            glyph_lbl.setFixedSize(36, 36)
        else:
            glyph_lbl = QLabel(glyph, objectName="labelLarge")
            glyph_lbl.setStyleSheet(
                "font-size: 28px; color: #2DD4FF; background: transparent;"
            )
            glyph_lbl.setAlignment(Qt.AlignCenter)
            glyph_lbl.setFixedWidth(40)

        title_lbl = QLabel(title, objectName="sectionTitle")
        title_lbl.setStyleSheet("font-size: 14px;")
        self._left_col.addWidget(glyph_lbl, alignment=Qt.AlignCenter)
        self._left_col.addWidget(title_lbl)
        outer.addLayout(self._left_col)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet("color: rgba(255,255,255,30); background: rgba(255,255,255,30); max-width: 1px;")
        sep.setFixedWidth(1)
        outer.addWidget(sep)

        # Prawa kolumna - kontener na pola
        self._fields_layout = QVBoxLayout()
        self._fields_layout.setSpacing(10)
        outer.addLayout(self._fields_layout, stretch=1)

    def _add_field(self, label: str, widget: QWidget) -> None:
        """Dodaj pole: [mała etykieta] [widget]"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(label.upper(), objectName="labelMuted")
        lbl.setFixedWidth(140)
        row.addWidget(lbl)
        row.addWidget(widget, stretch=1)
        self._fields_layout.addLayout(row)


class PotRow(_ConfigRow):
    """Wiersz konfiguracji potencjometru (z sekcją zaawansowaną)."""

    changed = Signal(int, object)       # idx, PotConfig
    calibrate_requested = Signal(int)    # V3: idx — kalibruj min/max
    move_requested = Signal(int, int)    # V4: physical_idx, direction (-1=góra, +1=dół)

    _REORDER_BTN_QSS = (
        "QPushButton { background: rgba(255,255,255,12);"
        "  border: 1px solid rgba(255,255,255,22); border-radius: 6px;"
        "  color: #A5ABC0; font-size: 11px; padding: 1px 0; min-width: 28px; }"
        "QPushButton:hover { background: rgba(45,212,255,30);"
        "  border-color: rgba(45,212,255,60); color: #2DD4FF; }"
        "QPushButton:disabled { color: rgba(255,255,255,18);"
        "  background: transparent; border-color: rgba(255,255,255,8); }"
    )

    def __init__(self, config: PotConfig, audio_backend=None,
                 settings=None, first: bool = False, last: bool = False,
                 parent=None):
        super().__init__(title=f"Potencjometr {config.idx + 1}", glyph="",
                         badge=config.idx + 1, parent=parent)
        self._config = config
        self._settings = settings

        # V4: Przyciski zmiany kolejności (góra / dół) pod badge'em.
        btn_row = QHBoxLayout()
        btn_row.setSpacing(3)
        self._up_btn = QPushButton("▲")
        self._up_btn.setObjectName("reorderBtn")
        self._up_btn.setFixedSize(28, 20)
        self._up_btn.setCursor(Qt.PointingHandCursor)
        self._up_btn.setStyleSheet(self._REORDER_BTN_QSS)
        self._up_btn.setEnabled(not first)
        self._up_btn.setToolTip("Przesuń w górę")
        self._down_btn = QPushButton("▼")
        self._down_btn.setObjectName("reorderBtn")
        self._down_btn.setFixedSize(28, 20)
        self._down_btn.setCursor(Qt.PointingHandCursor)
        self._down_btn.setStyleSheet(self._REORDER_BTN_QSS)
        self._down_btn.setEnabled(not last)
        self._down_btn.setToolTip("Przesuń w dół")
        btn_row.addWidget(self._up_btn)
        btn_row.addWidget(self._down_btn)
        self._left_col.addLayout(btn_row)

        self._up_btn.clicked.connect(
            lambda: self.move_requested.emit(self._config.idx, -1))
        self._down_btn.clicked.connect(
            lambda: self.move_requested.emit(self._config.idx, +1))

        recent = []
        if settings is not None:
            try:
                recent = list(settings.recent_apps)
            except Exception:
                recent = []

        # Akcja (combo)
        self._action_combo = QComboBox()
        self._action_combo.addItem("Głośność systemowa", PotAction.SYSTEM_VOLUME)
        self._action_combo.addItem("Głośność aplikacji", PotAction.APP_VOLUME)
        self._action_combo.addItem("Gra (auto-wykrywanie)", PotAction.GAME_VOLUME)
        self._action_combo.addItem("Wyłączony", PotAction.NONE)
        for i in range(self._action_combo.count()):
            if self._action_combo.itemData(i) == config.action:
                self._action_combo.setCurrentIndex(i)
                break
        self._action_combo.currentIndexChanged.connect(self._on_changed)
        self._action_combo.currentIndexChanged.connect(self._update_field_visibility)
        self._add_field("Akcja", self._action_combo)

        # Cel audio — AppPicker (widoczny tylko dla APP_VOLUME)
        self._app_picker = AppPicker(audio_backend=audio_backend,
                                      label="Źródło audio",
                                      recent_apps=recent)
        self._app_picker.set_target(config.target)
        self._app_picker.selection_changed.connect(lambda *_: self._on_changed())
        self._app_picker_wrapper = self._add_field_wrapped("Aplikacja", self._app_picker)

        # Czułość
        from PySide6.QtWidgets import QDoubleSpinBox
        self._sens = QDoubleSpinBox()
        self._sens.setRange(0.1, 4.0)
        self._sens.setSingleStep(0.1)
        self._sens.setValue(config.sensitivity)
        self._sens.setStyleSheet("background: rgba(40,44,64,220); border: 1px solid rgba(255,255,255,18); border-radius: 8px; padding: 6px;")
        self._sens.valueChanged.connect(lambda *_: self._on_changed())
        self._add_field("Czułość ×", self._sens)

        # --- Sekcja zaawansowana (zwijana) ---
        self._advanced_visible = False
        self._adv_toggle = QPushButton("Zaawansowane  ▾")
        self._adv_toggle.setCursor(Qt.PointingHandCursor)
        self._adv_toggle.setCheckable(True)
        self._adv_toggle.setChecked(False)
        self._adv_toggle.setStyleSheet(
            "QPushButton { text-align: left; background: transparent; "
            "border: none; color: #A5ABC0; padding: 2px 0; font-size: 12px; }"
            "QPushButton:hover { color: #2DD4FF; }"
        )
        self._adv_toggle.toggled.connect(self._on_toggle_advanced)
        self._fields_layout.addWidget(self._adv_toggle)

        self._adv_container = QWidget()
        self._adv_container.setVisible(False)
        adv_l = QVBoxLayout(self._adv_container)
        adv_l.setContentsMargins(0, 4, 0, 0)
        adv_l.setSpacing(10)

        # Krzywa odpowiedzi
        self._curve_combo = QComboBox()
        self._curve_combo.addItem("Liniowa", "linear")
        self._curve_combo.addItem("Logarytmiczna", "log")
        self._curve_combo.addItem("Eksponencjalna", "exp")
        self._curve_combo.addItem("Gamma (percepcyjna)", "gamma")
        self._curve_combo.addItem("S-krzywa (smoothstep)", "s-curve")
        for i in range(self._curve_combo.count()):
            if self._curve_combo.itemData(i) == config.curve:
                self._curve_combo.setCurrentIndex(i)
                break
        self._curve_combo.currentIndexChanged.connect(lambda *_: self._on_changed())
        adv_l.addLayout(self._mk_field("Krzywa", self._curve_combo))

        # Min / Max (slidery 0..100 → 0.0..1.0)
        self._min_slider = self._mk_slider(int(config.min_volume * 100))
        self._max_slider = self._mk_slider(int(config.max_volume * 100))
        self._min_slider.valueChanged.connect(lambda *_: self._on_changed())
        self._max_slider.valueChanged.connect(lambda *_: self._on_changed())
        adv_l.addLayout(self._mk_field("Zakres min %", self._min_slider))
        adv_l.addLayout(self._mk_field("Zakres max %", self._max_slider))

        # Odwróć kierunek
        self._invert = QCheckBox("Odwróć kierunek (lewo ↔ prawo)")
        self._invert.setChecked(config.invert)
        self._invert.toggled.connect(lambda *_: self._on_changed())
        adv_l.addWidget(self._invert)

        # V3: Kalibruj min/max
        self._calib_btn = QPushButton("◉ Kalibruj zakres")
        self._calib_btn.setCursor(Qt.PointingHandCursor)
        self._calib_btn.setStyleSheet(
            "QPushButton { background: rgba(45,212,255,20); border: 1px solid rgba(45,212,255,60);"
            "  border-radius: 6px; padding: 6px 12px; color: #2DD4FF; font-size: 12px; }"
            "QPushButton:hover { background: rgba(45,212,255,40); }"
        )
        self._calib_btn.clicked.connect(lambda: self.calibrate_requested.emit(self._config.idx))
        adv_l.addWidget(self._calib_btn)

        self._fields_layout.addWidget(self._adv_container)

        # Init field visibility based on current action
        self._update_field_visibility()

    def _mk_slider(self, initial: int):
        s = QSlider(Qt.Horizontal)
        s.setRange(0, 100)
        s.setValue(initial)
        s.setStyleSheet(
            "QSlider::groove:horizontal { height: 4px; background: rgba(255,255,255,40); border-radius: 2px; }"
            "QSlider::handle:horizontal { width: 14px; height: 14px; margin: -6px 0; "
            "background: #2DD4FF; border-radius: 7px; }"
        )
        return s

    def _mk_field(self, label: str, widget):
        """Wersja _add_field zwracająca layout (dla kontenera zaawansowanego)."""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(label.upper(), objectName="labelMuted")
        lbl.setFixedWidth(140)
        row.addWidget(lbl)
        row.addWidget(widget, stretch=1)
        return row

    def _on_toggle_advanced(self, checked: bool) -> None:
        self._advanced_visible = checked
        self._adv_container.setVisible(checked)
        self._adv_toggle.setText("Zaawansowane  ▴" if checked else "Zaawansowane  ▾")

    def _add_field_wrapped(self, label: str, widget) -> QWidget:
        """Dodaj pole zwracając wrapper widget (do ukrywania/pokazywania)."""
        wrapper = QWidget()
        wrapper.setStyleSheet("background: transparent;")
        row = QHBoxLayout(wrapper)
        row.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(label.upper(), objectName="labelMuted")
        lbl.setFixedWidth(140)
        row.addWidget(lbl)
        row.addWidget(widget, stretch=1)
        self._fields_layout.addWidget(wrapper)
        return wrapper

    def _update_field_visibility(self) -> None:
        """Pokaż/ukryj pola zależnie od wybranej akcji."""
        action = self._action_combo.currentData()
        self._app_picker_wrapper.setVisible(action == PotAction.APP_VOLUME)

    def _on_changed(self, *_args) -> None:
        lo = self._min_slider.value() / 100.0
        hi = self._max_slider.value() / 100.0
        if hi < lo:
            hi = lo
        cfg = PotConfig(
            idx=self._config.idx,
            enabled=self._config.enabled,
            action=self._action_combo.currentData(),
            target=self._app_picker.get_target(),
            sensitivity=float(self._sens.value()),
            smooth_ui=self._config.smooth_ui,
            curve=self._curve_combo.currentData(),
            min_volume=lo,
            max_volume=hi,
            invert=self._invert.isChecked(),
        )
        self._config = cfg
        self.changed.emit(cfg.idx, cfg)
        # Zapamiętaj aplikację w recent apps (jeśli ustawiony cel i settings dostępny)
        if self._settings is not None and cfg.target and cfg.target != "__system__":
            try:
                self._settings.remember_app(cfg.target)
            except Exception:
                pass

    def get_config(self) -> PotConfig:
        return self._config


class ButtonRow(_ConfigRow):
    """Wiersz konfiguracji przycisku."""

    changed = Signal(int, object)  # idx, ButtonConfig
    test_clicked = Signal(int)      # V3: idx — symuluj wciśnięcie przycisku

    def __init__(self, config: ButtonConfig, parent=None):
        super().__init__(title=f"Przycisk {config.idx + 1}", glyph="◻", parent=parent)
        self._config = config

        # Akcja
        self._action_combo = QComboBox()
        self._action_combo.addItem("Skrót klawiszowy", ButtonAction.HOTKEY)
        self._action_combo.addItem("Wycisz / Odcisz", ButtonAction.TOGGLE_MUTE)
        self._action_combo.addItem("Uruchom komendę", ButtonAction.RUN_COMMAND)
        self._action_combo.addItem("Wklej tekst", ButtonAction.PASTE_TEXT)
        self._action_combo.addItem("Brak", ButtonAction.NONE)
        for i in range(self._action_combo.count()):
            if self._action_combo.itemData(i) == config.action:
                self._action_combo.setCurrentIndex(i)
                break
        self._action_combo.currentIndexChanged.connect(lambda *_: self._on_action_changed())
        self._add_field("Akcja", self._action_combo)

        # Pole hotkeya (widoczne tylko dla HOTKEY)
        self._hotkey_field = HotkeyField()
        self._hotkey_field.set_value(config.hotkey)
        self._hotkey_field.hotkey_changed.connect(lambda *_: self._on_changed())
        self._hotkey_row = self._add_field_wrapped("Skrót", self._hotkey_field)

        # Pole komendy (widoczne tylko dla RUN_COMMAND)
        self._command_field = QLineEdit()
        self._command_field.setPlaceholderText("np. firefox, alacritty -e htop, systemctl suspend")
        self._command_field.setText(config.target if config.action == ButtonAction.RUN_COMMAND else "")
        self._command_field.setStyleSheet(
            "background: rgba(40,44,64,220); border: 1px solid rgba(255,255,255,18);"
            "border-radius: 8px; padding: 6px;")
        self._command_field.textChanged.connect(lambda *_: self._on_changed())
        self._command_row = self._add_field_wrapped("Komenda", self._command_field)

        # Pole tekstu do wklejenia (widoczne tylko dla PASTE_TEXT)
        from PySide6.QtWidgets import QPlainTextEdit
        self._paste_field = QPlainTextEdit()
        self._paste_field.setPlaceholderText("Tekst który zostanie wklejony...")
        self._paste_field.setFixedHeight(80)
        self._paste_field.setStyleSheet(
            "background: rgba(40,44,64,220); border: 1px solid rgba(255,255,255,18);"
            "border-radius: 8px; padding: 6px;")
        self._paste_field.setPlainText(config.target if config.action == ButtonAction.PASTE_TEXT else "")
        self._paste_field.textChanged.connect(lambda *_: self._on_changed())
        self._paste_row = self._add_field_wrapped("Tekst", self._paste_field)

        # Enter po wklejeniu (widoczne tylko dla PASTE_TEXT)
        self._paste_enter = QCheckBox("Naciśnij Enter po wklejeniu")
        self._paste_enter.setChecked(bool(getattr(config, "paste_enter", False)))
        self._paste_enter.toggled.connect(lambda *_: self._on_changed())
        self._paste_enter_row = self._add_field_wrapped("Enter", self._paste_enter)

        # Cel wyciszenia (widoczny tylko dla TOGGLE_MUTE)
        self._mute_target = QLineEdit()
        self._mute_target.setPlaceholderText("np. firefox (puste = system)")
        mute_txt = config.target if config.action == ButtonAction.TOGGLE_MUTE else ""
        self._mute_target.setText(mute_txt)
        self._mute_target.setStyleSheet(
            "background: rgba(40,44,64,220); border: 1px solid rgba(255,255,255,18);"
            "border-radius: 8px; padding: 6px;")
        self._mute_target.textChanged.connect(lambda *_: self._on_changed())
        self._mute_row = self._add_field_wrapped("Wycisz cel", self._mute_target)

        # Czy reagować na wciśnięcie czy puszczenie
        self._on_press = QCheckBox(
            "Reaguj przy WCIŚNIĘCIU (odznacz = przy PUSZCZENIU)")
        self._on_press.setChecked(config.on_press)
        self._on_press.toggled.connect(lambda *_: self._on_changed())
        self._add_field("Trigger", self._on_press)

        # V3: Przycisk Test — symuluj akcję przycisku
        self._test_btn = QPushButton("▶ Test")
        self._test_btn.setCursor(Qt.PointingHandCursor)
        self._test_btn.setFixedWidth(80)
        self._test_btn.setStyleSheet(
            "QPushButton { background: rgba(45,212,255,30); border: 1px solid rgba(45,212,255,80);"
            "  border-radius: 6px; padding: 4px 8px; color: #2DD4FF; font-size: 12px; }"
            "QPushButton:hover { background: rgba(45,212,255,50); }"
            "QPushButton:pressed { background: rgba(45,212,255,80); }"
        )
        self._test_btn.clicked.connect(lambda: self.test_clicked.emit(self._config.idx))
        self._add_field("Test", self._test_btn)

        self._update_field_visibility()

    def _add_field_wrapped(self, label: str, widget: QWidget) -> QWidget:
        """Dodaj pole zwracając wrapper widget (do ukrywania/pokazywania)."""
        wrapper = QWidget()
        wrapper.setStyleSheet("background: transparent;")
        row = QHBoxLayout(wrapper)
        row.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(label.upper(), objectName="labelMuted")
        lbl.setFixedWidth(140)
        row.addWidget(lbl)
        row.addWidget(widget, stretch=1)
        self._fields_layout.addWidget(wrapper)
        return wrapper

    def _on_action_changed(self) -> None:
        self._update_field_visibility()
        self._on_changed()

    def _update_field_visibility(self) -> None:
        action = self._action_combo.currentData()
        self._hotkey_row.setVisible(action == ButtonAction.HOTKEY)
        self._command_row.setVisible(action == ButtonAction.RUN_COMMAND)
        self._mute_row.setVisible(action == ButtonAction.TOGGLE_MUTE)
        self._paste_row.setVisible(action == ButtonAction.PASTE_TEXT)
        self._paste_enter_row.setVisible(action == ButtonAction.PASTE_TEXT)

    def _on_changed(self, *_args) -> None:
        action = self._action_combo.currentData()
        target = self._config.target
        if action == ButtonAction.RUN_COMMAND:
            target = self._command_field.text()
        elif action == ButtonAction.TOGGLE_MUTE:
            target = self._mute_target.text()
        elif action == ButtonAction.PASTE_TEXT:
            target = self._paste_field.toPlainText()
        cfg = ButtonConfig(
            idx=self._config.idx,
            action=action,
            hotkey=self._hotkey_field.value(),
            target=target,
            on_press=self._on_press.isChecked(),
            paste_enter=self._paste_enter.isChecked() if action == ButtonAction.PASTE_TEXT else False,
        )
        self._config = cfg
        self.changed.emit(cfg.idx, cfg)

    def get_config(self) -> ButtonConfig:
        return self._config
