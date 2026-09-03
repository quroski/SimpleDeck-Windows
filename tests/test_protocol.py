"""Testy protokołu binarnego - parowanie z firmware'owym protocol.c.

Weryfikują że implementacja CRC16-CCITT oraz ramkowania w Pythonie daje
identyczne wyniki jak ta w C.
"""
from __future__ import annotations

import pytest

from simple_deck.transport import protocol as P


class TestCRC16CCITT:
    """CRC16-CCITT-FALSE (poly 0x1021, init 0xFFFF, no reflection).

    Znane wektory testowe:
      crc(b"123456789") = 0x29B1   (CCITT-FALSE)
      crc(b"")           = 0xFFFF  (init value, no bytes processed)
      crc(b"A")          = 0xB915  (ręcznie wyliczone powyżej)
    """

    def test_known_vector_123456789(self):
        # Najbardziej standardowy wektor testowy CRC16-CCITT-FALSE
        assert P.crc16_ccitt(b"123456789") == 0x29B1

    def test_empty_input_returns_init(self):
        assert P.crc16_ccitt(b"") == 0xFFFF

    def test_single_byte_a(self):
        # Ręcznie wyliczone: crc("A"=0x41) = 0xB915 dla CCITT-FALSE
        assert P.crc16_ccitt(b"A") == 0xB915


class TestFrameRoundTrip:
    """Sprawdza że encode/decode są odwrotne."""

    @pytest.mark.parametrize("frame_type,ch,payload", [
        (P.FrameType.HEARTBEAT, 0, bytes([0x78, 0x56, 0x34, 0x12, 0x12])),
        (P.FrameType.BUTTON_EVT, 2, bytes([0x01])),
        (P.FrameType.POT_EVT,    4, bytes([0xFF, 0x0F])),   # 0x0FFF = 4095
        (P.FrameType.GET_VERSION, 0, b""),
        (P.FrameType.LED_CMD,    1, bytes([0x02])),
    ])
    def test_round_trip(self, frame_type, ch, payload):
        original = P.Frame(frame_type, ch, payload)
        encoded = original.encode()
        # Długość = REPORT_SIZE
        assert len(encoded) == P.REPORT_SIZE
        # Pierwszy bajt = SOF
        assert encoded[0] == P.SOF
        decoded = P.decode_frame(encoded)
        assert decoded is not None
        assert decoded.type == frame_type
        assert decoded.ch == ch
        assert decoded.payload == payload

    def test_decode_corrupted_crc_returns_none(self):
        f = P.Frame(P.FrameType.HEARTBEAT, 0, bytes([1, 2, 3, 4, 5]))
        encoded = bytearray(f.encode())
        # Psujemy pierwszy bajt CRC (pozycja = 4 + plen = 9)
        encoded[9] ^= 0xFF
        assert P.decode_frame(bytes(encoded)) is None

    def test_decode_corrupted_payload_returns_none(self):
        f = P.Frame(P.FrameType.HEARTBEAT, 0, bytes([1, 2, 3, 4, 5]))
        encoded = bytearray(f.encode())
        # Psujemy payload - CRC się nie zgodzi
        encoded[5] ^= 0xFF
        assert P.decode_frame(bytes(encoded)) is None

    def test_decode_truncated_returns_none(self):
        assert P.decode_frame(b"") is None
        assert P.decode_frame(b"\xA5") is None
        assert P.decode_frame(b"\xA5\x01\x00\x00") is None

    def test_decode_wrong_sof_returns_none(self):
        assert P.decode_frame(b"\x00" * 64) is None
        assert P.decode_frame(b"\xFF" * 64) is None

    def test_decode_too_long_payload_returns_none(self):
        # LEN > MAX_PAYLOAD
        bad = bytes([P.SOF, 0x01, 0x00, 0x40]) + b"\x00" * 60
        assert P.decode_frame(bad) is None


class TestPotPayload:
    """Rozpakowywanie wartości ADC."""

    @pytest.mark.parametrize("raw,expected", [
        (bytes([0x00, 0x00]), 0),
        (bytes([0xFF, 0x0F]), 0x0FFF),    # 4095 = max 12-bit
        (bytes([0x80, 0x00]), 0x0080),    # 128
    ])
    def test_parse(self, raw, expected):
        assert P.parse_pot_payload(raw) == expected

    def test_parse_empty(self):
        assert P.parse_pot_payload(b"") == 0


class TestHeartbeatPayload:
    def test_parse_uptime(self):
        # uptime = 0x12345678 ms, fw = 0x12 (1.2.x)
        pld = bytes([0x78, 0x56, 0x34, 0x12, 0x12])
        uptime, fw = P.parse_heartbeat_payload(pld)
        assert uptime == 0x12345678
        assert fw == 0x12

    def test_parse_truncated(self):
        assert P.parse_heartbeat_payload(b"\x01\x02") == (0, 0)


class TestLedCmdBuilder:
    def test_make_led_cmd(self):
        f = P.make_led_cmd(2, 1)  # LED 2 ON
        assert f.type == P.FrameType.LED_CMD
        assert f.ch == 2
        assert f.payload == bytes([1])

    def test_make_led_cmd_out_of_range(self):
        with pytest.raises(ValueError):
            P.make_led_cmd(99, 0)

    def test_make_led_cmd_legacy_no_kwargs(self):
        """Bez argumentów keyword → legacy plen=1."""
        f = P.make_led_cmd(0, 2)  # BLINK
        assert f.payload == bytes([2])

    def test_make_led_cmd_with_brightness(self):
        """plen=2: [mode, brightness]."""
        f = P.make_led_cmd(1, 3, brightness=128)  # DIM 50%
        assert f.payload == bytes([3, 128])

    def test_make_led_cmd_with_period(self):
        """plen=4: [mode, brightness, period_lo, period_hi]."""
        f = P.make_led_cmd(0, 5, brightness=200, period_ms=3000)  # BREATHE
        assert f.payload == bytes([5, 200, 0xB8, 0x0B])

    def test_make_led_cmd_with_arg(self):
        """plen=5: [mode, brightness, period_lo, period_hi, arg]."""
        f = P.make_led_cmd(3, 6, brightness=255, period_ms=100, arg=30)  # STROBE
        assert f.payload == bytes([6, 255, 100, 0, 30])

    def test_make_led_cmd_round_trip_extended(self):
        """Pełna ramka z rozszerzonym payloadem przeżywa encode→decode."""
        f = P.make_led_cmd(2, 4, brightness=150, period_ms=1000, arg=0)
        encoded = f.encode()
        decoded = P.decode_frame(encoded)
        assert decoded is not None
        assert decoded.type == P.FrameType.LED_CMD
        assert decoded.ch == 2
        assert decoded.payload == bytes([4, 150, 0xE8, 0x03, 0])


class TestVuCmdBuilder:
    """V2: builder ramki VU bar (mode=9)."""

    def test_make_vu_cmd_basic(self):
        f = P.make_vu_cmd(2, 128)
        assert f.type == P.FrameType.LED_CMD
        assert f.ch == 2
        assert f.payload == bytes([0x09, 128])

    def test_make_vu_cmd_zero(self):
        f = P.make_vu_cmd(0, 0)
        assert f.payload == bytes([0x09, 0])

    def test_make_vu_cmd_full(self):
        f = P.make_vu_cmd(4, 255)
        assert f.payload == bytes([0x09, 0xFF])

    def test_make_vu_cmd_level_clamped(self):
        f = P.make_vu_cmd(0, 999)
        assert f.payload[1] == 255
        f2 = P.make_vu_cmd(0, -50)
        assert f2.payload[1] == 0

    def test_make_vu_cmd_bad_channel(self):
        with pytest.raises(ValueError):
            P.make_vu_cmd(99, 0)

    def test_make_vu_cmd_round_trip(self):
        f = P.make_vu_cmd(1, 200)
        decoded = P.decode_frame(f.encode())
        assert decoded is not None
        assert decoded.type == P.FrameType.LED_CMD
        assert decoded.ch == 1
        assert decoded.payload == bytes([0x09, 200])


class TestFirmwareCRCConsistency:
    """Cross-check: upewnij się że CRC zgadza się z ręcznie wyliczonym.

    Firmware w C liczy:
      crc16_ccitt_update(0xFFFF, byte) ...
    Tutaj symulujemy ten sam algorytm krok po kroku.
    """
    def test_matches_manual_computation(self):
        # Dane testowe
        data = bytes([0x01, 0x00, 0x02, 0xAB, 0xCD])
        # Ręczna implementacja iteracyjna (bez tablicy LUT) - identyczna jak C
        crc = 0xFFFF
        for b in data:
            crc ^= b << 8
            for _ in range(8):
                if crc & 0x8000:
                    crc = ((crc << 1) ^ 0x1021) & 0xFFFF
                else:
                    crc = (crc << 1) & 0xFFFF
        assert crc == P.crc16_ccitt(data)
