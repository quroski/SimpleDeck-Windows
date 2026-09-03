"""Testy HeartbeatWatchdog - timeout i reset."""
from __future__ import annotations

from PySide6.QtCore import QTimer

from simple_deck.transport.watchdog import HeartbeatWatchdog


class TestHeartbeatWatchdog:
    def test_initial_state_no_timeout_emitted(self, qapp):
        wd = HeartbeatWatchdog(timeout_ms=100, parent=qapp)
        # Bez start() - brak timeout
        assert not wd._timer.isActive()

    def test_start_activates_timer(self, qapp):
        wd = HeartbeatWatchdog(timeout_ms=100, parent=qapp)
        wd.start()
        assert wd._timer.isActive()
        wd.stop()

    def test_stop_deactivates_timer(self, qapp):
        wd = HeartbeatWatchdog(timeout_ms=100, parent=qapp)
        wd.start()
        wd.stop()
        assert not wd._timer.isActive()

    def test_timeout_signal_emitted(self, qapp, qtbot):
        wd = HeartbeatWatchdog(timeout_ms=50, parent=qapp)
        wd.start()
        with qtbot.waitSignal(wd.timeout, timeout=500) as blocker:
            pass
        assert blocker.signal_triggered

    def test_heartbeat_resets_timer(self, qapp, qtbot):
        """Heartbeat wołane przed timeout resetuje timer - brak przedwczesnego timeout."""
        wd = HeartbeatWatchdog(timeout_ms=200, parent=qapp)
        wd.start()
        # Wywołaj heartbeat po 80ms (przed 200ms timeout)
        QTimer.singleShot(80, wd.heartbeat)
        # Daj czas - watchdog nie powinien wystrzelić przed 200ms
        fired_early = []
        wd.timeout.connect(lambda: fired_early.append(1))
        qtbot.wait(150)
        # Po 150ms watchdog nie wystrzelił (bo heartbeat @80ms go zresetował)
        assert len(fired_early) == 0
        wd.stop()

    def test_heartbeat_when_not_active_is_safe(self, qapp):
        """heartbeat() przed start() - bez crasha (zgodnie z fixem)."""
        wd = HeartbeatWatchdog(timeout_ms=100, parent=qapp)
        wd.heartbeat()  # nie powinno nic zrobić (timer nie aktywny)

    def test_timeout_ms_property(self, qapp):
        wd = HeartbeatWatchdog(timeout_ms=4500, parent=qapp)
        assert wd.timeout_ms == 4500
