"""Low-level framing of the ZKTeco PullSDK ("C3") protocol.

Every request and every reply uses the same envelope::

    AA | ver | cmd | len_lo | len_hi | data[len] | crc_lo | crc_hi | 55

* ``ver`` is ``0x01`` in requests and ``0x00`` in replies.
* ``len`` is the little-endian length of ``data``.
* ``crc`` is a CRC-16/ARC computed over ``ver .. data`` (start byte excluded).
* Once a session is open, ``data`` starts with four bytes:
  ``session_lo session_hi request_lo request_hi``; the device echoes them back.

See ``docs/PROTOCOL.md`` for the full description.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from .errors import ProtocolError

START = 0xAA
END = 0x55
REQUEST_VERSION = 0x01

DEFAULT_PORT = 4370
DISCOVERY_PORT = 65535
DISCOVERY_PAYLOAD = b"CallSecurityDevice"

#: Session id and request number sent in the very first (connect) frame.
INITIAL_SESSION = 0xFEFE


class Command(IntEnum):
    """Request command codes."""

    CONNECT_SESSIONLESS = 0x01
    DISCONNECT = 0x02
    SET_PARAM = 0x03
    GET_PARAM = 0x04
    CONTROL = 0x05
    TABLE_CONFIG = 0x06
    SET_DATA = 0x07
    GET_DATA = 0x08
    DELETE_DATA = 0x09
    COUNT_DATA = 0x0A
    RTLOG_BINARY = 0x0B
    BUFFER_READ = 0x0E
    BUFFER_FREE = 0x0F
    DISCOVER = 0x14
    CONNECT = 0x76
    RTLOG_KEYVALUE = 0x79


class Reply(IntEnum):
    """Reply command codes."""

    OK = 0xC8
    ERROR = 0xC9
    #: The answer is too large and was put in a device-side buffer that must be
    #: fetched with :attr:`Command.BUFFER_READ` (see ``client.read_table``).
    BUFFERED = 0x0D


#: Error codes carried in the last byte of an ``ERROR`` reply (signed byte).
DEVICE_ERRORS = {
    -13: "command not supported by this device",
    -14: "wrong communication password",
}


def crc16(data: bytes) -> int:
    """CRC-16/ARC (reflected polynomial 0xA001, initial value 0)."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def build_frame(command: int, data: bytes = b"", session: int | None = None,
                request: int | None = None) -> bytes:
    """Build a complete request frame.

    ``session`` and ``request`` are prepended to ``data`` when ``session`` is set.
    """
    if session is not None:
        data = session.to_bytes(2, "little") + (request or 0).to_bytes(2, "little") + data
    if len(data) > 0xFFFF:
        raise ValueError(f"payload too large for one frame ({len(data)} bytes)")
    body = bytes([REQUEST_VERSION, command]) + len(data).to_bytes(2, "little") + data
    return bytes([START]) + body + crc16(body).to_bytes(2, "little") + bytes([END])


@dataclass
class Frame:
    """A decoded reply frame."""

    command: int
    version: int
    data: bytes


def parse_header(header: bytes) -> tuple[int, int, int]:
    """Decode the first five bytes of a frame: returns ``(command, version, length)``."""
    if len(header) != 5 or header[0] != START:
        raise ProtocolError(f"invalid frame header: {header.hex()}")
    return header[2], header[1], int.from_bytes(header[3:5], "little")


def parse_frame(raw: bytes) -> Frame:
    """Validate and decode a complete frame (header, data, CRC and end marker)."""
    command, version, length = parse_header(raw[:5])
    if len(raw) != length + 8:
        raise ProtocolError(f"frame length mismatch: header says {length}, got {len(raw) - 8}")
    if raw[-1] != END:
        raise ProtocolError("missing end marker")
    expected = crc16(raw[1:-3])
    received = int.from_bytes(raw[-3:-1], "little")
    if expected != received:
        raise ProtocolError(f"bad checksum: received {received:04x}, expected {expected:04x}")
    return Frame(command, version, bytes(raw[5:-3]))
