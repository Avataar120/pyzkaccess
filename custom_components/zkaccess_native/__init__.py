"""ZKAccess Native: ZKTeco PullSDK / ZKAccess devices in Home Assistant, without any DLL."""
from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_ID, Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration
from pyzkaccess_native import User

from . import websocket
from .const import DOMAIN
from .coordinator import ZKCoordinator
from .panel import async_register_panel, async_rename_panel, async_unregister_panel
from .store import AccessStore

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.EVENT, Platform.SENSOR, Platform.SWITCH]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type ZKConfigEntry = ConfigEntry[ZKCoordinator]

_BASE = {vol.Required(ATTR_DEVICE_ID): cv.string}
USER_ID = vol.All(vol.Coerce(int), vol.Range(1, 999_999_999))
SCHEMAS = {
    "set_user": vol.Schema({**_BASE, vol.Required("user_id"): USER_ID,
                            vol.Optional("name"): cv.string,
                            vol.Optional("card"): cv.positive_int,
                            vol.Optional("code"): cv.string}),
    "delete_user": vol.Schema({**_BASE, vol.Required("user_id"): USER_ID}),
    "grant": vol.Schema({**_BASE, vol.Required("user_id"): USER_ID,
                         vol.Optional("timezone", default=1): cv.positive_int,
                         vol.Optional("doors", default=1): cv.positive_int}),
    "revoke": vol.Schema({**_BASE, vol.Required("user_id"): USER_ID}),
    "open_door": vol.Schema({**_BASE, vol.Optional("door", default=1): cv.positive_int,
                             vol.Optional("seconds", default=5): vol.All(int, vol.Range(1, 254))}),
    "sync_clock": vol.Schema(_BASE),
    "backup": vol.Schema(_BASE),
    "list_users": vol.Schema(_BASE),
    # Person / category model (prefer these to the raw UserID services above).
    "set_access": vol.Schema({**_BASE, vol.Required("person"): vol.All(vol.Coerce(int), vol.Range(1, 99_999)),
                              vol.Optional("category"): vol.All(vol.Coerce(int), vol.Range(0, 9)),
                              vol.Required("enabled"): cv.boolean}),
    "rename_person": vol.Schema({**_BASE, vol.Required("person"): vol.All(vol.Coerce(int), vol.Range(1, 99_999)),
                                 vol.Required("name"): vol.All(cv.string, vol.Length(min=1, max=24))}),
}


def _coordinator(hass: HomeAssistant, device_id: str) -> ZKCoordinator:
    device = dr.async_get(hass).async_get(device_id)
    if device:
        for entry_id in device.config_entries:
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry and entry.domain == DOMAIN and hasattr(entry, "runtime_data"):
                return entry.runtime_data
    raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded",
                                 translation_placeholders={"device": device_id})


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services and the panel API once for all devices."""
    websocket.async_register(hass)

    async def handle(call: ServiceCall):
        coord = _coordinator(hass, call.data[ATTR_DEVICE_ID])
        dev = coord.client
        d = call.data
        service = call.service

        if service == "set_access":
            person = d["person"]
            if not any(p["num"] == person for p in (coord.snapshot or {}).get("persons", [])):
                raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unknown_person",
                                             translation_placeholders={"person": str(person)})
            if "category" in d:
                await coord.async_run(coord.manager.category_enable, person, d["category"], d["enabled"])
            else:
                await coord.async_run(coord.manager.person_enable, person, d["enabled"])
            return None
        if service == "rename_person":
            person = d["person"]
            if not any(p["num"] == person for p in (coord.snapshot or {}).get("persons", [])):
                raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unknown_person",
                                             translation_placeholders={"person": str(person)})
            ha_person = coord.store.person_meta(person).get("ha_person")
            await coord.async_run(coord.manager.person_save, person, d["name"], ha_person)
            return None
        if service == "backup":
            name = await coord.async_run(coord.manager.backup_create, "service", refresh=False)
            return {"file": name}
        if service == "set_user":
            user = await coord.async_run(dev.save_user, User(d["user_id"], name=d.get("name"),
                                                             card_no=d.get("card"), password=d.get("code")))
            return {"user_id": user.user_id, "name": user.name}
        if service == "delete_user":
            await coord.async_run(dev.delete_user, d["user_id"])
        elif service == "grant":
            await coord.async_run(dev.grant, d["user_id"], d["timezone"], d["doors"])
        elif service == "revoke":
            await coord.async_run(dev.revoke, d["user_id"])
        elif service == "open_door":
            await coord.async_run(dev.open_door, d["door"], d["seconds"], refresh=False)
        elif service == "sync_clock":
            await coord.async_run(dev.set_time, refresh=False)
        elif service == "list_users":
            users = await coord.async_run(dev.get_users, refresh=False)
            return {"users": [{"user_id": u.user_id, "name": u.name, "card": u.card_no or None,
                               "has_code": bool(u.password)} for u in users]}
        return None

    for name, schema in SCHEMAS.items():
        returns = name in ("set_user", "list_users", "backup")
        hass.services.async_register(DOMAIN, name, handle, schema=schema,
                                     supports_response=SupportsResponse.OPTIONAL if returns
                                     else SupportsResponse.NONE)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ZKConfigEntry) -> bool:
    store = AccessStore(hass, entry.entry_id)
    await store.async_load()
    coordinator = ZKCoordinator(hass, entry, store)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await coordinator.async_setup_schedules()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    integration = await async_get_integration(hass, DOMAIN)
    await async_register_panel(hass, str(integration.version), store.data.get("panel_title"))
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def _reload(hass: HomeAssistant, entry: ZKConfigEntry) -> None:
    # A new poll interval is applied live; other option changes need a reload.
    if entry.runtime_data.apply_options(dict(entry.options)):
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ZKConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_shutdown()
        if not [e for e in hass.config_entries.async_loaded_entries(DOMAIN) if e.entry_id != entry.entry_id]:
            async_unregister_panel(hass)
    return ok


async def async_remove_entry(hass: HomeAssistant, entry: ZKConfigEntry) -> None:
    await AccessStore(hass, entry.entry_id).async_remove()
