"""Testy HIDDevice - reader/writer z mockiem hid."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from simple_deck.transport.hid_device import HIDDevice, HIDError
from simple_deck.transport.protocol import Frame, FrameType, make_led_cmd


class TestHIDDeviceLifecycle:
    def test_is_open_false_initially(self):
        d = HIDDevice()
        assert d.is_open is False

    def test_open_succeeds_with_mock(self):
        d = HIDDevice()
        with patch("simple_deck.transport.hid_device.hid") as mock_hid:
            mock_dev = MagicMock()
            mock_dev.get_manufacturer_string.return_value = "Test"
            mock_dev.get_product_string.return_value = "TestProduct"
            mock_hid.device.return_value = mock_dev
            d.open()
            assert d.is_open is True
            d.close()

    def test_open_raises_on_oserror(self):
        d = HIDDevice()
        with patch("simple_deck.transport.hid_device.hid") as mock_hid:
            mock_hid.device.side_effect = OSError("not found")
            with pytest.raises(HIDError):
                d.open()

    def test_close_idempotent(self):
        d = HIDDevice()
        # Wielokrotne close bez open nie crashuje
        d.close()
        d.close()

    def test_is_present_true_when_enumerate_returns_list(self):
        with patch("simple_deck.transport.hid_device.hid") as mock_hid:
            mock_hid.enumerate.return_value = [{"path": b"x"}]
            assert HIDDevice.is_present(0x1209, 0xDE10) is True

    def test_is_present_false_when_enumerate_empty(self):
        with patch("simple_deck.transport.hid_device.hid") as mock_hid:
            mock_hid.enumerate.return_value = []
            assert HIDDevice.is_present() is False

    def test_is_present_false_on_exception(self):
        with patch("simple_deck.transport.hid_device.hid") as mock_hid:
            mock_hid.enumerate.side_effect = Exception("no USB")
            assert HIDDevice.is_present() is False


class TestHIDDeviceWrite:
    def test_write_frame_raises_when_not_open(self):
        d = HIDDevice()
        with pytest.raises(HIDError):
            d.write_frame(make_led_cmd(0, 1))

    def test_write_frame_catches_oserror(self):
        """B7 fix: write_frame łapie OSError i zamienia na HIDError."""
        d = HIDDevice()
        # Wstrzyknij mock device
        mock_dev = MagicMock()
        mock_dev.write.side_effect = OSError("device gone")
        d._device = mock_dev
        with pytest.raises(HIDError):
            d.write_frame(make_led_cmd(0, 1))

    def test_write_frame_success_returns_bytes(self):
        d = HIDDevice()
        mock_dev = MagicMock()
        mock_dev.write.return_value = 65  # 1 byte Report ID + 64 payload
        d._device = mock_dev
        n = d.write_frame(make_led_cmd(0, 1))
        assert n == 65

    def test_write_frame_negative_return_raises(self):
        d = HIDDevice()
        mock_dev = MagicMock()
        mock_dev.write.return_value = -1
        d._device = mock_dev
        with pytest.raises(HIDError):
            d.write_frame(make_led_cmd(0, 1))

    def test_write_frame_catches_valueerror(self):
        """hidapi ValueError('not open') (brak uprawnień /dev/hidraw) → HIDError.

        Regression: wcześniej ValueError uciekał z write_frame → crash aplikacji
        przy starcie gdy urządzenie podłączone ale bez praw dostępu.
        """
        d = HIDDevice()
        mock_dev = MagicMock()
        mock_dev.write.side_effect = ValueError("not open")
        d._device = mock_dev
        with pytest.raises(HIDError):   # NIE ValueError
            d.write_frame(make_led_cmd(0, 1))


class TestHIDDeviceReader:
    def test_reader_callback_on_frame(self):
        """Reader woła on_frame callback gdy dostanie poprawną ramkę."""
        d = HIDDevice()
        frames_received = []
        # Po pierwszej ramce ustaw stop - pętla while-not-stop wykona dokładnie
        # jedną iterację (read→decode→callback→set stop→exit na nast. sprawdzeniu).
        def cb(frame):
            frames_received.append(frame)
            d._stop_event.set()
        d.set_callbacks(on_frame=cb)
        # Symuluj poprawną ramkę w buforze
        encoded = Frame(FrameType.HEARTBEAT, 0,
                        bytes([0x78, 0x56, 0x34, 0x12, 0x12])).encode()
        mock_dev = MagicMock()
        mock_dev.read.return_value = list(encoded)
        d._device = mock_dev
        d._reader_loop()
        assert len(frames_received) == 1
        assert frames_received[0].type == FrameType.HEARTBEAT

    def test_reader_callback_on_disconnect_on_oserror(self):
        """Reader woła on_disconnect gdy dostanie OSError."""
        d = HIDDevice()
        disconnect_count = [0]
        d.set_callbacks(on_disconnect=lambda: disconnect_count.__setitem__(0, 1))
        mock_dev = MagicMock()
        mock_dev.read.side_effect = OSError("USB unplugged")
        d._device = mock_dev
        d._reader_loop()
        assert disconnect_count[0] == 1

    def test_reader_disconnect_on_valueerror(self):
        """Reader: ValueError('not open') traktowane jako disconnect, nie retry.

        Regression: wcześniej ValueError wpadał w broad-Exception → reader
        spinał się logując co 50 ms zamiast się rozłączyć.
        """
        d = HIDDevice()
        disconnected = []
        d.set_callbacks(on_disconnect=lambda: disconnected.append(1))
        mock_dev = MagicMock()
        mock_dev.read.side_effect = ValueError("not open")
        d._device = mock_dev
        d._reader_loop()   # bez pre-set stop; ValueError → disconnect → break
        assert len(disconnected) == 1
