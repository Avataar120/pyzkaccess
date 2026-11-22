"""Real-time log (RTLog) records.

The binary RTLog command (``0x0B``) returns zero or more 16-byte records:

========  ===========================  =============================
Bytes     Access event                 Door / alarm status (code 255)
========  ===========================  =============================
0-3       card number (LE)             alarm state, one byte per door
4-7       UserID (LE)                  door sensor state, one byte per door
8         verification mode
9         door / reader number
10        event code                   255
11        in/out state
12-15     time (ZK date format)        time (ZK date format)
========  ===========================  =============================
"""
from __future__ import annotations

import time
import warnings
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from typing import Dict, Hashable, List, Optional, Union

from .codec import decode_datetime

RECORD_SIZE = 16
STATUS_EVENT = 255


class EventType(IntEnum):
    """Event codes, as listed in the public PullSDK documentation.

    Devices may report codes that are not listed here; they are kept as plain ints.
    """

    NORMAL_PUNCH_OPEN = 0
    PUNCH_DURING_NORMAL_OPEN_TZ = 1
    FIRST_CARD_NORMAL_OPEN = 2
    MULTI_CARD_OPEN = 3
    EMERGENCY_PASSWORD_OPEN = 4
    OPEN_DURING_NORMAL_OPEN_TZ = 5
    LINKAGE_EVENT_TRIGGERED = 6
    CANCEL_ALARM = 7
    REMOTE_OPENING = 8
    REMOTE_CLOSING = 9
    DISABLE_INTRADAY_NORMAL_OPEN_TZ = 10
    ENABLE_INTRADAY_NORMAL_OPEN_TZ = 11
    OPEN_AUX_OUTPUT = 12
    CLOSE_AUX_OUTPUT = 13
    FINGERPRINT_OPEN = 14
    MULTI_CARD_OPEN_FINGERPRINT = 15
    FINGERPRINT_DURING_NORMAL_OPEN_TZ = 16
    CARD_AND_FINGERPRINT_OPEN = 17
    FIRST_CARD_NORMAL_OPEN_FINGERPRINT = 18
    FIRST_CARD_NORMAL_OPEN_CARD_AND_FINGERPRINT = 19
    TOO_SHORT_PUNCH_INTERVAL = 20
    DOOR_INACTIVE_TZ = 21
    ILLEGAL_TZ = 22
    ACCESS_DENIED = 23
    ANTI_PASSBACK = 24
    INTERLOCK = 25
    MULTI_CARD_AUTHENTICATION = 26
    UNREGISTERED_CARD = 27
    OPENING_TIMEOUT = 28
    CARD_EXPIRED = 29
    PASSWORD_ERROR = 30
    TOO_SHORT_FINGERPRINT_INTERVAL = 31
    MULTI_CARD_AUTHENTICATION_FINGERPRINT = 32
    FINGERPRINT_EXPIRED = 33
    UNREGISTERED_FINGERPRINT = 34
    DOOR_INACTIVE_TZ_FINGERPRINT = 35
    DOOR_INACTIVE_TZ_EXIT_BUTTON = 36
    FAILED_TO_CLOSE_DURING_NORMAL_OPEN_TZ = 37
    VERIFY_TYPE_INVALID = 41
    WIEGAND_FORMAT_ERROR = 42
    DURESS_PASSWORD_OPEN = 101
    OPENED_ACCIDENTALLY = 102
    DURESS_FINGERPRINT_OPEN = 103
    DOOR_OPENED_CORRECTLY = 200
    DOOR_CLOSED_CORRECTLY = 201
    EXIT_BUTTON_OPEN = 202
    MULTI_CARD_OPEN_CARD_AND_FINGERPRINT = 203
    NORMAL_OPEN_TZ_OVER = 204
    REMOTE_NORMAL_OPENING = 205
    DEVICE_START = 206
    DOOR_OPENED_BY_SUPERUSER = 208
    AUX_INPUT_DISCONNECTED = 220
    AUX_INPUT_SHORTED = 221
    DOOR_ALARM_STATUS = 255

    @property
    def label(self) -> str:
        return self.name.replace("_", " ").capitalize().replace(" tz", " time zone")


#: Event codes that mean "someone was granted access" (the door was released).
GRANTED_EVENTS = frozenset({0, 1, 2, 3, 4, 5, 14, 15, 16, 17, 18, 19, 101, 103, 203, 208})
#: Event codes that mean "someone was refused".
DENIED_EVENTS = frozenset({20, 21, 22, 23, 24, 25, 27, 29, 30, 31, 33, 34, 35, 41, 42})


class VerifyMode(IntEnum):
    NONE = 0
    FINGERPRINT = 1
    PASSWORD = 3
    CARD = 4
    CARD_OR_FINGERPRINT = 6
    CARD_AND_FINGERPRINT = 10
    CARD_AND_PASSWORD = 11
    OTHER = 200


class InOutState(IntEnum):
    ENTRY = 0
    NONE = 2
    EXIT = 3


class DoorSensor(IntEnum):
    UNKNOWN = 0
    CLOSED = 1
    OPEN = 2


def _enum(cls, value):
    try:
        return cls(value)
    except ValueError:
        return value


def event_label(code: int) -> str:
    """Human readable name of an event code (``"Event 207"`` when unknown)."""
    try:
        return EventType(code).label
    except ValueError:
        return f"Event {code}"


@dataclass
class AccessEvent:
    """An access event (anything but a status record)."""

    event: Union[EventType, int]
    user_id: int
    card: int
    door: int
    verify_mode: Union[VerifyMode, int]
    in_out: Union[InOutState, int]
    time: Optional[datetime]
    raw: bytes

    @property
    def granted(self) -> bool:
        return int(self.event) in GRANTED_EVENTS

    @property
    def denied(self) -> bool:
        return int(self.event) in DENIED_EVENTS

    @property
    def label(self) -> str:
        return event_label(int(self.event))

    @property
    def pin(self) -> int:
        warnings.warn("'pin' is deprecated, use 'user_id'", DeprecationWarning, stacklevel=2)
        return self.user_id


@dataclass
class DoorStatus:
    """Door and alarm status (event code 255), returned on every poll."""

    alarms: bytes  # one byte per door: bit 0 = alarm, bit 1 = door open timeout
    sensors: List[Union[DoorSensor, int]]  # one entry per door
    time: Optional[datetime]
    raw: bytes

    def door_open(self, door: int = 1) -> Optional[bool]:
        """``True``/``False`` from the door sensor, ``None`` when no sensor is configured."""
        state = self.sensors[door - 1]
        if state == DoorSensor.OPEN:
            return True
        if state == DoorSensor.CLOSED:
            return False
        return None

    def alarm(self, door: int = 1) -> bool:
        return bool(self.alarms[door - 1] & 0x01)

    def open_timeout(self, door: int = 1) -> bool:
        return bool(self.alarms[door - 1] & 0x02)


RTLogRecord = Union[AccessEvent, DoorStatus]


def parse_rtlog(data: bytes) -> List[RTLogRecord]:
    """Decode the answer of the binary RTLog command."""
    if len(data) % RECORD_SIZE:
        raise ValueError(f"RTLog length {len(data)} is not a multiple of {RECORD_SIZE}")
    out: List[RTLogRecord] = []
    for i in range(0, len(data), RECORD_SIZE):
        rec = bytes(data[i:i + RECORD_SIZE])
        when = decode_datetime(int.from_bytes(rec[12:16], "little"))
        if rec[10] == STATUS_EVENT:
            out.append(DoorStatus(rec[0:4], [_enum(DoorSensor, b & 0x0F) for b in rec[4:8]],
                                  when, rec))
        else:
            out.append(AccessEvent(
                event=_enum(EventType, rec[10]),
                user_id=int.from_bytes(rec[4:8], "little"),
                card=int.from_bytes(rec[0:4], "little"),
                door=rec[9],
                verify_mode=_enum(VerifyMode, rec[8]),
                in_out=_enum(InOutState, rec[11]),
                time=when,
                raw=rec,
            ))
    return out


class RepeatDetector:
    """Detects a credential presented again shortly after (a "double badge").

    Feed it every access event; :meth:`check` returns ``True`` when the same
    key (typically the UserID, or the card number for unknown cards) was seen
    less than ``window`` seconds before. A third presentation starts a new
    pair, so a double is reported only once.
    """

    def __init__(self, window: float = 3.0, clock=time.monotonic):
        self.window = window
        self._clock = clock
        self._last: Dict[Hashable, float] = {}

    def check(self, key: Hashable) -> bool:
        now = self._clock()
        last = self._last.get(key)
        if last is not None and now - last <= self.window:
            del self._last[key]
            return True
        self._last[key] = now
        # forget old entries
        for k in [k for k, t in self._last.items() if now - t > self.window]:
            del self._last[k]
        return False
