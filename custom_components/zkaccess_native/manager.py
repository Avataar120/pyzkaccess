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

from .const import CARDS_PER_CATEGORY, CATEGORY_TYPES, EXTRA_CARD_BASE, IDENTIFY_PINS, NUMBERING_PLAIN
from .journal import Journal, JournalError, State
from .store import AccessStore

_LOGGER = logging.getLogger(__name__)

#: Longest name the device is known to keep.
NAME_MAX = 24
#: Device fields read for the snapshot (fingerprint data is never needed there).
TEMPLATE_LIGHT = ["UID", "Pin", "FingerID", "Size", "Valid"]


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
        """``(person, category)`` of a UserID, extra card UserIDs included."""
        if self.plain:
            return (user_id, 0) if user_id > 0 else None
        return self._scheme.split(self.base(user_id))

    @property
    def max_cards(self) -> int:
        return 1 if self.plain else CARDS_PER_CATEGORY

    def card_user_id(self, person: int, category: int, slot: int) -> int | None:
        """UserID holding card ``slot`` (0 = the category's own UserID) of (person, category);
        ``None`` when that slot does not exist (person 99: 9990-9999 are reserved)."""
        user_id = self.user_id(person, category)
        if not slot:
            return user_id
        if slot >= self.max_cards or EXTRA_CARD_BASE + user_id in IDENTIFY_PINS:
            return None
        return EXTRA_CARD_BASE + user_id

    def card_slot(self, user_id: int) -> int:
        """0 for a category's own UserID, n for the UserID of its card n + 1."""
        if self.plain or user_id in IDENTIFY_PINS:
            return 0
        base = user_id - EXTRA_CARD_BASE
        return 1 if 0 <= base < EXTRA_CARD_BASE and self._scheme.split(base) else 0

    def base(self, user_id: int) -> int:
        """The category's own UserID (itself, or the one an extra card UserID belongs to)."""
        return user_id - EXTRA_CARD_BASE if self.card_slot(user_id) else user_id


class AccessManager:
    """Device operations for one configured device."""

    def __init__(self, client: ZKAccess, store: AccessStore, backup_dir: Path,
                 door_count: Callable[[], int]) -> None:
        self.client = client
        self.store = store
        self.backup_dir = backup_dir
        self._door_count = door_count
        #: every operation that touches fingerprints or rewrites users goes through it
        self.journal = Journal(backup_dir / "journal.json")

    @staticmethod
    def _created(state: State, user_id: int) -> None:
        """Expected state: ``user_id`` exists after the operation (it may be created)."""
        entry = state[str(user_id)]
        if entry["user"] is None:
            entry["user"] = {"Pin": user_id}

    @property
    def numbering(self) -> Numbering:
        return Numbering(self.store.data["numbering"])

    @property
    def door_mask(self) -> int:
        return (1 << max(1, self._door_count())) - 1

    # ------------------------------------------------------------------ reading
    def read_raw(self) -> dict[str, list[dict[str, Any]]]:
        """Users, templates (without data) and authorizations, as device rows
        (the temporary UserIDs of an identification are left out)."""
        dev = self.client

        def keep(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [r for r in rows if r.get("Pin") not in IDENTIFY_PINS]
        return {
            "users": keep(dev.read_table("user")),
            "templates": keep(dev.read_table("templatev10", fields=TEMPLATE_LIGHT)),
            "auths": keep(dev.read_table("userauthorize")),
        }

    # ----------------------------------------------------------- identification
    @staticmethod
    def _template_row(user_id: int, finger_id: int, source: Template, slot: int) -> dict[str, Any]:
        """A templatev10 row written directly, with the internal slot already known (what
        save_template would send, without re-reading the user table)."""
        row = Template(user_id=user_id, finger_id=finger_id, template=source.template, valid=source.valid).to_row()
        row["UID"] = slot
        row.setdefault("EndTag", 13)
        return row

    def identify_copy(self, user_id: int) -> tuple[dict[int, int], dict[int, Template], int]:
        """Step 1 of the isolation: copy each template of ``user_id`` to its own temporary
        UserID, without access rights (the device then reports that UserID for this finger).
        Returns ``({temporary UserID: finger slot}, {finger slot: template}, slot of user_id)``.
        Nothing is removed here; on error the temporary UserIDs are deleted again.

        Few exchanges with the device (each costs about half a second on some readers):
        2 reads, then per template 1 user write, 1 read for the slots, 1 template write."""
        templates = self.client.get_templates(user_id)
        if len(templates) < 2:
            raise SafetyError(f"UserID {user_id} has fewer than 2 fingerprints")
        users = self.client.read_table("user", fields=["UID", "Pin"])
        existing = {u["Pin"] for u in users}
        owner_slot = next((u.get("UID") for u in users if u["Pin"] == user_id), None)
        if owner_slot is None:
            raise SafetyError(f"UserID {user_id} does not exist")
        free = [pin for pin in IDENTIFY_PINS if pin not in existing]
        if len(free) < len(templates):
            raise SafetyError("not enough free temporary UserIDs (9990-9999)")
        # Journal first: the templates (data included) are on disk before anything changes,
        # and the end of the identification must give back exactly this state.
        before = self.journal.begin(self.client, f"identify {user_id}", [user_id, *IDENTIFY_PINS])
        mapping: dict[int, int] = {}
        try:
            for t, pin in zip(templates, free):
                self.client.write_records("user", [{"Pin": pin, "Name": "identify"}])
                mapping[pin] = t.finger_id
            slots = {u["Pin"]: u.get("UID") for u in self.client.read_table("user", fields=["UID", "Pin"])}
            taken = [v for v in slots.values() if v is not None]
            for pin in mapping:
                # Same guard as save_template: a template must never land on a shared slot.
                if slots.get(pin) is None or taken.count(slots[pin]) > 1:
                    raise SafetyError(f"temporary UserID {pin} got no unique internal slot")
            for t, pin in zip(templates, mapping):
                self.client.write_records("templatev10", [self._known_template(self._template_row(pin, 0, t, slots[pin]))])
        except Exception:
            try:
                self.journal.abort(self.client, f"identify {user_id}", before)
            except Exception:  # noqa: BLE001 - journal kept, settled by the coordinator
                _LOGGER.exception("identify %s: putting back after a failed copy failed (journal kept)", user_id)
            raise
        return mapping, {t.finger_id: t for t in templates}, owner_slot

    def _known_template(self, row: dict[str, Any]) -> dict[str, Any]:
        names = set(self.client.table("templatev10").field_names)
        return {k: v for k, v in row.items() if k in names}

    def _delete_temporary(self, pin: int) -> None:
        """A temporary UserID never had rights: its templates and its record (2 requests)."""
        self.client.delete_records("templatev10", {"Pin": pin})
        self.client.delete_records("user", {"Pin": pin})

    def identify_hide(self, user_id: int) -> None:
        """Step 2 of the isolation: remove the templates of ``user_id`` (they are
        kept on the temporary UserIDs), so the device reports the temporary UserID."""
        self.client.delete_templates(user_id)

    def identify_restore(self, user_id: int, mapping: dict[int, int],
                         templates: dict[int, Template] | None = None, slot: int | None = None) -> None:
        """Put the fingerprints back (see :meth:`_identify_restore`), then check the result
        against the journal: exactly the same templates, bound to the user's slot, no
        temporary UserID left. Otherwise everything is written back from the journal."""
        op = f"identify {user_id}"
        before = self.journal.load()["before"] if self.journal.exists() else None
        try:
            self._identify_restore(user_id, mapping, templates, slot)
        except Exception:
            if before is None:
                raise
            _LOGGER.exception("%s: putting the fingerprints back failed; writing them back from the journal", op)
            self.journal.abort(self.client, op, before)
            return
        if before is not None:
            try:
                self.journal.finish(self.client, op, before, before)
            except JournalError:
                raise
            except SafetyError as ex:  # a difference was found and written back: done
                _LOGGER.warning("%s", ex)

    def _identify_restore(self, user_id: int, mapping: dict[int, int],
                          templates: dict[int, Template] | None = None, slot: int | None = None) -> None:
        """Put the templates back on ``user_id``, check them, then delete the temporary
        UserIDs. Safe to run again after a partial restore. With ``templates`` and ``slot``
        (kept from the copy) nothing is re-read but the check; without them (after a
        restart) the templates are read back from the temporary UserIDs."""
        current = self.client.get_templates(user_id, with_data=False)
        present = {t.finger_id for t in current}
        missing_fingers = [(pin, finger) for pin, finger in mapping.items() if finger not in present]
        if missing_fingers:
            if slot is None:
                slot = next((u.get("UID") for u in self.client.read_table("user", fields=["UID", "Pin"])
                             if u["Pin"] == user_id), None)
                if slot is None:
                    raise SafetyError(f"UserID {user_id} does not exist")
            for pin, finger in missing_fingers:
                source = (templates or {}).get(finger)
                if source is None:
                    found = self.client.get_templates(pin)
                    if not found:
                        raise SafetyError(f"temporary UserID {pin} has no template (finger {finger} of {user_id})")
                    source = found[0]
                self.client.write_records("templatev10", [self._known_template(self._template_row(user_id, finger, source, slot))])
            current = self.client.get_templates(user_id, with_data=False)
            present = {t.finger_id for t in current}
        missing = set(mapping.values()) - present
        if missing:
            raise SafetyError(f"fingers {sorted(missing)} of UserID {user_id} could not be restored")
        if slot is not None and any(t.slot is not None and t.slot != slot for t in current):
            # A template bound to another internal slot matches someone else: bind it back.
            self.client.rebind_templates(user_id, slot)
        for pin in mapping:
            self._delete_temporary(pin)

    def repair_slots(self, user_ids: list[int]) -> list[int]:
        """Rebind templates bound to another user's slot (journaled: same templates after)."""
        return self.journal.run(self.client, f"repair slots of {user_ids}", user_ids,
                                self.client.repair_template_slots, strict=False)

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
        extra_cards: dict[int, list[dict[str, Any]]] = {}
        for u in sorted(raw["users"], key=lambda r: r["Pin"]):
            uid = u["Pin"]
            slot = numbering.card_slot(uid)
            if slot:
                # Extra card of a category: shown on the category, not as a UserID of its own.
                extra_cards.setdefault(numbering.base(uid), []).append(
                    {"slot": slot, "user_id": uid, "card": u.get("CardNo") or None,
                     "authorized": uid in authorized})
                continue
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
            for cat in person["categories"]:
                cat["cards"] = [{"slot": 0, "user_id": cat["user_id"], "card": cat["card"],
                                 "authorized": cat["authorized"]}] + extra_cards.pop(cat["user_id"], [])
            meta = self.store.data["persons"].get(str(num), {})
            person["phones"] = [{"device_id": dev, **{k: v for k, v in ph.items() if k != "person"}}
                                for dev, ph in self.store.data["phones"].items() if ph["person"] == num]
            person["enabled"] = not self.store.is_disabled(num)
            person["categories_off"] = list(self.store.data.get("categories_off", {}).get(str(num), []))
            person["ha_person"] = meta.get("ha_person")
            person["categories"].sort(key=lambda c: c["digit"])
        for base, cards in extra_cards.items():
            # Extra card UserID whose category UserID is gone: listed as not assigned.
            for c in cards:
                unassigned.append({"user_id": c["user_id"], "fingers": [], "card": c["card"], "has_code": False,
                                   "authorized": c["authorized"], "name": f"card {c['slot'] + 1} of {base}"})
        return {
            "numbering": self.store.data["numbering"],
            "persons": [persons[k] for k in sorted(persons)],
            "unassigned": sorted(unassigned, key=lambda u: u["user_id"]),
            "max_cards": numbering.max_cards,
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
        if cat.get("relay") and not self.store.is_disabled(person) and not self.store.is_category_off(person, digit):
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
        first = self.numbering.user_id(person, 0)
        scope = self.person_user_ids(person) or [first]

        def rename() -> None:
            # Rewriting a user record can move its internal slot on the MA500: journaled.
            user_ids = self.person_user_ids(person) or [self.ensure_user(person, 0)]
            for uid in user_ids:
                self.client.save_user(User(user_id=uid, name=name))
        self.journal.run(self.client, f"rename person {person}", scope, rename,
                         lambda st, _r: self._created(st, first) if first in scope else None)
        self.store.person_meta(person)["ha_person"] = ha_person
        self.store.save()
        return person

    def person_delete(self, person: int) -> None:
        for uid in self.person_user_ids(person):
            self.client.delete_user(uid)
            self._forget_fingers(uid)
        self.store.data["persons"].pop(str(person), None)
        self.store.data["disabled"].pop(str(person), None)
        for device_id in [d for d, p in self.store.data["phones"].items() if p["person"] == person]:
            del self.store.data["phones"][device_id]
        self.store.save()

    # ------------------------------------------------------------------ phones
    def phone_update(self, device_id: str, digit: int | None = None, enabled: bool | None = None,
                     name: str | None = None) -> None:
        phone = self.store.data["phones"].get(device_id)
        if phone is None:
            raise SafetyError("unknown phone")
        if digit is not None:
            if self.store.category(digit) is None:
                raise SafetyError(f"category {digit} is not configured")
            phone["digit"] = int(digit)
        if enabled is not None:
            phone["enabled"] = bool(enabled)
        if name:
            phone["name"] = name.strip()[:40]
        self.store.save()

    def phone_remove(self, device_id: str) -> None:
        self.store.data["phones"].pop(device_id, None)
        self.store.save()

    def tags_save(self, tags: dict[str, dict[str, Any]]) -> None:
        self.store.data["tags"] = {str(t): {"door": int(v.get("door", 1))} for t, v in tags.items()}
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
                split = self.numbering.split(uid)
                if split and self.store.is_category_off(*split):
                    continue  # category switched off for this person meanwhile
                self.client.grant(uid, tz, mask)
            # Categories switched back on while the person was disabled get their rights too.
            for uid in self.person_user_ids(person):
                split = self.numbering.split(uid)
                if split:
                    self.apply_rights(uid, split[1], person)
        self.store.save()

    def category_enable(self, person: int, digit: int, enabled: bool) -> None:
        """Switch one category on or off for one person. Off: the device refuses its
        credentials (rights removed) and Home Assistant treats them as denied."""
        if self.store.category(digit) is None:
            raise SafetyError(f"category {digit} is not configured")
        off = set(self.store.data.setdefault("categories_off", {}).get(str(person), []))
        off.discard(digit) if enabled else off.add(digit)
        if off:
            self.store.data["categories_off"][str(person)] = sorted(off)
        else:
            self.store.data["categories_off"].pop(str(person), None)
        if not self.store.is_disabled(person):
            for slot in range(self.numbering.max_cards):
                user_id = self.numbering.card_user_id(person, digit, slot)
                if user_id is not None and self.client.get_user(user_id) is not None:
                    self.apply_rights(user_id, digit, person)
        self.store.save()

    # ---------------------------------------------------------------- fingers
    def _forget_fingers(self, user_id: int) -> None:
        for key in [k for k in self.store.data["fingers"] if k.split(":")[0] == str(user_id)]:
            del self.store.data["fingers"][key]

    def finger_delete(self, user_id: int, finger_id: int) -> None:
        def change(state: State, _result: Any) -> None:
            state[str(user_id)]["templates"].pop(str(finger_id), None)
        self.journal.run(self.client, f"delete fingerprint {user_id}/{finger_id}", [user_id],
                         lambda: self.client.delete_templates(user_id, finger_id), change)
        self.store.set_physical_finger(user_id, finger_id, None)
        self.store.save()

    def finger_place(self, user_id: int, finger_id: int, physical: int | None,
                     person_user_ids: list[int]) -> None:
        """Set (or clear, with ``None``) the physical finger of a template. Integration-side only."""
        if physical is None:
            self.store.set_physical_finger(user_id, finger_id, None)
        else:
            self.store.set_physical_finger(user_id, finger_id, physical, person_user_ids or [user_id])
        self.store.save()

    def finger_update(self, user_id: int, finger_id: int, digit: int | None = None,
                      physical: int | None = None) -> tuple[int, int]:
        """Move a fingerprint to another category of the same person and/or set its finger."""
        split = self.numbering.split(user_id)
        if split is None:
            raise SafetyError(f"UserID {user_id} does not belong to a person")
        person, current = split
        if digit is not None and digit != current:
            target = self.numbering.user_id(person, digit)
            source = user_id

            def move() -> int:
                self.ensure_user(person, digit)
                return self.client.move_template(source, finger_id, target)

            def change(state: State, new_slot: int) -> None:
                moved = state[str(source)]["templates"].pop(str(finger_id))
                self._created(state, target)
                state[str(target)]["templates"][str(new_slot)] = moved
            old_physical = self.store.physical_finger(user_id, finger_id)
            new_slot = self.journal.run(self.client, f"move fingerprint {source}/{finger_id} to {target}",
                                        [source, target], move, change)
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
        user_id = self.numbering.user_id(person, digit)
        # Creating a user: the device may hand out a slot another user has (journaled check).
        self.journal.run(self.client, f"prepare enrollment {user_id}", [user_id],
                         lambda: self.ensure_user(person, digit), lambda st, _r: self._created(st, user_id))
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
                       code: str | None = "keep", slot: int | None = 0) -> int:
        """Set or remove (``None``) card ``slot`` (0 = first card, ``None`` = first free one)
        and/or the keypad code of a category. Card 1 and the code are on the category's
        UserID; card 2 on its extra UserID (see ``EXTRA_CARD_BASE``), created and deleted
        with the card. Returns the UserID written."""
        numbering = self.numbering
        main = numbering.user_id(person, digit)
        if slot is None:
            if card in ("keep", None):
                raise SafetyError("a card slot is needed to remove a card")
            users = {u.user_id: u for u in self.client.get_users()}
            slots = {s: numbering.card_user_id(person, digit, s) for s in range(numbering.max_cards)}
            free = [s for s, uid in slots.items()
                    if uid is not None and not (users.get(uid) or User(user_id=0)).card_no]
            if not free:
                raise SafetyError("all cards of this category are used: remove or replace one")
            slot = free[0]
        target = numbering.card_user_id(person, digit, slot)
        if target is None:
            raise SafetyError(f"card {slot + 1} is not available for person {person}"
                              + (f": UserID {EXTRA_CARD_BASE + main} is reserved for finger identification"
                                 if slot == 1 and not numbering.plain else ""))
        if slot and code != "keep":
            raise SafetyError("the keypad code belongs to the category's own UserID (card 1)")
        if card != "keep" and card:
            owner = [u.user_id for u in self.client.get_users() if u.card_no == card]
            if owner and owner != [target]:
                raise SafetyError(f"card {card} is already used by UserID {owner[0]}")
        if code not in ("keep", None) and (not str(code).isdigit() or not 1 <= len(str(code)) <= 6):
            raise SafetyError("the keypad code must be 1 to 6 digits")
        if slot:
            return self._extra_card_set(person, digit, target, card)
        changes: dict[str, Any] = {}
        if card != "keep":
            changes["card_no"] = int(card) if card else 0
        if code != "keep":
            changes["password"] = str(code) if code else ""

        def write() -> None:
            self.ensure_user(person, digit)
            if changes:
                self.client.save_user(User(user_id=main, **changes))
        self.journal.run(self.client, f"card/code of {main}", [main], write,
                         lambda st, _r: self._created(st, main))
        return main

    def _extra_card_set(self, person: int, digit: int, user_id: int, card: int | None | str) -> int:
        """Card on an extra UserID: created with the card (name and rights of the category),
        deleted with it."""
        if card == "keep":
            return user_id
        main = self.numbering.user_id(person, digit)
        if not card:
            if self.client.get_user(user_id) is not None:
                self.client.delete_user(user_id)
            return user_id

        def write() -> None:
            self.ensure_user(person, digit)
            name = (self.client.get_user(main) or User(user_id=main)).name
            existed = self.client.get_user(user_id) is not None
            self.client.save_user(User(user_id=user_id, name=name, card_no=int(card)), merge=existed)
            self.apply_rights(user_id, digit, person)
        self.journal.run(self.client, f"card of {user_id}", [main, user_id], write,
                         lambda st, _r: (self._created(st, main), self._created(st, user_id)))
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
        if self.store.category(digit) is None:
            raise SafetyError(f"category {digit} is not configured")
        existed = self.client.get_user(target) is not None
        scope = sorted({user_id, target, *self.person_user_ids(person)})

        def assign() -> dict[int, int]:
            self.ensure_user(person, digit)
            if not existed and source.name and not self.client.get_user(target).name:
                for uid in self.person_user_ids(person):
                    self.client.save_user(User(user_id=uid, name=source.name))
            moved = self.client.move_templates(user_id, target)
            self.client.delete_user(user_id)
            changes: dict[str, Any] = {}
            if source.card_no:
                changes["card_no"] = source.card_no
            if source.password:
                changes["password"] = source.password
            if changes:
                self.client.save_user(User(user_id=target, **changes))
            return moved

        def change(state: State, moved: dict[int, int]) -> None:
            templates = state[str(user_id)]["templates"]
            self._created(state, target)
            for old_slot, new_slot in moved.items():
                state[str(target)]["templates"][str(new_slot)] = templates[str(old_slot)]
            state[str(user_id)] = {"user": None, "auths": [], "templates": {}}
        moved = self.journal.run(self.client, f"assign UserID {user_id} to {target}", scope, assign, change)
        for old_slot, new_slot in moved.items():
            physical = self.store.physical_finger(user_id, old_slot)
            if physical is not None:
                self.store.set_physical_finger(target, new_slot, physical, self.person_user_ids(person))
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
            entry = {"type": ctype, "name": name, "relay": bool(cat.get("relay")),
                     "second": None if second in (None, "") else int(second)}
            if ctype == "toggle":
                if not cat.get("entity_id"):
                    raise SafetyError(f"toggle category {digit} needs an entity")
                entry.update(entity_id=str(cat["entity_id"]), on=int(cat["on"]), off=int(cat["off"]))
            clean[str(digit)] = entry
        clean.setdefault("0", {"type": "open", "name": "Open", "relay": True, "second": None})
        for digit, cat in clean.items():
            if cat["second"] is not None and str(cat["second"]) not in clean:
                raise SafetyError(f"second presentation of {digit} points to an unknown category")
            if cat["type"] == "toggle":
                for key in ("on", "off"):
                    target = clean.get(str(cat[key]))
                    if target is None or target["type"] == "toggle":
                        raise SafetyError(f"toggle category {digit}: '{key}' must be a non-toggle category")
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
                                  "disabled", "fingers", "tags", "phones")}

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
                if mode == "replace" or key not in ("persons", "fingers", "disabled", "tags", "phones"):
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
