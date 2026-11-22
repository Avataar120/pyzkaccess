"""WebSocket API used by the management panel (admins only)."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN, NUMBERING_CATEGORY, NUMBERING_PLAIN
from .coordinator import ZKCoordinator, signal_changed, signal_event

ENTRY = vol.Required("entry_id")


def _coordinator(hass: HomeAssistant, entry_id: str) -> ZKCoordinator:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN or not hasattr(entry, "runtime_data"):
        raise HomeAssistantError(f"unknown or unloaded device {entry_id}")
    return entry.runtime_data


def _state(coord: ZKCoordinator) -> dict[str, Any]:
    info = coord.info
    status = coord.data.status if coord.data else None
    doors = (info.lock_count if info else None) or 1
    return {
        "device": {
            "title": coord.config_entry.title,
            "serial": coord.config_entry.unique_id,
            "firmware": info.firmware if info else None,
            "machine_type": info.machine_type if info else None,
            "doors": doors,
            "connected": coord.last_update_success,
        },
        "door_status": [
            {"door": d, "open": status.door_open(d), "alarm": status.alarm(d) or status.open_timeout(d)}
            for d in range(1, doors + 1)
        ] if status else [],
        "snapshot": coord.snapshot,
        "categories": coord.store.categories,
        "second_window": coord.store.data.get("second_window", 3.0),
        "numbering": coord.store.data["numbering"],
        "settings": {"backup": coord.store.data["backup"],
                     "notify_keypad": coord.store.data.get("notify_keypad", True)},
        "enrollment": coord.enrollment,
        "tags": coord.store.data["tags"],
        "phone_pairing": coord.phone_pairing and {k: v for k, v in coord.phone_pairing.items() if k != "until"},
        "clock_offset": coord.clock_offset,
    }


def _reply(connection: websocket_api.ActiveConnection, msg: dict, result: Any = None) -> None:
    connection.send_result(msg["id"], result)


async def _guard(connection, msg, coro) -> None:
    try:
        result = await coro
    except HomeAssistantError as ex:
        connection.send_error(msg["id"], "failed", str(ex))
        return
    _reply(connection, msg, result)


# --------------------------------------------------------------------- reading
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/devices"})
@websocket_api.require_admin
@callback
def ws_devices(hass: HomeAssistant, connection, msg) -> None:
    out = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        if hasattr(entry, "runtime_data"):
            out.append({"entry_id": entry.entry_id, "title": entry.title, "serial": entry.unique_id})
    _reply(connection, msg, out)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/state", ENTRY: str,
                                  vol.Optional("refresh", default=False): bool})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_state(hass: HomeAssistant, connection, msg) -> None:
    async def run():
        coord = _coordinator(hass, msg["entry_id"])
        if msg["refresh"] or coord.snapshot is None:
            await coord.async_refresh_snapshot(silent=True)
        return _state(coord)
    await _guard(connection, msg, run())


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/history", ENTRY: str,
                                  vol.Optional("limit", default=200): vol.All(int, vol.Range(1, 500))})
@websocket_api.require_admin
@callback
def ws_history(hass: HomeAssistant, connection, msg) -> None:
    coord = _coordinator(hass, msg["entry_id"])
    _reply(connection, msg, list(reversed(coord.store.data["history"][-msg["limit"]:])))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/subscribe", ENTRY: str})
@websocket_api.require_admin
@callback
def ws_subscribe(hass: HomeAssistant, connection, msg) -> None:
    """Push access events and "changed" notices until the panel unsubscribes."""
    entry_id = msg["entry_id"]
    _coordinator(hass, entry_id)

    @callback
    def on_event(ev: dict) -> None:
        connection.send_message(websocket_api.event_message(msg["id"], {"kind": "event", "event": ev}))

    @callback
    def on_changed(extra: dict | None = None) -> None:
        connection.send_message(websocket_api.event_message(msg["id"], {"kind": "changed", **(extra or {})}))

    unsubs = [async_dispatcher_connect(hass, signal_event(entry_id), on_event),
              async_dispatcher_connect(hass, signal_changed(entry_id), on_changed)]

    @callback
    def unsubscribe() -> None:
        for unsub in unsubs:
            unsub()

    connection.subscriptions[msg["id"]] = unsubscribe
    _reply(connection, msg)


# --------------------------------------------------------------------- persons
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/person/save", ENTRY: str,
                                  vol.Optional("person"): vol.Any(None, int), vol.Required("name"): str,
                                  vol.Optional("ha_person"): vol.Any(None, str)})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_person_save(hass, connection, msg) -> None:
    coord = _coordinator(hass, msg["entry_id"])
    await _guard(connection, msg, coord.async_run(coord.manager.person_save, msg.get("person"),
                                                 msg["name"], msg.get("ha_person")))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/person/delete", ENTRY: str,
                                  vol.Required("person"): int})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_person_delete(hass, connection, msg) -> None:
    coord = _coordinator(hass, msg["entry_id"])

    async def run():
        await coord.async_backup(f"before deleting person {msg['person']}")
        await coord.async_run(coord.manager.person_delete, msg["person"])
    await _guard(connection, msg, run())


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/person/enable", ENTRY: str,
                                  vol.Required("person"): int, vol.Required("enabled"): bool})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_person_enable(hass, connection, msg) -> None:
    coord = _coordinator(hass, msg["entry_id"])
    await _guard(connection, msg, coord.async_run(coord.manager.person_enable, msg["person"], msg["enabled"]))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/credential/set", ENTRY: str,
                                  vol.Required("person"): int, vol.Required("digit"): vol.All(int, vol.Range(0, 9)),
                                  vol.Optional("card"): vol.Any(None, vol.All(int, vol.Range(1, 4294967295))),
                                  vol.Optional("code"): vol.Any(None, str)})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_credential_set(hass, connection, msg) -> None:
    coord = _coordinator(hass, msg["entry_id"])
    card = msg["card"] if "card" in msg else "keep"
    code = msg["code"] if "code" in msg else "keep"
    await _guard(connection, msg, coord.async_run(coord.manager.credential_set, msg["person"],
                                                 msg["digit"], card, code))


# --------------------------------------------------------------------- fingers
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/finger/delete", ENTRY: str,
                                  vol.Required("user_id"): int, vol.Required("finger_id"): int})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_finger_delete(hass, connection, msg) -> None:
    coord = _coordinator(hass, msg["entry_id"])
    await _guard(connection, msg, coord.async_run(coord.manager.finger_delete, msg["user_id"], msg["finger_id"]))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/finger/update", ENTRY: str,
                                  vol.Required("user_id"): int, vol.Required("finger_id"): int,
                                  vol.Optional("digit"): vol.Any(None, vol.All(int, vol.Range(0, 9))),
                                  vol.Optional("physical"): vol.Any(None, vol.All(int, vol.Range(0, 9)))})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_finger_update(hass, connection, msg) -> None:
    coord = _coordinator(hass, msg["entry_id"])
    if msg.get("digit") is None:
        # Only the physical finger (or "not placed"): Home Assistant data, no device access.
        split = coord.manager.numbering.split(msg["user_id"])
        person_ids = coord.person_user_ids(split[0]) if split else []
        coord.manager.finger_place(msg["user_id"], msg["finger_id"], msg.get("physical"), person_ids)
        coord.async_rebuild()
        _reply(connection, msg, [msg["user_id"], msg["finger_id"]])
        return
    await _guard(connection, msg, coord.async_run(coord.manager.finger_update, msg["user_id"], msg["finger_id"],
                                                 msg.get("digit"), msg.get("physical")))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/enroll/start", ENTRY: str,
                                  vol.Required("person"): int, vol.Required("digit"): vol.All(int, vol.Range(0, 9)),
                                  vol.Optional("physical"): vol.Any(None, vol.All(int, vol.Range(0, 9)))})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_enroll_start(hass, connection, msg) -> None:
    coord = _coordinator(hass, msg["entry_id"])

    async def run():
        if coord.identify is not None:
            raise HomeAssistantError("a finger identification is running")
        user_id, baseline = await coord.async_run(coord.manager.enroll_start, msg["person"], msg["digit"])
        enr = coord.start_enrollment(user_id, baseline, msg.get("physical"))
        return {"user_id": user_id, "state": enr["state"]}
    await _guard(connection, msg, run())


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/enroll/cancel", ENTRY: str})
@websocket_api.require_admin
@callback
def ws_enroll_cancel(hass, connection, msg) -> None:
    _coordinator(hass, msg["entry_id"]).enrollment = None
    _reply(connection, msg)


# -------------------------------------------------------------- identification
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/identify/start", ENTRY: str,
                                  vol.Optional("person"): vol.Any(None, int),
                                  vol.Optional("physical"): vol.Any(None, vol.All(int, vol.Range(0, 9)))})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_identify_start(hass, connection, msg) -> None:
    """Identify the next finger placed on the reader. Its events are not published
    (no automation runs); with ``physical``, the finger found is placed on the hand."""
    coord = _coordinator(hass, msg["entry_id"])

    async def run():
        return coord.start_identify(msg.get("person"), msg.get("physical"))
    await _guard(connection, msg, run())


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/identify/cancel", ENTRY: str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_identify_cancel(hass, connection, msg) -> None:
    coord = _coordinator(hass, msg["entry_id"])
    await _guard(connection, msg, coord.async_identify_cancel())


# ---------------------------------------------------------------------- phones
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/phone/pair_start", ENTRY: str,
                                  vol.Required("person"): int, vol.Required("digit"): vol.All(int, vol.Range(0, 9))})
@websocket_api.require_admin
@callback
def ws_phone_pair_start(hass, connection, msg) -> None:
    coord = _coordinator(hass, msg["entry_id"])
    if not coord.store.data["tags"]:
        connection.send_error(msg["id"], "no_tag", "declare at least one NFC tag in Settings first")
        return
    if coord.store.category(msg["digit"]) is None:
        connection.send_error(msg["id"], "failed", f"category {msg['digit']} is not configured")
        return
    pairing = coord.start_phone_pairing(msg["person"], msg["digit"])
    _reply(connection, msg, {"state": pairing["state"]})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/phone/pair_cancel", ENTRY: str})
@websocket_api.require_admin
@callback
def ws_phone_pair_cancel(hass, connection, msg) -> None:
    _coordinator(hass, msg["entry_id"]).phone_pairing = None
    _reply(connection, msg)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/phone/update", ENTRY: str,
                                  vol.Required("device_id"): str,
                                  vol.Optional("digit"): vol.All(int, vol.Range(0, 9)),
                                  vol.Optional("enabled"): bool, vol.Optional("name"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_phone_update(hass, connection, msg) -> None:
    coord = _coordinator(hass, msg["entry_id"])

    async def run():
        coord.manager.phone_update(msg["device_id"], msg.get("digit"), msg.get("enabled"), msg.get("name"))
        coord.async_rebuild()
    await _guard(connection, msg, run())


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/phone/remove", ENTRY: str,
                                  vol.Required("device_id"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_phone_remove(hass, connection, msg) -> None:
    coord = _coordinator(hass, msg["entry_id"])

    async def run():
        coord.manager.phone_remove(msg["device_id"])
        coord.async_rebuild()
    await _guard(connection, msg, run())


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/tags/save", ENTRY: str,
                                  vol.Required("tags"): dict})
@websocket_api.require_admin
@callback
def ws_tags_save(hass, connection, msg) -> None:
    coord = _coordinator(hass, msg["entry_id"])
    try:
        coord.manager.tags_save(msg["tags"])
    except (ValueError, TypeError) as ex:
        connection.send_error(msg["id"], "failed", str(ex))
        return
    _reply(connection, msg)


# ------------------------------------------------------------------ unassigned
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/unassigned/assign", ENTRY: str,
                                  vol.Required("user_id"): int, vol.Required("person"): int,
                                  vol.Required("digit"): vol.All(int, vol.Range(0, 9))})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_unassigned_assign(hass, connection, msg) -> None:
    coord = _coordinator(hass, msg["entry_id"])
    await _guard(connection, msg, coord.async_run(coord.manager.assign_unassigned, msg["user_id"],
                                                 msg["person"], msg["digit"]))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/user/delete", ENTRY: str,
                                  vol.Required("user_id"): int})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_user_delete(hass, connection, msg) -> None:
    coord = _coordinator(hass, msg["entry_id"])
    await _guard(connection, msg, coord.async_run(coord.manager.delete_user_id, msg["user_id"]))


# ------------------------------------------------------------------ categories
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/categories/save", ENTRY: str,
                                  vol.Required("categories"): dict,
                                  vol.Optional("second_window"): vol.Any(None, vol.Coerce(float)),
                                  vol.Optional("numbering"): vol.In([NUMBERING_CATEGORY, NUMBERING_PLAIN])})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_categories_save(hass, connection, msg) -> None:
    coord = _coordinator(hass, msg["entry_id"])
    if "numbering" in msg:
        coord.store.data["numbering"] = msg["numbering"]
    await _guard(connection, msg, coord.async_run(coord.manager.categories_save, msg["categories"],
                                                 msg.get("second_window")))


# ------------------------------------------------------------------------ door
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/door/get", ENTRY: str,
                                  vol.Optional("door", default=1): int})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_door_get(hass, connection, msg) -> None:
    coord = _coordinator(hass, msg["entry_id"])

    async def run():
        settings = await coord.async_run(coord.manager.door_get, msg["door"], refresh=False)
        now = await coord.async_run(coord.client.get_time, refresh=False)
        return {"settings": settings, "device_time": now.isoformat() if now else None}
    await _guard(connection, msg, run())


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/door/set", ENTRY: str,
                                  vol.Optional("door", default=1): int, vol.Required("settings"): dict})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_door_set(hass, connection, msg) -> None:
    coord = _coordinator(hass, msg["entry_id"])
    await _guard(connection, msg, coord.async_run(coord.manager.door_set, msg["door"], msg["settings"],
                                                 refresh=False))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/door/control", ENTRY: str,
                                  vol.Optional("door", default=1): int,
                                  vol.Required("action"): vol.In(["open", "hold", "close", "cancel_alarm"]),
                                  vol.Optional("seconds", default=5): vol.All(int, vol.Range(1, 254))})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_door_control(hass, connection, msg) -> None:
    coord = _coordinator(hass, msg["entry_id"])
    await _guard(connection, msg, coord.async_run(coord.manager.door_control, msg["door"], msg["action"],
                                                 msg["seconds"], refresh=False))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/clock/sync", ENTRY: str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_clock_sync(hass, connection, msg) -> None:
    coord = _coordinator(hass, msg["entry_id"])
    await _guard(connection, msg, coord.async_run(coord.client.set_time, refresh=False))


# --------------------------------------------------------------------- backups
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/backup/list", ENTRY: str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_backup_list(hass, connection, msg) -> None:
    coord = _coordinator(hass, msg["entry_id"])
    await _guard(connection, msg, hass.async_add_executor_job(coord.manager.backup_list))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/backup/create", ENTRY: str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_backup_create(hass, connection, msg) -> None:
    coord = _coordinator(hass, msg["entry_id"])
    await _guard(connection, msg, coord.async_run(coord.manager.backup_create, "manual", refresh=False))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/backup/download", ENTRY: str,
                                  vol.Required("name"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_backup_download(hass, connection, msg) -> None:
    coord = _coordinator(hass, msg["entry_id"])
    await _guard(connection, msg, hass.async_add_executor_job(coord.manager.backup_read, msg["name"]))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/backup/restore", ENTRY: str,
                                  vol.Optional("name"): str, vol.Optional("content"): str,
                                  vol.Required("mode"): vol.In(["merge", "replace"])})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_backup_restore(hass, connection, msg) -> None:
    coord = _coordinator(hass, msg["entry_id"])

    async def run():
        content = msg.get("content")
        if content is None:
            content = await hass.async_add_executor_job(coord.manager.backup_read, msg["name"])
        return await coord.async_run(coord.manager.backup_restore, content, msg["mode"])
    await _guard(connection, msg, run())


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/backup/import", ENTRY: str,
                                  vol.Required("content"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_backup_import(hass, connection, msg) -> None:
    coord = _coordinator(hass, msg["entry_id"])
    await _guard(connection, msg, hass.async_add_executor_job(coord.manager.backup_import, msg["content"]))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/settings/save", ENTRY: str,
                                  vol.Optional("backup"): {vol.Optional("on_change"): bool,
                                                           vol.Optional("nightly"): bool,
                                                           vol.Optional("keep"): vol.All(int, vol.Range(1, 500))},
                                  vol.Optional("notify_keypad"): bool})
@websocket_api.require_admin
@callback
def ws_settings_save(hass, connection, msg) -> None:
    coord = _coordinator(hass, msg["entry_id"])
    if "backup" in msg:
        coord.store.data["backup"].update(msg["backup"])
    if "notify_keypad" in msg:
        coord.store.data["notify_keypad"] = msg["notify_keypad"]
    coord.store.save()
    _reply(connection, msg)


COMMANDS = [ws_devices, ws_state, ws_history, ws_subscribe, ws_person_save, ws_person_delete,
            ws_person_enable, ws_credential_set, ws_finger_delete, ws_finger_update, ws_enroll_start,
            ws_enroll_cancel, ws_identify_start, ws_identify_cancel, ws_phone_pair_start, ws_phone_pair_cancel, ws_phone_update, ws_phone_remove,
            ws_tags_save, ws_unassigned_assign, ws_user_delete, ws_categories_save, ws_door_get,
            ws_door_set, ws_door_control, ws_clock_sync, ws_backup_list, ws_backup_create,
            ws_backup_download, ws_backup_restore, ws_backup_import, ws_settings_save]


@callback
def async_register(hass: HomeAssistant) -> None:
    for command in COMMANDS:
        websocket_api.async_register_command(hass, command)
