"""Typed views over the most common device tables.

The generic API (``client.read_table`` / ``write_records``) works with plain
dicts keyed by the device's own field names. These dataclasses are a thin,
friendlier layer for the ``user``, ``templatev10`` and ``userauthorize`` tables.
Field names that a given firmware does not expose are simply omitted.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, fields
from typing import Any, ClassVar, Dict, Optional

Row = Dict[str, Any]


class _Model:
    TABLE: ClassVar[str]
    #: python attribute -> device field name
    FIELDS: ClassVar[Dict[str, str]]

    @classmethod
    def from_row(cls, row: Row):
        kwargs = {attr: row[name] for attr, name in cls.FIELDS.items() if name in row}
        return cls(**kwargs)  # type: ignore[call-arg]

    def to_row(self, include_none: bool = False) -> Row:
        out: Row = {}
        for attr, name in self.FIELDS.items():
            value = getattr(self, attr)
            if value is not None or include_none:
                out[name] = value
        return out


@dataclass
class User(_Model):
    """A record of the ``user`` table."""

    TABLE: ClassVar[str] = "user"
    FIELDS: ClassVar[Dict[str, str]] = {
        "pin": "Pin", "name": "Name", "card_no": "CardNo", "password": "Password",
        "group": "Group", "start_time": "StartTime", "end_time": "EndTime",
        "privilege": "Privilege", "super_authorize": "SuperAuthorize", "uid": "UID",
    }

    pin: int
    name: Optional[str] = None
    card_no: Optional[int] = None
    password: Optional[str] = None
    group: Optional[int] = None
    start_time: Optional[int] = None
    end_time: Optional[int] = None
    privilege: Optional[int] = None
    super_authorize: Optional[int] = None
    #: Internal id, assigned by the device; it cannot be chosen by the client.
    uid: Optional[int] = None


@dataclass
class Template(_Model):
    """A fingerprint template (``templatev10`` table, ZKFinger v10 format)."""

    TABLE: ClassVar[str] = "templatev10"
    FIELDS: ClassVar[Dict[str, str]] = {
        "pin": "Pin", "finger_id": "FingerID", "template": "Template", "size": "Size",
        "valid": "Valid", "uid": "UID", "reserved": "Resverd", "end_tag": "EndTag",
    }

    pin: int
    finger_id: int = 0
    template: bytes = b""
    size: Optional[int] = None
    valid: Optional[int] = 1
    uid: Optional[int] = None
    reserved: Optional[int] = None
    end_tag: Optional[int] = None

    def __post_init__(self):
        if isinstance(self.template, str):
            self.template = base64.b64decode(self.template)
        if self.size is None:
            self.size = len(self.template)

    def __repr__(self) -> str:  # do not dump biometric data in logs
        return (f"Template(pin={self.pin}, finger_id={self.finger_id}, size={self.size}, "
                f"valid={self.valid}, uid={self.uid})")


@dataclass
class Authorization(_Model):
    """A record of the ``userauthorize`` table: which doors a user may open, and when.

    ``door_mask`` is a bit mask on multi-door controllers (door 1 = 1, door 2 = 2,
    door 3 = 4, door 4 = 8); single-door readers use 1.
    """

    TABLE: ClassVar[str] = "userauthorize"
    FIELDS: ClassVar[Dict[str, str]] = {
        "pin": "Pin", "timezone_id": "AuthorizeTimezoneId", "door_mask": "AuthorizeDoorId",
    }

    pin: int
    timezone_id: int = 1
    door_mask: int = 1


def model_fields(model) -> list:
    return [f.name for f in fields(model)]
