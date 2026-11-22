"""Integration-side data that the device cannot hold.

The device stores users, credentials and rights. Home Assistant keeps what
gives them meaning: what each category digit means, which physical finger a
template is, the rights a disabled person had, the link to a Home Assistant
person, and the activity history.
"""
from __future__ import annotations

import copy
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (DEFAULT_CATEGORIES, DEFAULT_SECOND_WINDOW, DOMAIN, HISTORY_SIZE,
                    NUMBERING_CATEGORY)

STORAGE_VERSION = 1

DEFAULTS: dict[str, Any] = {
    "numbering": NUMBERING_CATEGORY,
    "second_window": DEFAULT_SECOND_WINDOW,
    "categories": DEFAULT_CATEGORIES,
    # person number (str) -> {"ha_person": entity_id | None}
    "persons": {},
    # person number (str) -> list of [user_id, timezone_id, door_mask] removed when disabled
    "disabled": {},
    # person number (str) -> category digits switched off for that person
    "categories_off": {},
    # "user_id:finger_id" -> physical finger 0-9 (0 = left little ... 9 = right little)
    "fingers": {},
    # UserIDs already seen (to notify about users enrolled on the keypad)
    "known_user_ids": None,
    # NFC tag id -> {"door": 1}: Home Assistant tags that act as readers for phones
    "tags": {},
    # phone (Home Assistant device id) -> {"person": n, "digit": d, "name": str, "enabled": bool}
    "phones": {},
    "backup": {"on_change": True, "nightly": True, "keep": 30},
    "notify_keypad": True,
    "history": [],
}


class AccessStore:
    """Persistent JSON store, one per configured device."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")
        self.data: dict[str, Any] = copy.deepcopy(DEFAULTS)

    async def async_load(self) -> None:
        stored = await self._store.async_load()
        if stored:
            for key, value in stored.items():
                self.data[key] = value
        self.data.setdefault("categories", copy.deepcopy(DEFAULT_CATEGORIES))
        self.data["categories"].setdefault("0", copy.deepcopy(DEFAULT_CATEGORIES["0"]))

    def save(self, delay: float = 1.0) -> None:
        self._store.async_delay_save(lambda: self.data, delay)

    async def async_save_now(self) -> None:
        """Write to disk at once (for state that must survive a crash)."""
        await self._store.async_save(self.data)

    async def async_remove(self) -> None:
        await self._store.async_remove()

    # ------------------------------------------------------------------ helpers
    @property
    def categories(self) -> dict[str, dict[str, Any]]:
        return self.data["categories"]

    def category(self, digit: int) -> dict[str, Any] | None:
        return self.categories.get(str(digit))

    def category_name(self, digit: int) -> str:
        cat = self.category(digit)
        return cat["name"] if cat else f"Category {digit}"

    def person_meta(self, person: int) -> dict[str, Any]:
        return self.data["persons"].setdefault(str(person), {"ha_person": None})

    def is_disabled(self, person: int) -> bool:
        return str(person) in self.data["disabled"]

    def is_category_off(self, person: int, digit: int | None) -> bool:
        return digit is not None and int(digit) in self.data.get("categories_off", {}).get(str(person), [])

    def physical_finger(self, user_id: int, finger_id: int) -> int | None:
        return self.data["fingers"].get(f"{user_id}:{finger_id}")

    def set_physical_finger(self, user_id: int, finger_id: int, physical: int | None,
                            person_user_ids: list[int] | None = None) -> None:
        """Record which physical finger a template is. A physical finger belongs to
        one template only, among all the UserIDs of the same person."""
        key = f"{user_id}:{finger_id}"
        if physical is None:
            self.data["fingers"].pop(key, None)
            return
        owners = {str(u) for u in (person_user_ids or [user_id])}
        for other, value in list(self.data["fingers"].items()):
            if value == physical and other.split(":")[0] in owners:
                del self.data["fingers"][other]
        self.data["fingers"][key] = physical

    def add_history(self, item: dict[str, Any]) -> None:
        history = self.data["history"]
        history.append(item)
        del history[:-HISTORY_SIZE]
