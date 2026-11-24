"""Polling coordinator: one TCP session per device, shared by entities, services and the panel."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from datetime import datetime
from pyzkaccess_native import (AccessEvent, DeviceError, DeviceInfo, DoorStatus, RepeatDetector, ZKAccess,
                               ZKConnectionError, ZKError)
from pyzkaccess_native.events import DENIED_EVENTS, GRANTED_EVENTS

from .const import (CONF_POLL_INTERVAL, CONF_SYNC_CLOCK, DEFAULT_SECOND_WINDOW, DEFAULT_SYNC_CLOCK, DOMAIN,
                    ENROLL_TIMEOUT_SECONDS, EVENT_ACCESS, IDENTIFY_PINS, PANEL_URL, RESULT_ACTION,
                    RESULT_DENIED, RESULT_GRANTED, RESULT_OTHER, RETRY_MAX_SECONDS, RETRY_MIN_SECONDS,
                    SNAPSHOT_REFRESH_SECONDS, TOGGLE_INACTIVE_STATES)
# signal_* and poll_interval are re-exported: other modules import them from here.
from .helpers import (UNKNOWN_CREDENTIAL, ZKData, access_method, poll_interval,  # noqa: F401
                      public_event, signal_changed, signal_event)
from .identify import IdentifyMixin
from .maintenance import MaintenanceMixin
from .manager import AccessManager
from .phones import PhoneMixin
from .store import AccessStore

_LOGGER = logging.getLogger(__name__)
T = TypeVar("T")

class ZKCoordinator(IdentifyMixin, PhoneMixin, MaintenanceMixin, DataUpdateCoordinator[ZKData]):
    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, store: AccessStore) -> None:
        interval = poll_interval(entry.options)
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry,
                         update_interval=timedelta(seconds=interval), always_update=False)
        self.live_options: dict[str, Any] = dict(entry.options)
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
        # Wall-clock seconds: device event times and Home Assistant times (phones) compare.
        self._repeat = RepeatDetector(store.data.get("second_window", DEFAULT_SECOND_WINDOW), clock=time.time)
        self.enrollment: Optional[dict[str, Any]] = None
        #: finger identification in progress (reader events are not published meanwhile)
        self.identify: Optional[dict[str, Any]] = None
        self._identify_restoring = False
        #: templates and owner slot kept from the copy, so the restore re-reads nothing
        self._identify_cache: Optional[tuple[int, dict[int, Any], int]] = None
        #: median response time of the device (s), measured every hour
        self.response_time: Optional[float] = None
        #: monotonic time until which the device is booting after a reboot
        self._boot_until: Optional[float] = None
        #: repairs: since when the device does not answer; automatic reboots in the last 24 h
        self._unreachable_since: Optional[float] = None
        self._unreachable_issue = False
        self._slow_restarts: list[float] = []
        self._slot_repair_running = False
        #: settling a journal left by an interrupted fingerprint operation
        self._journal_recovering = False
        self._journal_retry_at = 0.0
        self._identify_isolating = False
        self._last_auto_restart: Optional[float] = None  # monotonic time of the last automatic reboot
        self._unsub_hourly: Optional[Callable[[], None]] = None
        #: restore running in the background after a finger was identified
        self._restore_task: Optional[asyncio.Task] = None
        #: door -> deadline: the device's "remote opening" echo of a door opened for a phone
        self._phone_echo: dict[int, float] = {}
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
        except DeviceError as ex:
            if ex.code == -14:          # communication password refused: ask the user for it
                await self.hass.async_add_executor_job(self._connection_failed)
                raise ConfigEntryAuthFailed(f"{self.client.host} refused the communication password") from ex
            if self.client.connected:
                await self.hass.async_add_executor_job(self._connection_failed)
            self._track_reachability(False)
            raise UpdateFailed(f"cannot reach {self.client.host}: {ex}") from ex
        except (ZKError, OSError) as ex:
            if self.client.connected:  # failed inside a session: close it, retry later
                await self.hass.async_add_executor_job(self._connection_failed)
            self._track_reachability(False)
            raise UpdateFailed(f"cannot reach {self.client.host}: {ex}") from ex
        self._track_reachability(True)
        if raw is not None:
            self._apply_raw(raw)
        if self.enrollment:
            self._update_enrollment(enrolled)
        access = [r for r in records if isinstance(r, AccessEvent) and not self._ignored(r)]
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
        elif self._journal_due():
            self.hass.async_create_task(self._async_journal_recover())
        if self.phone_pairing and time.monotonic() > self.phone_pairing["until"]:
            self.phone_pairing = None
            async_dispatcher_send(self.hass, signal_changed(self.config_entry.entry_id), {"pair": {"state": "timeout"}})
        status = next((r for r in reversed(records) if isinstance(r, DoorStatus)), None)
        return ZKData(status=status or (self.data.status if self.data else None),
                      events=events, snapshot=self.snapshot)

    def _ignored(self, ev: AccessEvent) -> bool:
        """Device events that are not accesses of their own."""
        if ev.user_id in IDENTIFY_PINS and self.identify is None:
            return True  # temporary UserID of an identification that just ended
        until = self._phone_echo.get(ev.door)
        if until is not None and int(ev.event) == 8:
            del self._phone_echo[ev.door]
            # The device reports the door the integration opened for a phone as a
            # "remote opening": the phone access is already in the history.
            return time.monotonic() < until
        return False

    def _identify_busy(self) -> bool:
        """An identification is running or its fingerprints are not all back in place."""
        return bool(self.identify is not None or self.store.data.get("identify_pending")
                    or self._identify_isolating or self._identify_restoring)

    @callback
    def _apply_raw(self, raw: dict[str, Any], force: bool = False) -> None:
        """Rebuild the snapshot when the device content changed (or when ``force``:
        integration-side data such as categories or physical fingers changed)."""
        if raw is not self._last_raw and self._identify_busy():
            # During an identification the fingerprints of the UserID being isolated sit on
            # hidden temporary UserIDs: the device content is transient. Keep the view from
            # before (re-read once everything is back in place).
            return
        self._last_raw = raw
        self._check_template_slots(raw)
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
                who = {"person": person["num"], "person_name": person["name"],
                       "digit": cat["digit"], "ha_person": person["ha_person"]}
                for card in cat.get("cards") or [{"user_id": cat["user_id"]}]:
                    self._user_index[card["user_id"]] = who
        user_ids = {u["Pin"] for u in raw["users"]}
        known = self.store.data.get("known_user_ids")
        if known is not None and self.store.data.get("notify_keypad", True):
            for uid in sorted(user_ids - set(known)):
                self._notify_new_user(uid)
        self.store.data["known_user_ids"] = sorted(user_ids)
        self._check_lost_fingerprints(raw, known)
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

    @callback
    def _check_template_slots(self, raw: dict[str, Any]) -> None:
        """Safety net: every template must be bound to its own user's internal slot. One that
        is not fails, or matches whoever owns that slot (seen on the MA500 after a user moved
        slot). Rebind them at once and tell the user."""
        if (self._slot_repair_running or self.store.data.get("identify_pending")
                or self.manager.journal.exists()):
            return
        slots = {u["Pin"]: u.get("UID") for u in raw["users"]}
        wrong = sorted({t["Pin"] for t in raw["templates"]
                        if t.get("UID") is not None and t["Pin"] in slots and slots[t["Pin"]] != t["UID"]})
        if not wrong:
            return
        self._slot_repair_running = True

        async def repair() -> None:
            try:
                fixed = await self.async_run(self.manager.repair_slots, wrong, background=True)
            except HomeAssistantError as ex:
                _LOGGER.error("Fingerprints of UserIDs %s are bound to another slot; repair failed: %s", wrong, ex)
                return
            finally:
                self._slot_repair_running = False
            if fixed:
                _LOGGER.warning("Fingerprints of UserIDs %s were bound to another user's slot: rebound", fixed)
                persistent_notification.async_create(
                    self.hass,
                    f"The fingerprints of UserID(s) {', '.join(map(str, fixed))} were bound to another "
                    "internal slot of the device (they could fail or be recognized as another person). "
                    "They have been rebound to their own user.",
                    title=f"{self.config_entry.title}: fingerprints repaired",
                    notification_id=f"{DOMAIN}_{self.config_entry.entry_id}_slots")
        self.hass.async_create_task(repair())

    @staticmethod
    def _fingerprint_keys(raw: dict[str, Any]) -> list[str]:
        return sorted(f"{t['Pin']}:{t['FingerID']}" for t in raw["templates"])

    @callback
    def _check_lost_fingerprints(self, raw: dict[str, Any], known_users: Optional[list[int]]) -> None:
        """Alert when fingerprints or UserIDs disappear without an operation of the
        integration (keypad, other software, device fault): they can be restored from a
        backup taken before."""
        if (self.identify is not None or self.store.data.get("identify_pending")
                or self.manager.journal.exists()):
            return  # an operation is under way: compared once it is settled
        current = self._fingerprint_keys(raw)
        known = self.store.data.get("known_fingerprints")
        self.store.data["known_fingerprints"] = current
        lost = sorted(set(known or []) - set(current), key=lambda k: tuple(map(int, k.split(":"))))
        gone_users = sorted(set(known_users or []) - {u["Pin"] for u in raw["users"]})
        if known is None or not (lost or gone_users):
            return
        _LOGGER.warning("Fingerprints %s / UserIDs %s disappeared from the device without an operation "
                        "of Home Assistant", lost, gone_users)
        items = [f"UserID {k.split(':')[0]}, finger slot {k.split(':')[1]}" for k in lost]
        items += [f"UserID {u} (whole user)" for u in gone_users]
        persistent_notification.async_create(
            self.hass,
            "These disappeared from the device without an operation of Home Assistant (deleted on the "
            "keypad, by other software, or a device fault):\n- " + "\n- ".join(items) +
            f"\n\nIf this was not intended, restore them from a backup in the "
            f"[access control panel](/{PANEL_URL}) (Settings, Backups, \u201cMerge\u201d).",
            title=f"{self.config_entry.title}: fingerprints disappeared",
            notification_id=f"{DOMAIN}_{self.config_entry.entry_id}_lost")

    # --------------------------------------------------------------- journal
    def _journal_due(self) -> bool:
        """A journal is on disk while no operation runs: an operation was interrupted
        (restart, lost connection, frozen device) and its UserIDs must be checked."""
        return (self.manager.journal.exists() and not self._journal_recovering
                and self.identify is None and not self.store.data.get("identify_pending")
                and not self._identify_restoring and not self._identify_isolating
                and time.monotonic() >= self._journal_retry_at)

    async def _async_journal_recover(self) -> None:
        self._journal_recovering = True
        notification_id = f"{DOMAIN}_{self.config_entry.entry_id}_journal"
        try:
            op = await self.async_run(lambda: self.manager.journal.recover(self.client), background=True)
        except HomeAssistantError as ex:
            self._journal_retry_at = time.monotonic() + 60
            _LOGGER.error("An interrupted fingerprint operation could not be settled yet (retried in 60 s): %s", ex)
            persistent_notification.async_create(
                self.hass,
                "A fingerprint operation was interrupted and the fingerprints concerned could not be "
                f"checked or put back yet ({ex}). Home Assistant retries every minute; the state before "
                f"the operation is kept on disk (`{self.manager.journal.path}`). Nothing else is done "
                "on the device meanwhile.",
                title=f"{self.config_entry.title}: fingerprints to check", notification_id=notification_id)
            return
        finally:
            self._journal_recovering = False
        persistent_notification.async_dismiss(self.hass, notification_id)
        if op:
            persistent_notification.async_create(
                self.hass,
                f"The operation \u201c{op}\u201d was interrupted. Its fingerprints were checked and put "
                "back exactly as they were before it: do it again if needed.",
                title=f"{self.config_entry.title}: fingerprints put back",
                notification_id=f"{notification_id}_done")

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
        disabled = bool(who and (self.store.is_disabled(who["person"])
                                 or self.store.is_category_off(who["person"], digit)))
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
        digit, cat, gesture, toggled = self._resolve(digit, cat, result, ("user", ev.user_id) if ev.user_id else None,
                                                     when=ev.time.timestamp() if ev.time else None)
        if gesture == "double":
            result = RESULT_ACTION
        if who and result in (RESULT_GRANTED, RESULT_ACTION) and self.store.is_category_off(who["person"], digit):
            result = RESULT_DENIED  # second presentation / toggle led to a category switched off
        return self._event(result=result, code=code, event_name=ev.label, user_id=ev.user_id, who=who,
                           digit=digit, cat=cat, gesture=gesture, toggled=toggled,
                           method=self._method(ev), card=ev.card or None, door=ev.door,
                           verify_mode=int(ev.verify_mode),
                           when=ev.time.isoformat() if ev.time else None)

    def _method(self, ev: AccessEvent) -> str:
        """How the person was identified. A refusal for lack of rights (event 23) comes with
        verify mode 0 on the MA500: the method is then deduced from the credentials the
        UserID has (a card in the event; otherwise its only kind of credential)."""
        method = access_method(ev)
        if method != "other" or not ev.user_id:
            return method
        cred = next((c for p in (self.snapshot or {}).get("persons", []) for c in p["categories"]
                     if c["user_id"] == ev.user_id), None)
        if cred is None:
            # An extra card UserID holds nothing but its card.
            extra = any(card["user_id"] == ev.user_id for p in (self.snapshot or {}).get("persons", [])
                        for c in p["categories"] for card in c.get("cards", [])[1:])
            return "card" if extra else method
        kinds = [k for k, has in (("fingerprint", bool(cred["fingers"])), ("card", bool(cred["card"])),
                                  ("code", bool(cred["has_code"]))) if has]
        return kinds[0] if len(kinds) == 1 else method

    def _resolve(self, digit: Optional[int], cat: Optional[dict[str, Any]], result: str,
                 repeat_key: Any, when: Optional[float] = None) -> tuple[Optional[int], Optional[dict[str, Any]], str, bool]:
        """Second presentation, then toggle: the category that finally applies."""
        gesture = "single"
        if result in (RESULT_GRANTED, RESULT_ACTION) and repeat_key is not None:
            self._repeat.window = float(self.store.data.get("second_window", DEFAULT_SECOND_WINDOW))
            if self._repeat.check(repeat_key, when) and cat and cat.get("second") is not None:
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
            # For automations: a card the device knows (the number itself stays private).
            "card_known": bool(user_id) if method == "card" else None,
            "phone": phone,
            "door": door,
            "verify_mode": verify_mode,
            "time": when or dt_util.now().isoformat(),
        }

    @callback
    def _publish(self, events: list[dict[str, Any]]) -> None:
        for ev in events:
            # Any user (and the recorder) can read the bus: no card number there.
            self.hass.bus.async_fire(EVENT_ACCESS, public_event(ev))
            self.store.add_history(ev)
            async_dispatcher_send(self.hass, signal_event(self.config_entry.entry_id), ev)
        if events:
            self.store.save(delay=10)

    # -------------------------------------------------------------- operations
    async def _async_wait_boot(self, background: bool) -> None:
        """While the device boots after a reboot: an operation started by the user stops with
        a clear message; an internal one (schedules, identification) waits for the end."""
        remaining = (self._boot_until or 0.0) - time.monotonic()
        if remaining <= 0:
            return
        if not background:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="device_rebooting",
                                     translation_placeholders={"seconds": str(int(remaining) + 1)})
        await asyncio.sleep(remaining)

    async def async_run(self, func: Callable[..., T], *args: Any, refresh: bool = True,
                        background: bool = False) -> T:
        """Run a manager operation in the executor, then refresh the snapshot.

        ``background``: an internal operation (schedules, identification), which waits while
        the device reboots instead of failing with a message for the user.
        An identification restore running in the background goes first: an operation
        started meanwhile (delete the finger just identified...) waits for it."""
        await self._async_wait_boot(background)
        task = self._restore_task
        if task is not None and not task.done() and asyncio.current_task() is not task:
            await asyncio.wait({task})

        def job() -> T:
            # One uninterrupted sequence: the polling cannot slip between its commands.
            with self.client.lock:
                self._ensure_connected(force=True)
                return func(*args)

        try:
            result = await self.hass.async_add_executor_job(job)
        except (ZKError, OSError) as ex:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="device_error",
                                     translation_placeholders={"error": str(ex)}) from ex
        if refresh:
            await self.async_refresh_snapshot(silent=True)
        return result

    async def async_refresh_snapshot(self, silent: bool = False) -> None:
        await self._async_wait_boot(background=silent)

        def job() -> dict[str, Any]:
            with self.client.lock:
                self._ensure_connected(force=True)
                return self.manager.read_raw()

        try:
            raw = await self.hass.async_add_executor_job(job)
        except (ZKError, OSError) as ex:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="unreachable",
                                     translation_placeholders={"host": self.client.host, "error": str(ex)}) from ex
        if self._identify_busy():
            return  # transient content (see _apply_raw): read again after the identification
        self._snapshot_at = time.monotonic()
        if silent:
            # Changes made by the integration itself: the new baseline, no alert.
            self.store.data["known_user_ids"] = sorted({u["Pin"] for u in raw["users"]})
            self.store.data["known_fingerprints"] = self._fingerprint_keys(raw)
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
                return [card["user_id"] for c in p["categories"] for card in c.get("cards") or [c]]
        return []

    async def async_backup(self, reason: str) -> Optional[str]:
        if self.store.data.get("identify_pending") or self.manager.journal.exists():
            _LOGGER.debug("Backup skipped: a fingerprint operation is not settled")
            return None
        try:
            return await self.async_run(self.manager.backup_create, reason, refresh=False, background=True)
        except HomeAssistantError as ex:
            _LOGGER.warning("Backup of %s failed: %s",
                            self.config_entry.title.strip() or self.config_entry.unique_id, ex)
            return None

    def start_enrollment(self, user_id: int, baseline: list[int], physical: Optional[int]) -> dict[str, Any]:
        self.enrollment = {"user_id": user_id, "baseline": baseline, "physical": physical,
                           "state": "waiting", "started": time.monotonic()}
        return self.enrollment

    def apply_options(self, options: dict[str, Any]) -> bool:
        """Apply changed options live when possible (only the poll interval can be).
        False when Home Assistant must reload the entry."""
        changed = {k for k in set(options) | set(self.live_options)
                   if options.get(k) != self.live_options.get(k)}
        if changed - {CONF_POLL_INTERVAL}:
            return False
        self.update_interval = timedelta(seconds=poll_interval(options))
        self.live_options = dict(options)
        return True

    # ---------------------------------------------------------------- lifecycle
    async def async_setup_schedules(self) -> None:
        self._unsub_tags = self.hass.bus.async_listen("tag_scanned", self._on_tag)

        async def nightly(_now) -> None:
            await self.async_run(self._measure_clock, refresh=False, background=True)
            if self.config_entry.options.get(CONF_SYNC_CLOCK, DEFAULT_SYNC_CLOCK):
                try:
                    await self.async_run(self.client.set_time, refresh=False, background=True)
                except HomeAssistantError as ex:
                    _LOGGER.warning("Clock sync failed: %s", ex)
            if self.store.data["backup"].get("nightly", True):
                await self.async_backup("nightly")
            if self.maintenance.get("nightly_restart", True):
                await self.async_restart_device("nightly")

        async def hourly(_now) -> None:
            await self.async_check_response_time()

        self._unsub_nightly = async_track_time_change(self.hass, nightly, hour=4, minute=0, second=0)
        self._unsub_hourly = async_track_time_change(self.hass, hourly, minute=30, second=0)

    async def async_shutdown(self) -> None:
        # Home Assistant also calls this on unload for coordinators bound to a config
        # entry: clear each listener once so a second call does not remove it again.
        for unsub in (self._unsub_nightly, self._unsub_backup, self._unsub_tags, self._unsub_hourly):
            if unsub:
                unsub()
        self._unsub_nightly = self._unsub_backup = self._unsub_tags = self._unsub_hourly = None
        self.identify = None
        if self.store.data.get("identify_pending"):
            try:
                await self._async_identify_restore()
            except Exception:  # noqa: BLE001 - retried at the next start
                _LOGGER.exception("Identification: restore at shutdown failed")
        await super().async_shutdown()
        await self.hass.async_add_executor_job(self.client.disconnect)
