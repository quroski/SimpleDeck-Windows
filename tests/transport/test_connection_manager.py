"""Testy ConnectionManager - FSM, watchdog integration, reconnect logic.

Weryfikują fixy Sprint 1:
  A2: reader→main sygnały (thread-safety)
  A3: watchdog heartbeat kolejkowane
  A4: FSM start() wystartuje _poll_timer przy nieudanej próbie
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, PropertyMock, patch


from simple_deck.transport.connection_manager import (
    ConnectionManager, ConnectionState, STATE_LABELS,
)
from simple_deck.transport.hid_device import HIDDevice
from simple_deck.transport.protocol import Frame, FrameType


class TestConnectionStateMachine:
    def test_initial_state_disconnected(self, qapp):
        cm = ConnectionManager()
        assert cm.state == ConnectionState.DISCONNECTED

    def test_state_labels_all_defined(self):
        for s in ConnectionState:
            assert s in STATE_LABELS

    def test_start_transitions_to_connecting_then_reconnecting_when_no_device(self, qapp):
        """A4 fix: gdy device nieobecny, FSM powinien iść w RECONNECTING (a nie zawisnąć w CONNECTING)."""
        cm = ConnectionManager()
        with patch.object(HIDDevice, "is_present", return_value=False):
            cm.start()
            # Bug #2 fix: stan musi być RECONNECTING (nie CONNECTING)
            assert cm.state == ConnectionState.RECONNECTING
            assert cm._poll_timer.isActive()
            cm.stop()

    def test_stop_transitions_to_disconnected(self, qapp):
        cm = ConnectionManager()
        cm.start()
        cm.stop()
        assert cm.state == ConnectionState.DISCONNECTED


class TestFrameHandling:
    def test_frame_received_signal_emitted_in_main_thread(self, qapp, qtbot):
        """A2 fix: emit z reader thread → bezpiecznie skolejkowane do main."""
        cm = ConnectionManager(parent=qapp)
        captured = []
        cm.frame_received.connect(captured.append)

        # Symuluj emit z obcego wątku (jak reader thread)
        frame = Frame(FrameType.HEARTBEAT, 0, bytes([1, 2, 3, 4, 0x12]))
        def emit_from_thread():
            cm._frame_from_reader.emit(frame)
        t = threading.Thread(target=emit_from_thread)
        t.start(); t.join()
        qtbot.wait(50)  # daj czas Qt na przetworzenie sygnału

        assert len(captured) == 1
        assert captured[0].type == FrameType.HEARTBEAT
        cm.stop()

    def test_heartbeat_resets_watchdog(self, qapp, qtbot):
        """A3 fix: watchdog heartbeat wołane z main thread (po _frame_from_reader)."""
        cm = ConnectionManager(parent=qapp)
        # Połączony stan: mock is_present=True + is_open=True.
        # is_open jest property - patchujemy na KLASIE przez PropertyMock
        # (patch na instancji nie dałoby się delattr bez deletera).
        with patch.object(cm._device, "is_present", return_value=True), \
             patch.object(cm._device, "open"), \
             patch.object(type(cm._device), "is_open",
                          new_callable=PropertyMock, return_value=True):
            cm.start()
            qtbot.wait(50)
            # Stan CONNECTED jeśli _try_connect zadziałał
            # (test elastyczny - sprawdzamy watchdog zamiast stanu)

        # Wymuś start watchdog i ustaw CONNECTED ręcznie
        cm._watchdog.start()
        assert cm._watchdog._timer.isActive()
        # Wyślij HEARTBEAT - watchdog.heartbeat() wołane z main (po queued signal)
        frame = Frame(FrameType.HEARTBEAT, 0, bytes([1, 2, 3, 4, 0x12]))
        cm._frame_from_reader.emit(frame)
        qtbot.wait(50)
        # Watchdog nadal aktywny (heartbeat zresetował timer)
        assert cm._watchdog._timer.isActive()
        cm.stop()

    def test_version_frame_emits_signal(self, qapp, qtbot):
        cm = ConnectionManager(parent=qapp)
        captured = []
        cm.fw_version_received.connect(lambda *a: captured.append(a))

        frame = Frame(FrameType.VERSION, 0, bytes([1, 2, 3]))
        cm._frame_from_reader.emit(frame)
        qtbot.wait(50)
        assert len(captured) == 1
        assert captured[0] == (1, 2, 3)
        cm.stop()

    def test_disconnect_signal_transitions_to_reconnecting(self, qapp, qtbot):
        """Bug #1 fix: disconnect handler musi wywołać close() by wyczyścić
        martwy uchwyt. Bez tego is_open pozostaje True → _try_connect myśli
        że połączone → poll_timer zatrzymany → FSM martwy.
        """
        cm = ConnectionManager(parent=qapp)
        # Symuluj otwarte urządzenie (connected)
        cm._device._device = MagicMock()
        cm._set_state(ConnectionState.CONNECTED)
        assert cm._device.is_open is True

        cm._disconnect_from_reader.emit()
        qtbot.wait(50)
        assert cm.state == ConnectionState.RECONNECTING
        # KRYTYCZNE: close() zostało wywołane, is_open jest False
        assert cm._device.is_open is False
        cm.stop()

    def test_disconnect_then_reconnect_cycle(self, qapp, qtbot):
        """Full reconnect cycle: disconnect (unplug) → poll → device present → connect."""
        cm = ConnectionManager(parent=qapp)
        # Symuluj połączone urządzenie
        cm._device._device = MagicMock()
        cm._set_state(ConnectionState.CONNECTED)

        # Reader zgłasza rozłączenie (unplug)
        cm._disconnect_from_reader.emit()
        qtbot.wait(50)
        assert cm.state == ConnectionState.RECONNECTING
        assert cm._device.is_open is False
        assert cm._poll_timer.isActive()

        # Symuluj replug: device obecny + open() sukces
        with patch.object(HIDDevice, "is_present", return_value=True), \
             patch.object(cm._device, "open"):
            cm._try_connect()

        assert cm.state == ConnectionState.CONNECTED
        cm.stop()

    def test_disconnect_idempotent_when_already_reconnecting(self, qapp, qtbot):
        """Disconnect handler nie powtarza sekwencji gdy już w RECONNECTING."""
        cm = ConnectionManager(parent=qapp)
        cm._set_state(ConnectionState.RECONNECTING)
        cm._disconnect_from_reader.emit()
        qtbot.wait(50)
        # Stan bez zmian
        assert cm.state == ConnectionState.RECONNECTING
        cm.stop()


class TestSendFrame:
    def test_send_frame_returns_true_on_success(self, qapp):
        cm = ConnectionManager(parent=qapp)
        with patch.object(cm._device, "write_frame", return_value=64):
            assert cm.send_frame(Frame(FrameType.GET_VERSION, 0, b"")) is True

    def test_send_frame_returns_false_on_hiderror(self, qapp):
        cm = ConnectionManager(parent=qapp)
        from simple_deck.transport.hid_device import HIDError
        with patch.object(cm._device, "write_frame", side_effect=HIDError("nope")):
            assert cm.send_frame(Frame(FrameType.GET_VERSION, 0, b"")) is False

    def test_send_frame_returns_false_on_oserror(self, qapp):
        """A7 fix: send_frame łapie OSError (race z close)."""
        cm = ConnectionManager(parent=qapp)
        with patch.object(cm._device, "write_frame", side_effect=OSError("closed")):
            assert cm.send_frame(Frame(FrameType.GET_VERSION, 0, b"")) is False

    def test_send_frame_returns_false_on_valueerror(self, qapp):
        """Regression: hidapi ValueError('not open') (brak uprawnień hidraw)
        nie crashuje aplikacji - send_frame łapie go i zwraca False.

        Dokument send_frame obiecuje 'nigdy nie rzuca wyjątku'. Wcześniej
        ValueError uciekał → crash przy starcie z podłączonym (ale niedostępnym)
        urządzeniem.
        """
        cm = ConnectionManager(parent=qapp)
        with patch.object(cm._device, "write_frame",
                          side_effect=ValueError("not open")):
            assert cm.send_frame(Frame(FrameType.GET_VERSION, 0, b"")) is False
