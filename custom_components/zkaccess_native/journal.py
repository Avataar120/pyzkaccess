"""Write-ahead journal of the operations that touch fingerprints.

Before an operation writes or removes fingerprint templates (or rewrites a user record,
which can move its internal slot on the MA500), the full state of the UserIDs it touches
is written to disk: user record, authorizations, and every template with its data. After
the operation the device is read back and compared with what the operation must have
produced, template by template (same finger slots, same bytes, bound to the user's own
internal slot, a slot no other user has). Any difference, any error, or a stop in the middle
(restart, power cut, frozen device) puts these UserIDs back exactly as they were, from the
journal; a journal left on disk is settled at the next opportunity.

Template bytes read back from the MA500 are identical to the ones written (checked on
backups taken before and after rewrites, 22/11/2026), so the comparison is exact.
"""
from __future__ import annotations

import base64
import copy
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from pyzkaccess_native import SafetyError, Template, User, ZKAccess

_LOGGER = logging.getLogger(__name__)

FORMAT = "zkaccess-native-journal/1"

#: UserID -> {"user": row or None, "auths": [rows], "templates": {finger_id: {"data", "valid"}}}
State = dict[str, dict[str, Any]]


class JournalError(SafetyError):
    """The fingerprints could not be verified nor put back: the journal is kept."""


# ------------------------------------------------------------------------ reading
def capture(client: ZKAccess, user_ids: Iterable[int]) -> State:
    """State of ``user_ids`` on the device (template data included)."""
    users = {u["Pin"]: u for u in client.read_table("user")}
    state: State = {}
    for uid in sorted(set(user_ids)):
        row = users.get(uid)
        templates = client.get_templates(uid) if row is not None else []
        auths = client.read_table("userauthorize", where={"Pin": uid}) if row is not None else []
        state[str(uid)] = {
            "user": row,
            "auths": auths,
            "templates": {str(t.finger_id): {"data": base64.b64encode(t.template).decode(),
                                             "valid": t.valid, "slot": t.slot} for t in templates},
        }
    # Slots of every user, to check that the ones of these UserIDs are not shared.
    state["_slots"] = {str(pin): u.get("UID") for pin, u in users.items()}
    return state


def compare(expected: State, actual: State) -> list[str]:
    """Differences between the expected and the actual state (empty: identical)."""
    problems = []
    slots = actual.get("_slots", {})
    for key, exp in expected.items():
        if key.startswith("_"):
            continue
        act = actual.get(key) or {"user": None, "templates": {}}
        if (exp["user"] is None) != (act["user"] is None):
            problems.append(f"UserID {key}: {'should not exist' if exp['user'] is None else 'missing'}")
            continue
        exp_t = {f: t["data"] for f, t in exp["templates"].items()}
        act_t = {f: t["data"] for f, t in act["templates"].items()}
        if exp_t != act_t:
            lost = sorted(set(exp_t) - set(act_t))
            extra = sorted(set(act_t) - set(exp_t))
            changed = sorted(f for f in set(exp_t) & set(act_t) if exp_t[f] != act_t[f])
            problems.append(f"UserID {key}: fingerprints differ (missing {lost}, unexpected {extra}, "
                            f"different {changed})")
        if act["user"] is None:
            continue
        slot = act["user"].get("UID")
        if slot is not None:
            shared = [p for p, s in slots.items() if s == slot and p != key]
            if shared:
                problems.append(f"UserID {key}: internal slot {slot} also used by UserID {shared}")
            wrong = [f for f, t in act["templates"].items() if t.get("slot") is not None and t["slot"] != slot]
            if wrong:
                problems.append(f"UserID {key}: fingerprints {wrong} bound to another internal slot")
    return problems


def template_row(client: ZKAccess, user_id: int, finger_id: int, data: bytes,
                 valid: Optional[int], slot: Optional[int]) -> dict[str, Any]:
    """A templatev10 row bound to ``slot`` (only the fields the device knows)."""
    row = Template(user_id=user_id, finger_id=finger_id, template=data,
                   valid=1 if valid is None else valid).to_row()
    if slot is not None:
        row["UID"] = slot
    row.setdefault("EndTag", 13)
    names = set(client.table("templatev10").field_names)
    return {k: v for k, v in row.items() if k in names}


# -------------------------------------------------------------------- putting back
def restore(client: ZKAccess, before: State) -> None:
    """Put the UserIDs of ``before`` back as they were (only those that differ), then
    check. Raises JournalError if the device still differs."""
    current = capture(client, _uids(before))
    for key in _uids(before):
        if not compare({str(key): before[str(key)]}, current):
            continue
        uid, b = key, before[str(key)]
        exists = current[str(uid)]["user"] is not None
        if b["user"] is None:
            # Created by the operation (a temporary or a new UserID): remove it again.
            if exists:
                client.delete_user(uid)
            else:
                client.delete_records("templatev10", {"Pin": uid})
            continue
        if not exists:
            client.save_user(User.from_row(b["user"]), merge=False)
        if b["auths"] and not client.read_table("userauthorize", where={"Pin": uid}):
            client.write_records("userauthorize", b["auths"])
        users = client.read_table("user", fields=["UID", "Pin"])
        slot = next((u.get("UID") for u in users if u["Pin"] == uid), None)
        if slot is not None and any(u.get("UID") == slot and u["Pin"] != uid for u in users):
            raise JournalError(f"UserID {uid}: internal slot {slot} is shared, fingerprints not written back")
        client.delete_records("templatev10", {"Pin": uid})
        for finger, t in sorted(b["templates"].items(), key=lambda kv: int(kv[0])):
            client.write_records("templatev10", [template_row(
                client, uid, int(finger), base64.b64decode(t["data"]), t.get("valid"), slot)])
    problems = compare(before, capture(client, _uids(before)))
    if problems:
        raise JournalError("fingerprints could not be put back: " + "; ".join(problems))


def _uids(state: State) -> list[int]:
    return [int(k) for k in state if not k.startswith("_")]


def expect(before: State, change: Callable[[State], None]) -> State:
    """A copy of ``before`` modified by ``change`` (what an operation must produce)."""
    after = copy.deepcopy(before)
    change(after)
    return after


# ---------------------------------------------------------------------- the file
class Journal:
    """The journal file of one device (at most one operation at a time)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> dict[str, Any]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("format") != FORMAT:
            raise JournalError(f"unknown journal format in {self.path}")
        return data

    def write(self, op: str, before: State) -> None:
        """Durable write (fsync, atomic rename) before anything is changed."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        tmp = self.path.with_suffix(".tmp")
        payload = json.dumps({"format": FORMAT, "op": op, "created": datetime.now().isoformat(timespec="seconds"),
                              "before": before})
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
        dir_fd = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)

    # ------------------------------------------------------------ operations
    def begin(self, client: ZKAccess, op: str, user_ids: Iterable[int], strict: bool = True) -> State:
        """Record the state of ``user_ids`` before ``op``. ``strict``: refuse to start
        when a template of these UserIDs is already bound to a wrong or shared slot
        (only the slot repair itself starts from such a state)."""
        if self.exists():
            raise JournalError("a previous fingerprint operation is not settled yet; "
                               "it is being checked, try again in a moment")
        before = capture(client, user_ids)
        problems = compare(before, before)
        if problems and strict:
            raise SafetyError("; ".join(problems))
        self.write(op, before)
        return before

    def finish(self, client: ZKAccess, op: str, before: State, expected: State) -> None:
        """Check the result; put everything back if it is not exactly the expected one."""
        problems = compare(expected, capture(client, _uids(expected)))
        if not problems:
            self.clear()
            return
        _LOGGER.error("%s: unexpected result (%s); putting the fingerprints back", op, "; ".join(problems))
        restore(client, before)
        self.clear()
        raise SafetyError(f"{op}: the result could not be verified ({problems[0]}); "
                          "the fingerprints were put back as before")

    def abort(self, client: ZKAccess, op: str, before: State) -> None:
        """After an error: put everything back (if anything changed)."""
        restore(client, before)
        self.clear()
        _LOGGER.warning("%s failed: the fingerprints were checked and put back as before", op)

    def run(self, client: ZKAccess, op: str, user_ids: Iterable[int], func: Callable[[], Any],
            change: Optional[Callable[[State, Any], None]] = None, strict: bool = True) -> Any:
        """Run ``func`` journaled; ``change(after, result)`` describes what it must do
        to the state (nothing: the fingerprints must be unchanged)."""
        before = self.begin(client, op, user_ids, strict)
        try:
            result = func()
        except Exception:
            try:
                self.abort(client, op, before)
            except Exception:  # noqa: BLE001 - the journal stays, settled later
                _LOGGER.exception("%s failed and the fingerprints could not be put back yet "
                                  "(journal kept, retried automatically)", op)
            raise
        expected = expect(before, lambda s: change(s, result)) if change else before
        self.finish(client, op, before, expected)
        return result

    def recover(self, client: ZKAccess) -> Optional[str]:
        """Settle a journal left by an interrupted operation: nothing to do if the device
        is as before, otherwise put it back. Returns the operation name when the device
        had to be changed, None otherwise."""
        if not self.exists():
            return None
        data = self.load()
        before = data["before"]
        if not compare(before, capture(client, _uids(before))):
            self.clear()
            _LOGGER.info("Journal of %s (%s): device unchanged, cleared", data["op"], data["created"])
            return None
        restore(client, before)
        self.clear()
        _LOGGER.warning("Journal of %s (%s): interrupted operation, fingerprints put back as before",
                        data["op"], data["created"])
        return data["op"]
