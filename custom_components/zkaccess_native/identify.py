"""Finger identification: the reader used as a sensor, with the temporary isolation of a
UserID that has several fingerprints."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from pyzkaccess_native import AccessEvent

from .const import DOMAIN, IDENTIFY_STEP2_TIMEOUT_SECONDS, IDENTIFY_TIMEOUT_SECONDS
from .helpers import access_method, signal_changed

_LOGGER = logging.getLogger(__package__ + ".coordinator")


class IdentifyMixin:
    """Part of ZKCoordinator (mixed in); uses its client, store, manager and snapshot."""

    # ----------------------------------------------------------- identification
    def start_identify(self, person: Optional[int], physical: Optional[int]) -> dict[str, Any]:
        if self.identify is not None:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="identify_running")
        if self.store.data.get("identify_pending"):
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="identify_undoing")
        if self.enrollment is not None:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="enroll_running")
        self.identify = {"state": "waiting", "step": 1, "person": person, "physical": physical,
                         "started": time.monotonic()}
        self._identify_notify()
        return self._identify_public()

    def _identify_public(self, idf: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        idf = idf if idf is not None else (self.identify or {})
        return {k: v for k, v in idf.items() if k not in ("started", "mapping")}

    def _identify_notify(self, idf: Optional[dict[str, Any]] = None) -> None:
        async_dispatcher_send(self.hass, signal_changed(self.config_entry.entry_id),
                              {"identify": self._identify_public(idf)})

    def _fingers_of(self, user_id: int) -> list[int]:
        for p in (self.snapshot or {}).get("persons", []):
            for c in p["categories"]:
                if c["user_id"] == user_id:
                    return [f["finger_id"] for f in c["fingers"]]
        return []

    @callback
    def _identify_event(self, ev: AccessEvent) -> None:
        idf = self.identify
        if idf is None or idf["state"] not in ("waiting", "again", "unknown", "notyet", "other"):
            return  # busy (isolating / restoring)
        if access_method(ev) in ("card", "code", "remote"):
            return
        uid = ev.user_id or 0
        _LOGGER.info("Identification step %s: the device reported UserID %s (event %s)",
                     idf["step"], uid, int(ev.event))
        if idf["step"] == 2:
            mapping = idf.get("mapping") or {}
            if uid in mapping:
                # The answer is known: show it at once, put the fingerprints back behind it.
                user_id = idf["user_id"]
                self._identify_found(user_id, mapping[uid])
                self._restore_task = self.hass.async_create_task(self._async_identify_restore())
            elif uid and uid != idf["user_id"] and idf.get("last_other") == uid:
                # The same other UserID twice: really another finger than the one of step 1.
                idf.update(state="other")
                self._identify_notify()
            else:
                # Not conclusive: a copy written on a new UserID can take a few tens of seconds
                # to be matched by the MA500 (unknown finger, or the former owner meanwhile),
                # and a finger badly placed gives the same. Ask again, decide on a later reading.
                idf["last_other"] = uid if uid and uid != idf["user_id"] else None
                idf.update(state="notyet")
                self._identify_notify()
            return
        fingers = self._fingers_of(uid) if uid else []
        if not fingers:
            idf.update(state="unknown")
            self._identify_notify()
            return
        if len(fingers) == 1:
            self._identify_found(uid, fingers[0])
            return
        idf.update(state="isolating", step=2, user_id=uid)
        self._identify_notify()
        self.hass.async_create_task(self._async_identify_isolate(idf, uid))

    async def _async_identify_isolate(self, idf: dict[str, Any], user_id: int) -> None:
        try:
            mapping, templates, slot = await self.async_run(self.manager.identify_copy, user_id, refresh=False, background=True)
            self._identify_cache = (user_id, templates, slot)
            # Recorded on disk before anything is removed: a restart undoes the isolation.
            self.store.data["identify_pending"] = {"user_id": user_id,
                                                   "temps": {str(k): v for k, v in mapping.items()}}
            await self.store.async_save_now()
            await self.async_run(self.manager.identify_hide, user_id, refresh=False, background=True)
        except HomeAssistantError as ex:
            _LOGGER.warning("Identification: isolating UserID %s failed: %s", user_id, ex)
            if self.identify is idf:
                self.identify = None
            self._identify_notify({**idf, "state": "error", "error": str(ex)})
            await self._async_identify_restore()
            return
        if self.identify is not idf:  # cancelled meanwhile
            await self._async_identify_restore()
            return
        # No wait here: the MA500 takes 20 to 45 s to match a copy written on a new UserID
        # (measured 22/11), but an early reading (unknown finger, former owner) only asks for
        # the finger again ("notyet"), and step 2 gets a longer timeout.
        idf.update(state="again", mapping=mapping, started=time.monotonic())
        self._identify_notify()

    async def async_wait_restore(self) -> None:
        """Wait for a background restore (before starting another identification)."""
        task = self._restore_task
        if task is not None and not task.done():
            await asyncio.wait({task})

    async def _async_identify_restore(self) -> bool:
        """Undo a pending isolation (also after a restart). True when nothing is left."""
        pending = self.store.data.get("identify_pending")
        if not pending:
            return True
        if self._identify_restoring:
            return False
        self._identify_restoring = True
        try:
            mapping = {int(k): v for k, v in pending["temps"].items()}
            cache = self._identify_cache
            templates, slot = (cache[1], cache[2]) if cache and cache[0] == pending["user_id"] else (None, None)
            await self.async_run(self.manager.identify_restore, pending["user_id"], mapping, templates, slot,
                                 refresh=False, background=True)
        except HomeAssistantError as ex:
            _LOGGER.error("Identification: restoring the fingerprints of UserID %s failed (will retry): %s",
                          pending["user_id"], ex)
            return False
        finally:
            self._identify_restoring = False
        self.store.data.pop("identify_pending", None)
        self._identify_cache = None
        await self.store.async_save_now()
        self._snapshot_at = None
        return True

    @callback
    def _identify_found(self, user_id: int, finger_id: int) -> None:
        idf = self.identify or {}
        who = self._user_index.get(user_id) or {}
        placed = False
        if idf.get("physical") is not None and who and who.get("person") == idf.get("person"):
            self.store.set_physical_finger(user_id, finger_id, idf["physical"],
                                           self.person_user_ids(who["person"]) or [user_id])
            self.store.save()
            placed = True
        result = {**idf, "state": "found", "user_id": user_id, "finger_id": finger_id,
                  "found_person": who.get("person"), "found_name": who.get("person_name"),
                  "placed": placed}
        self.identify = None
        self.async_rebuild()
        self._identify_notify(result)

    @callback
    def _identify_check_timeout(self) -> None:
        idf = self.identify
        if idf is None or idf["state"] in ("isolating", "restoring"):
            return
        limit = IDENTIFY_STEP2_TIMEOUT_SECONDS if idf["step"] == 2 else IDENTIFY_TIMEOUT_SECONDS
        if time.monotonic() - idf["started"] > limit:
            self.identify = None
            self._identify_notify({**idf, "state": "timeout"})
            if self.store.data.get("identify_pending"):
                self.hass.async_create_task(self._async_identify_restore())

    async def async_identify_cancel(self) -> None:
        idf, self.identify = self.identify, None
        if idf is not None:
            self._identify_notify({**idf, "state": "cancelled"})
        await self._async_identify_restore()
