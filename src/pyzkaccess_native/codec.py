"""Encoding of tables, records, conditions, parameters and dates.

The device describes its own tables (command ``0x06``) as text lines::

    user=1,UID=i1,CardNo=i2,Pin=i3,Password=s4,...

``i`` is an integer, ``s`` a string and ``B`` a binary blob. Inside records each
value is encoded as ``<length><bytes>`` where the length takes one byte for
``i``/``s`` and two bytes (little-endian) for ``B``. Integers are little-endian
and use the minimal number of bytes; zero may be sent as an empty value.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping

from .errors import ProtocolError

Row = Dict[str, Any]

#: Encoding used for string fields. ``surrogateescape`` keeps unknown bytes
#: intact so a value read from the device can be written back unchanged.
TEXT_ENCODING = "utf-8"
TEXT_ERRORS = "surrogateescape"


@dataclass(frozen=True)
class Field:
    """One column of a device table."""

    name: str
    type: str  # "i", "s" or "B"
    index: int


@dataclass
class Table:
    """A device table and its columns, as reported by the device."""

    name: str
    index: int
    fields: List[Field] = field(default_factory=list)

    def field(self, name: str) -> Field:
        for f in self.fields:
            if f.name == name:
                return f
        raise KeyError(f"table {self.name!r} has no field {name!r} "
                       f"(available: {', '.join(f.name for f in self.fields)})")

    def by_index(self, index: int) -> Field:
        for f in self.fields:
            if f.index == index:
                return f
        raise ProtocolError(f"table {self.name!r} has no field #{index}")

    @property
    def field_names(self) -> List[str]:
        return [f.name for f in self.fields]


def parse_table_config(text: str) -> Dict[str, Table]:
    """Parse the answer of the table configuration command (``0x06``)."""
    tables: Dict[str, Table] = {}
    for line in text.replace("\r", "\n").split("\n"):
        line = line.strip().strip("\x00")
        if not line or "=" not in line:
            continue
        parts = line.split(",")
        name, index = parts[0].split("=", 1)
        table = Table(name.strip(), int(index))
        for part in parts[1:]:
            if "=" not in part:
                continue
            fname, spec = part.split("=", 1)
            spec = spec.strip()
            if len(spec) >= 2 and spec[0] in "isB" and spec[1:].isdigit():
                table.fields.append(Field(fname.strip(), spec[0], int(spec[1:])))
        tables[table.name] = table
    return tables


# --- values -------------------------------------------------------------------

def encode_int(value: int) -> bytes:
    value = int(value)
    if value < 0:
        raise ValueError(f"negative integers are not supported: {value}")
    raw = value.to_bytes((value.bit_length() + 7) // 8, "little") if value else b""
    if len(raw) > 0xFF:
        raise ValueError("integer too large")
    return bytes([len(raw)]) + raw


def encode_str(value: str) -> bytes:
    raw = str(value).encode(TEXT_ENCODING, TEXT_ERRORS)
    if len(raw) > 0xFF:
        raise ValueError(f"string too long ({len(raw)} bytes, max 255)")
    return bytes([len(raw)]) + raw


def encode_bin(value: bytes) -> bytes:
    value = bytes(value)
    if len(value) > 0xFFFF:
        raise ValueError("binary value too long")
    return len(value).to_bytes(2, "little") + value


def encode_value(f: Field, value: Any) -> bytes:
    if f.type == "i":
        return encode_int(value if value not in (None, "") else 0)
    if f.type == "B":
        return encode_bin(value or b"")
    return encode_str("" if value is None else value)


def decode_records(table: Table, data: bytes) -> List[Row]:
    """Decode a ``GET_DATA`` answer: ``<table> <n> <field#>... <records>``."""
    if len(data) < 2:
        return []
    count = data[1]
    fields = [table.by_index(i) for i in data[2:2 + count]]
    pos = 2 + count
    rows: List[Row] = []
    end = len(data)
    while pos < end:
        row: Row = {}
        for f in fields:
            if f.type == "B":
                length = int.from_bytes(data[pos:pos + 2], "little")
                pos += 2
                row[f.name] = bytes(data[pos:pos + length])
            else:
                length = data[pos]
                pos += 1
                raw = bytes(data[pos:pos + length])
                if f.type == "i":
                    row[f.name] = int.from_bytes(raw, "little")
                else:
                    row[f.name] = raw.decode(TEXT_ENCODING, TEXT_ERRORS)
            pos += length
            if pos > end:
                raise ProtocolError(f"truncated record in table {table.name!r}")
        rows.append(row)
    return rows


def encode_records(table: Table, rows: Iterable[Mapping[str, Any]],
                   skip: Iterable[str] = ()) -> bytes:
    """Encode the payload of a ``SET_DATA`` request.

    All rows must carry the same set of fields; only the fields present in the
    first row (minus ``skip``) are sent.
    """
    rows = list(rows)
    if not rows:
        raise ValueError("no record to write")
    skip = set(skip)
    names = [n for n in rows[0] if n not in skip]
    fields = sorted((table.field(n) for n in names), key=lambda f: f.index)
    out = bytearray([table.index, len(fields)] + [f.index for f in fields])
    for row in rows:
        missing = [f.name for f in fields if f.name not in row]
        if missing:
            raise ValueError(f"all records must have the same fields; missing {missing}")
        for f in fields:
            out += encode_value(f, row[f.name])
    return bytes(out)


def encode_conditions(table: Table, where: Mapping[str, Any] | None) -> bytes:
    """Encode ``<count> [<field#> <len> <value>]...`` (used by GET_DATA and DELETE_DATA)."""
    where = where or {}
    out = bytearray([len(where)])
    for name, value in where.items():
        f = table.field(name)
        out.append(f.index)
        out += encode_value(f, value)
    return bytes(out)


def encode_query(table: Table, fields: Iterable[str] | None = None,
                 where: Mapping[str, Any] | None = None) -> bytes:
    """Encode the payload of a ``GET_DATA`` request."""
    wanted = table.fields if fields is None else [table.field(n) for n in fields]
    indexes = sorted(f.index for f in wanted)
    # The byte between the field list and the conditions is always 0 in captures
    # of the official SDK; its meaning is unknown.
    return bytes([table.index, len(indexes)] + indexes + [0]) + encode_conditions(table, where)


# --- parameters -----------------------------------------------------------------

def parse_params(data: bytes) -> Dict[str, str]:
    """Parse a ``Name=value,Name2=value2`` answer. Unknown parameters are omitted by the device."""
    text = data.decode(TEXT_ENCODING, "replace").strip("\x00")
    out: Dict[str, str] = {}
    for item in text.split(","):
        if "=" in item:
            k, v = item.split("=", 1)
            out[k.strip()] = v.strip("\x00")
    return out


def encode_params(params: Mapping[str, Any]) -> bytes:
    return ",".join(f"{k}={v}" for k, v in params.items()).encode(TEXT_ENCODING)


# --- dates -----------------------------------------------------------------------

def encode_datetime(dt: datetime) -> int:
    """Encode a (naive, device local time) datetime to the ZK 32-bit format.

    Every month is counted as 31 days and every year as 12 x 31 days::

        ((year - 2000) * 372 + (month - 1) * 31 + (day - 1)) * 86400 + h*3600 + m*60 + s
    """
    return (((dt.year - 2000) * 372 + (dt.month - 1) * 31 + (dt.day - 1)) * 86400
            + dt.hour * 3600 + dt.minute * 60 + dt.second)


def decode_datetime(value: int) -> datetime | None:
    """Decode the ZK 32-bit date format. Returns ``None`` for 0 or invalid values."""
    if not value:
        return None
    days, secs = divmod(int(value), 86400)
    try:
        return datetime(2000 + days // 372, (days // 31) % 12 + 1, days % 31 + 1,
                        secs // 3600, (secs // 60) % 60, secs % 60)
    except ValueError:
        return None
