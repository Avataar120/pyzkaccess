"""ZKAccess Native: ZKTeco PullSDK / ZKAccess devices in Home Assistant, without any DLL."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_ID, Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType
from pyzkaccess_native import User, ZKError
from pyzkaccess_native import backup as zk_backup

from .const import DOMAIN
from .coordinator import ZKCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.EVENT, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type ZKConfigEntry = ConfigEntry[ZKCoordinator]

_BASE = {vol.Required(ATTR_DEVICE_ID): cv.string}
SCHEMAS = {
    "set_user": vol.Schema({**_BASE, vol.Required("pin"): cv.positive_int,
                            vol.Optional("name"): cv.string,
                            vol.Optional("card"): cv.positive_int,
                            vol.Optional("password"): cv.string}),
    "delete_user": vol.Schema({**_BASE, vol.Required("pin"): cv.positive_int}),
    "grant": vol.Schema({**_BASE, vol.Required("pin"): cv.positive_int,
                         vol.Optional("timezone", default=1): cv.positive_int,
                         vol.Optional("doors", default=1): cv.positive_int}),
    "revoke": vol.Schema({**_BASE, vol.Required("pin"): cv.positive_int}),
    "open_door": vol.Schema({**_BASE, vol.Optional("door", default=1): cv.positive_int,
                             vol.Optional("seconds", default=5): vol.All(int, vol.Range(1, 254))}),
    "sync_clock": vol.Schema(_BASE),
    "backup": vol.Schema(_BASE),
    "list_users": vol.Schema(_BASE),
}


def _coordinator(hass: HomeAssistant, device_id: str) -> ZKCoordinator:
    device = dr.async_get(hass).async_get(device_id)
    if device:
        for entry_id in device.config_entries:
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry and entry.domain == DOMAIN and hasattr(entry, "runtime_data"):
                return entry.runtime_data
    raise ServiceValidationError(f"{device_id} is not a loaded ZKAccess device")


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services once for all devices."""

    async def handle(call: ServiceCall):
        coord = _coordinator(hass, call.data[ATTR_DEVICE_ID])
        dev = coord.client
        d = call.data

        def run():
            if not dev.connected:
                dev.connect()
            if call.service == "set_user":
                user = dev.save_user(User(d["pin"], name=d.get("name"), card_no=d.get("card"),
                                          password=d.get("password")))
                return {"pin": user.pin, "name": user.name, "uid": user.uid}
            if call.service == "delete_user":
                dev.delete_user(d["pin"])
            elif call.service == "grant":
                dev.grant(d["pin"], d["timezone"], d["doors"])
            elif call.service == "revoke":
                dev.revoke(d["pin"])
            elif call.service == "open_door":
                dev.open_door(d["door"], d["seconds"])
            elif call.service == "sync_clock":
                dev.set_time()
            elif call.service == "list_users":
                return {"users": [{"pin": u.pin, "name": u.name, "card": u.card_no, "uid": u.uid}
                                  for u in dev.get_users()]}
            elif call.service == "backup":
                folder = Path(hass.config.path(DOMAIN))
                folder.mkdir(exist_ok=True)
                path = folder / f"backup_{coord.config_entry.unique_id}_{datetime.now():%Y%m%d_%H%M%S}.json"
                snap = zk_backup.backup(dev)
                zk_backup.save(snap, path)
                path.chmod(0o600)  # contains biometric templates
                return {"file": str(path), "tables": {k: len(v) for k, v in snap["tables"].items()}}
            return None

        try:
            result = await hass.async_add_executor_job(run)
        except ZKError as ex:
            raise HomeAssistantError(f"{call.service} failed: {ex}") from ex
        coord.refresh_counts_soon()
        return result

    for name, schema in SCHEMAS.items():
        returns = name in ("set_user", "list_users", "backup")
        hass.services.async_register(DOMAIN, name, handle, schema=schema,
                                     supports_response=SupportsResponse.OPTIONAL if returns
                                     else SupportsResponse.NONE)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ZKConfigEntry) -> bool:
    coordinator = ZKCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def _reload(hass: HomeAssistant, entry: ZKConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ZKConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_shutdown()
    return ok
