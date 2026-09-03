"""Warstwa transportu - HID + protokół binarny + auto-reconnect.

Public API:
    from simple_deck.transport import (
        ConnectionManager, ConnectionState, Frame, FrameType,
        protocol, HIDDevice,
    )
"""
from .connection_manager import ConnectionManager, ConnectionState, STATE_LABELS
from .hid_device import HIDDevice, HIDError
from .protocol import (Frame, FrameType, ErrorCode, VID, PID,
                       make_get_version, make_led_cmd, make_cfg_cmd,
                       decode_frame, crc16_ccitt, parse_pot_payload,
                       parse_heartbeat_payload)
from .watchdog import HeartbeatWatchdog

__all__ = [
    "ConnectionManager", "ConnectionState", "STATE_LABELS",
    "HIDDevice", "HIDError",
    "Frame", "FrameType", "ErrorCode",
    "VID", "PID",
    "make_get_version", "make_led_cmd", "make_cfg_cmd",
    "decode_frame", "crc16_ccitt",
    "parse_pot_payload", "parse_heartbeat_payload",
    "HeartbeatWatchdog",
]
