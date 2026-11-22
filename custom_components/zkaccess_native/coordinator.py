"""Polling coordinator: one TCP session per device, shared by entities, services and the panel."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, TypeVar

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from pyzkaccess_native import (AccessEvent, DeviceInfo, DoorStatus, RepeatDetector, ZKAccess,
                               ZKError)
from pyzkaccess_native.events import DENIED_EVENTS, GRANTED_EVENTS

from .const import (CONF_POLL_INTERVAL, CONF_SYNC_CLOCK, DEFAULT_POLL_INTERVAL,
                    DEFAULT_SYNC_CLOCK, DOMAIN, ENROLL_TIMEOUT_SECONDS, EVENT_ACCESS, PANEL_URL,
                    RESULT_ACTION, RESULT_DENIED, RESULT_GRANTED, RESULT_OTHER,
                    SNAPSHOT_REFRESH_SECONDS)
from .manager import AccessManager
from .store import AccessStore

_LOGGER = logging.getLogger(__name__)
T = TypeVar("T")

#: Event codes meaning "credential not registered": never an action.
UNKNOWN_CREDENTIAL = {27, 34}
FINGERPRINT_EVENTS = {14, 15, 16, 17, 18, 19, 31, 32, 33, 34, 35, 103, 203}
CODE_EVENTS = {4, 30, 101}
REMOTE_EVENTS = {7, 8, 9, 10, 11, 12, 13, 205}


def signal_event(entry_id: str) -> str:
    return f"{DOMAIN}_{entry_id}_event"


def signal_changed(entry_id: str) -> str:
    return f"{DOMAIN}_{entry_id}_changed"


def access_method(ev: AccessEvent) -> str:
    code = int(ev.event)
    if code in REMOTE_EVENTS:
        return "remote"
    if code in FINGERPRINT_EVENTS:
        return "fingerprint"
    if code in CODE_EVENTS:
        return "code"
    if ev.card:
        return "card"
    return {1: "fingerprint", 3: "code", 4: "card"}.get(int(ev.verify_mode), "other")


@dataclass
class ZKData:
    """Snapshot published to entities and to the panel after each poll."""

    status: Optional[DoorStatus] = None
    events: list[dict[str, Any]] = field(default_factory=list)
    snapshot: Optional[dict[str, Any]] = None


class ZKCoordinator(DataUpdateCoordinator[ZKData]):
    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, store: AccessStore) -> None:
        interval = entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry,
                         update_interval=timedelta(seconds=interval), always_update=False)
        self.client = ZKAccess(entry.data[CONF_HOST], entry.data.get(CONF_PORT, 4370),
                               entry.data.get(CONF_PASSWORD, ""))
        self.store = store
        self.info: Optional[DeviceInfo] = None
        backup_dir = Path(hass.config.path(DOMAIN, "backups", entry.unique_id or entry.entry_id))
        self.manager = AccessManager(self.client, store, backup_dir,
                                     lambda: (self.info.lock_count if self.info else None) or 1)
        self.snapshot: Optional[dict[str, Any]] = None
        self._raw_hash: Optional[str] = None
        self._user_index: dict[int, dict[str, Any]] = {}
        self._snapshot_at: Optional[float] = None  # None = refresh on the next poll
        self._repeat = RepeatDetector(store.data.get("second_window", 3.0))
        self.enrollment: Optional[dict[str, Any]] = None
        self._unsub_nightly: Optional[Callable[[], None]] = None
        self._unsub_backup: Optional[Callable[[], None]] = None

    # ------------------------------------------------------------ executor side
    def _connect(self) -> None:
        self.client.connect()
        self.info = self.client.info()
        if self.config_entry.options.get(CONF_SYNC_CLOCK, DEFAULT_SYNC_CLOCK):
            self.client.set_time()

    def _poll(self) -> tuple[list[Any], Optional[dict[str, Any]], Optional[int]]:
        if not self.client.connected:
            self._connect()
        records = self.client.poll()
        raw = None
        if self._snapshot_at is None or time.monotonic() - self._snapshot_at > SNAPSHOT_REFRESH_SECONDS:
            raw = self.manager.read_raw()
            self._snapshot_at = time.monotonic()
        enrolled = None
        if self.enrollment:
            enrolled = self.manager.enroll_check(self.enrollment["user_id"], self.enrollment["baseline"])
        return records, raw, enrolled

    # --------------------------------------------------------------- loop side
    async def _async_update_data(self) -> ZKData:
        try:
            records, raw, enrolled = await self.hass.async_add_executor_job(self._poll)
        except (ZKError, OSError) as ex:
            await self.hass.async_add_executor_job(self.client.close)
            raise UpdateFailed(f"cannot reach {self.client.host}: {ex}") from ex
        if raw is not None:
            self._apply_raw(raw)
        if self.enrollment:
            self._update_enrollment(enrolled)
        events = [self._enrich(r) for r in records if isinstance(r, AccessEvent)]
        for ev in events:
            self.hass.bus.async_fire(EVENT_ACCESS, ev)
            self.store.add_history(ev)
            async_dispatcher_send(self.hass, signal_event(self.config_entry.entry_id), ev)
        if events:
            self.store.save(delay=10)
        status = next((r for r in reversed(records) if isinstance(r, DoorStatus)), None)
        return ZKData(status=status or (self.data.status if self.data else None),
                      events=events, snapshot=self.snapshot)

    @callback
    def _apply_raw(self, raw: dict[str, Any]) -> None:
        digest = self.manager.fingerprint(raw)
        if digest == self._raw_hash:
            return
        first = self._raw_hash is None
        self._raw_hash = digest
        self.snapshot = self.manager.build(raw)
        self._user_index = {}
        for person in self.snapshot["persons"]:
            for cat in person["categories"]:
                self._user_index[cat["user_id"]] = {"person": person["num"], "person_name": person["name"],
                                                    "digit": cat["digit"], "ha_person": person["ha_person"]}
        user_ids = {u["Pin"] for u in raw["users"]}
        known = self.store.data.get("known_user_ids")
        if known is not None and self.store.data.get("notify_keypad", True):
            for uid in sorted(user_ids - set(known)):
                self._notify_new_user(uid)
        self.store.data["known_user_ids"] = sorted(user_ids)
        self.store.save()
        async_dispatcher_send(self.hass, signal_changed(self.config_entry.entry_id))
        if not first and self.store.data["backup"].get("on_change", True):
            # Debounced: several quick changes give one backup.
            if self._unsub_backup:
                self._unsub_backup()

            async def run(_now) -> None:
                self._unsub_backup = None
                await self.async_backup("change detected")

            self._unsub_backup = async_call_later(self.hass, 20, run)

    def _notify_new_user(self, user_id: int) -> None:
        persistent_notification.async_create(
            self.hass,
            f"UserID {user_id} was added on the device keypad. Open the "
            f"[access control panel](/{PANEL_URL}) to name it or assign it to a person.",
            title=f"{self.config_entry.title}: new user",
            notification_id=f"{DOMAIN}_{self.config_entry.entry_id}_new_{user_id}",
        )

    def _update_enrollment(self, finger_id: Optional[int]) -> None:
        enr = self.enrollment
        assert enr is not None
        if finger_id is not None:
            split = self.manager.numbering.split(enr["user_id"])
            person_ids = [c["user_id"] for p in (self.snapshot or {}).get("persons", [])
                          if split and p["num"] == split[0] for c in p["categories"]]
            if enr.get("physical") is not None:
                self.store.set_physical_finger(enr["user_id"], finger_id, enr["physical"],
                                               person_ids or [enr["user_id"]])
                self.store.save()
            enr.update(state="done", finger_id=finger_id)
            self.enrollment = None
            self._snapshot_at = None
            async_dispatcher_send(self.hass, signal_changed(self.config_entry.entry_id), {"enroll": enr})
        elif time.monotonic() - enr["started"] > ENROLL_TIMEOUT_SECONDS:
            enr["state"] = "timeout"
            self.enrollment = None
            async_dispatcher_send(self.hass, signal_changed(self.config_entry.entry_id), {"enroll": enr})

    def _enrich(self, ev: AccessEvent) -> dict[str, Any]:
        """Turn a device event into the integration's access event."""
        code = int(ev.event)
        who = self._user_index.get(ev.user_id) if ev.user_id else None
        digit = who["digit"] if who else None
        cat = self.store.category(digit) if digit is not None else None
        disabled = bool(who and self.store.is_disabled(who["person"]))
        if code in GRANTED_EVENTS:
            result = RESULT_GRANTED
        elif code in DENIED_EVENTS:
            # A known credential of a category without relay rights is refused by the
            # device on purpose: for Home Assistant it is an action.
            if who and cat and not cat.get("relay") and code not in UNKNOWN_CREDENTIAL and not disabled:
                result = RESULT_ACTION
            else:
                result = RESULT_DENIED
        else:
            result = RESULT_OTHER
        if disabled and result in (RESULT_GRANTED, RESULT_ACTION):
            result = RESULT_DENIED
        gesture = "single"
        if result in (RESULT_GRANTED, RESULT_ACTION) and ev.user_id:
            self._repeat.window = float(self.store.data.get("second_window", 3.0))
            if self._repeat.check(ev.user_id) and cat and cat.get("second") is not None:
                digit = int(cat["second"])
                cat = self.store.category(digit)
                gesture = "double"
                result = RESULT_ACTION
        device = dr.async_get(self.hass).async_get_device({(DOMAIN, self.config_entry.unique_id)})
        return {
            "device_id": device.id if device else None,
            "result": result,
            "granted": result == RESULT_GRANTED,
            "denied": result == RESULT_DENIED,
            "event": code,
            "event_name": ev.label,
            "user_id": ev.user_id,
            "person": who["person"] if who else None,
            "person_name": (who["person_name"] or None) if who else None,
            "ha_person": who["ha_person"] if who else None,
            "category": cat["name"] if cat else None,
            "category_digit": digit,
            "category_type": cat["type"] if cat else None,
            "gesture": gesture,
            "method": access_method(ev),
            "card": ev.card or None,
            "door": ev.door,
            "verify_mode": int(ev.verify_mode),
            "time": ev.time.isoformat() if ev.time else dt_util.now().isoformat(),
        }

    # -------------------------------------------------------------- operations
    async def async_run(self, func: Callable[..., T], *args: Any, refresh: bool = True) -> T:
        """Run a manager operation in the executor, then refresh the snapshot."""
        def job() -> T:
            if not self.client.connected:
                self._connect()
            return func(*args)

        try:
            result = await self.hass.async_add_executor_job(job)
        except ZKError as ex:
            raise HomeAssistantError(str(ex)) from ex
        if refresh:
            await self.async_refresh_snapshot(silent=True)
        return result

    async def async_refresh_snapshot(self, silent: bool = False) -> None:
        def job() -> dict[str, Any]:
            if not self.client.connected:
                self._connect()
            return self.manager.read_raw()

        try:
            raw = await self.hass.async_add_executor_job(job)
        except (ZKError, OSError) as ex:
            raise HomeAssistantError(f"cannot reach {self.client.host}: {ex}") from ex
        self._snapshot_at = time.monotonic()
        if silent:
            self.store.data["known_user_ids"] = sorted({u["Pin"] for u in raw["users"]})
        self._apply_raw(raw)

    async def async_backup(self, reason: str) -> Optional[str]:
        try:
            return await self.async_run(self.manager.backup_create, reason, refresh=False)
        except HomeAssistantError as ex:
            _LOGGER.warning("Backup of %s failed: %s", self.config_entry.title, ex)
            return None

    def start_enrollment(self, user_id: int, baseline: list[int], physical: Optional[int]) -> dict[str, Any]:
        self.enrollment = {"user_id": user_id, "baseline": baseline, "physical": physical,
                           "state": "waiting", "started": time.monotonic()}
        return self.enrollment

    # ---------------------------------------------------------------- lifecycle
    async def async_setup_schedules(self) -> None:
        async def nightly(_now) -> None:
            if self.config_entry.options.get(CONF_SYNC_CLOCK, DEFAULT_SYNC_CLOCK):
                try:
                    await self.async_run(self.client.set_time, refresh=False)
                except HomeAssistantError as ex:
                    _LOGGER.warning("Clock sync failed: %s", ex)
            if self.store.data["backup"].get("nightly", True):
                await self.async_backup("nightly")

        self._unsub_nightly = async_track_time_change(self.hass, nightly, hour=4, minute=0, second=0)

    async def async_shutdown(self) -> None:
        for unsub in (self._unsub_nightly, self._unsub_backup):
            if unsub:
                unsub()
        await super().async_shutdown()
        await self.hass.async_add_executor_job(self.client.disconnect)
