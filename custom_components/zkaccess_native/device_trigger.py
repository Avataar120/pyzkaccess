"""Device triggers: react to an access without writing an event trigger by hand.

In the automation editor: "MA500 reader - Category Garage presented" (optionally by one
person), "Access denied", "Unknown card", "Unknown fingerprint". The trigger variables carry
the whole access event in ``trigger.event.data`` (person_name, category, method...).
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.device_automation.exceptions import InvalidDeviceAutomationConfig
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import CALLBACK_TYPE, Event, HassJob, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, EVENT_ACCESS, RESULT_ACTION, RESULT_DENIED, RESULT_GRANTED

CONF_SUBTYPE = "subtype"
CONF_PERSON = "person"

TYPE_CATEGORY = "category"
TYPE_DENIED = "denied"
TYPE_UNKNOWN_CARD = "unknown_card"
TYPE_UNKNOWN_FINGER = "unknown_fingerprint"
TRIGGER_TYPES = (TYPE_CATEGORY, TYPE_DENIED, TYPE_UNKNOWN_CARD, TYPE_UNKNOWN_FINGER)
UNKNOWN_CODES = {TYPE_UNKNOWN_CARD: 27, TYPE_UNKNOWN_FINGER: 34}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend({
    vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES),
    vol.Optional(CONF_SUBTYPE): cv.string,          # "8 · Garage": category digit, then its name
    vol.Optional(CONF_PERSON): vol.Coerce(int),     # person number, optional
})


def _coordinator(hass: HomeAssistant, device_id: str):
    device = dr.async_get(hass).async_get(device_id)
    if device:
        # Matched by the device identifier (the entry's serial number), not by the deprecated
        # DeviceEntry.config_entries.
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            if (DOMAIN, entry.unique_id) in device.identifiers and hasattr(entry, "runtime_data"):
                return entry.runtime_data
    return None


async def async_validate_trigger_config(hass: HomeAssistant, config: ConfigType) -> ConfigType:
    config = TRIGGER_SCHEMA(config)
    if config[CONF_TYPE] == TYPE_CATEGORY and _digit(config.get(CONF_SUBTYPE)) is None:
        raise InvalidDeviceAutomationConfig("a category trigger needs a category, e.g. '8 · Garage'")
    return config


def _digit(subtype: Any) -> str | None:
    head = str(subtype or "").strip()[:1]
    return head if head.isdigit() else None


async def async_get_triggers(hass: HomeAssistant, device_id: str) -> list[dict[str, Any]]:
    base = {CONF_PLATFORM: "device", CONF_DOMAIN: DOMAIN, CONF_DEVICE_ID: device_id}
    coord = _coordinator(hass, device_id)
    categories = coord.store.categories if coord else {}
    # The subtype reads "8 · Garage" in the editor; only the digit is matched, so renaming a
    # category does not break existing automations.
    triggers = [{**base, CONF_TYPE: TYPE_CATEGORY, CONF_SUBTYPE: f"{digit} · {categories[digit]['name']}"}
                for digit in sorted(categories, key=int)]
    triggers += [{**base, CONF_TYPE: t} for t in (TYPE_DENIED, TYPE_UNKNOWN_CARD, TYPE_UNKNOWN_FINGER)]
    return triggers


async def async_get_trigger_capabilities(hass: HomeAssistant, config: ConfigType) -> dict[str, vol.Schema]:
    """Optional person filter (category triggers)."""
    if config.get(CONF_TYPE) != TYPE_CATEGORY:
        return {}
    coord = _coordinator(hass, config[CONF_DEVICE_ID])
    persons = (coord.snapshot or {}).get("persons", []) if coord else []
    options = {p["num"]: (p["name"] or f"#{p['num']}") for p in persons}
    if not options:
        return {}
    return {"extra_fields": vol.Schema({vol.Optional(CONF_PERSON): vol.In(options)})}


async def async_attach_trigger(hass: HomeAssistant, config: ConfigType, action: TriggerActionType,
                               trigger_info: TriggerInfo) -> CALLBACK_TYPE:
    device_id = config[CONF_DEVICE_ID]
    kind = config[CONF_TYPE]
    subtype = config.get(CONF_SUBTYPE)
    digit = _digit(subtype)
    person = config.get(CONF_PERSON)
    job = HassJob(action)
    trigger_data = trigger_info["trigger_data"]

    @callback
    def matches(data: dict[str, Any]) -> bool:
        if data.get("device_id") != device_id:
            return False
        result = data.get("result")
        if kind == TYPE_CATEGORY:
            return (result in (RESULT_GRANTED, RESULT_ACTION) and str(data.get("category_digit")) == str(digit)
                    and (person is None or data.get("person") == person))
        if kind == TYPE_DENIED:
            return result == RESULT_DENIED
        return result == RESULT_DENIED and data.get("event") == UNKNOWN_CODES[kind]

    @callback
    def listener(event: Event) -> None:
        if not matches(event.data):
            return
        hass.async_run_hass_job(job, {"trigger": {
            **trigger_data, CONF_PLATFORM: "device", CONF_DOMAIN: DOMAIN, CONF_DEVICE_ID: device_id,
            CONF_TYPE: kind, CONF_SUBTYPE: subtype, CONF_PERSON: person, "event": event,
            "description": f"ZKAccess {kind}",
        }}, event.context)

    return hass.bus.async_listen(EVENT_ACCESS, listener)
