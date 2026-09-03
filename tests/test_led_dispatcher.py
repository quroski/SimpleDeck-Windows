"""Testy LedDispatcher V2 — wysyła poziom VU bar gdy potencjometr głośności się zmienia."""
from __future__ import annotations



from simple_deck.core.led_dispatcher import LedDispatcher
from simple_deck.core.profile import PotAction, PotConfig, Profile
from simple_deck.transport.protocol import FrameType


def _profile_with_pot(idx: int = 0, action=PotAction.SYSTEM_VOLUME,
                      enabled: bool = True, vu_bar: bool = True) -> Profile:
    p = Profile(name="T")
    p.pots[idx] = PotConfig(idx=idx, action=action, enabled=enabled)
    p.vu_bar_enabled = vu_bar
    return p


class TestVUSending:
    def test_pot_level_sends_vu_frame(self, qapp, bus, mock_connection):
        p = _profile_with_pot(2, action=PotAction.SYSTEM_VOLUME)
        disp = LedDispatcher(bus, mock_connection, parent=qapp)
        disp.set_profile(p)
        bus.pot_level.emit(2, 0.5)
        mock_connection.send_frame.assert_called_once()
        frame = mock_connection.send_frame.call_args[0][0]
        assert frame.type == FrameType.LED_CMD
        assert frame.ch == 2
        assert frame.payload[0] == 0x09   # VU_BAR mode
        assert frame.payload[1] == 127    # ~0.5 * 255

    def test_pot_zero_sends_level_zero(self, qapp, bus, mock_connection):
        p = _profile_with_pot(0)
        disp = LedDispatcher(bus, mock_connection, parent=qapp)
        disp.set_profile(p)
        bus.pot_level.emit(0, 0.0)
        frame = mock_connection.send_frame.call_args[0][0]
        assert frame.payload[1] == 0

    def test_pot_full_sends_level_255(self, qapp, bus, mock_connection):
        p = _profile_with_pot(0)
        disp = LedDispatcher(bus, mock_connection, parent=qapp)
        disp.set_profile(p)
        bus.pot_level.emit(0, 1.0)
        frame = mock_connection.send_frame.call_args[0][0]
        assert frame.payload[1] == 255

    def test_app_volume_also_sends(self, qapp, bus, mock_connection):
        p = _profile_with_pot(1, action=PotAction.APP_VOLUME)
        disp = LedDispatcher(bus, mock_connection, parent=qapp)
        disp.set_profile(p)
        bus.pot_level.emit(1, 0.8)
        frame = mock_connection.send_frame.call_args[0][0]
        assert frame.payload[1] == 204   # 0.8 * 255 = 204


class TestFiltering:
    def test_disabled_pot_ignored(self, qapp, bus, mock_connection):
        p = _profile_with_pot(0, enabled=False)
        disp = LedDispatcher(bus, mock_connection, parent=qapp)
        disp.set_profile(p)
        bus.pot_level.emit(0, 0.5)
        mock_connection.send_frame.assert_not_called()

    def test_none_action_pot_drives_led(self, qapp, bus, mock_connection):
        """V4: potencjometry z action=NONE również sterują linijką VU."""
        p = _profile_with_pot(0, action=PotAction.NONE)
        disp = LedDispatcher(bus, mock_connection, parent=qapp)
        disp.set_profile(p)
        bus.pot_level.emit(0, 0.5)
        mock_connection.send_frame.assert_called_once()
        frame = mock_connection.send_frame.call_args[0][0]
        assert frame.payload[1] == 127

    def test_vu_bar_disabled_ignored(self, qapp, bus, mock_connection):
        p = _profile_with_pot(0, vu_bar=False)
        disp = LedDispatcher(bus, mock_connection, parent=qapp)
        disp.set_profile(p)
        bus.pot_level.emit(0, 0.5)
        mock_connection.send_frame.assert_not_called()

    def test_no_profile_ignored(self, qapp, bus, mock_connection):
        disp = LedDispatcher(bus, mock_connection, parent=qapp)
        # nie ustawiono profilu
        bus.pot_level.emit(0, 0.5)
        mock_connection.send_frame.assert_not_called()


class TestLevelClamping:
    def test_level_above_1_clamped(self, qapp, bus, mock_connection):
        p = _profile_with_pot(0)
        disp = LedDispatcher(bus, mock_connection, parent=qapp)
        disp.set_profile(p)
        bus.pot_level.emit(0, 2.0)
        frame = mock_connection.send_frame.call_args[0][0]
        assert frame.payload[1] == 255

    def test_level_below_0_clamped(self, qapp, bus, mock_connection):
        p = _profile_with_pot(0)
        disp = LedDispatcher(bus, mock_connection, parent=qapp)
        disp.set_profile(p)
        bus.pot_level.emit(0, -0.5)
        frame = mock_connection.send_frame.call_args[0][0]
        assert frame.payload[1] == 0
