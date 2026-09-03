"""Testy PotDispatcher - mapowanie ADC → głośność + krzywe + throttle."""
from __future__ import annotations

from unittest.mock import MagicMock

from simple_deck.core.pot_dispatcher import PotDispatcher, _apply_curve
from simple_deck.core.profile import PotAction, PotConfig, Profile


def _profile_with_pot(**pot_kw) -> Profile:
    p = Profile(name="T")
    p.pots[0] = PotConfig(idx=0, **pot_kw)
    return p


class TestCurve:
    def test_linear(self):
        assert _apply_curve(0.5, "linear") == 0.5
        assert _apply_curve(0.0, "linear") == 0.0
        assert _apply_curve(1.0, "linear") == 1.0

    def test_log_sqrt(self):
        assert abs(_apply_curve(0.25, "log") - 0.5) < 1e-6   # sqrt(0.25)=0.5
        assert _apply_curve(0.0, "log") == 0.0

    def test_exp_square(self):
        assert abs(_apply_curve(0.5, "exp") - 0.25) < 1e-6   # 0.5^2=0.25

    def test_clamps_outside_range(self):
        assert _apply_curve(2.0, "linear") == 1.0
        assert _apply_curve(-1.0, "linear") == 0.0

    def test_unknown_curve_defaults_linear(self):
        assert _apply_curve(0.3, "bogus") == 0.3


class TestPotDispatcherMapping:
    def test_none_action_does_nothing(self, qapp, bus):
        audio = MagicMock()
        disp = PotDispatcher(bus, audio)
        disp.set_profile(_profile_with_pot(action=PotAction.NONE))
        bus.pot_event.emit(0, 2048)
        audio.set_volume.assert_not_called()

    def test_system_volume_linear(self, qapp, bus):
        audio = MagicMock()
        disp = PotDispatcher(bus, audio)
        disp.set_profile(_profile_with_pot(action=PotAction.SYSTEM_VOLUME,
                                           curve="linear"))
        bus.pot_event.emit(0, 2048)   # ~0.5
        audio.set_volume.assert_called_once()
        vol = audio.set_volume.call_args[0][0]
        assert abs(vol - 0.5) < 1e-2
        # target=None dla system
        assert audio.set_volume.call_args.kwargs.get("target") is None

    def test_app_volume_uses_target(self, qapp, bus):
        audio = MagicMock()
        disp = PotDispatcher(bus, audio)
        disp.set_profile(_profile_with_pot(action=PotAction.APP_VOLUME,
                                           target="spotify"))
        bus.pot_event.emit(0, 4095)
        audio.set_volume.assert_called_once_with(1.0, target="spotify")

    def test_invert(self, qapp, bus):
        audio = MagicMock()
        disp = PotDispatcher(bus, audio)
        disp.set_profile(_profile_with_pot(action=PotAction.SYSTEM_VOLUME,
                                           invert=True))
        bus.pot_event.emit(0, 4095)   # pełna → odwrócone → 0
        vol = audio.set_volume.call_args[0][0]
        assert vol < 0.01

    def test_min_max_range(self, qapp, bus):
        audio = MagicMock()
        disp = PotDispatcher(bus, audio)
        disp.set_profile(_profile_with_pot(action=PotAction.SYSTEM_VOLUME,
                                           min_volume=0.2, max_volume=0.8))
        bus.pot_event.emit(0, 0)       # min → 0.2
        assert abs(audio.set_volume.call_args[0][0] - 0.2) < 1e-2
        bus.pot_event.emit(0, 4095)    # max → 0.8 (może być w throttle)
        disp._flush()                  # wyślij oczekujące natychmiast
        assert abs(audio.set_volume.call_args[0][0] - 0.8) < 1e-2

    def test_log_curve_applied(self, qapp, bus):
        audio = MagicMock()
        disp = PotDispatcher(bus, audio)
        disp.set_profile(_profile_with_pot(action=PotAction.SYSTEM_VOLUME,
                                           curve="log"))
        bus.pot_event.emit(0, 1023)   # norm=0.25 → sqrt → 0.5
        vol = audio.set_volume.call_args[0][0]
        assert abs(vol - 0.5) < 1e-2

    def test_out_of_range_idx_ignored(self, qapp, bus):
        audio = MagicMock()
        disp = PotDispatcher(bus, audio)
        disp.set_profile(_profile_with_pot(action=PotAction.SYSTEM_VOLUME))
        bus.pot_event.emit(99, 2048)   # nieistniejący pot
        audio.set_volume.assert_not_called()

    def test_no_audio_backend_safe(self, qapp, bus):
        disp = PotDispatcher(bus, None)
        disp.set_profile(_profile_with_pot(action=PotAction.SYSTEM_VOLUME))
        bus.pot_event.emit(0, 2048)   # nie crashuje bez backendu


class TestPotLevelDecoupled:
    """V4: pot_level emitowany niezależnie od akcji potencjometru i backendu audio.

    Pozwala to LedDispatcher sterować VU barem nawet dla potów z action=NONE
    oraz gdy backend audio jest niedostępny (np. brak PulseAudio).
    """

    def test_pot_level_emitted_for_none_action(self, qapp, bus):
        """Pot z action=NONE nadal emituje pot_level (dla wskaźnika LED)."""
        pot_level_calls = []
        bus.pot_level.connect(lambda i, v: pot_level_calls.append((i, v)))
        audio = MagicMock()
        disp = PotDispatcher(bus, audio)
        disp.set_profile(_profile_with_pot(action=PotAction.NONE))
        bus.pot_event.emit(0, 2048)
        assert len(pot_level_calls) == 1
        assert pot_level_calls[0][0] == 0
        assert abs(pot_level_calls[0][1] - 0.5) < 1e-2   # 2048/4095 ≈ 0.5

    def test_pot_level_emitted_without_audio_backend(self, qapp, bus):
        """Bez backendu audio pot_level nadal leci (LED bar działa)."""
        pot_level_calls = []
        bus.pot_level.connect(lambda i, v: pot_level_calls.append((i, v)))
        disp = PotDispatcher(bus, None)
        disp.set_profile(_profile_with_pot(action=PotAction.SYSTEM_VOLUME))
        bus.pot_event.emit(0, 4095)
        assert len(pot_level_calls) == 1
        assert pot_level_calls[0][1] > 0.99

    def test_raw_level_respects_global_invert(self, qapp, bus):
        """Dla action=NONE, _raw_level stosuje global_invert (jak _map_volume)."""
        pot_level_calls = []
        bus.pot_level.connect(lambda i, v: pot_level_calls.append((i, v)))
        settings = MagicMock()
        settings.invert_all_pots = True
        disp = PotDispatcher(bus, MagicMock(), settings=settings)
        disp.set_profile(_profile_with_pot(action=PotAction.NONE))
        bus.pot_event.emit(0, 4095)   # pełna → invert → ~0
        assert pot_level_calls[0][1] < 0.01


class TestThrottle:
    def test_first_event_sent_immediately(self, qapp, bus):
        audio = MagicMock()
        disp = PotDispatcher(bus, audio)
        disp.set_profile(_profile_with_pot(action=PotAction.SYSTEM_VOLUME))
        bus.pot_event.emit(0, 1000)
        assert audio.set_volume.call_count == 1


class TestGlobalInvert:
    """Globalne odwrócenie wszystkich potencjometrów (XOR z per-pot invert).

    Scenariusze:
      - settings.invert_all_pots=True, cfg.invert=False → odwróć
      - settings.invert_all_pots=True, cfg.invert=True  → NIE odwróć (XOR)
      - settings.invert_all_pots=False (domyślnie)       → per-pot decyduje
    """

    def test_global_invert_alone(self, qapp, bus):
        audio = MagicMock()
        settings = MagicMock()
        settings.invert_all_pots = True
        disp = PotDispatcher(bus, audio, settings=settings)
        disp.set_profile(_profile_with_pot(action=PotAction.SYSTEM_VOLUME,
                                            invert=False))
        bus.pot_event.emit(0, 4095)   # pełna → globalnie odwrócone → 0
        vol = audio.set_volume.call_args[0][0]
        assert vol < 0.01

    def test_global_and_per_pot_cancel_out(self, qapp, bus):
        audio = MagicMock()
        settings = MagicMock()
        settings.invert_all_pots = True
        disp = PotDispatcher(bus, audio, settings=settings)
        disp.set_profile(_profile_with_pot(action=PotAction.SYSTEM_VOLUME,
                                            invert=True))
        bus.pot_event.emit(0, 4095)   # pełna → global AND per-pot → brak odwrócenia → ~1.0
        vol = audio.set_volume.call_args[0][0]
        assert vol > 0.99

    def test_no_settings_uses_per_pot_only(self, qapp, bus):
        """Bez obiektu Settings, global_invert nie jest stosowany (back-compat)."""
        audio = MagicMock()
        disp = PotDispatcher(bus, audio)   # brak settings
        disp.set_profile(_profile_with_pot(action=PotAction.SYSTEM_VOLUME,
                                            invert=False))
        bus.pot_event.emit(0, 4095)
        vol = audio.set_volume.call_args[0][0]
        assert vol > 0.99   # bez inwersji


class TestPotValueCache:
    """Persistencja wartości potencjometrów do settings.last_pot_values."""

    def test_pot_event_updates_settings_cache(self, qapp, bus):
        """Każde POT_EVT aktualizuje settings.last_pot_values w locie."""
        settings = MagicMock()
        settings.last_pot_values = [-1] * 5
        audio = MagicMock()
        disp = PotDispatcher(bus, audio, settings=settings)
        disp.set_profile(_profile_with_pot(action=PotAction.SYSTEM_VOLUME))
        bus.pot_event.emit(2, 2048)
        assert settings.last_pot_values[2] == 2048

    def test_pot_event_out_of_range_safe(self, qapp, bus):
        """POT_EVT z idx >= 5 nie crashuje."""
        settings = MagicMock()
        settings.last_pot_values = [-1] * 5
        audio = MagicMock()
        disp = PotDispatcher(bus, audio, settings=settings)
        disp.set_profile(_profile_with_pot(action=PotAction.SYSTEM_VOLUME))
        bus.pot_event.emit(10, 1234)   # nieistniejący idx
        # Nie crashuje, cache bez zmian
        assert settings.last_pot_values == [-1, -1, -1, -1, -1]

    def test_cache_updated_even_for_disabled_pot(self, qapp, bus):
        """POT_EVT aktualizuje cache niezależnie czy pot jest przypisany."""
        settings = MagicMock()
        settings.last_pot_values = [-1] * 5
        audio = MagicMock()
        disp = PotDispatcher(bus, audio, settings=settings)
        # Pot z akcją NONE - audio nie wołane ale cache aktualizowany
        disp.set_profile(_profile_with_pot(action=PotAction.NONE))
        bus.pot_event.emit(0, 4095)   # idx=0 = NONE
        assert settings.last_pot_values[0] == 4095
        audio.set_volume.assert_not_called()
