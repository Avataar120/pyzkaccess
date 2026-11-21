"""Counters and last access sensors."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZKConfigEntry
from .entity import ZKEntity


async def async_setup_entry(hass: HomeAssistant, entry: ZKConfigEntry,
                            add: AddEntitiesCallback) -> None:
    coord = entry.runtime_data
    add([ZKCount(coord, "users"), ZKCount(coord, "templates"), ZKLastPin(coord)])


class ZKCount(ZKEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, what: str) -> None:
        super().__init__(coordinator, what)
        self.what = what
        self._attr_translation_key = what

    @property
    def native_value(self) -> int | None:
        return getattr(self.coordinator.data, self.what, None) if self.coordinator.data else None


class ZKLastPin(ZKEntity, SensorEntity):
    """PIN of the last user who was granted access."""

    _attr_translation_key = "last_user"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "last_user")
        self._attr_native_value = None

    @callback
    def _handle_coordinator_update(self) -> None:
        data = self.coordinator.data
        granted = [e for e in (data.events if data else []) if e.granted and e.pin]
        if granted:
            last = granted[-1]
            self._attr_native_value = last.pin
            self._attr_extra_state_attributes = {"door": last.door, "event": int(last.event),
                                                 "time": last.time.isoformat() if last.time else None}
        super()._handle_coordinator_update()
