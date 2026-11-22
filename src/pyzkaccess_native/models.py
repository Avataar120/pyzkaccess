"""Typed views over the most common device tables.

The generic API (``client.read_table`` / ``write_records``) works with plain
dicts keyed by the device's own field names. These dataclasses are a thin,
friendlier layer for the ``user``, ``templatev10`` and ``userauthorize`` tables.
Field names that a given firmware does not expose are simply omitted.

Vocabulary:

* **UserID**: the number that identifies a user (typed on the keypad). The
  device stores it in a field called ``Pin``.
* **slot**: an internal number the device gives each user (field ``UID``),
  used to bind fingerprint templates to their owner. It cannot be chosen.
"""
from __future__ import annotations

import base64
import warnings
from dataclasses import dataclass, fields
from typing import Any, ClassVar, Dict, Optional

Row = Dict[str, Any]


def _deprecated(old: str, new: str) -> None:
    warnings.warn(f"'{old}' is deprecated, use '{new}'", DeprecationWarning, stacklevel=3)


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

    # Backwards compatibility with 0.1 ("pin" was renamed "user_id").
    @property
    def pin(self) -> int:
        _deprecated("pin", "user_id")
        return self.user_id  # type: ignore[attr-defined]


@dataclass
class User(_Model):
    """A record of the ``user`` table."""

    TABLE: ClassVar[str] = "user"
    FIELDS: ClassVar[Dict[str, str]] = {
        "user_id": "Pin", "name": "Name", "card_no": "CardNo", "password": "Password",
        "group": "Group", "start_time": "StartTime", "end_time": "EndTime",
        "privilege": "Privilege", "super_authorize": "SuperAuthorize", "slot": "UID",
    }

    user_id: int
    name: Optional[str] = None
    card_no: Optional[int] = None
    #: keypad code (digits, as a string)
    password: Optional[str] = None
    group: Optional[int] = None
    #: validity period, ZK date format (0 = none); see ``codec.encode_datetime``
    start_time: Optional[int] = None
    end_time: Optional[int] = None
    privilege: Optional[int] = None
    super_authorize: Optional[int] = None
    #: internal slot, assigned by the device; it cannot be chosen by the client
    slot: Optional[int] = None

    @property
    def uid(self) -> Optional[int]:
        _deprecated("uid", "slot")
        return self.slot


@dataclass
class Template(_Model):
    """A fingerprint template (``templatev10`` table, ZKFinger v10 format).

    ``finger_id`` is a slot number (0-9) within the user, not necessarily a
    physical finger: devices that enroll on their keypad number fingers in
    the order they are enrolled.
    """

    TABLE: ClassVar[str] = "templatev10"
    FIELDS: ClassVar[Dict[str, str]] = {
        "user_id": "Pin", "finger_id": "FingerID", "template": "Template", "size": "Size",
        "valid": "Valid", "slot": "UID", "reserved": "Resverd", "end_tag": "EndTag",
    }

    user_id: int
    finger_id: int = 0
    template: bytes = b""
    size: Optional[int] = None
    valid: Optional[int] = 1
    slot: Optional[int] = None
    reserved: Optional[int] = None
    end_tag: Optional[int] = None

    def __post_init__(self):
        if isinstance(self.template, str):
            self.template = base64.b64decode(self.template)
        if self.size is None and self.template:
            self.size = len(self.template)

    def to_row(self, include_none: bool = False) -> Row:
        row = super().to_row(include_none)
        if not self.template:  # read without data: never write an empty template
            row.pop("Template", None)
        return row

    def __repr__(self) -> str:  # do not dump biometric data in logs
        return (f"Template(user_id={self.user_id}, finger_id={self.finger_id}, size={self.size}, "
                f"valid={self.valid}, slot={self.slot})")

    @property
    def uid(self) -> Optional[int]:
        _deprecated("uid", "slot")
        return self.slot


@dataclass
class Authorization(_Model):
    """A record of the ``userauthorize`` table: which doors a user may open, and when.

    ``door_mask`` is a bit mask on multi-door controllers (door 1 = 1, door 2 = 2,
    door 3 = 4, door 4 = 8); single-door readers use 1.
    """

    TABLE: ClassVar[str] = "userauthorize"
    FIELDS: ClassVar[Dict[str, str]] = {
        "user_id": "Pin", "timezone_id": "AuthorizeTimezoneId", "door_mask": "AuthorizeDoorId",
    }

    user_id: int
    timezone_id: int = 1
    door_mask: int = 1


def model_fields(model) -> list:
    return [f.name for f in fields(model)]
