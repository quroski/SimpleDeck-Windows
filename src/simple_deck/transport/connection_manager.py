"""Connection Manager - automatyczne łączenie i reconnect.

Zestawia FSM (DISCONNECTED → CONNECTING → CONNECTED → RECONNECTING)
utrzymując aplikację odporną na odłączenie kabla USB lub utratę
pakietów heartbeat. UI NIE BLOKUJE SIĘ na żadnej z operacji.

Główne założenia:
  - HID reader działa w osobnym wątku daemon (HIDDevice).
  - Heartbeat watchdog (QTimer w głównym wątku) wykrywa zanik heartbeatu.
  - QTimer ``_poll_timer`` cyklicznie próbuje połączyć ponownie po rozłączeniu.
  - Wszystkie zmiany stanu emitowane sygnałem ``state_changed`` do UI.

THREAD-SAFETY (krytyczne!):
  - HIDDevice woła callbacki (on_frame/on_disconnect) ze swojego wątku readera.
  - NIE wolno dotykać QTimer / FSM z obcego wątku (Qt wyrzuci warning lub crash).
  - Dlatego callbacki readera robią TYLKO ``emit()`` na prywatnych sygnałach
    ``_frame_from_reader`` / ``_disconnect_from_reader``. Qt automatycznie
    skolejkuje je do wątku ConnectionManagera (głównego) przez QueuedConnection.
  - Cała logika modyfikująca QTimer / stan FSM jest w handlerach tych sygnałów,
    uruchamianych w głównym wątku.
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from .hid_device import HIDDevice, HIDError
from .protocol import Frame, FrameType, make_get_version, parse_heartbeat_payload
from .watchdog import HeartbeatWatchdog

log = logging.getLogger(__name__)


class ConnectionState(Enum):
    """Stany FSM managera połączenia."""
    DISCONNECTED = "disconnected"   # nieaktywny - nie próbujemy łączyć
    CONNECTING = "connecting"       # pierwsza próba łączenia po start()
    CONNECTED = "connected"         # urządzenie otwarte, watchdog działa
    RECONNECTING = "reconnecting"   # po rozłączeniu - cykliczne próby


# Etykiety widoczne w UI dla każdego stanu (z polskim tłumaczeniem)
STATE_LABELS: dict[ConnectionState, str] = {
    ConnectionState.DISCONNECTED: "Nieaktywny",
    ConnectionState.CONNECTING: "Łączenie…",
    ConnectionState.CONNECTED: "Połączony",
    ConnectionState.RECONNECTING: "Ponowne łączenie…",
}


class ConnectionManager(QObject):
    """FSM zarządzający połączeniem z MCU.

    Signals:
        state_changed(ConnectionState): zmiana stanu FSM.
        frame_received(Frame): odebrano poprawną ramkę od MCU.
        fw_version_received(int, int, int): odebrano wersję FW.
        heartbeat_received(int, int): odebrano HEARTBEAT (uptime_ms, fw_packed).
    """

    # ---- Sygnały publiczne (dla UI / EventBus) ----
    state_changed = Signal(object)         # ConnectionState
    frame_received = Signal(object)         # Frame
    fw_version_received = Signal(int, int, int)
    heartbeat_received = Signal(int, int)  # uptime_ms, fw_packed

    # ---- Sygnały prywatne (most reader-thread → main-thread) ----
    # Qt auto-kolejkuje cross-thread przez QueuedConnection (typ argumentu znany).
    # Callbacki z wątku readera robią TYLKO emit() na tych sygnałach.
    _frame_from_reader = Signal(object)    # Frame
    _disconnect_from_reader = Signal()

    # Strojenie
    RECONNECT_INTERVAL_MS = 1000     # początkowy interwał reconnectu
    RECONNECT_MAX_INTERVAL_MS = 30000  # maksymalny interwał (30s)
    HEARTBEAT_TIMEOUT_MS = 4500      # 3 × 1.5 s (MCU wysyła co 1.5 s)

    def __init__(self, vid: Optional[int] = None, pid: Optional[int] = None,
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        self._device = HIDDevice(vid=vid or 0x1209, pid=pid or 0xDE10)

        # Most reader-thread → main-thread: callbacki readera emitują prywatne sygnały,
        # które są auto-kolejkowane do głównego wątku. Tu w głównym wątku
        # podłączamy je do handlerów modyfikujących QTimer i FSM.
        self._frame_from_reader.connect(self._handle_frame_in_main)
        self._disconnect_from_reader.connect(self._handle_disconnect_in_main)
        self._device.set_callbacks(
            on_frame=self._frame_from_reader.emit,
            on_disconnect=self._disconnect_from_reader.emit,
        )

        self._state = ConnectionState.DISCONNECTED

        # Watchdog heartbeatu (główny wątek Qt)
        self._watchdog = HeartbeatWatchdog(timeout_ms=self.HEARTBEAT_TIMEOUT_MS,
                                            parent=self)
        self._watchdog.timeout.connect(self._on_watchdog_timeout)

        # Timer prób łączenia (główny wątek Qt)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(self.RECONNECT_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._try_connect)

        # V8: Exponential backoff dla reconnectu
        self._reconnect_attempts = 0  # licznik prób
        self._current_reconnect_interval = self.RECONNECT_INTERVAL_MS

        # Statystyki
        self._last_heartbeat_uptime = 0
        self._last_connect_at = 0.0

    # ============================================================
    # API publiczne
    # ============================================================
    @property
    def state(self) -> ConnectionState:
        return self._state

    def start(self) -> None:
        """Uruchom managera - pierwsza próba łączenia natychmiast.

        Pojedyncza wejście do FSM. Jeśli pierwsza próba zawiedzie,
        ``_try_connect`` sam wystartuje ``_poll_timer`` (patrz niżej).
        """
        if self._state != ConnectionState.DISCONNECTED:
            return
        self._set_state(ConnectionState.CONNECTING)
        self._try_connect()
        # Zabezpieczenie: jeśli po pierwszej próbie nadal nie connected,
        # wystartuj timer prób łączenia (nie zawiesimy się w CONNECTING).
        if (self._state != ConnectionState.CONNECTED
                and not self._poll_timer.isActive()):
            self._poll_timer.start()

    def stop(self) -> None:
        """Całkowite zatrzymanie managera (np. przy wyjściu z aplikacji)."""
        self._poll_timer.stop()
        self._watchdog.stop()
        self._device.close()
        self._set_state(ConnectionState.DISCONNECTED)

    def send_frame(self, frame: Frame) -> bool:
        """Wyślij ramkę do MCU. Zwraca True jeśli się udało.

        Nigdy nie rzuca wyjątku - błędy są tylko logowane.
        """
        try:
            self._device.write_frame(frame)
            return True
        except HIDError as e:
            log.warning("send_frame failed: %s", e)
            return False
        except (OSError, ValueError) as e:
            # ValueError("not open") z hidapi: brak uprawnień do /dev/hidraw*
            # (udev) lub device martwy. Traktujemy jak zamknięte urządzenie.
            log.warning("send_frame %s (device closed/forbidden?): %s",
                        type(e).__name__, e)
            return False
        except Exception:
            # Ostateczna sieć bezpieczeństwa - send_frame obiecuje nie rzucać.
            log.exception("send_frame unexpected error")
            return False

    # ============================================================
    # FSM helpery (wołane TYLKO z głównego wątku)
    # ============================================================
    def _set_state(self, state: ConnectionState) -> None:
        if self._state == state:
            return
        log.info("connection: %s → %s", self._state.value, state.value)
        self._state = state
        self.state_changed.emit(state)

    def _ensure_reconnecting(self) -> None:
        """Przejdź do RECONNECTING jeśli jeszcze tam nie jesteśmy.

        Wołane z _try_connect gdy device nieobecny lub open() zawiodł.
        Bez tego FSM zawisa w CONNECTING (UI pokazuje „Łączenie…” zamiast
        „Ponowne łączenie…”).
        """
        if self._state not in (ConnectionState.RECONNECTING,
                               ConnectionState.DISCONNECTED):
            self._set_state(ConnectionState.RECONNECTING)

    def _try_connect(self) -> None:
        """Jedna próba otwarcia urządzenia. Sukces → CONNECTED + start watchdog.

        Wejście: wywoływane z głównego wątku (przez ``start()`` lub ``_poll_timer``).
        Jeśli device nieobecny / open() zawiedzie, wystartuje ``_poll_timer``
        (jeśli nie jest jeszcze aktywny) - FSM nigdy nie zawiesi się w CONNECTING.
        """
        if self._device.is_open:
            # Już połączone (np. race między start() a timerem) - zabezpieczenie
            if self._poll_timer.isActive():
                self._poll_timer.stop()
            self._reset_reconnect_backoff()
            return

        if not HIDDevice.is_present(self._device.vid, self._device.pid):
            # Urządzenie nie widoczne - przejdź do RECONNECTING i cyklicznie próbuj
            self._ensure_reconnecting()
            self._apply_reconnect_backoff()
            if not self._poll_timer.isActive():
                self._poll_timer.start()
            return

        try:
            self._device.open()
        except HIDError as e:
            log.info("connect attempt failed: %s", e)
            self._ensure_reconnecting()
            self._apply_reconnect_backoff()
            if not self._poll_timer.isActive():
                self._poll_timer.start()
            return

        # Sukces
        if self._poll_timer.isActive():
            self._poll_timer.stop()
        self._reset_reconnect_backoff()
        self._watchdog.start()
        self._set_state(ConnectionState.CONNECTED)
        # Poproś o wersję FW - PC dostanie ramkę VERSION za chwilę
        self.send_frame(make_get_version())

    def _apply_reconnect_backoff(self) -> None:
        """V8: Zastosuj exponential backoff - zwiększ interwał po każdej nieudanej próbie.
        
        Interwał rośnie wykładniczo: 1s → 2s → 4s → 8s → 16s → 30s (cap).
        """
        self._reconnect_attempts += 1
        # Oblicz nowy interwał: base * 2^(attempts-1), ale nie więcej niż max
        self._current_reconnect_interval = min(
            self.RECONNECT_INTERVAL_MS * (2 ** (self._reconnect_attempts - 1)),
            self.RECONNECT_MAX_INTERVAL_MS
        )
        self._poll_timer.setInterval(self._current_reconnect_interval)
        log.debug("reconnect backoff: attempt %d, interval %dms",
                  self._reconnect_attempts, self._current_reconnect_interval)

    def _reset_reconnect_backoff(self) -> None:
        """V8: Resetuj backoff po udanym połączeniu."""
        if self._reconnect_attempts > 0:
            log.debug("reconnect backoff reset after %d attempts", self._reconnect_attempts)
        self._reconnect_attempts = 0
        self._current_reconnect_interval = self.RECONNECT_INTERVAL_MS
        self._poll_timer.setInterval(self._current_reconnect_interval)

    # ============================================================
    # Handler w głównym wątku - odbiera sygnał z reader thread
    # ============================================================
    @Slot(object)
    def _handle_frame_in_main(self, frame: Frame) -> None:
        """Obsłuż ramkę z readera - logika w głównym wątku (bezpiecznie dla Qt)."""
        # Dystrybuuj do subskrybentów (EventBus etc.)
        self.frame_received.emit(frame)

        if frame.type == FrameType.HEARTBEAT:
            if len(frame.payload) >= 5:
                uptime, fw = parse_heartbeat_payload(frame.payload)
                self._last_heartbeat_uptime = uptime
                self.heartbeat_received.emit(uptime, fw)
            # Reset watchdog timer - main thread, bezpieczne dla QTimer
            self._watchdog.heartbeat()

        elif frame.type == FrameType.VERSION and len(frame.payload) >= 3:
            self.fw_version_received.emit(
                frame.payload[0], frame.payload[1], frame.payload[2]
            )

    @Slot()
    def _handle_disconnect_in_main(self) -> None:
        """Reader zgłosił rozłączenie (USB unplugged / read error).

        Logika w głównym wątku - bezpiecznie dotykamy QTimer.
        Idempotentne: jeśli już jesteśmy w RECONNECTING, nie powtarzamy sekwencji.

        KRYTYCZNE: trzeba wywołać close() by wyczyścić martwy uchwyt. Bez tego
        is_open pozostaje True → _try_connect myśli że już połączone → zatrzymuje
        poll_timer → FSM martwy (żaden timer nie działa).
        """
        if self._state == ConnectionState.RECONNECTING:
            # Już obsłużone (np. watchdog timeout wyprzedził readera)
            return
        log.info("disconnect detected by reader")
        self._watchdog.stop()
        self._device.close()
        self._set_state(ConnectionState.RECONNECTING)
        if not self._poll_timer.isActive():
            self._poll_timer.start()

    # ============================================================
    # Watchdog timeout (już w głównym wątku - QTimer)
    # ============================================================
    @Slot()
    def _on_watchdog_timeout(self) -> None:
        """Brak HEARTBEAT przez HEARTBEAT_TIMEOUT → wymuś reconnect.

        Idempotentne - jeśli reader już zgłosił rozłączenie, nie powtarzamy.
        """
        if self._state == ConnectionState.RECONNECTING:
            return
        log.warning("heartbeat timeout - forcing reconnect")
        self._device.close()
        self._set_state(ConnectionState.RECONNECTING)
        if not self._poll_timer.isActive():
            self._poll_timer.start()
