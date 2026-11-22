"""In-memory device emulator, for tests and for trying the library without hardware.

It speaks the same TCP protocol as a real device (session handshake, tables,
buffered reads, real-time log, control commands). Its behaviour follows what
was observed on real hardware, but it is not a reference: when in doubt, trust
the device.

Run one from a shell::

    python -m pyzkaccess_native.emulator --port 4370
"""
from __future__ import annotations

import argparse
import socketserver
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from . import codec, protocol
from .codec import Row, Table
from .protocol import Command, Reply

#: Table layout of a single-door fingerprint reader (same as a C3 panel).
DEFAULT_TABLE_CONFIG = "\n".join([
    "user=1,UID=i1,CardNo=i2,Pin=i3,Password=s4,Group=i5,StartTime=i6,EndTime=i7,Name=s8,"
    "Privilege=i9,SuperAuthorize=i10",
    "userauthorize=2,Pin=i1,AuthorizeTimezoneId=i2,AuthorizeDoorId=i3",
    "holiday=3,Holiday=i1,HolidayType=i2,Loop=i3",
    "timezone=4,TimezoneId=i1," + ",".join(
        f"{d}Time{n}=i{2 + i * 3 + n - 1}" for i, d in enumerate(
            ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Hol1", "Hol2", "Hol3"])
        for n in (1, 2, 3)),
    "transaction=5,Cardno=i1,Pin=i2,Verified=i3,DoorID=i4,EventType=i5,InOutState=i6,Time_second=i7",
    "firstcard=6,Pin=i1,DoorID=i2,TimezoneID=i3",
    "multimcard=7,Index=i1,DoorId=i2,Group1=i3,Group2=i4,Group3=i5,Group4=i6,Group5=i7",
    "inoutfun=8,Index=i1,EventType=i2,InAddr=i3,OutType=i4,OutAddr=i5,OutTime=i6,Reserved=i7",
    "templatev10=10,Size=i1,UID=i2,Pin=i3,FingerID=i4,Valid=i5,Template=B6,Resverd=i7,EndTag=i8",
])

#: Key fields used to decide whether a write updates an existing record.
KEYS = {"user": ("Pin",), "templatev10": ("Pin", "FingerID"), "userauthorize": ("Pin",),
        "timezone": ("TimezoneId",), "holiday": ("Holiday",)}


class DeviceState:
    """The emulated device memory."""

    def __init__(self, table_config: str = DEFAULT_TABLE_CONFIG, password: str = "",
                 buffer_threshold: int = 4096, chunk_size: int = 1024):
        self.tables: Dict[str, Table] = codec.parse_table_config(table_config)
        self.table_config = table_config
        self.data: Dict[str, List[Row]] = {name: [] for name in self.tables}
        self.params: Dict[str, str] = {
            "~SerialNumber": "EMU0000000001", "FirmVer": "Emulator 1.0", "MachineType": "101",
            "LockCount": "1", "ReaderCount": "1", "AuxInCount": "0", "AuxOutCount": "1",
            "IPAddress": "127.0.0.1", "~ZKFPVersion": "10", "~MaxUserCount": "300",
            "Door1Drivertime": "5", "Door1SensorType": "0", "Door1Detectortime": "15",
            "Door1VerifyType": "0", "Door1KeepOpenTimeZone": "0", "Door1CloseAndLock": "1",
        }
        self.password = password
        self.buffer_threshold = buffer_threshold
        self.chunk_size = chunk_size
        self.pending_events: List[bytes] = []
        self.controls: List[Tuple[int, ...]] = []
        self.clock_offset = 0
        self.lock = threading.Lock()
        self.next_uid = 1

    # -- helpers used by tests ----------------------------------------------
    def push_event(self, event: int, user_id: int = 0, card: int = 0, door: int = 1,
                   verify: int = 1, in_out: int = 0) -> None:
        stamp = codec.encode_datetime(datetime.now())
        self.pending_events.append(card.to_bytes(4, "little") + user_id.to_bytes(4, "little")
                                   + bytes([verify, door, event, in_out]) + stamp.to_bytes(4, "little"))

    def table_by_index(self, index: int) -> Table:
        return next(t for t in self.tables.values() if t.index == index)

    # -- command handlers ---------------------------------------------------
    def _match(self, table: Table, row: Row, cond: Dict[str, Any]) -> bool:
        return all(row.get(k) == v for k, v in cond.items())

    def _parse_conditions(self, table: Table, data: bytes) -> Dict[str, Any]:
        count, pos, out = data[0], 1, {}
        for _ in range(count):
            f = table.by_index(data[pos])
            length = data[pos + 1]
            raw = data[pos + 2:pos + 2 + length]
            out[f.name] = int.from_bytes(raw, "little") if f.type == "i" else raw.decode()
            pos += 2 + length
        return out

    def get_data(self, payload: bytes) -> bytes:
        table = self.table_by_index(payload[0])
        n = payload[1]
        indexes = list(payload[2:2 + n])
        cond = self._parse_conditions(table, payload[3 + n:])
        fields = [table.by_index(i) for i in indexes]
        out = bytearray(payload[:2 + n])
        for row in self.data[table.name]:
            if self._match(table, row, cond):
                for f in fields:
                    value = row.get(f.name, 0 if f.type == "i" else (b"" if f.type == "B" else ""))
                    if f.type == "i":
                        out += codec.encode_int(value) if value else b"\x01\x00"
                    else:
                        out += codec.encode_value(f, value)
        return bytes(out)

    def set_data(self, payload: bytes) -> None:
        table = self.table_by_index(payload[0])
        for row in codec.decode_records(table, payload):
            full = {f.name: (0 if f.type == "i" else b"" if f.type == "B" else "") for f in table.fields}
            full.update(row)
            keys = KEYS.get(table.name)
            rows = self.data[table.name]
            existing = next((r for r in rows if keys and all(r.get(k) == full.get(k) for k in keys)), None)
            if table.name == "user":
                # The device assigns the UID itself and ignores the one sent.
                full["UID"] = existing["UID"] if existing else self.next_uid
                if not existing:
                    self.next_uid += 1
            if existing is not None:
                rows[rows.index(existing)] = full
            else:
                rows.append(full)

    def delete_data(self, payload: bytes) -> None:
        table = self.table_by_index(payload[0])
        cond = self._parse_conditions(table, payload[1:])
        self.data[table.name] = [r for r in self.data[table.name] if not self._match(table, r, cond)]

    def rtlog(self) -> bytes:
        events, self.pending_events = self.pending_events, []
        stamp = codec.encode_datetime(datetime.now()).to_bytes(4, "little")
        status = bytes(4) + bytes([1, 0, 0, 0]) + bytes([0, 0, 255, 0]) + stamp
        return b"".join(events) + status

    def control(self, payload: bytes) -> None:
        self.controls.append(tuple(payload))
        op, p1, p2, p3 = payload[0], payload[1], payload[2], payload[3]
        if op == 1 and p2 == 1:
            self.push_event(8 if p3 else 9, door=p1, verify=200)
        elif op == 1 and p2 == 2:
            self.push_event(12 if p3 else 13, door=p1, verify=200)
        elif op == 2:
            self.push_event(7, door=1, verify=200)


class _Handler(socketserver.BaseRequestHandler):
    server: "Emulator"

    def _recv(self, size: int) -> bytes:
        buf = b""
        while len(buf) < size:
            chunk = self.request.recv(size - len(buf))
            if not chunk:
                raise ConnectionError
            buf += chunk
        return buf

    def _send(self, reply: int, data: bytes, prefix: bytes) -> None:
        body = bytes([0x00, reply]) + len(prefix + data).to_bytes(2, "little") + prefix + data
        self.request.sendall(bytes([protocol.START]) + body
                             + protocol.crc16(body).to_bytes(2, "little") + bytes([protocol.END]))

    def handle(self) -> None:
        state = self.server.state
        session: Optional[int] = None
        buffer = b""
        try:
            while True:
                header = self._recv(5)
                cmd, _, length = protocol.parse_header(header)
                frame = protocol.parse_frame(header + self._recv(length + 3))
                data = frame.data
                if cmd == Command.CONNECT:
                    if data[4:].decode() != state.password:
                        self._send(Reply.ERROR, bytes([256 - 14]), data[:4])
                        continue
                    session = 0x1234
                    self._send(Reply.OK, b"", session.to_bytes(2, "little") + data[2:4])
                    continue
                if cmd == Command.CONNECT_SESSIONLESS:
                    self._send(Reply.OK, b"", b"")
                    continue
                prefix, data = (data[:4], data[4:]) if session is not None else (b"", data)
                reply, out = Reply.OK, b""
                with state.lock:
                    if cmd == Command.DISCONNECT:
                        self._send(Reply.OK, b"", prefix)
                        return
                    if cmd == Command.GET_PARAM:
                        names = data.decode().split(",")
                        values = dict(state.params, DateTime=str(codec.encode_datetime(datetime.now())))
                        out = ",".join(f"{n}={values[n]}" for n in names if n in values).encode()
                    elif cmd == Command.SET_PARAM:
                        for k, v in codec.parse_params(data).items():
                            if k != "DateTime":
                                state.params[k] = v
                    elif cmd == Command.TABLE_CONFIG:
                        out = state.table_config.encode()
                    elif cmd == Command.GET_DATA:
                        out = state.get_data(data)
                        if len(out) > state.buffer_threshold:
                            buffer, size = out, len(out).to_bytes(4, "little")
                            reply = Reply.BUFFERED
                            out = b"\x00" + size + size + bytes(4) + state.chunk_size.to_bytes(4, "little")
                    elif cmd == Command.BUFFER_READ:
                        offset = int.from_bytes(data[:4], "little")
                        out = data[:4] + buffer[offset:offset + state.chunk_size]
                    elif cmd == Command.BUFFER_FREE:
                        buffer = b""
                    elif cmd == Command.SET_DATA:
                        state.set_data(data)
                    elif cmd == Command.DELETE_DATA:
                        state.delete_data(data)
                    elif cmd == Command.COUNT_DATA:
                        out = len(state.data[state.table_by_index(data[0]).name]).to_bytes(4, "little")
                    elif cmd == Command.RTLOG_BINARY:
                        out = state.rtlog()
                    elif cmd == Command.CONTROL:
                        state.control(data)
                    else:
                        reply, out = Reply.ERROR, bytes([256 - 13])
                self._send(reply, out, prefix)
        except (ConnectionError, OSError):
            return


class Emulator(socketserver.ThreadingTCPServer):
    """A TCP server emulating one device. Use as a context manager in tests::

        with Emulator() as emu:
            with ZKAccess("127.0.0.1", emu.port) as dev:
                ...
    """

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, host: str = "127.0.0.1", port: int = 0, state: Optional[DeviceState] = None):
        super().__init__((host, port), _Handler)
        self.state = state or DeviceState()
        self._thread: Optional[threading.Thread] = None

    @property
    def port(self) -> int:
        return self.server_address[1]

    def __enter__(self) -> "Emulator":
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.shutdown()
        self.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="pyzkaccess-native device emulator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4370)
    parser.add_argument("--password", default="")
    args = parser.parse_args()
    server = Emulator(args.host, args.port, DeviceState(password=args.password))
    print(f"Emulated device listening on {args.host}:{server.port} (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
