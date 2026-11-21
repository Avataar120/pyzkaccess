"""High-level client for ZKTeco PullSDK / ZKAccess devices (C3, inBio, MA500...)."""
from __future__ import annotations

import logging
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence

from . import codec, protocol
from .codec import Row, Table
from .errors import DeviceError, ProtocolError, SafetyError, ZKConnectionError
from .events import AccessEvent, DoorStatus, RTLogRecord, parse_rtlog
from .models import Authorization, Template, User
from .protocol import Command, Reply

_LOG = logging.getLogger(__name__)

#: Parameters read by :meth:`ZKAccess.info`.
INFO_PARAMS = ("~SerialNumber", "FirmVer", "~DeviceName", "DeviceName", "MachineType",
               "LockCount", "ReaderCount", "AuxInCount", "AuxOutCount", "IPAddress",
               "~ZKFPVersion", "~MaxUserCount", "~MaxUserFingerCount")


class ControlOperation:
    """Operation codes of the control command (``0x05``)."""

    OUTPUT = 1
    CANCEL_ALARM = 2
    RESTART = 3
    NORMAL_OPEN_STATE = 4


class OutputType:
    DOOR = 1
    AUX = 2


@dataclass
class DeviceInfo:
    """Identity and capabilities of a device. Unsupported values are ``None``."""

    serial_number: Optional[str]
    firmware: Optional[str]
    device_name: Optional[str]
    machine_type: Optional[int]
    lock_count: Optional[int]
    reader_count: Optional[int]
    aux_in_count: Optional[int]
    aux_out_count: Optional[int]
    ip_address: Optional[str]
    fingerprint_version: Optional[int]
    raw: Dict[str, str]


def _int_or_none(value: Optional[str]) -> Optional[int]:
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


class ZKAccess:
    """Synchronous client. One instance holds one TCP session.

    The device accepts a limited number of simultaneous sessions (often a single
    one): do not run ZKAccess, the official SDK or a second client against the
    same device at the same time.

    All public methods are thread-safe: requests are serialized by an internal lock.

    Example::

        with ZKAccess("192.168.1.201") as dev:
            print(dev.info().serial_number)
            for user in dev.get_users():
                print(user.pin, user.name)
    """

    def __init__(self, host: str, port: int = protocol.DEFAULT_PORT, password: str = "",
                 timeout: float = 5.0):
        self.host = host
        self.port = port
        self.password = password or ""
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._session: Optional[int] = None
        self._request_nr = protocol.INITIAL_SESSION
        self._tables: Optional[Dict[str, Table]] = None
        self._lock = threading.RLock()
        #: Command code of the last reply (``Reply.OK``, ``Reply.BUFFERED``...).
        self.last_reply: Optional[int] = None

    # ------------------------------------------------------------------ session
    def __enter__(self) -> "ZKAccess":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.disconnect()

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def connect(self) -> None:
        """Open the TCP connection and a session.

        Falls back to the session-less handshake (``0x01``) used by some older
        firmware when the session handshake (``0x76``) is rejected.
        """
        with self._lock:
            self.close()
            try:
                self._sock = socket.create_connection((self.host, self.port), self.timeout)
            except OSError as ex:
                self._sock = None
                raise ZKConnectionError(f"cannot connect to {self.host}:{self.port}: {ex}") from ex
            self._sock.settimeout(self.timeout)
            self._tables = None
            password = self.password.encode("ascii")
            try:
                self._session = protocol.INITIAL_SESSION
                self._request_nr = protocol.INITIAL_SESSION
                data = self._exchange(Command.CONNECT, password, strip_session=False)
                if len(data) < 2:
                    raise ProtocolError("connect answer carries no session id")
                self._session = int.from_bytes(data[0:2], "little")
                _LOG.debug("connected to %s, session %04x", self.host, self._session)
            except DeviceError as ex:
                if ex.code == -14:
                    self.close()
                    raise
                _LOG.debug("session handshake refused (%s), trying session-less", ex)
                self._session = None
                self._exchange(Command.CONNECT_SESSIONLESS, password)

    def disconnect(self) -> None:
        """Close the session politely, then the socket. Never raises."""
        with self._lock:
            if self._sock is not None:
                try:
                    self._exchange(Command.DISCONNECT)
                except Exception:  # noqa: BLE001 - best effort
                    pass
            self.close()

    def close(self) -> None:
        """Close the socket without the disconnect handshake."""
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
            self._sock = None

    # ---------------------------------------------------------------- transport
    def _recv_exact(self, size: int) -> bytes:
        assert self._sock is not None
        buf = bytearray()
        while len(buf) < size:
            chunk = self._sock.recv(size - len(buf))
            if not chunk:
                raise ZKConnectionError("connection closed by the device")
            buf += chunk
        return bytes(buf)

    def _next_request_nr(self) -> int:
        nr = self._request_nr
        # 16-bit counter; 0 is skipped (some implementations treat it as "no session").
        self._request_nr = (self._request_nr + 1) & 0xFFFF or 1
        return nr

    def _exchange(self, command: int, data: bytes = b"", strip_session: bool = True) -> bytes:
        """Send one request and return the reply data (session bytes removed)."""
        with self._lock:
            if self._sock is None:
                raise ZKConnectionError("not connected")
            frame = protocol.build_frame(
                command, data, self._session,
                self._next_request_nr() if self._session is not None else None)
            _LOG.debug(">> %s", frame.hex())
            try:
                self._sock.sendall(frame)
                header = self._recv_exact(5)
                reply, _, length = protocol.parse_header(header)
                raw = header + self._recv_exact(length + 3)
            except (OSError, ZKConnectionError) as ex:
                self.close()
                raise ZKConnectionError(f"communication with {self.host} failed: {ex}") from ex
            _LOG.debug("<< %s", raw.hex() if len(raw) < 256 else raw[:256].hex() + "...")
            payload = protocol.parse_frame(raw).data
            self.last_reply = reply
            if strip_session and self._session is not None and len(payload) >= 4:
                payload = payload[4:]
            if reply == Reply.ERROR:
                code = payload[-1] - 256 if payload and payload[-1] > 127 else (payload[-1] if payload else 0)
                raise DeviceError(code, protocol.DEVICE_ERRORS.get(code, "unknown error"))
            return payload

    def _read_buffer(self, header: bytes) -> bytes:
        """Fetch a large answer that the device placed in its buffer (reply ``0x0D``).

        ``header`` is ``00 | total(4) | total(4) | checksum(4) | chunk_size(4)``.
        """
        total = int.from_bytes(header[1:5], "little")
        buf = bytearray()
        try:
            while len(buf) < total:
                part = self._exchange(Command.BUFFER_READ, len(buf).to_bytes(4, "little"))
                if len(part) <= 4:
                    raise ProtocolError("empty buffer chunk")
                buf += part[4:]  # the reply repeats the offset first
        finally:
            if self._sock is not None:
                self._exchange(Command.BUFFER_FREE)
        return bytes(buf[:total])

    # --------------------------------------------------------------- parameters
    def get_params(self, names: Iterable[str]) -> Dict[str, str]:
        """Read device parameters. Names unknown to the firmware are absent from the result."""
        names = list(names)
        out: Dict[str, str] = {}
        # Keep each request short: some firmware truncate long parameter lists.
        for i in range(0, len(names), 10):
            out.update(codec.parse_params(
                self._exchange(Command.GET_PARAM, ",".join(names[i:i + 10]).encode())))
        return out

    def set_params(self, params: Mapping[str, Any]) -> None:
        """Write device parameters (``Name=value``)."""
        self._exchange(Command.SET_PARAM, codec.encode_params(params))

    def info(self) -> DeviceInfo:
        """Identity and capabilities of the device."""
        p = self.get_params(INFO_PARAMS)
        return DeviceInfo(
            serial_number=p.get("~SerialNumber"),
            firmware=p.get("FirmVer"),
            device_name=p.get("~DeviceName") or p.get("DeviceName") or None,
            machine_type=_int_or_none(p.get("MachineType")),
            lock_count=_int_or_none(p.get("LockCount")),
            reader_count=_int_or_none(p.get("ReaderCount")),
            aux_in_count=_int_or_none(p.get("AuxInCount")),
            aux_out_count=_int_or_none(p.get("AuxOutCount")),
            ip_address=p.get("IPAddress"),
            fingerprint_version=_int_or_none(p.get("~ZKFPVersion")),
            raw=p,
        )

    def get_time(self) -> Optional[datetime]:
        """Device clock (naive datetime, device local time)."""
        value = self.get_params(["DateTime"]).get("DateTime")
        return codec.decode_datetime(int(value)) if value else None

    def set_time(self, when: Optional[datetime] = None) -> None:
        """Set the device clock (defaults to the local time of this computer)."""
        self.set_params({"DateTime": codec.encode_datetime(when or datetime.now())})

    # -------------------------------------------------------------------- tables
    def tables(self, refresh: bool = False) -> Dict[str, Table]:
        """Table layout reported by the device (cached for the session)."""
        with self._lock:
            if self._tables is None or refresh:
                text = self._exchange(Command.TABLE_CONFIG).decode("ascii", "replace")
                self._tables = codec.parse_table_config(text)
            return self._tables

    def table(self, name: str) -> Table:
        try:
            return self.tables()[name]
        except KeyError:
            raise KeyError(f"unknown table {name!r} (device has: {', '.join(self.tables())})") from None

    def read_table(self, name: str, fields: Optional[Iterable[str]] = None,
                   where: Optional[Mapping[str, Any]] = None) -> List[Row]:
        """Read records. ``where`` is an equality filter, e.g. ``{"Pin": 100}``.

        Large answers (fingerprint templates...) are fetched through the device
        buffer transparently.
        """
        with self._lock:
            table = self.table(name)
            data = self._exchange(Command.GET_DATA, codec.encode_query(table, fields, where))
            if self.last_reply == Reply.BUFFERED:
                data = self._read_buffer(data)
            return codec.decode_records(table, data)

    def write_records(self, name: str, rows: Sequence[Mapping[str, Any]],
                      skip: Iterable[str] = ()) -> None:
        """Create or update records (``SET_DATA``).

        Warning: on the ``user`` table a write replaces the *whole* record: any
        field that is not sent is reset to empty/0. Use :meth:`save_user`, which
        merges with the stored record first.
        """
        if not rows:
            return
        with self._lock:
            table = self.table(name)
            self._exchange(Command.SET_DATA, codec.encode_records(table, rows, skip))

    def delete_records(self, name: str, where: Optional[Mapping[str, Any]],
                       allow_wipe: bool = False) -> None:
        """Delete the records matching ``where``.

        Without a condition the device erases the **whole table**; this needs
        ``allow_wipe=True``.
        """
        if not where and not allow_wipe:
            raise SafetyError(f"refusing to erase the whole {name!r} table "
                              "(pass a condition, or allow_wipe=True)")
        with self._lock:
            table = self.table(name)
            self._exchange(Command.DELETE_DATA,
                           bytes([table.index]) + codec.encode_conditions(table, where))

    def count(self, name: str) -> int:
        """Number of records in a table."""
        with self._lock:
            data = self._exchange(Command.COUNT_DATA, bytes([self.table(name).index, 0]))
            return int.from_bytes(data[:4], "little")

    # ----------------------------------------------------------------- real-time
    def poll(self) -> List[RTLogRecord]:
        """Fetch the real-time log once: new access events plus a door status record."""
        return parse_rtlog(self._exchange(Command.RTLOG_BINARY))

    def events(self, interval: float = 0.5, include_status: bool = False,
               reconnect: bool = True) -> Iterator[RTLogRecord]:
        """Poll the real-time log forever and yield records as they arrive.

        With ``reconnect=True`` a lost connection is reopened (with a back-off)
        instead of raising.
        """
        delay = 1.0
        while True:
            try:
                if not self.connected:
                    self.connect()
                for rec in self.poll():
                    if include_status or isinstance(rec, AccessEvent):
                        yield rec
                delay = 1.0
                time.sleep(interval)
            except ZKConnectionError:
                if not reconnect:
                    raise
                _LOG.warning("connection to %s lost, retrying in %.0f s", self.host, delay)
                time.sleep(delay)
                delay = min(delay * 2, 60)

    def door_status(self) -> Optional[DoorStatus]:
        """Latest door/alarm status. Note: this consumes pending events."""
        status = None
        for rec in self.poll():
            if isinstance(rec, DoorStatus):
                status = rec
        return status

    # ------------------------------------------------------------------- control
    def control(self, operation: int, p1: int = 0, p2: int = 0, p3: int = 0, p4: int = 0) -> None:
        """Raw control command (``0x05``)."""
        self._exchange(Command.CONTROL, bytes([operation, p1, p2, p3, p4]))

    def open_door(self, door: int = 1, seconds: int = 5) -> None:
        """Energize the lock relay of ``door`` for ``seconds`` (1-254)."""
        if not 1 <= seconds <= 254:
            raise ValueError("seconds must be between 1 and 254")
        self.control(ControlOperation.OUTPUT, door, OutputType.DOOR, seconds)

    def close_door(self, door: int = 1) -> None:
        """Release the lock relay of ``door`` immediately."""
        self.control(ControlOperation.OUTPUT, door, OutputType.DOOR, 0)

    def hold_door_open(self, door: int = 1) -> None:
        """Keep the lock relay of ``door`` energized until :meth:`close_door`."""
        self.control(ControlOperation.OUTPUT, door, OutputType.DOOR, 255)

    def set_aux_output(self, output: int = 1, seconds: int = 5) -> None:
        """Drive an auxiliary output: 0 = off, 1-254 = pulse length, 255 = on."""
        if not 0 <= seconds <= 255:
            raise ValueError("seconds must be between 0 and 255")
        self.control(ControlOperation.OUTPUT, output, OutputType.AUX, seconds)

    def cancel_alarm(self) -> None:
        self.control(ControlOperation.CANCEL_ALARM)

    def set_normal_open(self, door: int = 1, enabled: bool = True) -> None:
        """Enable or disable the "normal open" time zone of ``door`` for today."""
        self.control(ControlOperation.NORMAL_OPEN_STATE, door, int(enabled))

    def restart(self) -> None:
        """Reboot the device. The connection is closed."""
        try:
            self.control(ControlOperation.RESTART)
        finally:
            self.close()

    # --------------------------------------------------------------------- users
    def _known(self, table: str, row: Row) -> Row:
        names = set(self.table(table).field_names)
        return {k: v for k, v in row.items() if k in names}

    def get_users(self, pin: Optional[int] = None) -> List[User]:
        return [User.from_row(r) for r in self.read_table("user", where={"Pin": pin} if pin is not None else None)]

    def get_user(self, pin: int) -> Optional[User]:
        users = self.get_users(pin)
        return users[0] if users else None

    def save_user(self, user: User, merge: bool = True) -> User:
        """Create or update a user (the key is the PIN) and return the stored record.

        Writing a user replaces the whole record on the device. With
        ``merge=True`` (default) the attributes left to ``None`` are taken from
        the stored record, so ``save_user(User(pin=5, name="Bob"))`` only renames.

        The ``uid`` attribute is never sent: the device assigns it.
        """
        with self._lock:
            row = user.to_row()
            if merge:
                current = self.read_table("user", where={"Pin": user.pin})
                if current:
                    row = {**current[0], **row}
            row = self._known("user", row)
            row.pop("UID", None)
            self.write_records("user", [row])
            stored = self.get_user(user.pin)
            if stored is None:
                raise ProtocolError(f"user {user.pin} was not stored")
            return stored

    def delete_user(self, pin: int, cascade: bool = True) -> None:
        """Delete a user. With ``cascade`` its templates and authorizations go too
        (the device does not remove them by itself)."""
        with self._lock:
            if cascade:
                self.delete_records("templatev10", {"Pin": pin})
                self.delete_records("userauthorize", {"Pin": pin})
            self.delete_records("user", {"Pin": pin})

    # ----------------------------------------------------------------- templates
    def get_templates(self, pin: Optional[int] = None) -> List[Template]:
        where = {"Pin": pin} if pin is not None else None
        return [Template.from_row(r) for r in self.read_table("templatev10", where=where)]

    def save_template(self, template: Template, check_uid: bool = True) -> None:
        """Store a fingerprint template for an existing user.

        The template is bound to the user's device UID. With ``check_uid``
        (default) the write is refused if another user carries the same UID:
        some firmware hand out UIDs that are already in use, and a template
        written then could identify the wrong person.
        """
        with self._lock:
            users = self.read_table("user", fields=["UID", "Pin"])
            owner = next((u for u in users if u["Pin"] == template.pin), None)
            if owner is None:
                raise SafetyError(f"user {template.pin} does not exist; create it first")
            uid = owner.get("UID")
            if check_uid and uid is not None:
                clash = [u["Pin"] for u in users if u.get("UID") == uid and u["Pin"] != template.pin]
                if clash:
                    raise SafetyError(f"UID {uid} of user {template.pin} is also used by {clash}; "
                                      "refusing to write a template")
            row = template.to_row()
            if uid is not None:
                row["UID"] = uid
            row.setdefault("EndTag", 13)
            self.write_records("templatev10", [self._known("templatev10", row)])

    def delete_templates(self, pin: int, finger_id: Optional[int] = None) -> None:
        where: Dict[str, int] = {"Pin": pin}
        if finger_id is not None:
            where["FingerID"] = finger_id
        self.delete_records("templatev10", where)

    # ------------------------------------------------------------ authorizations
    def get_authorizations(self, pin: Optional[int] = None) -> List[Authorization]:
        where = {"Pin": pin} if pin is not None else None
        return [Authorization.from_row(r) for r in self.read_table("userauthorize", where=where)]

    def grant(self, pin: int, timezone_id: int = 1, door_mask: int = 1) -> None:
        """Allow ``pin`` to open the doors of ``door_mask`` during ``timezone_id``.

        Time zone 1 must exist in the ``timezone`` table (it is "always" on
        factory devices).
        """
        self.write_records("userauthorize", [Authorization(pin, timezone_id, door_mask).to_row()])

    def revoke(self, pin: int) -> None:
        """Remove every authorization of ``pin``."""
        self.delete_records("userauthorize", {"Pin": pin})
