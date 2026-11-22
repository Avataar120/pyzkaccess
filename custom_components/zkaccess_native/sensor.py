"""Counters and last access sensors."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZKConfigEntry
from .const import RESULT_ACTION, RESULT_GRANTED
from .entity import ZKEntity


async def async_setup_entry(hass: HomeAssistant, entry: ZKConfigEntry,
                            add: AddEntitiesCallback) -> None:
    coord = entry.runtime_data
    add([ZKCount(coord, "persons"), ZKCount(coord, "fingerprints"), ZKCount(coord, "cards"),
         ZKLastPerson(coord)])


class ZKCount(ZKEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, what: str) -> None:
        super().__init__(coordinator, what)
        self.what = what
        self._attr_translation_key = what

    @property
    def native_value(self) -> int | None:
        snap = self.coordinator.snapshot
        return snap["counts"].get(self.what) if snap else None


class ZKLastPerson(ZKEntity, SensorEntity):
    """Name (or UserID) of the last person who was let in or triggered an action."""

    _attr_translation_key = "last_person"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "last_person")
        self._attr_native_value = None

    @callback
    def _handle_coordinator_update(self) -> None:
        data = self.coordinator.data
        known = [e for e in (data.events if data else [])
                 if e["result"] in (RESULT_GRANTED, RESULT_ACTION) and e["user_id"]]
        if known:
            last = known[-1]
            self._attr_native_value = last["person_name"] or f"UserID {last['user_id']}"
            self._attr_extra_state_attributes = {
                k: last[k] for k in ("person", "user_id", "category", "method", "gesture", "door", "time")}
        super()._handle_coordinator_update()
