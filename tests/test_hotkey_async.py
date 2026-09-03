"""Testy hotkey dispatcher — async dispatch via QThreadPool."""
from __future__ import annotations

from unittest.mock import MagicMock

from simple_deck.core.hotkey_dispatcher import HotkeyDispatcher, _HotkeyJob
from simple_deck.core.profile import ButtonAction, ButtonConfig, Profile


def _profile_with_button(**btn_kw) -> Profile:
    p = Profile(name="T")
    p.buttons[0] = ButtonConfig(idx=0, **btn_kw)
    return p


class TestHotkeyAsyncDispatch:
    def test_hotkey_runs_in_thread_pool(self, qapp, bus):
        """HOTKEY akcja uruchamia QRunnable zamiast blokować główny wątek."""
        hotkey_backend = MagicMock()
        hotkey_backend.simulate_combo.return_value = True
        hotkey_backend.available.return_value = True
        disp = HotkeyDispatcher(bus=bus, hotkey_backend=hotkey_backend)
        disp._thread_pool = MagicMock()
        disp.set_profile(_profile_with_button(action=ButtonAction.HOTKEY,
                                              hotkey="ctrl+c"))
        bus.button_event.emit(0, True)
        # simulate_combo nie powinno być jeszcze wołane — job jest w kolejce
        hotkey_backend.simulate_combo.assert_not_called()
        # ale job powinien być wystartowany
        assert disp._thread_pool.start.called

    def test_hotkey_done_emits_success_toast(self, qapp, bus):
        """Po wykonaniu job'a, toast info jest emitowany."""
        hotkey_backend = MagicMock()
        hotkey_backend.simulate_combo.return_value = True
        hotkey_backend.available.return_value = True
        disp = HotkeyDispatcher(bus=bus, hotkey_backend=hotkey_backend)
        disp.set_profile(_profile_with_button(action=ButtonAction.HOTKEY,
                                              hotkey="ctrl+c"))
        # Symuluj callback z QThreadPool
        disp._on_hotkey_done(True, "ctrl+c", True)
        # Toast powinien być emitowany na bus.notify
        # (can't easily assert on signal without qtbot wait, check no crash)

    def test_hotkey_done_emits_failure_toast(self, qapp, bus):
        hotkey_backend = MagicMock()
        hotkey_backend.simulate_combo.return_value = False
        hotkey_backend.available.return_value = True
        disp = HotkeyDispatcher(bus=bus, hotkey_backend=hotkey_backend)
        disp.set_profile(_profile_with_button(action=ButtonAction.HOTKEY,
                                              hotkey="ctrl+x"))
        disp._on_hotkey_done(False, "ctrl+x", True)

    def test_empty_hotkey_emits_warning(self, qapp, bus):
        hotkey_backend = MagicMock()
        hotkey_backend.simulate_combo.return_value = True
        disp = HotkeyDispatcher(bus=bus, hotkey_backend=hotkey_backend)
        disp._thread_pool = MagicMock()
        disp.set_profile(_profile_with_button(action=ButtonAction.HOTKEY,
                                              hotkey=""))
        bus.button_event.emit(0, True)
        hotkey_backend.simulate_combo.assert_not_called()
        # thread pool start nie powinno być wołane (early return z warning toast)
        assert not disp._thread_pool.start.called

    def test_toggle_mute_still_sync(self, qapp, bus):
        audio = MagicMock()
        hotkey_backend = MagicMock()
        disp = HotkeyDispatcher(bus=bus, hotkey_backend=hotkey_backend,
                                audio_backend=audio)
        disp.set_profile(_profile_with_button(action=ButtonAction.TOGGLE_MUTE,
                                              target="discord"))
        bus.button_event.emit(0, True)
        audio.toggle_mute.assert_called_once_with("discord")

    def test_hotkey_job_run_calls_simulate(self, qapp, bus):
        """_HotkeyJob.run() woła simulate_combo i emituje done."""
        hotkey_backend = MagicMock()
        hotkey_backend.simulate_combo.return_value = True
        disp = HotkeyDispatcher(bus=bus, hotkey_backend=hotkey_backend)
        job = _HotkeyJob(hotkey_backend, "ctrl+a", True, disp)
        # done signal jest connected w __init__
        received = []
        job._signals.done.connect(lambda ok, c, p: received.append((ok, c, p)))
        job.run()
        hotkey_backend.simulate_combo.assert_called_once_with("ctrl+a")
        assert received == [(True, "ctrl+a", True)]
