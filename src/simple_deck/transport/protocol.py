"""Simple Deck - lustro protokołu binarnego firmware'u STM32.

Implementuje te same ramki co ``firmware/include/protocol.h``, tak aby Python
i MCU mówiły tym samym językiem.

Format ramki (po odjęciu prefiksu Report ID 0x00 przez host stack):

    +------+------+------+-----+---------------------+--------+--------+
    | SOF  | TYPE | CH   | LEN | PAYLOAD[0..LEN-1]   | CRC_LO | CRC_HI |
    | 0xA5 | 1 B  | 1 B  | 1 B | do MAX_PAYLOAD      | 1 B    | 1 B    |
    +------+------+------+-----+---------------------+--------+--------+
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Optional

# Konstanty USB/HID - identyfikator urządzenia
VID: int = 0x1209   # pid.codes (public)
PID: int = 0xDE10   # GREJEM Stream Deck

# Format ramki
SOF: int = 0xA5
MAX_PAYLOAD: int = 32
REPORT_SIZE: int = 64
# Prefiks Report ID wymagany przez hidapi w hid_write (konwencja "Report 0")
HID_REPORT_ID: int = 0x00


class FrameType(IntEnum):
    HEARTBEAT = 0x01      # MCU→PC: payload = uptime(LE4) + fw_version(1)
    BUTTON_EVT = 0x02     # MCU→PC: payload = state(1) ; CH = numer przycisku
    POT_EVT = 0x03        # MCU→PC: payload = value(LE2) ; CH = numer potencjometru
    LED_CMD = 0x04        # PC→MCU: V3 globalne tryby linijki LED.
                          #   VU_BAR (9): plen=2 [mode, level], CH=kanał.
                          #   SOLID..BUTTONS (10..15): plen≥2 [mode, bright,
                          #     speed_lo, speed_hi, arg].
                          #   MANUAL (16): plen≥2 [mode, b0..bN].
                          #   Legacy (0..7) → NAK ERR_BAD_TYPE.
    CFG_CMD = 0x05        # PC→MCU: payload = [deadband, slow, fast, thr]
    ACK = 0x10            # payload = acked type
    NAK = 0x11            # payload = err_code
    GET_VERSION = 0x12    # PC→MCU
    VERSION = 0x13        # MCU→PC: payload = major, minor, patch
    ERROR = 0xFF


class ErrorCode(IntEnum):
    OK = 0x00
    BAD_CRC = 0x09
    BAD_FRAME = 0x0A
    BAD_TYPE = 0x0C
    BAD_CHANNEL = 0x0D
    OVERFLOW = 0x0E


# Stałe sprzętowe - liczone z board.h firmware'u
POT_COUNT: int = 5
BUTTON_COUNT: int = 4
LED_COUNT: int = 8
ACTIVE_LED_COUNT: int = 3   # V3: fizycznie podłączone LEDy
ADC_RANGE: int = 4096  # 12-bit ADC: 0..4095


def _build_crc16_table() -> list[int]:
    """Buduje 256-elementową tabelę LUT dla CRC16-CCITT (poly 0x1021).

    Generowana raz przy imporcie — ~8× szybsza niż bit-by-bit na hot pathie.
    Output jest matematycznie identyczny z implementacją bit-po-bicie
    (weryfikowane przez TestFirmwareCRCConsistency / TestCRCLUT).
    """
    table = []
    for b in range(256):
        crc = b << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
        table.append(crc)
    return table


_CRC16_TABLE = _build_crc16_table()


def crc16_ccitt(data: bytes) -> int:
    """CRC16-CCITT (poly 0x1021, init 0xFFFF) — wersja z tabelą LUT.

    Identyczny wynik do firmware/src/protocol.c (bit-po-bicie), ale ~8× szybsza.
    """
    crc = 0xFFFF
    for b in data:
        crc = ((crc << 8) ^ _CRC16_TABLE[((crc >> 8) ^ b) & 0xFF]) & 0xFFFF
    return crc


@dataclass(frozen=True)
class Frame:
    """Ramka protokołu po stronie Pythona."""
    type: FrameType
    ch: int
    payload: bytes

    def encode(self) -> bytes:
        """Zwraca 64-bajtową ramkę z paddingiem zerami (gotowa do hid_write bez prefiksu)."""
        if len(self.payload) > MAX_PAYLOAD:
            raise ValueError(f"payload too long: {len(self.payload)} > {MAX_PAYLOAD}")
        body = bytes([self.type.value, self.ch, len(self.payload)]) + self.payload
        crc = crc16_ccitt(body)
        frame = bytes([SOF]) + body + bytes([crc & 0xFF, (crc >> 8) & 0xFF])
        return frame.ljust(REPORT_SIZE, b"\x00")


def decode_frame(report: bytes) -> Optional[Frame]:
    """Parsuje 64-bajtowy raport HID (bez prefiksu Report ID).

    Zwraca ``Frame`` lub ``None`` jeśli raport jest uszkodzony (CRC, długość,
    nieznany SOF). Nigdy nie rzuca wyjątku - parser ma być odporny.
    """
    if len(report) < 6:
        return None
    if report[0] != SOF:
        return None
    type_, ch, plen = report[1], report[2], report[3]
    if plen > MAX_PAYLOAD:
        return None
    if len(report) < 4 + plen + 2:
        return None
    body = report[1:4 + plen]
    crc_recv = report[4 + plen] | (report[5 + plen] << 8)
    if crc16_ccitt(body) != crc_recv:
        return None
    try:
        ftype = FrameType(type_)
    except ValueError:
        return None
    payload = bytes(report[4:4 + plen])
    return Frame(ftype, ch, payload)


# --- Konstruktory ramek (dla wygody) ---
def make_led_cmd(
    idx: int,
    mode: int,
    *,
    brightness: int | None = None,
    period_ms: int | None = None,
    arg: int | None = None,
) -> Frame:
    """Buduje ramkę LED_CMD.

    V2: Używaj ``make_vu_cmd`` dla wskaźnika VU bar (mode=9).
    Ta funkcja jest zachowana dla kompatybilności wstecznej (testy).
    """
    if not 0 <= idx < LED_COUNT:
        raise ValueError(f"LED idx out of range: {idx}")
    payload = bytes([mode & 0xFF])
    if brightness is not None:
        payload += bytes([brightness & 0xFF])
    if period_ms is not None:
        payload += bytes([period_ms & 0xFF, (period_ms >> 8) & 0xFF])
    if arg is not None:
        payload += bytes([arg & 0xFF])
    return Frame(FrameType.LED_CMD, idx, payload)


def make_vu_cmd(ch: int, level: int) -> Frame:
    """V2/V3: Buduje ramkę LED_CMD dla wskaźnika VU bar (mode=9).

    Args:
        ch: aktywny kanał (potencjometr) 0..4.
        level: poziom głośności 0..255 (0= wszystkie zgaszone, 255=pełna linijka).
    """
    if not 0 <= ch < POT_COUNT:
        raise ValueError(f"VU channel out of range: {ch}")
    level = max(0, min(255, int(level)))
    return Frame(FrameType.LED_CMD, ch, bytes([0x09, level]))


# --- V3: Konstruktory nowych trybów linijki LED ---

def make_led_mode_cmd(
    mode: int,
    brightness: int = 255,
    speed_ms: int = 0,
    arg: int = 0,
) -> Frame:
    """V3: Buduje ramkę LED_CMD dla trybu statycznego (SOLID..BUTTONS).

    Payload: [mode, brightness, speed_lo, speed_hi, arg]

    Args:
        mode: LED_MODE_SOLID(10)..LED_MODE_BUTTONS(15).
        brightness: globalna jasność 0..255.
        speed_ms: okres animacji w ms (0 = domyślny firmware'u).
        arg: argument wzorca (np. duty cycle % dla STROBE_BAR).
    """
    brightness = max(0, min(255, int(brightness)))
    speed_ms = max(0, min(65535, int(speed_ms)))
    arg = max(0, min(255, int(arg)))
    return Frame(FrameType.LED_CMD, 0, bytes([
        mode & 0xFF,
        brightness,
        speed_ms & 0xFF,
        (speed_ms >> 8) & 0xFF,
        arg,
    ]))


def make_led_manual_cmd(levels: list[int] | tuple[int, ...]) -> Frame:
    """V3: Buduje ramkę LED_CMD dla trybu ręcznego (MANUAL=16).

    Payload: [16, b0, b1, ..., bN] — jasność per LED (0..255).

    Args:
        levels: lista jasności, max ACTIVE_LED_COUNT elementów.
    """
    clamped = [max(0, min(255, int(lv))) for lv in levels[:ACTIVE_LED_COUNT]]
    return Frame(FrameType.LED_CMD, 0, bytes([0x10]) + bytes(clamped))


def make_get_version() -> Frame:
    return Frame(FrameType.GET_VERSION, 0, b"")


def make_cfg_cmd(deadband: int, alpha_slow: int, alpha_fast: int, send_thr: int) -> Frame:
    return Frame(FrameType.CFG_CMD, 0,
                 bytes([deadband & 0xFF, alpha_slow & 0xFF,
                        alpha_fast & 0xFF, send_thr & 0xFF]))


def make_hid_report(frame: Frame) -> bytes:
    """Zwraca 65-bajtowy bufor z prefixem Report ID 0x00 dla ``hid_write``."""
    return bytes([HID_REPORT_ID]) + frame.encode()


def parse_heartbeat_payload(payload: bytes) -> tuple[int, int]:
    """Rozpakowuje payload HEARTBEAT → (uptime_ms, fw_version_packed)."""
    if len(payload) < 5:
        return (0, 0)
    uptime = payload[0] | (payload[1] << 8) | (payload[2] << 16) | (payload[3] << 24)
    fw = payload[4]
    return (uptime, fw)


def parse_pot_payload(payload: bytes) -> int:
    """Rozpakowuje payload POT_EVT → wartość 12-bit (0..4095)."""
    if len(payload) < 2:
        return 0
    return payload[0] | (payload[1] << 8)
