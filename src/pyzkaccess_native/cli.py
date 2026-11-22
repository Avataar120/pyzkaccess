"""Command line interface: ``zkaccess --host 192.168.1.201 <command>``."""
from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

from . import __version__, backup as backup_mod
from .client import ZKAccess
from .discovery import discover, scan
from .errors import ZKError
from .events import AccessEvent
from .models import User


def _parse_where(items: Optional[List[str]]) -> Optional[Dict[str, Any]]:
    if not items:
        return None
    out: Dict[str, Any] = {}
    for item in items:
        key, _, value = item.partition("=")
        out[key] = int(value) if value.isdigit() else value
    return out


def _jsonable(value: Any) -> Any:
    if isinstance(value, bytes):
        return base64.b64encode(value).decode()
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _print_rows(rows: List[Dict[str, Any]], as_json: bool) -> None:
    if as_json:
        print(json.dumps([{k: _jsonable(v) for k, v in r.items()} for r in rows], indent=1,
                         ensure_ascii=False))
        return
    if not rows:
        print("(no record)")
        return
    cols = list(rows[0])

    def cell(v: Any) -> str:
        return f"<{len(v)} bytes>" if isinstance(v, bytes) else str(v)

    widths = {c: max(len(c), *(len(cell(r.get(c, ""))) for r in rows)) for c in cols}
    print("  ".join(c.ljust(widths[c]) for c in cols))
    for r in rows:
        print("  ".join(cell(r.get(c, "")).ljust(widths[c]) for c in cols))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="zkaccess", description="ZKTeco PullSDK / ZKAccess device tool")
    p.add_argument("--host", default=os.environ.get("ZKACCESS_HOST"),
                   help="device IP address (or $ZKACCESS_HOST)")
    p.add_argument("--port", type=int, default=int(os.environ.get("ZKACCESS_PORT", 4370)))
    p.add_argument("--password", default=os.environ.get("ZKACCESS_PASSWORD", ""),
                   help="communication password (or $ZKACCESS_PASSWORD)")
    p.add_argument("--timeout", type=float, default=5.0)
    p.add_argument("-v", "--verbose", action="count", default=0, help="-v info, -vv frame dump")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("discover", help="find devices on the local network (UDP broadcast)")
    sc = sub.add_parser("scan", help="find devices on any subnet: try TCP port 4370 on ranges")
    sc.add_argument("networks", nargs="+", help="ranges such as 192.168.20.0/24 (at most /22 each)")
    sub.add_parser("info", help="device identity and capabilities")
    sub.add_parser("tables", help="list tables and their fields")

    s = sub.add_parser("params", help="read or write device parameters")
    s.add_argument("items", nargs="+", help="Name to read, or Name=value to write")

    s = sub.add_parser("time", help="read the device clock, or set it with --set")
    s.add_argument("--set", nargs="?", const="now", metavar="YYYY-MM-DDTHH:MM:SS")

    s = sub.add_parser("read", help="read a table")
    s.add_argument("table")
    s.add_argument("--where", action="append", metavar="Field=value")
    s.add_argument("--fields", help="comma separated field list")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("count", help="number of records in a table")
    s.add_argument("table")

    s = sub.add_parser("users", help="list users")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("user-set", help="create or update a user (other fields are kept)")
    s.add_argument("user_id", type=int, help="UserID")
    s.add_argument("--name")
    s.add_argument("--card", type=int)
    s.add_argument("--user-password", dest="user_password", help="keypad password of the user")

    s = sub.add_parser("user-delete", help="delete a user with its templates and authorizations")
    s.add_argument("user_id", type=int, help="UserID")
    s.add_argument("--yes", action="store_true", help="do not ask for confirmation")

    s = sub.add_parser("grant", help="authorize a user on doors")
    s.add_argument("user_id", type=int, help="UserID")
    s.add_argument("--timezone", type=int, default=1)
    s.add_argument("--doors", type=int, default=1, help="door bit mask (1, 2, 4, 8)")

    s = sub.add_parser("revoke", help="remove every authorization of a user")
    s.add_argument("user_id", type=int, help="UserID")

    s = sub.add_parser("backup", help="save configuration tables to a JSON file")
    s.add_argument("file")
    s.add_argument("--logs", action="store_true", help="also save transaction/oplog tables")

    s = sub.add_parser("restore", help="write a JSON backup back to the device")
    s.add_argument("file")
    s.add_argument("--wipe", action="store_true", help="erase the tables first (exact copy)")
    s.add_argument("--yes", action="store_true", help="do not ask for confirmation")

    s = sub.add_parser("events", help="print access events in real time (Ctrl+C to stop)")
    s.add_argument("--status", action="store_true", help="also print door status records")
    s.add_argument("--interval", type=float, default=0.5)
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("door", help="drive the lock relay")
    s.add_argument("action", choices=["open", "close", "hold"])
    s.add_argument("--door", type=int, default=1)
    s.add_argument("--seconds", type=int, default=5)

    s = sub.add_parser("aux", help="drive an auxiliary output")
    s.add_argument("output", type=int)
    s.add_argument("seconds", type=int, help="0 = off, 1-254 = pulse, 255 = on")

    sub.add_parser("cancel-alarm", help="cancel the current alarm")
    sub.add_parser("restart", help="reboot the device")
    return p


def _confirm(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def run(args: argparse.Namespace) -> int:
    if args.cmd in ("discover", "scan"):
        if args.cmd == "discover":
            devices = discover()
        else:
            try:
                devices = scan(args.networks, port=args.port, password=args.password)
            except ValueError as ex:
                print(f"error: {ex}", file=sys.stderr)
                return 2
        _print_rows([{"ip": d.ip, "mac": d.mac, "serial": d.serial_number or d.raw.get("auth"),
                      "name": (d.device_name or "").strip(), "firmware": d.firmware} for d in devices], False)
        return 0
    if not args.host:
        print("error: --host (or $ZKACCESS_HOST) is required", file=sys.stderr)
        return 2

    with ZKAccess(args.host, args.port, args.password, args.timeout) as dev:
        cmd = args.cmd
        if cmd == "info":
            info = dev.info()
            for k, v in vars(info).items():
                if k != "raw":
                    print(f"{k:20} {v}")
        elif cmd == "tables":
            for t in dev.tables().values():
                print(f"{t.index:3} {t.name:15} " + ", ".join(f"{f.name}:{f.type}" for f in t.fields))
        elif cmd == "params":
            writes = dict(i.split("=", 1) for i in args.items if "=" in i)
            reads = [i for i in args.items if "=" not in i]
            if writes:
                dev.set_params(writes)
            for k, v in dev.get_params(reads or list(writes)).items():
                print(f"{k}={v}")
        elif cmd == "time":
            if args.set:
                when = datetime.now() if args.set == "now" else datetime.fromisoformat(args.set)
                dev.set_time(when)
            device_time = dev.get_time()
            print(f"device: {device_time}  local: {datetime.now().replace(microsecond=0)}")
        elif cmd == "read":
            fields = args.fields.split(",") if args.fields else None
            _print_rows(dev.read_table(args.table, fields, _parse_where(args.where)), args.json)
        elif cmd == "count":
            print(dev.count(args.table))
        elif cmd == "users":
            rows = [{"UserID": u.user_id, "Name": u.name, "Card": u.card_no or "",
                     "Code": "yes" if u.password else "", "Slot": u.slot} for u in dev.get_users()]
            _print_rows(rows, args.json)
        elif cmd == "user-set":
            user = dev.save_user(User(args.user_id, name=args.name, card_no=args.card, password=args.user_password))
            _print_rows([{"UserID": user.user_id, "Name": user.name, "Card": user.card_no or "",
                          "Code": "yes" if user.password else "", "Slot": user.slot}], False)
        elif cmd == "user-delete":
            if not _confirm(f"Delete user {args.user_id}, its templates and authorizations?", args.yes):
                return 1
            dev.delete_user(args.user_id)
        elif cmd == "grant":
            dev.grant(args.user_id, args.timezone, args.doors)
        elif cmd == "revoke":
            dev.revoke(args.user_id)
        elif cmd == "backup":
            snap = backup_mod.backup(dev, include_logs=args.logs)
            backup_mod.save(snap, args.file)
            print(", ".join(f"{k}: {len(v)}" for k, v in snap["tables"].items()))
        elif cmd == "restore":
            snap = backup_mod.load(args.file)
            what = "ERASE and replace" if args.wipe else "merge into"
            if not _confirm(f"{what} the device tables {list(snap['tables'])}?", args.yes):
                return 1
            print(backup_mod.restore(dev, snap, wipe=args.wipe))
        elif cmd == "events":
            try:
                for rec in dev.events(args.interval, include_status=args.status):
                    if args.json:
                        d = {k: _jsonable(v) for k, v in vars(rec).items() if k != "raw"}
                        print(json.dumps(d), flush=True)
                    elif isinstance(rec, AccessEvent):
                        print(f"{rec.time}  door {rec.door}  user {rec.user_id:<6} card {rec.card:<10} "
                              f"{int(rec.event):3} {rec.label}", flush=True)
                    else:
                        print(f"{rec.time}  status  sensors {[int(s) for s in rec.sensors]} "
                              f"alarms {rec.alarms.hex()}", flush=True)
            except KeyboardInterrupt:
                pass
        elif cmd == "door":
            {"open": lambda: dev.open_door(args.door, args.seconds),
             "close": lambda: dev.close_door(args.door),
             "hold": lambda: dev.hold_door_open(args.door)}[args.action]()
        elif cmd == "aux":
            dev.set_aux_output(args.output, args.seconds)
        elif cmd == "cancel-alarm":
            dev.cancel_alarm()
        elif cmd == "restart":
            dev.restart()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=[logging.WARNING, logging.INFO, logging.DEBUG][min(args.verbose, 2)],
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        return run(args)
    except ZKError as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
