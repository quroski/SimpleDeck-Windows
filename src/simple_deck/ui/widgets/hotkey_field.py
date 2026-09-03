"""HotkeyField - pole do przechwytywania i wyświetlania skrótu klawiszowego.

V3 fix: Zamiast focus-based keyPressEvent (który przeciekał do WM i aktywował
akcje zamiast przechwytywania), używamy modalnego HotkeyCaptureDialog z
grabKeyboard(). Dialog przechwytuje wszystkie klawisze bez przecieku.

V4: Na Linux/X11 dodatkowo XGrabKey przez python-xlib — grab'uje klawisze na
poziomie serwera X, więc WM nie widzi kombinacji typu Super+E. Na Wayland /
Windows fallback do Qt grabKeyboard. Dodatkowo „Wpisz ręcznie…" pozwala
wpisać combo tekstowo gdy capture nie działa.

Klik w pole → otwiera dialog → użytkownik naciska kombinację → dialog zamyka się
i ustawia combo w polu. ESC anuluje, Backspace czyści.
"""
from __future__ import annotations

import sys
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit,
                                QPushButton, QVBoxLayout)


# Qt.Key → nazwa czytelna (krótka, normalizowana).
# Modyfikatory: Ctrl, Shift, Alt, Meta/Super/Win
# Specjalne: MediaPlay, F1-F12, interpunkcja
QT_KEY_NAMES = {
    # Modyfikatory
    Qt.Key_Control: "Ctrl",
    Qt.Key_Shift:   "Shift",
    Qt.Key_Alt:     "Alt",
    Qt.Key_AltGr:   "AltGr",
    Qt.Key_Meta:    "Meta",
    Qt.Key_Super_L: "Super", Qt.Key_Super_R: "Super",
    # Spacja / Enter / Tab
    Qt.Key_Space:      "Space",
    Qt.Key_Return:     "Enter", Qt.Key_Enter: "Enter",
    Qt.Key_Tab:        "Tab",
    Qt.Key_Backtab:    "Tab",
    Qt.Key_Backspace:  "Backspace",
    # Nawigacja
    Qt.Key_Insert:   "Insert", Qt.Key_Delete: "Delete",
    Qt.Key_Home:     "Home",   Qt.Key_End: "End",
    Qt.Key_PageUp:   "PageUp", Qt.Key_PageDown: "PageDown",
    Qt.Key_Left:     "Left",   Qt.Key_Right: "Right",
    Qt.Key_Up:       "Up",     Qt.Key_Down: "Down",
    # Escape
    Qt.Key_Escape: "Esc",
    # Multimedia (często używane w Stream Decku)
    Qt.Key_MediaPlay:     "MediaPlay",
    Qt.Key_MediaPause:    "MediaPause",
    Qt.Key_MediaTogglePlayPause: "MediaPlay",
    Qt.Key_MediaNext:     "MediaNext",
    Qt.Key_MediaPrevious: "MediaPrev",
    Qt.Key_VolumeUp:      "VolUp",
    Qt.Key_VolumeDown:    "VolDown",
    Qt.Key_VolumeMute:    "VolMute",
    Qt.Key_MediaStop:     "MediaStop",
    # Interpunkcja (m12 fix - wcześniej spadały do "Key_Comma" etc.)
    Qt.Key_Comma:         ",",      # ,
    Qt.Key_Period:        ".",      # .
    Qt.Key_Slash:         "/",      # /
    Qt.Key_Semicolon:     ";",      # ;
    Qt.Key_Apostrophe:    "'",      # '
    Qt.Key_QuoteLeft:     "`",      # `
    Qt.Key_Minus:         "-",      # -
    Qt.Key_Equal:         "=",      # =
    Qt.Key_BracketLeft:   "[",      # [
    Qt.Key_BracketRight:  "]",      # ]
    Qt.Key_Backslash:     "\\",     # \
    # Numpad
    Qt.Key_Plus:          "+",
    Qt.Key_Asterisk:      "*",
}


def _qt_key_to_name(key: int) -> str:
    """Mapa klucza Qt na czytelną nazwę.

    Modyfikatory są filtrowane przez wywołującego (keyPressEvent).
    Zwraca None-equivalent ("") jeśli nie rozpoznano - ale zwykle zwraca nazwę.
    """
    if key in QT_KEY_NAMES:
        return QT_KEY_NAMES[key]
    # Litery A-Z (duże)
    if Qt.Key_A <= key <= Qt.Key_Z:
        return chr(ord("A") + (key - Qt.Key_A))
    # Cyfry 0-9
    if Qt.Key_0 <= key <= Qt.Key_9:
        return chr(ord("0") + (key - Qt.Key_0))
    # Klawisze funkcyjne F1-F12
    if Qt.Key_F1 <= key <= Qt.Key_F12:
        return f"F{key - Qt.Key_F1 + 1}"
    # Fallback - nazwa Qt lub "Key{N}"
    try:
        text = Qt.Key(key).name
        return text.capitalize() if text else f"Key{key}"
    except Exception:
        return f"Key{key}"


def _format_combo(mods: Qt.KeyboardModifiers, key_name: str) -> str:
    """Zbuduj combo string w stylu 'Ctrl+Shift+D'.

    Modyfikatory w canonicalnej kolejności: Ctrl, Shift, Alt, Super, AltGr.
    key_name jest dodawany na końcu jeśli to nie jest sam modyfikator.
    """
    parts = []
    if mods & Qt.ControlModifier: parts.append("Ctrl")
    if mods & Qt.ShiftModifier:   parts.append("Shift")
    if mods & Qt.AltModifier:     parts.append("Alt")
    if mods & Qt.MetaModifier:    parts.append("Super")
    # AltGr jest osobnym modyfikatorem na europejskich klawiaturach
    # (Qt.Key_AltGr generuje też GroupSwitchModifier w niektórych Qt)
    group_mod = getattr(Qt, "GroupSwitchModifier", None) or getattr(Qt, "GroupModifier", None)
    if group_mod is not None and (mods & group_mod) and "Alt" not in parts:
        parts.append("AltGr")
    if key_name and key_name not in ("Ctrl", "Shift", "Alt", "Super", "Meta", "AltGr"):
        parts.append(key_name)
    return "+".join(parts)


# Mapowanie synonimów modyfikatorów → canonicalna nazwa.
# Użytkownik może wpisać win, windows, meta → wszystkie zamieniane na „Super".
_MOD_SYNONYMS = {
    "ctrl": "Ctrl", "control": "Ctrl", "ctl": "Ctrl",
    "shift": "Shift", "shft": "Shift",
    "alt": "Alt", "option": "Alt", "opt": "Alt",
    "super": "Super", "win": "Super", "windows": "Super",
        "meta": "Super", "cmd": "Super", "command": "Super",
    "altgr": "AltGr", "altgrl": "AltGr", "altgrr": "AltGr",
}


def _normalize_combo_token(token: str) -> str:
    """Normalizuj pojedynczy token combo z ręcznego wpisu.

    „ctrl" → „Ctrl", „WIN" → „Super", „d" → „D", „mediaplay" → „MediaPlay".
    Puste tokeny są odrzucane (zwracane jako '').
    """
    t = token.strip()
    if not t:
        return ""
    low = t.lower()
    # Najpierw synonimy modyfikatorów (case-insensitive)
    if low in _MOD_SYNONYMS:
        return _MOD_SYNONYMS[low]
    # Pojedynczy znak — upper case (litery, cyfry, interpunkcja)
    if len(t) == 1:
        return t.upper()
    # Funkcyjne (F1..F12) — zachowaj „F" upper + numer
    if low[0] == "f" and low[1:].isdigit():
        n = int(low[1:])
        if 1 <= n <= 12:
            return f"F{n}"
    # Multimedia i specjalne — CamelCase z mapy, fallback Capitalize
    media_map = {
        "mediaplay": "MediaPlay", "mediapause": "MediaPause",
        "medianext": "MediaNext", "mediaprev": "MediaPrev",
        "mediastop": "MediaStop",
        "volup": "VolUp", "voldown": "VolDown", "volmute": "VolMute",
        "space": "Space", "enter": "Enter", "return": "Enter",
        "tab": "Tab", "esc": "Esc", "escape": "Esc",
        "insert": "Insert", "delete": "Delete",
        "home": "Home", "end": "End",
        "pageup": "PageUp", "pagedown": "PageDown",
        "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    }
    if low in media_map:
        return media_map[low]
    # Fallback — capitalize pierwsza litera
    return t[:1].upper() + t[1:]


class HotkeyCaptureDialog(QDialog):
    """V3: Modalny dialog do przechwytywania skrótu klawiszowego.

    Używa grabKeyboard() aby zapobiec przeciekowi klawiszy do WM i innych
    handlerów. To naprawia bug gdzie naciśnięcie Super+E otwierało menedżer
    plików zamiast zostać przechwycone jako skrót.
    """

    # Modyfikatory które ignorujemy (czekamy na klawisz właściwy)
    _MODIFIER_KEYS = frozenset({
        Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta,
        Qt.Key_Super_L, Qt.Key_Super_R, Qt.Key_AltGr,
    })

    def __init__(self, current_hotkey: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Przechwytywanie skrótu")
        self.setModal(True)
        self.setFixedSize(400, 260)
        self._combo: Optional[str] = None
        self._captured = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(12)

        lbl = QLabel("Naciśnij kombinację klawiszy…")
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet("font-size: 14px; color: rgba(255,255,255,180);")
        lay.addWidget(lbl)

        self._display = QLabel(current_hotkey or "…")
        self._display.setAlignment(Qt.AlignCenter)
        self._display.setStyleSheet(
            "font-size: 22px; font-weight: 700; padding: 16px;"
            "background: rgba(45,212,255,24); border-radius: 8px;"
        )
        lay.addWidget(self._display)

        hint = QLabel("ESC = anuluj  ·  Backspace = wyczyść")
        hint.setStyleSheet("font-size: 11px; color: rgba(255,255,255,100);")
        hint.setAlignment(Qt.AlignCenter)
        lay.addWidget(hint)

        btn_row = QHBoxLayout()
        manual_btn = QPushButton("✎  Wpisz ręcznie…")
        manual_btn.setCursor(Qt.PointingHandCursor)
        manual_btn.setToolTip(
            "Wpisz skrót ręcznie — przydatne gdy OS przechwytuje kombinację")
        manual_btn.clicked.connect(self._on_manual_input)
        clear_btn = QPushButton("Wyczyść")
        clear_btn.clicked.connect(self._on_clear)
        cancel_btn = QPushButton("Anuluj")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(manual_btn)
        btn_row.addStretch()
        btn_row.addWidget(clear_btn)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        key = e.key()
        mods = e.modifiers()

        if key == Qt.Key_Escape:
            self.reject()
            return

        if key == Qt.Key_Backspace and not mods:
            self._on_clear()
            return

        # Sam modyfikator — czekaj na właściwy klawisz
        if key in self._MODIFIER_KEYS:
            return

        key_name = _qt_key_to_name(key)
        combo = _format_combo(mods, key_name)
        self._combo = combo
        self._display.setText(combo)
        self._captured = True

        # Auto-zamknij po krótkim opóźnieniu (wizualne potwierdzenie)
        QTimer.singleShot(250, self.accept)

    def _on_clear(self) -> None:
        self._combo = ""
        self._captured = True
        self.accept()

    def _on_manual_input(self) -> None:
        """Otwórz dialog do ręcznego wpisania skrótu.

        Przydatne gdy OS przechwytuje kombinację (np. Super+E, Ctrl+Alt+Del)
        i grabKeyboard() nie pomaga. Użytkownik wpisuje tekst np. „Super+E"
        a my normalizujemy format (title-case modyfikatorów, + jako separator).
        """
        from PySide6.QtWidgets import QInputDialog

        text, ok = QInputDialog.getText(
            self, "Wpisz skrót ręcznie",
            "Skrót (np. Ctrl+Shift+D, Super+E, MediaPlay, F5):",
            text=self._combo or "")
        if not ok:
            return   # anulowano — zostań w dialogu przechwytywania
        text = text.strip()
        if not text:
            self._on_clear()
            return
        # Normalizacja: rozdziel po +, title-case każdy człon, połącz z powrotem.
        # Akceptujemy dowolny modyfikator / klawisz — bez ścisłej walidacji,
        # bo backend hotkey ma własne mapowanie i po prostu zignoruje nieznane.
        parts = [_normalize_combo_token(p) for p in text.split("+")]
        combo = "+".join(p for p in parts if p)
        self._combo = combo
        self._display.setText(combo)
        self._captured = True
        QTimer.singleShot(150, self.accept)

    def get_combo(self) -> str:
        """Zwraca przechwycony combo (lub '' jeśli wyczyszczono/anulowano)."""
        return self._combo if self._captured else ""


class HotkeyField(QLineEdit):
    """Pole wyświetlające skrót. Klik = otwórz dialog przechwytywania.

    V3: Zastąpiono focus-based capturing modalnym dialogiem z grabKeyboard().
    """

    hotkey_changed = Signal(str)  # combo string

    _MODIFIER_KEYS = HotkeyCaptureDialog._MODIFIER_KEYS

    def __init__(self, placeholder: str = "Kliknij aby ustawić…", parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setReadOnly(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self._combo: Optional[str] = None

    def mousePressEvent(self, e) -> None:
        """Klik → otwórz dialog przechwytywania."""
        super().mousePressEvent(e)
        if e.button() != Qt.LeftButton:
            return
        self._open_capture_dialog()

    def _open_capture_dialog(self) -> None:
        """Otwórz modalny dialog i ustaw wynik.

        Na Linux/X11 dodatkowo grab'uje klawiaturę na poziomie serwera X
        (XGrabKey) aby WM nie przechwytywał skrótów typu Super+E.
        Na Wayland/Windows fallback do Qt grabKeyboard().
        """
        dlg = HotkeyCaptureDialog(current_hotkey=self._combo or "", parent=self.window())
        # X11 keyboard grab (Linux only) — zapobiega przeciekowi skrótów do WM
        grabber = None
        if sys.platform.startswith("linux"):
            try:
                from ...platform.x11_grab import X11KeyboardGrabber
                grabber = X11KeyboardGrabber()
                if not grabber.grab():
                    grabber = None
            except Exception:
                grabber = None
        _is_linux = sys.platform.startswith("linux")
        if _is_linux:
            dlg.grabKeyboard()
        try:
            result = dlg.exec()
        finally:
            if _is_linux:
                dlg.releaseKeyboard()
            if grabber is not None:
                grabber.release()

        if result == QDialog.Accepted:
            combo = dlg.get_combo()
            self._combo = combo or None
            self.setText(combo)
            self.hotkey_changed.emit(combo)

    def value(self) -> str:
        """Aktualnie ustawiony combo (lub '' jeśli brak)."""
        return self._combo or ""

    def set_value(self, combo: str) -> None:
        """Programowo ustaw combo (np. wczytanie z profilu)."""
        self._combo = combo or None
        self.setText(combo or "")
