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
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from datetime import datetime
from pyzkaccess_native import (AccessEvent, DeviceInfo, DoorStatus, RepeatDetector, ZKAccess,
                               ZKConnectionError, ZKError)
from pyzkaccess_native.events import DENIED_EVENTS, GRANTED_EVENTS

from .const import (CONF_OPEN_SECONDS, CONF_POLL_INTERVAL, CONF_SYNC_CLOCK, DEFAULT_OPEN_SECONDS,
                    DEFAULT_POLL_INTERVAL, DEFAULT_SYNC_CLOCK, DOMAIN, ENROLL_TIMEOUT_SECONDS,
                    EVENT_ACCESS, IDENTIFY_TIMEOUT_SECONDS, PANEL_URL, PHONE_PAIR_TIMEOUT_SECONDS, TOGGLE_INACTIVE_STATES,
                    RESULT_ACTION, RESULT_DENIED, RESULT_GRANTED, RESULT_OTHER,
                    RETRY_MAX_SECONDS, RETRY_MIN_SECONDS, SNAPSHOT_REFRESH_SECONDS)
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
        #: finger identification in progress (reader events are not published meanwhile)
        self.identify: Optional[dict[str, Any]] = None
        self._identify_restoring = False
        self._unsub_nightly: Optional[Callable[[], None]] = None
        self._unsub_backup: Optional[Callable[[], None]] = None
        self._unsub_tags: Optional[Callable[[], None]] = None
        self.phone_pairing: Optional[dict[str, Any]] = None
        self._last_raw: Optional[dict[str, Any]] = None
        #: device clock minus Home Assistant clock (seconds), measured before each sync
        self.clock_offset: Optional[float] = None
        # Reconnection back-off: a device that stops answering is not hammered every poll.
        self._retry_delay = RETRY_MIN_SECONDS
        self._retry_at = 0.0

    # ------------------------------------------------------------ executor side
    def _ensure_connected(self, force: bool = False) -> None:
        """Open a session if needed. Between failed attempts the polling waits longer
        and longer (``RETRY_MIN_SECONDS`` doubling up to ``RETRY_MAX_SECONDS``); an
        explicit user operation (``force``) tries at once."""
        if self.client.connected:
            return
        if not force and time.monotonic() < self._retry_at:
            raise ZKConnectionError(f"next connection attempt in {self._retry_at - time.monotonic():.0f} s")
        try:
            self._connect()
        except (ZKError, OSError):
            self._connection_failed()
            raise
        self._retry_delay = RETRY_MIN_SECONDS
        self._retry_at = 0.0

    def _connection_failed(self) -> None:
        self.client.close()
        self._retry_at = time.monotonic() + self._retry_delay
        self._retry_delay = min(self._retry_delay * 2, RETRY_MAX_SECONDS)

    def _connect(self) -> None:
        self.client.connect()
        self.info = self.client.info()
        self._measure_clock()
        if self.config_entry.options.get(CONF_SYNC_CLOCK, DEFAULT_SYNC_CLOCK):
            self.client.set_time()

    def _measure_clock(self) -> None:
        try:
            device_now = self.client.get_time()
        except ZKError:
            return
        if device_now:
            self.clock_offset = round((device_now - datetime.now()).total_seconds())

    def _poll(self) -> tuple[list[Any], Optional[dict[str, Any]], Optional[int]]:
        with self.client.lock:
            return self._poll_locked()

    def _poll_locked(self) -> tuple[list[Any], Optional[dict[str, Any]], Optional[int]]:
        self._ensure_connected()
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
            if self.client.connected:  # failed inside a session: close it, retry later
                await self.hass.async_add_executor_job(self._connection_failed)
            raise UpdateFailed(f"cannot reach {self.client.host}: {ex}") from ex
        if raw is not None:
            self._apply_raw(raw)
        if self.enrollment:
            self._update_enrollment(enrolled)
        access = [r for r in records if isinstance(r, AccessEvent)]
        if self.identify is not None:
            # Identification: the reader is used as a sensor only. Its events reach
            # neither the automations, nor the event entity, nor the history.
            for record in access:
                self._identify_event(record)
            self._identify_check_timeout()
            events: list[dict[str, Any]] = []
        else:
            events = [self._enrich(r) for r in access]
        self._publish(events)
        if self.store.data.get("identify_pending") and self.identify is None and not self._identify_restoring:
            self.hass.async_create_task(self._async_identify_restore())
        if self.phone_pairing and time.monotonic() > self.phone_pairing["until"]:
            self.phone_pairing = None
            async_dispatcher_send(self.hass, signal_changed(self.config_entry.entry_id), {"pair": {"state": "timeout"}})
        status = next((r for r in reversed(records) if isinstance(r, DoorStatus)), None)
        return ZKData(status=status or (self.data.status if self.data else None),
                      events=events, snapshot=self.snapshot)

    @callback
    def _apply_raw(self, raw: dict[str, Any], force: bool = False) -> None:
        """Rebuild the snapshot when the device content changed (or when ``force``:
        integration-side data such as categories or physical fingers changed)."""
        self._last_raw = raw
        digest = self.manager.fingerprint(raw)
        device_changed = digest != self._raw_hash
        if not device_changed and not force:
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
        if device_changed and not first and self.store.data["backup"].get("on_change", True):
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
        digit, cat, gesture, toggled = self._resolve(digit, cat, result, ("user", ev.user_id) if ev.user_id else None)
        if gesture == "double":
            result = RESULT_ACTION
        return self._event(result=result, code=code, event_name=ev.label, user_id=ev.user_id, who=who,
                           digit=digit, cat=cat, gesture=gesture, toggled=toggled,
                           method=access_method(ev), card=ev.card or None, door=ev.door,
                           verify_mode=int(ev.verify_mode),
                           when=ev.time.isoformat() if ev.time else None)

    def _resolve(self, digit: Optional[int], cat: Optional[dict[str, Any]], result: str,
                 repeat_key: Any) -> tuple[Optional[int], Optional[dict[str, Any]], str, bool]:
        """Second presentation, then toggle: the category that finally applies."""
        gesture = "single"
        if result in (RESULT_GRANTED, RESULT_ACTION) and repeat_key is not None:
            self._repeat.window = float(self.store.data.get("second_window", 3.0))
            if self._repeat.check(repeat_key) and cat and cat.get("second") is not None:
                digit = int(cat["second"])
                cat = self.store.category(digit)
                gesture = "double"
        toggled = False
        if cat and cat.get("type") == "toggle" and result in (RESULT_GRANTED, RESULT_ACTION):
            state = self.hass.states.get(cat.get("entity_id", ""))
            active = state is not None and state.state not in TOGGLE_INACTIVE_STATES
            digit = int(cat["on"] if active else cat["off"])
            cat = self.store.category(digit)
            toggled = True
        return digit, cat, gesture, toggled

    def _event(self, *, result: str, code: Optional[int], event_name: str, user_id: Optional[int],
               who: Optional[dict[str, Any]], digit: Optional[int], cat: Optional[dict[str, Any]],
               gesture: str, toggled: bool, method: str, card: Optional[int], door: int,
               verify_mode: Optional[int], when: Optional[str], phone: Optional[str] = None) -> dict[str, Any]:
        # One device per config entry: avoids DeviceRegistry.async_get_device, deprecated in
        # HA 2026.x (identifiers no longer unique across config entries), removed in 2027.8.
        devices = dr.async_entries_for_config_entry(dr.async_get(self.hass), self.config_entry.entry_id)
        device = devices[0] if devices else None
        return {
            "device_id": device.id if device else None,
            "result": result,
            "granted": result == RESULT_GRANTED,
            "denied": result == RESULT_DENIED,
            "event": code,
            "event_name": event_name,
            "user_id": user_id,
            "person": who["person"] if who else None,
            "person_name": (who["person_name"] or None) if who else None,
            "ha_person": who["ha_person"] if who else None,
            "category": cat["name"] if cat else None,
            "category_digit": digit,
            "category_type": cat["type"] if cat else None,
            "gesture": gesture,
            "toggle": toggled,
            "method": method,
            "card": card,
            "phone": phone,
            "door": door,
            "verify_mode": verify_mode,
            "time": when or dt_util.now().isoformat(),
        }

    @callback
    def _publish(self, events: list[dict[str, Any]]) -> None:
        for ev in events:
            self.hass.bus.async_fire(EVENT_ACCESS, ev)
            self.store.add_history(ev)
            async_dispatcher_send(self.hass, signal_event(self.config_entry.entry_id), ev)
        if events:
            self.store.save(delay=10)

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
        allowed = bool(phone and phone.get("enabled", True) and cat and not self.store.is_disabled(phone["person"]))
        result = RESULT_ACTION if allowed else RESULT_DENIED
        digit, cat, gesture, toggled = self._resolve(digit, cat, result, ("phone", device_id))
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
                await self.async_run(self.manager.door_control, door, "open", seconds, refresh=False)
            except HomeAssistantError as ex:
                _LOGGER.warning("Phone access: cannot open door %s: %s", door, ex)

    # -------------------------------------------------------------- operations
    async def async_run(self, func: Callable[..., T], *args: Any, refresh: bool = True) -> T:
        """Run a manager operation in the executor, then refresh the snapshot."""
        def job() -> T:
            # One uninterrupted sequence: the polling cannot slip between its commands.
            with self.client.lock:
                self._ensure_connected(force=True)
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
            with self.client.lock:
                self._ensure_connected(force=True)
                return self.manager.read_raw()

        try:
            raw = await self.hass.async_add_executor_job(job)
        except (ZKError, OSError) as ex:
            raise HomeAssistantError(f"cannot reach {self.client.host}: {ex}") from ex
        self._snapshot_at = time.monotonic()
        if silent:
            self.store.data["known_user_ids"] = sorted({u["Pin"] for u in raw["users"]})
        self._apply_raw(raw, force=True)

    @callback
    def async_rebuild(self) -> None:
        """Rebuild the view after an integration-side change, without reading the device."""
        if self._last_raw is not None:
            self._apply_raw(self._last_raw, force=True)

    def person_user_ids(self, person: int) -> list[int]:
        """UserIDs of a person, from the last snapshot (no device access)."""
        for p in (self.snapshot or {}).get("persons", []):
            if p["num"] == person:
                return [c["user_id"] for c in p["categories"]]
        return []

    async def async_backup(self, reason: str) -> Optional[str]:
        if self.store.data.get("identify_pending"):
            _LOGGER.debug("Backup skipped: fingerprints are isolated for an identification")
            return None
        try:
            return await self.async_run(self.manager.backup_create, reason, refresh=False)
        except HomeAssistantError as ex:
            _LOGGER.warning("Backup of %s failed: %s", self.config_entry.title, ex)
            return None

    def start_enrollment(self, user_id: int, baseline: list[int], physical: Optional[int]) -> dict[str, Any]:
        self.enrollment = {"user_id": user_id, "baseline": baseline, "physical": physical,
                           "state": "waiting", "started": time.monotonic()}
        return self.enrollment

    # ----------------------------------------------------------- identification
    def start_identify(self, person: Optional[int], physical: Optional[int]) -> dict[str, Any]:
        if self.identify is not None:
            raise HomeAssistantError("an identification is already running")
        if self.store.data.get("identify_pending"):
            raise HomeAssistantError("the previous identification is still being undone, try again")
        if self.enrollment is not None:
            raise HomeAssistantError("an enrollment is running")
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
        if idf is None or idf["state"] not in ("waiting", "again", "unknown", "other"):
            return  # busy (isolating / restoring)
        if access_method(ev) in ("card", "code", "remote"):
            return
        uid = ev.user_id or 0
        if idf["step"] == 2:
            mapping = idf.get("mapping") or {}
            if uid in mapping:
                idf.update(state="restoring")
                self._identify_notify()
                self.hass.async_create_task(self._async_identify_finish(idf["user_id"], mapping[uid]))
            else:
                idf.update(state="other")  # another finger than the one of step 1
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
            mapping = await self.async_run(self.manager.identify_copy, user_id, refresh=False)
            # Recorded on disk before anything is removed: a restart undoes the isolation.
            self.store.data["identify_pending"] = {"user_id": user_id,
                                                   "temps": {str(k): v for k, v in mapping.items()}}
            await self.store.async_save_now()
            await self.async_run(self.manager.identify_hide, user_id, refresh=False)
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
        idf.update(state="again", mapping=mapping, started=time.monotonic())
        self._identify_notify()

    async def _async_identify_finish(self, user_id: int, finger_id: int) -> None:
        if not await self._async_identify_restore():
            idf = self.identify or {}
            self.identify = None
            self._identify_notify({**idf, "state": "error",
                                   "error": "the fingerprints could not be put back yet; retrying"})
            return
        self._identify_found(user_id, finger_id)

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
            await self.async_run(self.manager.identify_restore, pending["user_id"], mapping, refresh=False)
        except HomeAssistantError as ex:
            _LOGGER.error("Identification: restoring the fingerprints of UserID %s failed (will retry): %s",
                          pending["user_id"], ex)
            return False
        finally:
            self._identify_restoring = False
        self.store.data.pop("identify_pending", None)
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
        if time.monotonic() - idf["started"] > IDENTIFY_TIMEOUT_SECONDS:
            self.identify = None
            self._identify_notify({**idf, "state": "timeout"})
            if self.store.data.get("identify_pending"):
                self.hass.async_create_task(self._async_identify_restore())

    async def async_identify_cancel(self) -> None:
        idf, self.identify = self.identify, None
        if idf is not None:
            self._identify_notify({**idf, "state": "cancelled"})
        await self._async_identify_restore()

    # ---------------------------------------------------------------- lifecycle
    async def async_setup_schedules(self) -> None:
        self._unsub_tags = self.hass.bus.async_listen("tag_scanned", self._on_tag)

        async def nightly(_now) -> None:
            await self.async_run(self._measure_clock, refresh=False)
            if self.config_entry.options.get(CONF_SYNC_CLOCK, DEFAULT_SYNC_CLOCK):
                try:
                    await self.async_run(self.client.set_time, refresh=False)
                except HomeAssistantError as ex:
                    _LOGGER.warning("Clock sync failed: %s", ex)
            if self.store.data["backup"].get("nightly", True):
                await self.async_backup("nightly")

        self._unsub_nightly = async_track_time_change(self.hass, nightly, hour=4, minute=0, second=0)

    async def async_shutdown(self) -> None:
        # Home Assistant also calls this on unload for coordinators bound to a config
        # entry: clear each listener once so a second call does not remove it again.
        for unsub in (self._unsub_nightly, self._unsub_backup, self._unsub_tags):
            if unsub:
                unsub()
        self._unsub_nightly = self._unsub_backup = self._unsub_tags = None
        self.identify = None
        if self.store.data.get("identify_pending"):
            try:
                await self._async_identify_restore()
            except Exception:  # noqa: BLE001 - retried at the next start
                _LOGGER.exception("Identification: restore at shutdown failed")
        await super().async_shutdown()
        await self.hass.async_add_executor_job(self.client.disconnect)
