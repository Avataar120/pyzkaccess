"""Phones: a Home Assistant NFC tag scanned by a paired phone acts like a badge."""
from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.core import Event
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (CONF_OPEN_SECONDS, DEFAULT_OPEN_SECONDS, PHONE_ECHO_SECONDS, PHONE_PAIR_TIMEOUT_SECONDS,
                    RESULT_ACTION, RESULT_DENIED, RESULT_GRANTED)
from .helpers import signal_changed

_LOGGER = logging.getLogger(__package__ + ".coordinator")


class PhoneMixin:
    """Part of ZKCoordinator (mixed in); uses its client, store, manager and snapshot."""

    # ------------------------------------------------------------------- phones
    def start_phone_pairing(self, person: int, digit: int) -> dict[str, Any]:
        self.phone_pairing = {"person": person, "digit": digit, "state": "waiting",
                              "until": time.monotonic() + PHONE_PAIR_TIMEOUT_SECONDS}
        return self.phone_pairing

    async def _on_tag(self, event: Event) -> None:
        """A Home Assistant tag was scanned: phone pairing or phone access."""
        tag_id = event.data.get("tag_id")
        device_id = event.data.get("device_id")
        tag = self.store.data["tags"].get(str(tag_id))
        if tag is None or not device_id:
            return
        entry_id = self.config_entry.entry_id
        device = dr.async_get(self.hass).async_get(device_id)
        device_name = (device.name_by_user or device.name) if device else device_id
        pairing = self.phone_pairing
        if pairing and time.monotonic() < pairing["until"]:
            self.store.data["phones"][device_id] = {"person": pairing["person"], "digit": pairing["digit"],
                                                    "name": device_name, "enabled": True}
            self.store.save()
            self.phone_pairing = None
            self._snapshot_at = None
            await self.async_refresh_snapshot(silent=True)
            async_dispatcher_send(self.hass, signal_changed(entry_id),
                                  {"pair": {"state": "done", "device_id": device_id, "name": device_name}})
            return
        door = int(tag.get("door", 1))
        phone = self.store.data["phones"].get(device_id)
        who = None
        digit = cat = None
        if phone:
            digit = phone["digit"]
            cat = self.store.category(digit)
            person = self.snapshot and next((p for p in self.snapshot["persons"] if p["num"] == phone["person"]), None)
            who = {"person": phone["person"], "person_name": person["name"] if person else None,
                   "ha_person": person["ha_person"] if person else None}
        allowed = bool(phone and phone.get("enabled", True) and cat and not self.store.is_disabled(phone["person"])
                       and not self.store.is_category_off(phone["person"], digit))
        result = RESULT_ACTION if allowed else RESULT_DENIED
        digit, cat, gesture, toggled = self._resolve(digit, cat, result, ("phone", device_id))
        if allowed and self.store.is_category_off(phone["person"], digit):
            allowed, result = False, RESULT_DENIED
        if allowed and (cat or {}).get("relay"):
            result = RESULT_GRANTED  # the integration releases the door itself (below)
        user_id = None
        if who and digit is not None:
            try:
                user_id = self.manager.numbering.user_id(who["person"], digit)
            except Exception:  # noqa: BLE001 - informative only
                user_id = None
        ev = self._event(result=result, code=None,
                         event_name="Phone access" if result != RESULT_DENIED else "Phone refused",
                         user_id=user_id, who=who, digit=digit, cat=cat, gesture=gesture, toggled=toggled,
                         method="phone", card=None, door=door, verify_mode=None, when=None, phone=device_name)
        self._publish([ev])
        if result == RESULT_GRANTED and (cat or {}).get("relay"):
            seconds = int(self.config_entry.options.get(CONF_OPEN_SECONDS, DEFAULT_OPEN_SECONDS))
            try:
                self._phone_echo[door] = time.monotonic() + PHONE_ECHO_SECONDS
                await self.async_run(self.manager.door_control, door, "open", seconds, refresh=False)
            except HomeAssistantError as ex:
                _LOGGER.warning("Phone access: cannot open door %s: %s", door, ex)
