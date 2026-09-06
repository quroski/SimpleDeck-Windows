"""DeckMap - wizualne odwzorowanie fizycznego urządzenia GREJEM Stream Deck.

V2: Pokazuje 5 potencjometrów (z paskami wartości), 4 przyciski i 8-LED
linijkę VU bar (wskaźnik głośności aktywnego kanału).
Dane na żywo z EventBus - potencjometry animują się płynnie, przyciski
podświetlają po wciśnięciu, VU bar odzwierciedla poziom głośności.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, Signal, Slot
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QProgressBar, QPushButton,
                               QSizePolicy, QVBoxLayout)

from ...core.event_bus import EventBus
from ...transport.protocol import (ADC_RANGE, ACTIVE_LED_COUNT,
                                   BUTTON_COUNT, POT_COUNT)

# V1.0.3: wspólny styl przycisku ✎ (rename) — inline QSS, zależny od akcentu
# jest niemożliwy bez recoloru, więc celowo neutralny szary→cyan hover.
_PENCIL_QSS = (
    "QPushButton { background: transparent; border: none; color: #6A7080;"
    "  font-size: 11px; padding: 0; }"
    "QPushButton:hover { color: #2DD4FF; }"
)
# V1.0.3: styl pola edycji nazwy w komórce — dopasowany do glassmorphism.
_EDIT_QSS = (
    "QLineEdit { background: rgba(13, 15, 23, 200);"
    "  border: 1px solid rgba(45, 212, 255, 120); border-radius: 5px;"
    "  color: #F5F7FA; font-size: 11px; font-weight: 700; padding: 1px 5px;"
    "  selection-background-color: rgba(45, 212, 255, 90); }"
)


class _CellRenamer(QObject):
    """V1.0.3: Event filter pola edycji nazwy — Escape anuluje edycję.

    editingFinished (Enter / focus-out) obsługuje commit normalnie; Escape
    jest jedynym klawiszem, który ma PRZERWAĆ edycję bez zapisu, więc
    łapiemy go zanim QLineEdit zamknie edycję.
    """

    escapePressed = Signal()

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            self.escapePressed.emit()
            return True  # połknij — nie propaguj do dialogów/rodzica
        return super().eventFilter(obj, event)


class _PotCell(QFrame):
    """Karta wizualizująca jeden potencjometr: nazwa + wartość + pasek.

    V1.0.3: tytuł jest edytowalny — przycisk ✎ włącza tryb edycji (QLineEdit
    zamiast etykiety), Enter/focus-out commituje, Escape anuluje. Pusta nazwa
    = wróć do domyślnej "POT N". Zmiana emitowana sygnałem ``renamed``.
    """

    clicked = Signal()
    renamed = Signal(int, str)  # idx, nowa nazwa ("" = domyślna)

    def __init__(self, idx: int, parent=None):
        super().__init__(parent)
        self._idx = idx
        self._label = ""          # V1.0.3: własna nazwa (pusta = domyślna)
        self._last_value = -1  # V7: cache by pominąć identyczne setValue/setText
        self.setObjectName("deckCell")
        self.setProperty("selected", "false")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(96)
        self.setCursor(Qt.PointingHandCursor)

        lay = QVBoxLayout(self)
        # V1.0.2: margins 14→10 — przy min. szerokości okna (820) komórki mają
        # ~90 px; mniejsze marginesy zostawiają miejsce na "POT N" + wartość.
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(4)

        title = QLabel(f"POT {idx + 1}", objectName="deckCellTitle")
        title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        title.setToolTip(self._default_title)
        # V1.0.2: Ignored — domyślny minimumSizeHint etykiety (szerokość
        # tekstu "POT N") blokował skalowanie komórki przy zwężaniu okna.
        title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._title = title

        # V1.0.3: przycisk ✎ + pole edycji (startowo ukryte)
        self._pencil = QPushButton("✎", toolTip="Zmień nazwę")
        self._pencil.setObjectName("deckPencil")
        self._pencil.setFixedSize(16, 16)
        self._pencil.setCursor(Qt.PointingHandCursor)
        self._pencil.setStyleSheet(_PENCIL_QSS)
        self._pencil.setFocusPolicy(Qt.NoFocus)
        self._pencil.clicked.connect(self._begin_edit)

        self._edit = QLineEdit(self)
        self._edit.setObjectName("deckCellEdit")
        self._edit.setStyleSheet(_EDIT_QSS)
        self._edit.setPlaceholderText(self._default_title)
        self._edit.setVisible(False)
        self._edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._renamer = _CellRenamer(self._edit)
        self._renamer.escapePressed.connect(self._cancel_edit)
        self._edit.installEventFilter(self._renamer)
        # Enter / focus-out → commit (native QLineEdit editingFinished)
        self._edit.editingFinished.connect(self._commit_edit)

        self._value_label = QLabel("0", objectName="deckCellValue")
        self._value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._value_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(3)
        head.addWidget(title, stretch=1)
        head.addWidget(self._pencil, 0, Qt.AlignVCenter)
        head.addWidget(self._edit, stretch=1)
        head.addWidget(self._value_label)
        lay.addLayout(head)

        # Pasek wartości
        self._bar = QProgressBar()
        self._bar.setRange(0, ADC_RANGE - 1)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)
        # V1.0.2: Domyślny minimumSizeHint QProgressBar (111 px) — główny
        # winowajca sztywnego minimum komórki (149 px → DeckMap 827 px, więcej
        # niż cały panel treści przy min. oknie). Policy Ignored każe layoutowi
        # zignorować hint — pasek skaluje się do dowolnej szerokości.
        # (Uwaga: setMinimumWidth(0) NIE nadpisuje minimumSizeHint — tylko
        # pozytywne minimum wygrywa z hintem, stąd Ignored.)
        self._bar.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._bar.setStyleSheet("""
            QProgressBar {
                background: rgba(255, 255, 255, 24);
                border: none;
                border-radius: 3px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                            stop:0 #2DD4FF, stop:1 #9B5CFF);
                border-radius: 3px;
            }
        """)
        lay.addWidget(self._bar)

    # --- V1.0.3: rename ---
    @property
    def _default_title(self) -> str:
        return f"POT {self._idx + 1}"

    def set_label(self, label: str) -> None:
        """Ustaw nazwę własną (pusta = domyślna "POT N")."""
        label = (label or "").strip()
        if label == self._label:
            return
        self._label = label
        self._title.setText(label if label else self._default_title)
        self._title.setToolTip(label if label else self._default_title)

    def _begin_edit(self) -> None:
        self._title.setVisible(False)
        self._edit.setVisible(True)
        self._edit.setText(self._label)
        self._edit.selectAll()
        self._edit.setFocus(Qt.OtherFocusReason)

    def _commit_edit(self) -> None:
        text = self._edit.text().strip()
        self._finish_edit()
        if text == self._label:
            return
        self.set_label(text)
        self.renamed.emit(self._idx, self._label)

    def _cancel_edit(self) -> None:
        self._finish_edit()

    def _finish_edit(self) -> None:
        self._edit.setVisible(False)
        self._title.setVisible(True)
        self.setCursor(Qt.PointingHandCursor)

    @Slot(int)
    def set_value(self, value: int) -> None:
        v = max(0, min(ADC_RANGE - 1, value))
        # V7: pomiń identyczne wartości — eliminuje ~500 setText/s + repaints
        # gdy wartość ADC tnie przez deadband i skacze między 2-3 sąsiadami.
        if v == self._last_value:
            return
        self._last_value = v
        self._bar.setValue(v)
        self._value_label.setText(f"{v * 100 // (ADC_RANGE - 1)}%")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            if self._edit.isVisible():
                # V1.0.3: klik w komórkę w trakcie edycji = zatwierdź nazwę
                # (nie nawiguj — edycja ma pierwszeństwo).
                self._commit_edit()
            else:
                self.clicked.emit()
        super().mousePressEvent(event)


class _ButtonCell(QPushButton):
    """Karta wizualizująca jeden przycisk. Podświetla się gdy wciśnięty.

    V1.0.3: komórka jest klikalna (``setEnabled(True)`` zamiast disabled —
    klik emituje ``clicked``, który MainWindow mapuje na nawigację do strony
    PRZYCISKI). Podświetlenie wciśnięcia z MCU nadal steruje property
    ``pressed`` (QSS), nie natywnym stanem QPushButton, więc klik nie koliduje
    ze wskaźnikiem. Przycisk ✎ włącza edycję nazwy (jak w _PotCell).
    """

    # UWAGA: NIE shadowujemy natywnego QPushButton.clicked(bool) — używamy
    # osobnego sygnału ``activated`` (shadowowanie sygnału C++ zmienia
    # sygnaturę emisji wewnętrznej i jest kruche w PySide6).
    activated = Signal(int)     # idx — klik na komórce (nawigacja)
    renamed = Signal(int, str)  # idx, nowa nazwa ("" = domyślna)

    def __init__(self, idx: int, parent=None):
        super().__init__(parent)
        self._idx = idx
        self._label = ""          # V1.0.3: własna nazwa (pusta = domyślna)
        self._editing = False     # V1.0.3: tryb edycji nazwy aktywny?
        self.setObjectName("deckButton")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(72)
        # V1.0.3: była disabled (ArrowCursor) — teraz klikalna. NoFocus,
        # żeby spacja/tab nie wchodziła w interakcję z wirtualną "klawiaturą".
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        self._pressed = False
        self.setProperty("pressed", "false")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(2)

        # --- Rząd tytułu: [nazwa] [✎] ---
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(3)

        self._title = QLabel(f"BTN {idx + 1}", objectName="deckCellTitle")
        self._title.setAlignment(Qt.AlignCenter)
        self._title.setToolTip(self._default_title)
        # V1.0.2: Ignored — nie blokuj skalowania przy małym oknie.
        self._title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._title.setAttribute(Qt.WA_TransparentForMouseEvents)
        head.addWidget(self._title, stretch=1)

        self._pencil = QPushButton("✎", self, toolTip="Zmień nazwę")
        self._pencil.setObjectName("deckPencil")
        self._pencil.setFixedSize(16, 16)
        self._pencil.setCursor(Qt.PointingHandCursor)
        self._pencil.setStyleSheet(_PENCIL_QSS)
        self._pencil.setFocusPolicy(Qt.NoFocus)
        self._pencil.clicked.connect(self._begin_edit)
        head.addWidget(self._pencil, 0, Qt.AlignVCenter)
        lay.addLayout(head)

        # V1.0.3: pole edycji nazwy — nakładka na tytuł, startowo ukryte.
        self._edit = QLineEdit(self)
        self._edit.setObjectName("deckCellEdit")
        self._edit.setStyleSheet(_EDIT_QSS)
        self._edit.setPlaceholderText(self._default_title)
        self._edit.setAlignment(Qt.AlignCenter)
        self._edit.setVisible(False)
        self._renamer = _CellRenamer(self._edit)
        self._renamer.escapePressed.connect(self._cancel_edit)
        self._edit.installEventFilter(self._renamer)
        self._edit.editingFinished.connect(self._commit_edit)
        lay.addWidget(self._edit)

        # Klik na komórce (poza edycją) → sygnał nawigacyjny activated(idx).
        super().clicked.connect(self._on_native_clicked)

        self._state_label = QLabel("—")
        self._state_label.setAlignment(Qt.AlignCenter)
        self._state_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._state_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        # V7: precompute oba warianty QSS raz — dawniej build stringa na
        # każdym wciśnięciu (string concat + QSS re-parse).
        self._state_label.setStyleSheet(self._STATE_QSS_RELEASED)
        lay.addWidget(self._state_label)

    _STATE_QSS_PRESSED = (
        "font-size: 11px; background: transparent; font-weight: 700; color: #2DD4FF;"
    )
    _STATE_QSS_RELEASED = (
        "font-size: 11px; background: transparent; font-weight: 700; color: #6A7080;"
    )

    # --- V1.0.3: rename ---
    @property
    def _default_title(self) -> str:
        return f"BTN {self._idx + 1}"

    def _on_native_clicked(self, _checked: bool = False) -> None:
        """Tłumacz natywny QPushButton.clicked(bool) na activated(idx)."""
        if self._editing:
            # V1.0.3: klik w komórkę w trakcie edycji = zatwierdź nazwę
            # (nie nawiguj — edycja ma pierwszeństwo).
            self._commit_edit()
            return
        self.activated.emit(self._idx)

    def set_label(self, label: str) -> None:
        """Ustaw nazwę własną (pusta = domyślna \"BTN N\")."""
        label = (label or "").strip()
        if label == self._label:
            return
        self._label = label
        self._title.setText(label if label else self._default_title)
        self._title.setToolTip(label if label else self._default_title)

    def _begin_edit(self) -> None:
        """Włącz tryb edycji nazwy (QLineEdit w miejsce tytułu)."""
        if self._editing:
            return
        self._editing = True
        # QSS: [editing="true"] przywraca neutralny wygląd (bez "pressed").
        self.setProperty("editing", "true")
        self.style().unpolish(self)
        self.style().polish(self)
        # Rząd tytułu (tytuł + ✎) się chowa — layout pominie niewidoczne
        # widgety i pole edycji zajmie pełną szerokość komórki.
        self._title.setVisible(False)
        self._pencil.setVisible(False)
        self._edit.setText(self._label)
        self._edit.selectAll()
        self._edit.setVisible(True)
        self._edit.setFocus(Qt.OtherFocusReason)

    def _commit_edit(self) -> None:
        text = self._edit.text().strip()
        self._finish_edit()
        if text == self._label:
            return
        self.set_label(text)
        self.renamed.emit(self._idx, self._label)

    def _cancel_edit(self) -> None:
        self._finish_edit()

    def _finish_edit(self) -> None:
        if not self._editing:
            return
        self._editing = False
        self.setProperty("editing", "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self._edit.setVisible(False)
        self._title.setVisible(True)
        self._pencil.setVisible(True)

    @Slot(bool)
    def set_pressed(self, pressed: bool) -> None:
        if self._pressed == pressed:
            return
        self._pressed = pressed
        self.setProperty("pressed", "true" if pressed else "false")
        # V8: Skip polish() - Qt will polish on next paint, saves ~20-30% style recalculations
        self.style().unpolish(self)
        # self.style().polish(self)  # Removed - not needed
        self._state_label.setText("WCIŚNIĘTY" if pressed else "—")
        self._state_label.setStyleSheet(
            self._STATE_QSS_PRESSED if pressed else self._STATE_QSS_RELEASED
        )


class _VolumeBar(QFrame):
    """V2: Wizualizacja 8-segmentowej linijki VU bar.

    Odbiera poziom 0..1 (od bus.pot_level) i zapala segmenty proporcjonalnie.
    Po 3 s bezczynności (brak aktualizacji) gasnie do trybu SLEEP.

    V6: Zamiast ``setStyleSheet()`` na każdym segmencie (3 wywołania/poziom
    × pot 50 Hz = 150 QSS re-parse'ów/s), używa ``setProperty("state", …)``
    + ``style().polish()``. QSS w ``glossy.qss`` ma reguły
    ``QFrame[state="on"]``, ``[state="half"]``, ``[state="off"]``.
    """

    def __init__(self, n_segments: int = ACTIVE_LED_COUNT, parent=None):
        super().__init__(parent)
        self._n = n_segments
        self._segments: list[QFrame] = []
        # Cache stanów by uniknąć zbędnego polish()
        self._seg_states: list[str] = ["off"] * n_segments

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        for i in range(self._n):
            seg = QFrame()
            # V1.0.2: Segmenty VU skalują się szerokością (min 8 px) zamiast
            # sztywnych 24 px — linijka kurczy się razem z oknem zamiast je
            # rozpychać. Wysokość zostaje stała.
            seg.setMinimumWidth(8)
            seg.setFixedHeight(16)
            seg.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            seg.setProperty("state", "off")
            # Inline QSS fallback (gdy globalny QSS nie ma reguł [state=...])
            seg.setStyleSheet(
                "QFrame[state=\"on\"]  { background: #2DD4FF; border-radius: 3px; }"
                "QFrame[state=\"half\"] { background: rgba(45, 212, 255, 128);"
                "                          border-radius: 3px; }"
                "QFrame[state=\"off\"]  { background: rgba(255,255,255,24);"
                "                          border-radius: 3px; }"
            )
            self._segments.append(seg)
            lay.addWidget(seg)
        lay.addStretch()

    @Slot(int, float)
    def set_level(self, pot_idx: int, level: float) -> None:
        """Ustaw poziom linijki (0..1). Zapala N z 8 segmentów.

        ``pot_idx`` przychodzi z sygnału ``bus.pot_level(idx, level)`` i jest
        ignorowany — VU bar pokazuje poziom aktywnego kanału.

        V6: ``setProperty`` + ``polish`` zamiast ``setStyleSheet`` —
        ~10× szybsze (brak QSS re-parse).
        V7: Pomiń gdy niewidoczny — eliminuje ~80 polishów/s gdy user na
        innej karcie lub zminimalizowany do tray. Pierwszy event po powrocie
        odświeży bar w ~30 ms.
        """
        del pot_idx  # nieużywany — sygnatura sterowana sygnałem pot_level
        if not self.isVisible():
            return
        level = max(0.0, min(1.0, level))
        lit = level * self._n
        for i, seg in enumerate(self._segments):
            frac = lit - i
            if frac >= 1.0:
                new_state = "on"
            elif frac > 0:
                new_state = "half"
            else:
                new_state = "off"
            # Tylko polish gdy stan się zmienił — unikaj zbędnego repaintu
            if self._seg_states[i] != new_state:
                self._seg_states[i] = new_state
                seg.setProperty("state", new_state)
                # V8: Skip polish() - Qt will polish on next paint, saves ~20-30% style recalculations
                seg.style().unpolish(seg)
                # seg.style().polish(seg)  # Removed - not needed


class DeckMap(QFrame):
    """Pełna wizualizacja urządzenia - 8-LED VU bar, grid 5 potencjometrów,
    grid 4 przycisków.

    Subskrybuje:
      - bus.pot_event   → animacja pasków potencjometrów
      - bus.button_event → podświetlanie przycisków
      - bus.pot_level    → V2: animacja linijki VU bar (poziom głośności)

    Przy starcie przyjmuje opcjonalny ``settings`` — jeśli ma cache wartości
    potencjometrów (last_pot_values), wyświetla je zanim MCU wyśle pierwsze
    POT_EVT. Dzięki temu paski nie są puste przy otwarciu aplikacji.
    """

    pot_clicked = Signal(int)    # idx potencjometru — klik na karcie Overview
    # V1.0.3: klik na komórce przycisku — MainWindow nawiguje do PRZYCISKI.
    button_clicked = Signal(int)
    # V1.0.3: (kind, idx, nazwa) — kind: "pot" | "btn". Pusty label = domyślna.
    label_renamed = Signal(str, int, str)

    def __init__(self, bus: EventBus, settings=None, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self._bus = bus
        self._settings = settings
        self._profile = None
        self._invert_all = bool(getattr(settings, "invert_all_pots", False)) if settings else False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(14)

        # === Tytuł sekcji ===
        title_row = QHBoxLayout()
        title = QLabel("URZĄDZENIE", objectName="sectionTitle")
        # V1.0.2: Subtitle miał minimumSizeHint ~576 px (długość tekstu) i
        # rozpychał całą kartę — DeckMap nie dawał się zwęzić poniżej 817 px.
        # Ignored = etykieta może się skrócić/utnąć zamiast rozpychać panel.
        # (setMinimumWidth(0) NIE nadpisuje minimumSizeHint.)
        subtitle = QLabel(f"GREJEM Stream Deck  ·  5 pot / 4 btn / {ACTIVE_LED_COUNT} LED bar",
                           objectName="sectionSubtitle")
        subtitle.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        subtitle.setMinimumWidth(0)
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(subtitle)
        outer.addLayout(title_row)

        # === V2: VU bar (8-LED linijka) ===
        vu_row = QHBoxLayout()
        vu_row.setSpacing(14)
        vu_label = QLabel("VU", objectName="labelMuted")
        vu_label.setFixedWidth(40)
        vu_row.addWidget(vu_label)
        self._vu_bar = _VolumeBar()
        vu_row.addWidget(self._vu_bar)
        outer.addLayout(vu_row)

        # === Potencjometry (grid 5x1) ===
        self._pot_grid = QGridLayout()
        self._pot_grid.setSpacing(10)
        self._pots = [_PotCell(i) for i in range(POT_COUNT)]
        for i, cell in enumerate(self._pots):
            cell.clicked.connect(lambda idx=i: self.pot_clicked.emit(idx))
            # V1.0.3: edycja nazwy w komórce → emit do profilu.
            cell.renamed.connect(self._on_label_renamed)
            self._pot_grid.addWidget(cell, 0, i)
        outer.addLayout(self._pot_grid)

        # === Przyciski (grid 4x1) ===
        btn_grid = QGridLayout()
        btn_grid.setSpacing(10)
        self._buttons = [_ButtonCell(i) for i in range(BUTTON_COUNT)]
        for i, cell in enumerate(self._buttons):
            # V1.0.3: komórki przycisków są klikalne (nawigacja do PRZYCISKI).
            cell.activated.connect(self.button_clicked.emit)
            cell.renamed.connect(self._on_label_renamed)
            btn_grid.addWidget(cell, 0, i)
        outer.addLayout(btn_grid)

        # === Subskrypcje EventBus ===
        bus.pot_event.connect(self._on_pot)
        bus.button_event.connect(self._on_button)
        bus.pot_level.connect(self._vu_bar.set_level)
        # V4: Przestaw komórki gdy użytkownik zmieni kolejność na PotsPage.
        bus.pot_order_changed.connect(self._arrange_pots)

        # === Restore cached pot values (jeśli dostępne) ===
        # MCU wysyła POT_EVT dopiero na ZMIANĘ wartości, więc bez tego paski
        # są puste do pierwszego ruchu potencjometrem.
        if settings is not None:
            cached = getattr(settings, "last_pot_values", None)
            if cached:
                for i, val in enumerate(cached):
                    if 0 <= i < len(self._pots) and val is not None and val >= 0:
                        self._pots[i].set_value(self._display_value(i, val))

    def set_profile(self, profile) -> None:
        self._profile = profile
        self._arrange_pots()
        # V1.0.3: załaduj własne nazwy kontrolek z profilu.
        if profile is not None:
            for i, cfg in enumerate(getattr(profile, "pots", []) or []):
                if 0 <= i < len(self._pots):
                    self._pots[i].set_label(getattr(cfg, "label", ""))
            for i, cfg in enumerate(getattr(profile, "buttons", []) or []):
                if 0 <= i < len(self._buttons):
                    self._buttons[i].set_label(getattr(cfg, "label", ""))

    def _on_label_renamed(self, idx: int, label: str) -> None:
        """V1.0.3: komórka zmieniła nazwę — przekaż do profilu przez MainWindow."""
        sender = self.sender()
        kind = "btn" if sender in self._buttons else "pot"
        self.label_renamed.emit(kind, idx, label)

    def _arrange_pots(self) -> None:
        """V4: Przestaw komórki potencjometrów wg ``pot_display_order``.

        ``self._pots`` pozostaje indeksowane kanałem fizycznym (0..4) —
        ``_on_pot`` nadal robi ``self._pots[idx]``. Zmienia się tylko pozycja
        w gridzie: ``self._pots[physical_idx]`` ląduje w kolumnie ``display_pos``.
        """
        if self._profile is None:
            return
        order = self._profile.pot_display_order
        for cell in self._pots:
            self._pot_grid.removeWidget(cell)
        for display_pos, physical_idx in enumerate(order):
            if 0 <= physical_idx < len(self._pots):
                self._pot_grid.addWidget(self._pots[physical_idx], 0, display_pos)

    def refresh_invert(self) -> None:
        if self._settings is not None:
            self._invert_all = bool(getattr(self._settings, "invert_all_pots", False))

    def _display_value(self, idx: int, adc: int) -> int:
        per_pot_invert = False
        if self._profile is not None and 0 <= idx < len(self._profile.pots):
            per_pot_invert = bool(getattr(self._profile.pots[idx], "invert", False))
        if per_pot_invert ^ self._invert_all:
            return ADC_RANGE - 1 - adc
        return adc

    @Slot(int, int)
    def _on_pot(self, idx: int, value: int) -> None:
        # V7: Pomiń gdy DeckMap niewidoczny — eliminuje ~100 setValue/setText/s
        # gdy user na innej karcie (Pots/Settings) lub zminimalizowany.
        if not self.isVisible() or not (0 <= idx < len(self._pots)):
            return
        self._pots[idx].set_value(self._display_value(idx, value))

    @Slot(int, bool)
    def _on_button(self, idx: int, pressed: bool) -> None:
        if not self.isVisible() or not (0 <= idx < len(self._buttons)):
            return
        self._buttons[idx].set_pressed(pressed)
