"""Testy HotkeyDispatcher — weryfikacja logiki on_press/release.

Regresja: użytkownik zgłasza że checkbox „Reaguj przy wciśnięciu" nie zmienia
zachowania. Ten test weryfikuje że dispatcher poprawnie reaguje na press vs
release event w zależności od cfg.on_press.

Scenariusze:
  - on_press=True  + press event   → FIRE
  - on_press=True  + release event → NO FIRE
  - on_press=False + press event   → NO FIRE
  - on_press=False + release event → FIRE
  - Zmiana on_press w locie        → natychmiastowy efekt
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from simple_deck.core.hotkey_dispatcher import HotkeyDispatcher
from simple_deck.core.profile import ButtonAction, ButtonConfig, Profile


def _profile_with_button(idx: int = 0, on_press: bool = True,
                         action=ButtonAction.HOTKEY, hotkey="Ctrl+D") -> Profile:
    p = Profile(name="T")
    p.buttons[idx] = ButtonConfig(idx=idx, action=action, hotkey=hotkey,
                                  on_press=on_press)
    return p


class TestOnPressLogic:
    """Testy logiki cfg.on_press w dispatcherze.

    V6: HOTKEY jest teraz async (QThreadPool). Testy sprawdzają że job jest
    startowany (``_thread_pool.start``) zamiast synchronicznego simulate_combo.
    """

    def test_on_press_true_fires_on_press(self, qapp, bus):
        """on_press=True → akcja odpala przy wciśnięciu (pressed=True)."""
        hotkey = MagicMock()
        disp = HotkeyDispatcher(bus=bus, hotkey_backend=hotkey)
        disp._thread_pool = MagicMock()
        disp.set_profile(_profile_with_button(0, on_press=True))
        bus.button_event.emit(0, True)   # press
        assert disp._thread_pool.start.called

    def test_on_press_true_does_not_fire_on_release(self, qapp, bus):
        """on_press=True → akcja NIE odpala przy puszczeniu."""
        hotkey = MagicMock()
        disp = HotkeyDispatcher(bus=bus, hotkey_backend=hotkey)
        disp._thread_pool = MagicMock()
        disp.set_profile(_profile_with_button(0, on_press=True))
        bus.button_event.emit(0, False)  # release
        hotkey.simulate_combo.assert_not_called()
        assert not disp._thread_pool.start.called

    def test_on_press_false_does_not_fire_on_press(self, qapp, bus):
        """on_press=False → akcja NIE odpala przy wciśnięciu."""
        hotkey = MagicMock()
        disp = HotkeyDispatcher(bus=bus, hotkey_backend=hotkey)
        disp._thread_pool = MagicMock()
        disp.set_profile(_profile_with_button(0, on_press=False))
        bus.button_event.emit(0, True)   # press
        assert not disp._thread_pool.start.called

    def test_on_press_false_fires_on_release(self, qapp, bus):
        """on_press=False → akcja odpala przy puszczeniu (pressed=False)."""
        hotkey = MagicMock()
        disp = HotkeyDispatcher(bus=bus, hotkey_backend=hotkey)
        disp._thread_pool = MagicMock()
        disp.set_profile(_profile_with_button(0, on_press=False))
        bus.button_event.emit(0, False)  # release
        assert disp._thread_pool.start.called


class TestOnPressChangeInPlace:
    """Zmiana on_press w locie (symulacja UI toggle) — natychmiastowy efekt.

    To jest kluczowy test dla bug report'u „checkbox nie zmienia zachowania".
    Dispatcher ma referencję do tego samego obiektu Profile co UI, więc
    mutacja profile.buttons[idx].on_press powinna być natychmiast widoczna.
    """

    def test_toggle_off_after_press(self, qapp, bus):
        """on_press=True → zmień na False → press nie odpala."""
        hotkey = MagicMock()
        profile = _profile_with_button(0, on_press=True)
        disp = HotkeyDispatcher(bus=bus, hotkey_backend=hotkey)
        disp._thread_pool = MagicMock()
        disp.set_profile(profile)

        # Symuluj: user toggla checkbox off
        profile.buttons[0] = ButtonConfig(
            idx=0, action=ButtonAction.HOTKEY, hotkey="Ctrl+D",
            on_press=False)

        bus.button_event.emit(0, True)   # press — NIE powinno odpalić
        hotkey.simulate_combo.assert_not_called()
        assert not disp._thread_pool.start.called

    def test_toggle_on_after_release(self, qapp, bus):
        """on_press=False → zmień na True → release nie odpala, press odpala."""
        hotkey = MagicMock()
        profile = _profile_with_button(0, on_press=False)
        disp = HotkeyDispatcher(bus=bus, hotkey_backend=hotkey)
        disp._thread_pool = MagicMock()
        disp.set_profile(profile)

        profile.buttons[0] = ButtonConfig(
            idx=0, action=ButtonAction.HOTKEY, hotkey="Ctrl+D",
            on_press=True)

        bus.button_event.emit(0, False)  # release — NIE powinno odpalić
        hotkey.simulate_combo.assert_not_called()
        assert not disp._thread_pool.start.called
        bus.button_event.emit(0, True)   # press — POWINNO odpalić
        assert disp._thread_pool.start.called


class TestFullPressEventReleaseSequence:
    """Pełna sekwencja press→release jak z fizycznego przycisku."""

    def test_on_press_true_fires_once_per_click(self, qapp, bus):
        """Klik = press + release. on_press=True → fire raz (na press)."""
        hotkey = MagicMock()
        disp = HotkeyDispatcher(bus=bus, hotkey_backend=hotkey)
        disp._thread_pool = MagicMock()
        disp.set_profile(_profile_with_button(0, on_press=True))
        bus.button_event.emit(0, True)
        bus.button_event.emit(0, False)
        assert disp._thread_pool.start.call_count == 1

    def test_on_press_false_fires_once_per_click(self, qapp, bus):
        """Klik = press + release. on_press=False → fire raz (na release)."""
        hotkey = MagicMock()
        disp = HotkeyDispatcher(bus=bus, hotkey_backend=hotkey)
        disp._thread_pool = MagicMock()
        disp.set_profile(_profile_with_button(0, on_press=False))
        bus.button_event.emit(0, True)
        bus.button_event.emit(0, False)
        assert disp._thread_pool.start.call_count == 1


class TestActionDispatch:
    """Weryfikacja że różne akcje są poprawnie dispatchowane."""

    def test_toggle_mute_on_press(self, qapp, bus):
        audio = MagicMock()
        disp = HotkeyDispatcher(bus=bus, hotkey_backend=MagicMock(),
                                 audio_backend=audio)
        p = Profile(name="T")
        p.buttons[1] = ButtonConfig(
            idx=1, action=ButtonAction.TOGGLE_MUTE, target="discord",
            on_press=True)
        disp.set_profile(p)
        bus.button_event.emit(1, True)
        audio.toggle_mute.assert_called_once_with("discord")

    def test_none_action_no_fire(self, qapp, bus):
        hotkey = MagicMock()
        audio = MagicMock()
        disp = HotkeyDispatcher(bus=bus, hotkey_backend=hotkey,
                                 audio_backend=audio)
        p = Profile(name="T")
        p.buttons[0] = ButtonConfig(
            idx=0, action=ButtonAction.NONE, hotkey="Ctrl+D",
            on_press=True)
        disp.set_profile(p)
        bus.button_event.emit(0, True)
        hotkey.simulate_combo.assert_not_called()
        audio.toggle_mute.assert_not_called()

    def test_hotkey_empty_skipped(self, qapp, bus):
        """HOTKEY z pustym hotkey string → nie crashuje, nie wysyła."""
        hotkey = MagicMock()
        disp = HotkeyDispatcher(bus=bus, hotkey_backend=hotkey)
        p = Profile(name="T")
        p.buttons[0] = ButtonConfig(
            idx=0, action=ButtonAction.HOTKEY, hotkey="",
            on_press=True)
        disp.set_profile(p)
        bus.button_event.emit(0, True)   # nie crashuje
        hotkey.simulate_combo.assert_not_called()

    def test_run_command_uses_target(self, qapp, bus):
        """RUN_COMMAND czyta komendę z cfg.target (nie cfg.hotkey)."""
        disp = HotkeyDispatcher(bus=bus, hotkey_backend=MagicMock())
        p = Profile(name="T")
        p.buttons[0] = ButtonConfig(
            idx=0, action=ButtonAction.RUN_COMMAND,
            target="echo hello", on_press=True)
        disp.set_profile(p)
        with pytest.MonkeyPatch().context() as m:
            mock_popen = MagicMock()
            m.setattr("subprocess.Popen", mock_popen)
            bus.button_event.emit(0, True)
            mock_popen.assert_called_once()
            args = mock_popen.call_args[0]
            assert "echo hello" in args[0]

    def test_run_command_empty_target_skipped(self, qapp, bus):
        """RUN_COMMAND z pustym target → nie uruchamia niczego."""
        disp = HotkeyDispatcher(bus=bus, hotkey_backend=MagicMock())
        p = Profile(name="T")
        p.buttons[0] = ButtonConfig(
            idx=0, action=ButtonAction.RUN_COMMAND,
            target="", on_press=True)
        disp.set_profile(p)
        with pytest.MonkeyPatch().context() as m:
            mock_popen = MagicMock()
            m.setattr("subprocess.Popen", mock_popen)
            bus.button_event.emit(0, True)
            mock_popen.assert_not_called()


class TestHotkeyFailureToast:
    """V4/V6: Gdy simulate_combo zawodzi (async), dispatcher emituje toast warning.

    V6: simulate_combo jest teraz wołane asynchronicznie przez QThreadPool.
    Testy wywołują ``_on_hotkey_done`` bezpośrednio by sprawdzić toast logikę.
    """

    def test_hotkey_backend_failure_emits_warning(self, qapp, bus):
        """simulate_combo → False → toast warning emitowany (via _on_hotkey_done)."""
        notify_calls = []
        bus.notify.connect(lambda lvl, msg: notify_calls.append((lvl, msg)))
        hotkey = MagicMock()
        hotkey.simulate_combo.return_value = False
        disp = HotkeyDispatcher(bus=bus, hotkey_backend=hotkey)
        disp.set_profile(_profile_with_button(0, hotkey="Ctrl+D"))
        # Symuluj callback z QThreadPool
        disp._on_hotkey_done(False, "Ctrl+D", True)
        assert any(lvl == "warning" and "wtype" in msg
                   for lvl, msg in notify_calls), notify_calls

    def test_hotkey_success_emits_info(self, qapp, bus):
        """simulate_combo → True → toast info (via _on_hotkey_done)."""
        notify_calls = []
        bus.notify.connect(lambda lvl, msg: notify_calls.append((lvl, msg)))
        hotkey = MagicMock()
        hotkey.simulate_combo.return_value = True
        disp = HotkeyDispatcher(bus=bus, hotkey_backend=hotkey)
        disp.set_profile(_profile_with_button(0, hotkey="Ctrl+D"))
        disp._on_hotkey_done(True, "Ctrl+D", True)
        assert any(lvl == "info" for lvl, msg in notify_calls), notify_calls

    def test_empty_hotkey_emits_warning_without_calling_backend(self, qapp, bus):
        """Pusty hotkey → toast warning, simulate_combo NIE wołany."""
        notify_calls = []
        bus.notify.connect(lambda lvl, msg: notify_calls.append((lvl, msg)))
        hotkey = MagicMock()
        disp = HotkeyDispatcher(bus=bus, hotkey_backend=hotkey)
        disp._thread_pool = MagicMock()
        p = Profile(name="T")
        p.buttons[0] = ButtonConfig(
            idx=0, action=ButtonAction.HOTKEY, hotkey="", on_press=True)
        disp.set_profile(p)
        bus.button_event.emit(0, True)
        hotkey.simulate_combo.assert_not_called()
        assert not disp._thread_pool.start.called
        assert any(lvl == "warning"
                   for lvl, msg in notify_calls), notify_calls
