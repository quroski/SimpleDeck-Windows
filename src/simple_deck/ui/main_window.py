"""Główne okno aplikacji Simple Deck.

Layout:
    +-----------------------------------------------------------+
    | ◈  Simple Deck                 ● Połączony / Łączenie…    |   <-- Header (draggable)
    |    by GREJEM INDUSTRIES                                  |
    +--------+--------------------------------------------------+
    |        |                                                  |
    | Nav   |   Strona (QStackedWidget)                       |
    |        |                                                  |
    +--------+--------------------------------------------------+

Frameless + własny title bar. Przesuwanie okna przez ``startSystemMove()``
wywoływane z ``mousePressEvent`` na headerze (PySide6 6.4+).
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QPoint, QTimer, Signal
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsDropShadowEffect,
                                QHBoxLayout, QLabel, QMainWindow, QPushButton,
                                QSizeGrip, QSizePolicy, QStackedWidget,
                                QVBoxLayout, QWidget)

from ..core.event_bus import EventBus
from ..core.profile import Profile
from ..core.profile_manager import ProfileManager
from ..core.settings import Settings
from ..transport.connection_manager import ConnectionManager, ConnectionState
# V7: OverviewPage importowany eagerly (budowany w __init__), pozostałe strony
# importowane lazily w _ensure_page() — oszczędza ~80-150 ms cold-start pomijając
# parse 897-line config_pages.py + 361-line led_page.py gdy user tylko patrzy na Overview.
from .pages.overview import OverviewPage
from .widgets.icon import IconLabel, clear_icon_cache
from .widgets.nav_sidebar import NavSidebar
from .widgets.profile_switcher import ProfileSwitcher
from .widgets.status_chip import StatusChip
from .widgets.toast import ToastHost

log = logging.getLogger(__name__)


def _resource_path(rel: str) -> Path:
    """Zwraca ścieżkę do zasobu aplikacji (QSS, ikony, etc.).

    W trybie dev: względem tego pliku (parents[3] = desktop/).
    W trybie PyInstaller (frozen): względem sys._MEIPASS.

    Patrz Sprint 4 (D8) - bez tego asset loading pęka w bundle PyInstallera.
    """
    if getattr(sys, "frozen", False):
        # PyInstaller one-file / one-folder - zasoby rozpakowane pod _MEIPASS
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        # Tryb dev: desktop/src/simple_deck/ui/main_window.py → parents[3] = desktop/
        base = Path(__file__).resolve().parents[3]
    return base / rel


ASSETS_DIR = _resource_path("assets")
QSS_PATH = ASSETS_DIR / "themes" / "glossy.qss"
DEFAULT_ACCENT_HEX = "#2DD4FF"   # domyślny akcent cyan (używany jako token w QSS)


class MainWindow(QMainWindow):
    """Frameless main window z własnym title barem."""

    # V7: Emitowane gdy okno staje się widoczne/ukryte (showEvent/hideEvent).
    # app.py podpina tu throttle WindowDetector (1 s → 3 s) i stop AppListCache.
    visibility_changed = Signal(bool)

    # Atrybuty ustawiane z zewnątrz przez app.py (tray + startup minimized).
    _tray_ref: Optional[object] = None
    _start_hidden: bool = False    # True → ukryj okno przy starcie (tylko tray)
    _start_minimized: bool = False  # True → showMinimized() przy starcie

    def __init__(self, bus: EventBus, connection: ConnectionManager,
                 audio_backend=None, profile_mgr: Optional[ProfileManager] = None,
                 settings=None, parent=None):
        super().__init__(parent)
        self._bus = bus
        self._conn = connection
        self._audio = audio_backend
        self._profile_mgr = profile_mgr
        self._settings = settings
        self._drag_offset: Optional[QPoint] = None

        # Akcent kolorystyczny (z ustawień) - steruje ikonami + QSS
        self._accent = (settings.accent_color
                        if isinstance(settings, Settings) else DEFAULT_ACCENT_HEX)
        # Szablon QSS (oryginalny tekst - do przebarwiania przy zmianie akcentu)
        self._qss_template = self._load_qss()

        # --- Window setup ---
        self.setWindowTitle("Simple Deck")
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        # V1.3.3: Niższe minimum — okno frameless musi dać się zmniejszyć na
        # ekranach z DPI scaling 125-150% (efektywnie ~1280x620 miejsca).
        self.setMinimumSize(820, 520)
        self.resize(*self._initial_size(settings=self._settings,
                                        screen=self.screen()))
        # V1.3.3: Krawędzie okna reagują na mysz (resize) — mouse tracking dla
        # fallbacku POSIX; na Windows resize obsługuje WM_NCHITTEST.
        self.setMouseTracking(True)

        # --- Root widget ---
        root = QWidget(objectName="root")
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(18, 14, 18, 14)
        root_layout.setSpacing(12)

        # Header (z własną obsługą drag - patrz _title_mouse_press)
        self._header = self._build_header()
        root_layout.addWidget(self._header)

        # Body (sidebar + content)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)

        # Sidebar
        self._nav = NavSidebar()
        self._nav.page_requested.connect(self._set_page)
        body.addWidget(self._nav)

        # Pages
        self._stack = QStackedWidget()
        self._stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        body.addWidget(self._stack, stretch=1)
        root_layout.addLayout(body, stretch=1)

        # V1.3.4: Widoczny uchwyt resize w prawym dolnym rogu (kropki).
        # QSizeGrip działa we frameless (sam woła startSystemResize) — daje
        # oczywisty punkt zaczepienia, gdy krawędź umyka.
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.addStretch()
        self._size_grip = QSizeGrip(root)
        bottom.addWidget(self._size_grip, 0, Qt.AlignBottom | Qt.AlignRight)
        root_layout.addLayout(bottom)

        # V6: Lazy page construction — Overview jest budowany od razu (to
        # domyślna strona i subskrybuje sygnały bus), pozostałe 4 strony są
        # budowane przy pierwszej wizycie. Oszczędza 200-400 ms cold-start.
        self._current_profile = None
        self._page_overview = OverviewPage(bus=bus, connection=connection,
                                            settings=settings)
        self._stack.addWidget(self._page_overview)
        # Pozostałe strony = None do pierwszego _ensure_page(idx)
        # V7: Brak adnotacji typu — klasy są importowane lazily w _ensure_page,
        # adnotacja wymagałaby eager importu (PEP 563 nie obejmuje lokalnych
        # adnotacji wewnątrz funkcji).
        self._page_pots = None
        self._page_buttons = None
        self._page_led = None
        self._page_settings = None
        self._stack.setCurrentWidget(self._page_overview)

        # Klik na potencjometr w Overview → nawigacja do PotsPage
        self._page_overview.pot_clicked.connect(lambda *_: self._set_page(1))
        # V1.0.3: klik na komórce przycisku w Overview → nawigacja do ButtonsPage
        self._page_overview.button_clicked.connect(lambda *_: self._set_page(2))
        # V1.0.3: edycja nazwy kontrolki w Overview → profil + debounced save
        self._page_overview.label_renamed.connect(self._on_label_renamed)

        # Status wiring
        self._conn.state_changed.connect(self._on_state_changed)
        self._on_state_changed(self._conn.state)

        # Akcent: zastosuj na starcie (QSS + ikony) i słuchaj zmian ze Settings
        self.apply_accent(self._accent)

        # Debounced zapis profilu po zmianach z Overview (rename nazw kontrolek).
        # Wzorzec jak w _BaseConfigPage: single-shot 500 ms po OSTATNIEJ zmianie.
        self._profile_save_timer = QTimer(self)
        self._profile_save_timer.setSingleShot(True)
        self._profile_save_timer.setInterval(500)
        self._profile_save_timer.timeout.connect(self._flush_profile_save)

        # Toast host - nietrwałe powiadomienia
        self._toast_host = ToastHost(self._bus, self, settings=self._settings, parent=self)
        self._toast_host.show()

        # Drop shadows dla headera/sidebar (V6: tylko te, nie karty)
        self._install_shadows()

    # ===================================================================
    # Budowa interfejsu
    # ===================================================================
    def _build_header(self) -> QFrame:
        header = QFrame(objectName="header")
        header.setFixedHeight(72)
        # Header musi accept hover i mouse events (dla drag)
        header.setAttribute(Qt.WA_Hover, True)
        header.setCursor(Qt.OpenHandCursor)

        h = QHBoxLayout(header)
        h.setContentsMargins(22, 12, 22, 12)
        h.setSpacing(14)

        # Logo
        logo = QLabel("◈", objectName="logo")
        logo.setAlignment(Qt.AlignCenter)
        logo.setFixedSize(44, 44)
        logo.setStyleSheet(
            "font-size: 28px; color: #2DD4FF; background: transparent;"
            "border: 1px solid rgba(45, 212, 255, 80); border-radius: 14px;"
        )
        h.addWidget(logo)

        # Tytuły
        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(0)
        title = QLabel("Simple Deck", objectName="titleLarge")
        subtitle = QLabel("by GREJEM INDUSTRIES", objectName="titleSubtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        h.addLayout(titles)

        h.addStretch()

        # Przełącznik profili (jeśli mamy ProfileManager)
        if self._profile_mgr is not None:
            self._profile_switcher = ProfileSwitcher(
                self._profile_mgr, accent=self._accent)
            h.addWidget(self._profile_switcher)

        # Status chip (przed przyciskami okna)
        self._status_chip = StatusChip()
        h.addWidget(self._status_chip)

        # Przycisk minimalizacji / zamknięcia
        minimize_btn = QPushButton("—", objectName="iconBtn")
        minimize_btn.setFixedSize(36, 36)
        minimize_btn.setCursor(Qt.PointingHandCursor)
        minimize_btn.setToolTip("Minimalizuj")
        minimize_btn.clicked.connect(self.showMinimized)
        h.addWidget(minimize_btn)

        close_btn = QPushButton("✕", objectName="iconBtn")
        close_btn.setFixedSize(36, 36)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setToolTip("Zamknij")
        close_btn.setStyleSheet(
            "QPushButton:hover { color: #FF5C6C; background: rgba(255, 92, 108, 30); border-radius: 8px; }"
        )
        close_btn.clicked.connect(self.close)
        h.addWidget(close_btn)

        # KRYTYCZNE: przechwytuj mousePressEvent NA HEADERZE (nie na QMainWindow)
        # Bo QMainWindow nigdy nie dostanie kliku w header - child QFrame go pochłania.
        # StartSystemMove (PySide6 6.4+) - natywne przesuwanie okna przez system operacyjny.
        header.mousePressEvent = self._title_mouse_press

        return header

    def _title_mouse_press(self, e: QMouseEvent) -> None:
        """Handler dla kliku w header - inicjuje drag okna przez system.

        Wywoływany jako zdarzenie header.mousePressEvent (nadpisane bezpośrednio),
        nie jako metoda QMainWindow - QMainWindow nigdy nie dostanie tego eventu.
        """
        if e.button() == Qt.LeftButton:
            wh = self.windowHandle()
            if wh is not None:
                # startSystemMove: asynchronicznie prosi OS o przejęcie kontroli
                # nad pozycją okna. Działa na Windows, X11, Wayland, macOS.
                if hasattr(wh, "startSystemMove"):
                    wh.startSystemMove()
                    e.accept()
                    return
            # Fallback dla starszych Qt (< 6.4): ręczny drag
            self._drag_offset = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def _install_shadows(self) -> None:
        """Nakłada QGraphicsDropShadowEffect tylko na header + sidebar.

        V6: Kiedyś działało na WSZYSTKIE QFrame#card w oknie (~10-20 kart).
        QGraphicsDropShadowEffect to najdroższy efekt Qt — każdy repaint karty
        wymaga offscreen render + blur. Teraz tylko header/sidebar (stałe,
        rzadko repaintowane), karty używają subtelnego border z QSS.

        Idempotentne - pomija widgety które już mają efekt.
        """
        for card in self.findChildren(QFrame):
            # V6: Tylko header i sidebar — karty ("card") są liczne i często
            # się repaintują (pot wiggle, overview). Cień na każdej = koszt.
            if card.objectName() in ("header", "sidebar"):
                if card.graphicsEffect() is not None:
                    continue
                shadow = QGraphicsDropShadowEffect(card)
                shadow.setBlurRadius(30)
                shadow.setColor(QColor(0, 0, 0, 140))
                shadow.setOffset(0, 8)
                card.setGraphicsEffect(shadow)

    def apply_accent(self, color: str) -> None:
        """Zmień akcent aplikacji na żywo: QSS + wszystkie IconLabel.

        ``color`` to hex (np. '#FF2EC4'). Domyślny cyan (#2DD4FF) w QSS jest
        zastępowany nowym kolorem. Ikony są przebarwiane przez clear_icon_cache
        + recolor IconLabel.
        """
        self._accent = color
        # 1. QSS - podmień domyślny akcent na nowy
        qss = self._qss_template.replace(DEFAULT_ACCENT_HEX, color)
        self.setStyleSheet(qss)
        # 1b. QSS na poziomie aplikacji - tematyzuje dialogi, menu kontekstowe,
        # tooltipy które nie są dziećmi MainWindow. Window-level QSS nadal ma
        # priorytet dla widgetów okna głównego (brak konfliktu).
        from PySide6.QtWidgets import QApplication as _QApplication
        app = QApplication.instance()
        if isinstance(app, _QApplication):
            app.setStyleSheet(qss)
        # 2. Ikony - wyczyść cache i przebarwij wszystkie IconLabel
        clear_icon_cache()
        for lbl in self.findChildren(IconLabel):
            lbl.set_color(color)
        # 2b. Pasek nawigacji (ikony aktywnego elementu)
        nav = getattr(self, "_nav", None)
        if nav is not None:
            nav.set_accent(color)
        # 3. Przełącznik profili w headerze
        sw = getattr(self, "_profile_switcher", None)
        if sw is not None:
            sw.set_accent(color)
        # 4. Przełącznik w Settings (embedowany) też (V6: lazy — może być None)
        ss = getattr(self._page_settings, "_profile_switcher", None) \
            if self._page_settings is not None else None
        if ss is not None:
            ss.set_accent(color)

    @staticmethod
    def _load_qss() -> str:
        if QSS_PATH.exists():
            try:
                return QSS_PATH.read_text(encoding="utf-8")
            except Exception:
                log.exception("failed to load QSS")
        log.warning("QSS file not found: %s", QSS_PATH)
        return ""

    # ===================================================================
    # Sloty
    # ===================================================================
    def _ensure_page(self, idx: int) -> None:
        """V6: Leniwa budowa strony przy pierwszej wizycie.

        Pages 1-4 (Pots/Buttons/LED/Settings) są budowane na żądanie by
        przyspieszyć cold-start o ~200-400 ms. Po pierwszym zbudowaniu są
        cache'owane (dodane do QStackedWidget).

        V7: Również import modułów jest lazy — oszczędza kolejze ~80-150 ms
        cold-start pomijając parse 897-line config_pages.py + 361-line led_page.py
        gdy user nigdy nie otwiera Pots/Buttons/LED/Settings.

        FIX: Buduj wszystkie strony od 1 do idx w kolejności — insertWidget(i)
        wymaga że indeksy 1..i-1 są już w stacku, inaczej lądują na złych
        pozycjach (np. skok do Przycisków bez wizyty w Potencjometrach).
        """
        for i in range(1, idx + 1):
            self._build_page(i)

    def _build_page(self, idx: int) -> None:
        """Zbuduj pojedynczą stronę jeśli jeszcze nie istnieje."""
        if idx == 0 or self._stack.widget(idx) is not None:
            return  # Overview (zawsze zbudowany) lub już zbudowana
        if idx == 1 and self._page_pots is None:
            from .pages.config_pages import PotsPage
            self._page_pots = PotsPage(bus=self._bus, connection=self._conn,
                                       audio_backend=self._audio,
                                       profile_mgr=self._profile_mgr,
                                       settings=self._settings)
            self._stack.insertWidget(1, self._page_pots)
            if self._current_profile is not None:
                self._page_pots.set_profile(self._current_profile)
        elif idx == 2 and self._page_buttons is None:
            from .pages.config_pages import ButtonsPage
            self._page_buttons = ButtonsPage(bus=self._bus,
                                             connection=self._conn,
                                             profile_mgr=self._profile_mgr)
            self._stack.insertWidget(2, self._page_buttons)
            if self._current_profile is not None:
                self._page_buttons.set_profile(self._current_profile)
        elif idx == 3 and self._page_led is None:
            from .pages.led_page import LedPage
            self._page_led = LedPage(bus=self._bus, connection=self._conn,
                                     profile_mgr=self._profile_mgr)
            self._stack.insertWidget(3, self._page_led)
            if self._current_profile is not None:
                self._page_led.set_profile(self._current_profile)
        elif idx == 4 and self._page_settings is None:
            from .pages.config_pages import SettingsPage
            self._page_settings = SettingsPage(bus=self._bus,
                                               connection=self._conn,
                                               profile_mgr=self._profile_mgr,
                                               audio_backend=self._audio,
                                               settings=self._settings)
            self._stack.insertWidget(4, self._page_settings)
            if self._current_profile is not None:
                pass  # SettingsPage nie ma set_profile
            self._page_settings.accent_changed.connect(self.apply_accent)
            # Apply current accent to newly-built settings page icons
            self.apply_accent(self._accent)

    def _set_page(self, idx: int) -> None:
        self._ensure_page(idx)
        if 0 <= idx < self._stack.count():
            self._stack.setCurrentIndex(idx)

    def _on_label_renamed(self, kind: str, idx: int, label: str) -> None:
        """V1.0.3: Zapisz własną nazwę kontrolki z Overview do profilu.

        kind: "pot" | "btn". Pusta nazwa = wróć do domyślnej ("POT N"/"BTN N").
        Karty na stronach POTS/PRZYCISKI pokazują badge z numerem kanału, więc
        nie wymagają rebuildu — tylko zapis profilu (debounced).
        """
        profile = self._current_profile
        if profile is None:
            return
        try:
            if kind == "pot" and 0 <= idx < len(profile.pots):
                profile.pots[idx].label = label
            elif kind == "btn" and 0 <= idx < len(profile.buttons):
                profile.buttons[idx].label = label
            else:
                return
        except (IndexError, AttributeError):
            return
        # V1.0.3b: zsynchronizuj nazwy na już zbudowanych stronach POTS/PRZYCISKI
        # (bez rebuildu — karty tylko odświeżają tytuł). Niezbudowane strony
        # pobiorą nazwy z profilu przy pierwszym _build_page → set_profile.
        if kind == "pot" and self._page_pots is not None:
            self._page_pots.refresh_labels()
        elif kind == "btn" and self._page_buttons is not None:
            self._page_buttons.refresh_labels()
        self._profile_save_timer.start()

    def _flush_profile_save(self) -> None:
        """Zapisz profil po wygaśnięciu debounce (rename nazw z Overview)."""
        if self._profile_mgr is None or self._current_profile is None:
            return
        try:
            self._profile_mgr.save(self._current_profile)
            log.debug("profile '%s' saved (label rename)",
                      self._current_profile.name)
        except Exception:
            log.exception("profile save failed")

    def _on_state_changed(self, state: ConnectionState) -> None:
        self._status_chip.set_state(state)

    def set_profile(self, profile: Profile) -> None:
        """Propaguj profil do stron konfiguracyjnych (tylko zbudowane)."""
        self._current_profile = profile
        self._page_overview.set_profile(profile)
        if self._page_pots is not None:
            self._page_pots.set_profile(profile)
        if self._page_buttons is not None:
            self._page_buttons.set_profile(profile)
        if self._page_led is not None:
            self._page_led.set_profile(profile)
        # V7: _install_shadows() wywoływane z __init__ i _ensure_page() —
        # nie ma potrzeby na ponowne findChildren(QFrame) przy każdym
        # set_profile. Header/sidebar mają już cienie, nowe karty dostają
        # w _ensure_page().

    # ===================================================================
    # Resize (V1.3.3) — frameless okno nie ma natywnych uchwytów rozmiaru
    # ===================================================================
    # Strefa przy krawędzi okna traktowana jako uchwyt resize (px).
    # V1.3.4: 8 → 12 — węższa strefa była słabo chwytalna przy DPI 125-150%.
    RESIZE_MARGIN = 12

    # Windows non-client hit-test codes (winuser.h)
    _HT = {
        Qt.LeftEdge: 10, Qt.RightEdge: 11, Qt.TopEdge: 12,
        Qt.BottomEdge: 15,
        Qt.LeftEdge | Qt.TopEdge: 13, Qt.RightEdge | Qt.TopEdge: 14,
        Qt.LeftEdge | Qt.BottomEdge: 16, Qt.RightEdge | Qt.BottomEdge: 17,
    }

    @classmethod
    def _initial_size(cls, settings=None, screen=None) -> tuple[int, int]:
        """Rozmiar startowy: zapisany w settings (jeśli był) → clamp do ekranu.

        Frameless okno bez natywnego WM musiało startować 1280x800, co na
        ekranach z DPI scaling 125-150% (available ~1280x620) wystawało poza
        ekran i — bez uchwytów resize — było nie do zmniejszenia. Teraz:
        1) przywróć ``settings.window_size`` jeśli zapisany i sensowny,
        2) obetnij do availableGeometry aktualnego ekranu.

        V1.0.2 fixes ("okno w niestandardowym rozmiarze"):
          - Saved sizes are validated against a sane minimum (900x600), so a
            corrupted / degenerate entry can no longer open a sliver window.
          - Clamping uses ``min(90%, 90%)`` of available geometry instead of
            the previous ``-24 px`` hack, so windows open at even, standard
            proportions on any screen (e.g. 1366x768 → ~1230x655).
          - Saved sizes that required clamping are *not* silently accepted —
            clamp result is returned but the caller persists only what the
            user actually chose (see _flush_settings).
        """
        DEFAULT_W, DEFAULT_H = 1280, 800
        MIN_W, MIN_H = 900, 600   # poniżej tego okno traci użyteczność

        w, h = DEFAULT_W, DEFAULT_H
        saved = getattr(settings, "window_size", None) \
            if settings is not None else None
        if isinstance(saved, (list, tuple)) and len(saved) == 2:
            try:
                sw, sh = int(saved[0]), int(saved[1])
                if sw >= MIN_W and sh >= MIN_H:
                    w, h = sw, sh
            except (TypeError, ValueError):
                pass
        if screen is not None:
            avail = screen.availableGeometry()
            # 90% dostępnego obszaru — pozostawia margines na paski zadań,
            # docki i tiling; zaokrąglenie do 2 px dla "równych" wartości.
            max_w = int(avail.width() * 0.9) & ~1
            max_h = int(avail.height() * 0.9) & ~1
            w = min(w, max_w)
            h = min(h, max_h)
        return max(w, MIN_W), max(h, MIN_H)

    def _edges_at(self, pos: QPoint) -> Qt.Edges:
        """Krawędzie okna nakładające się na punkt ``pos`` (wsp. okna).

        Punkt w lewym górnym rogu zwraca ``LeftEdge | TopEdge`` etc.;
        środek okna zwraca 0 (brak).
        """
        m = self.RESIZE_MARGIN
        edges = Qt.Edges()
        if pos.x() < m:
            edges |= Qt.LeftEdge
        elif pos.x() >= self.width() - m:
            edges |= Qt.RightEdge
        if pos.y() < m:
            edges |= Qt.TopEdge
        elif pos.y() >= self.height() - m:
            edges |= Qt.BottomEdge
        return edges

    def nativeEvent(self, eventType, message: int) -> object:
        """Windows: WM_NCHITTEST — krawędzie okna jako strefy resize OS-a.

        Natywny hit-test idzie do okna PRZED rozdzieleniem na child widgety,
        więc działa nawet tam, gdzie Qt-owy fallback (mousePressEvent) nigdy
        nie dostanie eventu (dzieci pochłaniają). Daje natywne kursory,
        płynny resize i Aero Snap za darmo.
        """
        if eventType == b"windows_generic_MSG":
            try:
                ht = self._nchittest(message)
            except Exception:
                ht = 0
            if ht:
                return True, ht
        return super().nativeEvent(eventType, message)

    def _nchittest(self, message) -> int:
        """Zwróć HT* dla WM_NCHITTEST lub 0 (= default/HTCLIENT)."""
        import ctypes
        from ctypes import wintypes

        class _MSG(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                        ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                        ("time", wintypes.DWORD), ("pt", wintypes.POINT),
                        ("lPrivate", ctypes.c_uint)]

        msg = _MSG.from_address(int(message))
        WM_NCHITTEST = 0x0084
        if msg.message != WM_NCHITTEST:
            return 0
        # lParam: podpisane 16-bit screen coords (multi-monitor: mogą być < 0)
        x = ctypes.c_short(msg.lParam & 0xFFFF).value
        y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
        edges = self._edges_at(self.mapFromGlobal(QPoint(x, y)))
        return self._HT.get(edges, 0)

    # ===================================================================
    # Dragging fallback (dla Qt < 6.4 bez startSystemMove)
    # ===================================================================
    def mousePressEvent(self, e):
        # V1.3.3: Klik w strefę krawędzi → systemowy resize (fallback Linux/
        # Wayland; na Windows robi to WM_NCHITTEST). Event dociera tutaj bo
        # marginesy root-widgetu (18/14 px) ignorują mousePress i Qt
        # propaguje je do QMainWindow.
        if e.button() == Qt.LeftButton:
            edges = self._edges_at(e.position().toPoint())
            if edges:
                wh = self.windowHandle()
                if wh is not None and hasattr(wh, "startSystemResize"):
                    wh.startSystemResize(edges)
                    e.accept()
                    return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        # Tylko fallback gdy startSystemMove niedostępny - ustawiono _drag_offset
        if self._drag_offset is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_offset)
            e.accept()

    def mouseReleaseEvent(self, e):
        self._drag_offset = None

    # ===================================================================
    # Klawiatura
    # ===================================================================
    def keyPressEvent(self, e):
        # ESC zamyka okno TYLKO w trybie debug (SIMPLE_DECK_DEBUG env).
        # W produkcji zamykanie przez krzyżyk w prawym górnym rogu headera.
        if e.key() == Qt.Key_Escape and os.environ.get("SIMPLE_DECK_DEBUG"):
            self.close()
        elif e.key() == Qt.Key_Q and e.modifiers() & Qt.ControlModifier:
            # Ctrl+Q zawsze zamyka (standardowy shortcut)
            self.close()
        else:
            super().keyPressEvent(e)

    # ===================================================================
    # Widoczność okna — V7 throttle sub-systemów gdy ukryte (tray)
    # ===================================================================
    def showEvent(self, event):
        """V7: Gdy okno staje się widoczne — wznow pełne interwały pollingu."""
        super().showEvent(event)
        self.visibility_changed.emit(True)

    def hideEvent(self, event):
        """V7: Gdy okno ukryte (tray/minimize) — zwolnij polling.

        Połączenie HID, hotkeye, LED VU, watchdog, reconnect nadal działają
        (te sub-systemy nie słuchają ``visibility_changed``). Tylko UI-fidelity
        work (repainty, enumeracje PA, detekcja aktywnej aplikacji) zwalnia.
        """
        super().hideEvent(event)
        self.visibility_changed.emit(False)

    # ===================================================================
    # Cleanup - wołane gdy okno się zamyka
    # ===================================================================
    def closeEvent(self, event):
        """Zatrzymaj ConnectionManager i inne zasoby przed zamknięciem.

        V6: Jeśli ``settings.minimize_to_tray_on_close`` jest True, zamykanie
        okna ukrywa je do tray'a zamiast kończyć aplikację.

        Bez tego: wątek HID reader ginie z procesem ale hid.device zostaje
        w stanie transitional, QTimery mogą tykać po close, profile save
        timer może nie zdążyć z flush.
        """
        # V6: Minimize to tray zamiast quit
        if (self._settings is not None
                and getattr(self._settings, "minimize_to_tray_on_close", False)
                and getattr(self._settings, "show_tray_icon", False)):
            log.info("MainWindow closeEvent — minimize to tray")
            # Flush settings before hiding (debounced saves might be pending)
            self._flush_settings()
            event.ignore()
            self.hide()
            return

        log.info("MainWindow closeEvent - cleanup")
        self._flush_settings()
        # Flush wszystkich debounced zapisów profilu (V6: lazy pages mogą być None)
        for page in (self._page_pots, self._page_buttons):
            if page is not None and hasattr(page, "_flush_save"):
                page._flush_save()
        # V6: Flush debounced settings save (CFG sliders, accent, etc.)
        if self._page_settings is not None and hasattr(self._page_settings, "_flush_save"):
            self._page_settings._flush_save()
        # Zatrzymaj połączenie (zabija reader thread + zamyka hid.device)
        try:
            self._conn.stop()
        except Exception:
            log.exception("connection.stop() failed during closeEvent")
        super().closeEvent(event)

    def _flush_settings(self):
        """Flush debounced settings + profile saves."""
        for page in (self._page_pots, self._page_buttons):
            if page is not None and hasattr(page, "_flush_save"):
                page._flush_save()
        if self._page_settings is not None and hasattr(self._page_settings, "_flush_save"):
            self._page_settings._flush_save()
        if self._settings is not None:
            # V1.3.3: Zapamiętaj rozmiar okna — przywrócony przy następnym
            # starcie (clamped do ekranu w _initial_size).
            self._settings.window_size = [self.width(), self.height()]
            try:
                from ..core.settings import settings_path
                self._settings.to_json(settings_path())
            except Exception:
                log.exception("settings flush failed")
