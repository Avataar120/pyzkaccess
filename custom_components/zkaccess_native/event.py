"""Access events as a Home Assistant event entity."""
from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZKConfigEntry
from .const import RESULT_ACTION, RESULT_DENIED, RESULT_GRANTED, RESULT_OTHER
from .entity import ZKEntity
from .helpers import PRIVATE_EVENT_FIELDS

# Entities pushed by the coordinator (device access is serialized by the coordinator anyway).
PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry: ZKConfigEntry,
                            add: AddEntitiesCallback) -> None:
    add([ZKAccessEvent(entry.runtime_data)])


class ZKAccessEvent(ZKEntity, EventEntity):
    """Fires ``granted`` / ``action`` / ``denied`` / ``other`` with person, category and method."""

    _attr_translation_key = "access"
    _attr_event_types = [RESULT_GRANTED, RESULT_ACTION, RESULT_DENIED, RESULT_OTHER]

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "access")

    @callback
    def _handle_coordinator_update(self) -> None:
        data = self.coordinator.data
        if not data or not data.events:
            return
        for ev in data.events:
            attrs = {k: v for k, v in ev.items() if k not in ("device_id", "result", *PRIVATE_EVENT_FIELDS)}
            self._trigger_event(ev["result"], attrs)
            self.async_write_ha_state()
