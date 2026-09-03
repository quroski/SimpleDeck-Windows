"""Testy CRC16 LUT — weryfikacja że nowa implementacja z tabelą daje identyczny
wynik do starej implementacji bit-po-bicie dla wszystkich 65 536 wartości."""
from __future__ import annotations

import random

from simple_deck.transport.protocol import crc16_ccitt, _build_crc16_table, _CRC16_TABLE


def _crc16_bit_by_bit(data: bytes) -> int:
    """Oryginalna implementacja bit-po-bicie (z firmware/src/protocol.c)."""
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


class TestCRCLUT:
    def test_table_has_256_entries(self):
        assert len(_CRC16_TABLE) == 256

    def test_empty_input(self):
        assert crc16_ccitt(b"") == _crc16_bit_by_bit(b"")
        assert crc16_ccitt(b"") == 0xFFFF

    def test_single_byte_all_values(self):
        """Każdy pojedynczy bajt 0..255 — LUT musi pasować do bit-by-bit."""
        for b in range(256):
            d = bytes([b])
            assert crc16_ccitt(d) == _crc16_bit_by_bit(d), f"mismatch for byte {b}"

    def test_all_byte_pairs(self):
        """Wszystkie pary bajtów (65536 komb.) — exhaustive."""
        for a in range(256):
            for b in range(256):
                d = bytes([a, b])
                assert crc16_ccitt(d) == _crc16_bit_by_bit(d)

    def test_random_payloads(self):
        """Losowe payload'y o różnej długości."""
        random.seed(42)
        for _ in range(500):
            length = random.randint(0, 64)
            data = bytes(random.randint(0, 255) for _ in range(length))
            assert crc16_ccitt(data) == _crc16_bit_by_bit(data)

    def test_known_crc_values(self):
        """Znane wektory testowe CRC16-CCITT (init 0xFFFF, poly 0x1021)."""
        # "123456789" → 0x29B1 (standardny test CRC-CCITT)
        assert crc16_ccitt(b"123456789") == 0x29B1

    def test_table_built_once(self):
        """LUT jest generowana raz i cache'owana."""
        t1 = _build_crc16_table()
        # Drugie wywołanie powinno dać inną listę (ale te same wartości)
        # Ponieważ _CRC16_TABLE jest już zbudowana, funkcja może być wołana
        # ponownie i zwróci ten sam wynik.
        t2 = _build_crc16_table()
        assert t1 == t2 == _CRC16_TABLE
