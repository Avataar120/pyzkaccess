"""Business logic of the management panel.

Everything here is synchronous and talks to the device: it runs in Home
Assistant's executor, one operation at a time (the client serializes requests
on its single session). The coordinator owns the instance.

Model: a *person* (number 1-99) owns one UserID per *category* (see
``pyzkaccess_native.people``). In "plain" numbering, a person is a UserID and
there is a single category.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from pyzkaccess_native import (CategoryScheme, DoorSettings, SafetyError, Template, User,
                               ZKAccess)
from pyzkaccess_native import backup as zk_backup

from .const import CATEGORY_TYPES, NUMBERING_PLAIN
from .store import AccessStore

_LOGGER = logging.getLogger(__name__)

#: Longest name the device is known to keep.
NAME_MAX = 24
#: Device fields read for the snapshot (fingerprint data is never needed there).
TEMPLATE_LIGHT = ["Pin", "FingerID", "Size", "Valid"]


class Numbering:
    """UserID <-> (person, category) for the configured numbering mode."""

    def __init__(self, mode: str) -> None:
        self.plain = mode == NUMBERING_PLAIN
        self._scheme = CategoryScheme(person_digits=2)

    @property
    def max_person(self) -> int:
        return 999_999_999 if self.plain else self._scheme.max_person

    def user_id(self, person: int, category: int) -> int:
        if self.plain:
            if category:
                raise SafetyError("categories are not used in plain numbering")
            return person
        return self._scheme.user_id(person, category)

    def split(self, user_id: int) -> tuple[int, int] | None:
        if self.plain:
            return (user_id, 0) if user_id > 0 else None
        return self._scheme.split(user_id)


class AccessManager:
    """Device operations for one configured device."""

    def __init__(self, client: ZKAccess, store: AccessStore, backup_dir: Path,
                 door_count: Callable[[], int]) -> None:
        self.client = client
        self.store = store
        self.backup_dir = backup_dir
        self._door_count = door_count

    @property
    def numbering(self) -> Numbering:
        return Numbering(self.store.data["numbering"])

    @property
    def door_mask(self) -> int:
        return (1 << max(1, self._door_count())) - 1

    # ------------------------------------------------------------------ reading
    def read_raw(self) -> dict[str, list[dict[str, Any]]]:
        """Users, templates (without data) and authorizations, as device rows."""
        dev = self.client
        return {
            "users": dev.read_table("user"),
            "templates": dev.read_table("templatev10", fields=TEMPLATE_LIGHT),
            "auths": dev.read_table("userauthorize"),
        }

    @staticmethod
    def fingerprint(raw: dict[str, Any]) -> str:
        """Short hash of the device content, to detect changes."""
        return hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()[:16]

    def build(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Turn device rows into the panel's view: persons, categories, unassigned."""
        numbering = self.numbering
        templates: dict[int, list[dict[str, Any]]] = {}
        for t in raw["templates"]:
            templates.setdefault(t["Pin"], []).append(t)
        authorized = {a["Pin"] for a in raw["auths"]}
        persons: dict[int, dict[str, Any]] = {}
        unassigned = []
        for u in sorted(raw["users"], key=lambda r: r["Pin"]):
            uid = u["Pin"]
            fingers = [{"finger_id": t["FingerID"], "size": t.get("Size"),
                        "physical": self.store.physical_finger(uid, t["FingerID"])}
                       for t in sorted(templates.get(uid, []), key=lambda r: r["FingerID"])]
            cred = {"user_id": uid, "fingers": fingers, "card": u.get("CardNo") or None,
                    "has_code": bool(u.get("Password")), "authorized": uid in authorized}
            split = numbering.split(uid)
            if split is None:
                unassigned.append({**cred, "name": u.get("Name") or ""})
                continue
            num, digit = split
            person = persons.setdefault(num, {"num": num, "name": "", "categories": []})
            if u.get("Name") and (not person["name"] or digit == 0):
                person["name"] = u["Name"]
            cat = self.store.category(digit)
            person["categories"].append({**cred, "digit": digit,
                                         "category": cat["name"] if cat else f"Category {digit}",
                                         "type": cat["type"] if cat else None})
        for num, person in persons.items():
            meta = self.store.data["persons"].get(str(num), {})
            person["enabled"] = not self.store.is_disabled(num)
            person["ha_person"] = meta.get("ha_person")
            person["categories"].sort(key=lambda c: c["digit"])
        return {
            "numbering": self.store.data["numbering"],
            "persons": [persons[k] for k in sorted(persons)],
            "unassigned": unassigned,
            "counts": {
                "persons": len(persons),
                "user_ids": len(raw["users"]),
                "fingerprints": len(raw["templates"]),
                "cards": sum(1 for u in raw["users"] if u.get("CardNo")),
            },
        }

    def person_user_ids(self, person: int, raw_users: list[dict[str, Any]] | None = None) -> list[int]:
        users = raw_users if raw_users is not None else self.client.read_table("user", fields=["Pin"])
        numbering = self.numbering
        return [u["Pin"] for u in users if (numbering.split(u["Pin"]) or (None,))[0] == person]

    # ------------------------------------------------------------------- rights
    def apply_rights(self, user_id: int, digit: int, person: int) -> None:
        """Give or remove the right to open, according to the category's relay setting."""
        cat = self.store.category(digit) or {"relay": False}
        if cat.get("relay") and not self.store.is_disabled(person):
            if not self.client.get_authorizations(user_id):
                self.client.grant(user_id, 1, self.door_mask)
        else:
            self.client.revoke(user_id)

    # ------------------------------------------------------------------ persons
    def next_person(self) -> int:
        numbering = self.numbering
        used = {s[0] for u in self.client.read_table("user", fields=["Pin"])
                if (s := numbering.split(u["Pin"]))}
        used |= {int(k) for k in self.store.data["persons"]}
        for num in range(1, numbering.max_person + 1):
            if num not in used:
                return num
        raise SafetyError("no free person number")

    def ensure_user(self, person: int, digit: int) -> int:
        """Make sure the UserID of (person, category) exists, named and with its rights."""
        if self.store.category(digit) is None:
            raise SafetyError(f"category {digit} is not configured")
        user_id = self.numbering.user_id(person, digit)
        if self.client.get_user(user_id) is None:
            others = [self.client.get_user(u) for u in self.person_user_ids(person)]
            name = next((u.name for u in others if u and u.name), "")
            self.client.save_user(User(user_id=user_id, name=name), merge=False)
            self.apply_rights(user_id, digit, person)
        return user_id

    def person_save(self, person: int | None, name: str, ha_person: str | None) -> int:
        name = (name or "").strip()
        if len(name.encode()) > NAME_MAX:
            raise SafetyError(f"name too long (max {NAME_MAX} bytes)")
        if person is None:
            person = self.next_person()
        user_ids = self.person_user_ids(person)
        if not user_ids:
            user_ids = [self.ensure_user(person, 0)]
        for uid in user_ids:
            self.client.save_user(User(user_id=uid, name=name))
        self.store.person_meta(person)["ha_person"] = ha_person
        self.store.save()
        return person

    def person_delete(self, person: int) -> None:
        for uid in self.person_user_ids(person):
            self.client.delete_user(uid)
            self._forget_fingers(uid)
        self.store.data["persons"].pop(str(person), None)
        self.store.data["disabled"].pop(str(person), None)
        self.store.save()

    def person_enable(self, person: int, enabled: bool) -> None:
        key = str(person)
        if not enabled:
            if key in self.store.data["disabled"]:
                return
            removed = []
            for uid in self.person_user_ids(person):
                removed += [[a.user_id, a.timezone_id, a.door_mask] for a in self.client.revoke(uid)]
            self.store.data["disabled"][key] = removed
        else:
            saved = self.store.data["disabled"].pop(key, None)
            if saved is None:
                return
            for uid, tz, mask in saved:
                self.client.grant(uid, tz, mask)
        self.store.save()

    # ---------------------------------------------------------------- fingers
    def _forget_fingers(self, user_id: int) -> None:
        for key in [k for k in self.store.data["fingers"] if k.split(":")[0] == str(user_id)]:
            del self.store.data["fingers"][key]

    def finger_delete(self, user_id: int, finger_id: int) -> None:
        self.client.delete_templates(user_id, finger_id)
        self.store.set_physical_finger(user_id, finger_id, None)
        self.store.save()

    def finger_update(self, user_id: int, finger_id: int, digit: int | None = None,
                      physical: int | None = None) -> tuple[int, int]:
        """Move a fingerprint to another category of the same person and/or set its finger."""
        split = self.numbering.split(user_id)
        if split is None:
            raise SafetyError(f"UserID {user_id} does not belong to a person")
        person, current = split
        if digit is not None and digit != current:
            target = self.ensure_user(person, digit)
            old_physical = self.store.physical_finger(user_id, finger_id)
            new_slot = self.client.move_template(user_id, finger_id, target)
            self.store.set_physical_finger(user_id, finger_id, None)
            user_id, finger_id = target, new_slot
            if physical is None:
                physical = old_physical
        if physical is not None:
            self.store.set_physical_finger(user_id, finger_id, physical, self.person_user_ids(person))
        self.store.save()
        return user_id, finger_id

    def enroll_start(self, person: int, digit: int) -> tuple[int, list[int]]:
        """Prepare a fingerprint enrollment on the keypad: returns the UserID to type
        and the finger slots it already uses."""
        user_id = self.ensure_user(person, digit)
        baseline = [t.finger_id for t in self.client.get_templates(user_id, with_data=False)]
        return user_id, baseline

    def enroll_check(self, user_id: int, baseline: list[int]) -> int | None:
        """The finger slot of a newly enrolled template, if any."""
        for t in self.client.get_templates(user_id, with_data=False):
            if t.finger_id not in baseline:
                return t.finger_id
        return None

    # ------------------------------------------------------------- credentials
    def credential_set(self, person: int, digit: int, card: int | None | str = "keep",
                       code: str | None = "keep") -> int:
        """Set or remove the card (``None`` removes) and/or the keypad code of a category."""
        if card != "keep" and card:
            owner = [u.user_id for u in self.client.get_users() if u.card_no == card]
            if owner and owner != [self.numbering.user_id(person, digit)]:
                raise SafetyError(f"card {card} is already used by UserID {owner[0]}")
        if code not in ("keep", None) and (not str(code).isdigit() or not 1 <= len(str(code)) <= 6):
            raise SafetyError("the keypad code must be 1 to 6 digits")
        user_id = self.ensure_user(person, digit)
        changes: dict[str, Any] = {}
        if card != "keep":
            changes["card_no"] = int(card) if card else 0
        if code != "keep":
            changes["password"] = str(code) if code else ""
        if changes:
            self.client.save_user(User(user_id=user_id, **changes))
        return user_id

    # -------------------------------------------------------------- unassigned
    def assign_unassigned(self, user_id: int, person: int, digit: int) -> int:
        """Move a UserID that does not follow the numbering to (person, category)."""
        source = self.client.get_user(user_id)
        if source is None:
            raise SafetyError(f"UserID {user_id} does not exist")
        target = self.numbering.user_id(person, digit)
        if target == user_id:
            return target
        existed = self.client.get_user(target) is not None
        target = self.ensure_user(person, digit)
        if not existed and source.name and not self.client.get_user(target).name:
            self.person_save(person, source.name, self.store.person_meta(person).get("ha_person"))
        for t in self.client.get_templates(user_id, with_data=False):
            self.client.move_template(user_id, t.finger_id, target)
        card, code = source.card_no, source.password
        self.client.delete_user(user_id)
        changes: dict[str, Any] = {}
        if card:
            changes["card_no"] = card
        if code:
            changes["password"] = code
        if changes:
            self.client.save_user(User(user_id=target, **changes))
        self._forget_fingers(user_id)
        self.store.save()
        return target

    def delete_user_id(self, user_id: int) -> None:
        self.client.delete_user(user_id)
        self._forget_fingers(user_id)
        self.store.save()

    # -------------------------------------------------------------- categories
    def categories_save(self, categories: dict[str, dict[str, Any]], second_window: float | None) -> None:
        clean: dict[str, dict[str, Any]] = {}
        for digit, cat in categories.items():
            if not str(digit).isdigit() or not 0 <= int(digit) <= 9:
                raise SafetyError(f"invalid category digit {digit}")
            ctype = "open" if digit == "0" else cat.get("type", "custom")
            if ctype not in CATEGORY_TYPES:
                raise SafetyError(f"unknown category type {ctype}")
            name = (cat.get("name") or ctype.capitalize()).strip()[:32]
            second = cat.get("second")
            clean[str(digit)] = {"type": ctype, "name": name, "relay": bool(cat.get("relay")),
                                 "second": None if second in (None, "") else int(second)}
        clean.setdefault("0", {"type": "open", "name": "Open", "relay": True, "second": None})
        for digit, cat in clean.items():
            if cat["second"] is not None and str(cat["second"]) not in clean:
                raise SafetyError(f"second presentation of {digit} points to an unknown category")
        old = self.store.categories
        self.store.data["categories"] = clean
        if second_window is not None:
            self.store.data["second_window"] = max(1.0, min(10.0, float(second_window)))
        # Re-apply the relay setting where it changed.
        numbering = self.numbering
        for u in self.client.read_table("user", fields=["Pin"]):
            split = numbering.split(u["Pin"])
            if not split:
                continue
            digit = str(split[1])
            if digit in clean and (digit not in old or old[digit].get("relay") != clean[digit]["relay"]):
                self.apply_rights(u["Pin"], split[1], split[0])
        self.store.save()

    # -------------------------------------------------------------------- door
    def door_get(self, door: int) -> dict[str, Any]:
        return vars(self.client.get_door_settings(door))

    def door_set(self, door: int, values: dict[str, Any]) -> None:
        allowed = set(DoorSettings.PARAMS)
        self.client.set_door_settings(DoorSettings(door=door, **{k: int(v) for k, v in values.items()
                                                                 if k in allowed and v is not None}))

    def door_control(self, door: int, action: str, seconds: int = 5) -> None:
        if action == "open":
            self.client.open_door(door, seconds)
        elif action == "hold":
            self.client.hold_door_open(door)
        elif action == "close":
            self.client.close_door(door)
        elif action == "cancel_alarm":
            self.client.cancel_alarm()
        else:
            raise SafetyError(f"unknown door action {action}")

    # ----------------------------------------------------------------- backups
    def _integration_state(self) -> dict[str, Any]:
        d = self.store.data
        return {k: d[k] for k in ("numbering", "second_window", "categories", "persons",
                                  "disabled", "fingers")}

    def backup_create(self, reason: str) -> str:
        snap = zk_backup.backup(self.client)
        snap["integration"] = self._integration_state()
        snap["reason"] = reason
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.backup_dir, 0o700)
        path = self.backup_dir / f"backup_{datetime.now():%Y%m%d_%H%M%S}.json"
        zk_backup.save(snap, path)
        path.chmod(0o600)
        keep = int(self.store.data["backup"].get("keep", 30))
        for old in sorted(self.backup_dir.glob("backup_*.json"))[:-keep]:
            old.unlink()
        return path.name

    def backup_list(self) -> list[dict[str, Any]]:
        out = []
        for path in sorted(self.backup_dir.glob("backup_*.json"), reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            tables = data.get("tables", {})
            out.append({"name": path.name, "created": data.get("created"), "reason": data.get("reason", ""),
                        "users": len(tables.get("user", [])),
                        "fingerprints": len(tables.get("templatev10", []))})
        return out

    def _backup_path(self, name: str) -> Path:
        path = (self.backup_dir / name).resolve()
        if path.parent != self.backup_dir.resolve() or not path.name.startswith("backup_"):
            raise SafetyError("invalid backup name")
        return path

    def backup_read(self, name: str) -> str:
        return self._backup_path(name).read_text(encoding="utf-8")

    def backup_restore(self, content: str, mode: str) -> dict[str, int]:
        snap = json.loads(content)
        if snap.get("format") != zk_backup.FORMAT:
            raise SafetyError("not a pyzkaccess-native backup")
        self.backup_create("before restore")
        result = zk_backup.restore(self.client, snap, wipe=(mode == "replace"))
        state = snap.get("integration")
        if state:
            for key, value in state.items():
                if mode == "replace" or key not in ("persons", "fingers", "disabled"):
                    self.store.data[key] = value
                else:
                    self.store.data[key].update(value)
            self.store.save()
        return result

    def backup_import(self, content: str) -> str:
        snap = json.loads(content)
        if snap.get("format") != zk_backup.FORMAT:
            raise SafetyError("not a pyzkaccess-native backup")
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        path = self.backup_dir / f"backup_{datetime.now():%Y%m%d_%H%M%S}_imported.json"
        path.write_text(content, encoding="utf-8")
        path.chmod(0o600)
        return path.name
