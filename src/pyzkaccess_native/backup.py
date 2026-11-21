"""Full backup and restore of a device's configuration tables, as JSON.

Fingerprint templates are restored byte for byte. Device UIDs cannot be chosen
by the client, so templates are re-bound to the UID the device gives each user
on restore.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

from .client import ZKAccess
from .codec import Row
from .errors import SafetyError

FORMAT = "pyzkaccess-native/1"

#: Configuration tables, in restore order (time zones before users, users
#: before templates and authorizations). Log tables are not restored.
CONFIG_TABLES = ("timezone", "holiday", "user", "templatev10", "template", "userauthorize",
                 "firstcard", "multimcard", "inoutfun")
LOG_TABLES = ("transaction", "oplog")


def _to_json(row: Row) -> Dict[str, Any]:
    return {k: (base64.b64encode(v).decode() if isinstance(v, bytes) else v) for k, v in row.items()}


def backup(dev: ZKAccess, tables: Optional[Iterable[str]] = None,
           include_logs: bool = False) -> Dict[str, Any]:
    """Read the device and return a JSON-serializable snapshot."""
    available = dev.tables()
    names = list(tables) if tables else [t for t in CONFIG_TABLES + (LOG_TABLES if include_logs else ())
                                         if t in available]
    info = dev.info()
    return {
        "format": FORMAT,
        "created": datetime.now().isoformat(timespec="seconds"),
        "device": {"serial_number": info.serial_number, "firmware": info.firmware,
                   "machine_type": info.machine_type},
        "schema": {n: [[f.name, f.type, f.index] for f in available[n].fields] for n in names},
        "tables": {n: [_to_json(r) for r in dev.read_table(n)] for n in names},
    }


def save(snapshot: Dict[str, Any], path: Union[str, Path]) -> None:
    Path(path).write_text(json.dumps(snapshot, indent=1, ensure_ascii=False), encoding="utf-8")


def load(path: Union[str, Path]) -> Dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("format") != FORMAT:
        raise ValueError(f"not a {FORMAT} backup")
    return data


def _from_json(dev: ZKAccess, table: str, rows: List[Dict[str, Any]]) -> List[Row]:
    binary = {f.name for f in dev.table(table).fields if f.type == "B"}
    known = set(dev.table(table).field_names)
    return [{k: (base64.b64decode(v) if k in binary else v) for k, v in r.items() if k in known}
            for r in rows]


def restore(dev: ZKAccess, snapshot: Dict[str, Any], tables: Optional[Iterable[str]] = None,
            wipe: bool = False) -> Dict[str, int]:
    """Write a snapshot back to the device. Returns the number of records per table.

    With ``wipe=True`` each restored table is erased first, so the device ends
    up with exactly the snapshot content. Without it, records are merged
    (existing PINs are overwritten, others are kept).
    """
    data = snapshot["tables"]
    names = [t for t in CONFIG_TABLES if t in data and (tables is None or t in tables)]
    names += [t for t in data if t not in names and t not in LOG_TABLES and (tables is None or t in tables)]
    unknown = [t for t in names if t not in dev.tables()]
    if unknown:
        raise SafetyError(f"the device has no table {unknown}")
    done: Dict[str, int] = {}
    with dev._lock:
        if wipe:
            for name in reversed(names):
                dev.delete_records(name, None, allow_wipe=True)
        uids: Dict[int, int] = {}
        for name in names:
            rows = _from_json(dev, name, data[name])
            if name == "user":
                for row in rows:
                    row.pop("UID", None)
                    dev.write_records(name, [row])
                stored = dev.read_table("user", fields=["UID", "Pin"])
                uids = {u["Pin"]: u["UID"] for u in stored}
                if len(set(uids.values())) != len(uids):
                    raise SafetyError("the device assigned duplicate UIDs; templates not restored")
            elif name in ("templatev10", "template"):
                for row in rows:
                    if "UID" in row and row.get("Pin") in uids:
                        row["UID"] = uids[row["Pin"]]
                    dev.write_records(name, [row])
            else:
                for row in rows:
                    dev.write_records(name, [row])
            done[name] = len(rows)
    return done
