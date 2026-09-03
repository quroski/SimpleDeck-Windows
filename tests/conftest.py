"""Wspólne fixtures dla wszystkich testów.

Kluczowe:
  - mock_hid: zmockuj moduł `hid` na poziomie sys.modules ZANIM cokolwiek
    z simple_deck.transport zostanie zaimportowane (hidapi nie dostępne w CI)
  - qapp: singleton QApplication dla Qt
  - qtbot: z pytest-qt
  - bus: świeży EventBus
  - mock_connection: MagicMock z prawdziwymi Signal-ami
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


# ============================================================================
#  Session-scoped mock modułu `hid` - instalowany ZANIM simple_deck.transport
#  zaimportuje `import hid`. Bez tego każdy test importujący cokolwiek z
#  simple_deck.transport zawiedzie z "No module named 'hid'" w środowisku bez USB.
# ============================================================================
@pytest.fixture(autouse=True, scope="session")
def _mock_hid_module():
    """Zmockuj moduł `hid` na poziomie sys.modules."""
    mock = MagicMock()
    mock.device = MagicMock(return_value=MagicMock())
    mock.enumerate = MagicMock(return_value=[])
    sys.modules["hid"] = mock
    # WAŻNE: simple_deck.transport.hid_device importuje `hid` przy ładowaniu
    # conftestu (zanim fixture wystartuje) - więc ma już referencję do PRAWDZI-
    # wego hidapi. Rebinduj ją, by testy NIGDY nie dotykały prawdziwego sprzętu
    # (np. podłączonego STM32 - bez tego testy są hardware-flaky).
    try:
        import simple_deck.transport.hid_device as _hd
        _hd.hid = mock
    except Exception:
        pass
    yield
    sys.modules.pop("hid", None)


# ============================================================================
#  QApplication - singleton dla całej sesji testowej.
#  pytest-qt dostarcza własny `qapp` fixture ale nadpisujemy dla kontroli.
# ============================================================================
@pytest.fixture(scope="session")
def qapp():
    """Pojedyncza QApplication dla całej sesji testowej."""
    from PySide6.QtWidgets import QApplication
    import os
    # Offscreen dla CI / headless
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    yield app
    # Nie wołaj app.quit() - kolejne testy mogą korzystać


@pytest.fixture
def qtbot(qapp, qtbot):
    """qtbot fixture z pytest-qt - zapewlia QApplication jest gotowy."""
    return qtbot


# ============================================================================
#  EventBus - świeży dla każdego testu
# ============================================================================
@pytest.fixture
def bus(qapp):
    from simple_deck.core.event_bus import EventBus
    return EventBus()


# ============================================================================
#  Mock ConnectionManager - ma prawdziwe sygnały ale MagicMock dla API
# ============================================================================
@pytest.fixture
def mock_connection(qapp):
    """Mock ConnectionManager z prawdziwymi Signal-ami (Qt nie mockuje Signal)."""
    from PySide6.QtCore import QObject, Signal
    from simple_deck.transport.connection_manager import ConnectionState

    class _MockSignals(QObject):
        state_changed = Signal(object)
        frame_received = Signal(object)
        fw_version_received = Signal(int, int, int)
        heartbeat_received = Signal(int, int)

    signals = _MockSignals()
    conn = MagicMock()
    # Keeper: QObject bez parenta ginie z GC gdy lokalna referencja wyjdzie
    # z fixture (bound-signal NIE trzyma źródła przy życiu) — wtedy każdy
    # .connect() pada "Signal source has been deleted".
    conn._signals_keeper = signals
    conn.state = ConnectionState.DISCONNECTED
    conn.state_changed = signals.state_changed
    conn.frame_received = signals.frame_received
    conn.fw_version_received = signals.fw_version_received
    conn.heartbeat_received = signals.heartbeat_received
    conn.send_frame = MagicMock(return_value=True)
    conn.stop = MagicMock()
    conn.start = MagicMock()
    return conn


# ============================================================================
#  Tymczasowy HOME - dla testów ProfileManager które piszą do ~/.config
# ============================================================================
@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    """Tymczasowy HOME by nie nadpisać prawdziwych profilów usera.

    Na Windows ``Path.home()`` (ntpath.expanduser) czyta ``USERPROFILE`` —
    samo ``HOME`` nie wystarcza i testy profili lekkomyślnie tworzyły
    pliki w prawdziwym ``~/.config/simple-deck/`` (kolizje między runami
    → fałszywe FAIL-e). Ustawiamy oba + HOMEDRIVE/HOMEPATH dla pewności.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOMEDRIVE", str(tmp_path.anchor))
    monkeypatch.setenv("HOMEPATH", str(tmp_path).replace(tmp_path.anchor, ""))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / ".local" / "share"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    return tmp_path


# ============================================================================
#  Aktywny profil z zawężonym typem (Profile, nie Profile | None).
#  ProfileManager.active/load/create deklarują Optional[Profile]; testy
#  używają tego fixture zamiast bezpośrednich odwołań by uniknąć
#  reportOptionalMemberAccess/reportArgumentType z Pylance.
# ============================================================================
@pytest.fixture
def profile(qapp, tmp_home):
    """Zwraca aktywny profil po ensure_default() — typowo Profile, nie None."""
    from simple_deck.core.profile_manager import ProfileManager

    mgr = ProfileManager()
    mgr.ensure_default()
    active = mgr.active
    assert active is not None, "ensure_default() musi zostawić aktywny profil"
    return mgr, active
