"""Access events as a Home Assistant event entity."""
from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZKConfigEntry
from .const import EVENT_TYPE_DENIED, EVENT_TYPE_GRANTED, EVENT_TYPE_OTHER
from .entity import ZKEntity


async def async_setup_entry(hass: HomeAssistant, entry: ZKConfigEntry,
                            add: AddEntitiesCallback) -> None:
    add([ZKAccessEvent(entry.runtime_data)])


class ZKAccessEvent(ZKEntity, EventEntity):
    """Fires ``granted`` / ``denied`` / ``other`` with the PIN, card and event code."""

    _attr_translation_key = "access"
    _attr_event_types = [EVENT_TYPE_GRANTED, EVENT_TYPE_DENIED, EVENT_TYPE_OTHER]

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "access")

    @callback
    def _handle_coordinator_update(self) -> None:
        data = self.coordinator.data
        if not data or not data.events:
            return
        for ev in data.events:
            kind = EVENT_TYPE_GRANTED if ev.granted else EVENT_TYPE_DENIED if ev.denied else EVENT_TYPE_OTHER
            payload = self.coordinator.event_payload(ev)
            payload.pop("device_id", None)
            self._trigger_event(kind, payload)
            self.async_write_ha_state()
