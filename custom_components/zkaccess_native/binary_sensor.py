"""Door sensor and alarm binary sensors (one pair per door)."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZKConfigEntry
from .entity import ZKEntity

# Entities read from the coordinator (device access is serialized by the coordinator anyway).
PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry: ZKConfigEntry,
                            add: AddEntitiesCallback) -> None:
    coord = entry.runtime_data
    doors = (coord.info.lock_count if coord.info else None) or 1
    entities: list = [ZKConnected(coord)]
    for door in range(1, doors + 1):
        entities += [ZKDoorSensor(coord, door), ZKAlarm(coord, door)]
    add(entities)


def _no_sensor(coordinator, door: int) -> bool:
    """The device reports no door contact for this door (sensor type "none")."""
    status = coordinator.data.status if coordinator.data else None
    return status is not None and status.door_open(door) is None


class ZKDoorSensor(ZKEntity, BinarySensorEntity):
    """State of the door contact wired to the device (unknown when none is configured)."""

    _attr_device_class = BinarySensorDeviceClass.DOOR
    _attr_translation_key = "door"

    def __init__(self, coordinator, door: int) -> None:
        super().__init__(coordinator, f"door_{door}")
        self.door = door
        self._attr_translation_placeholders = {"door": str(door)}
        # No contact wired: the entity would stay "unknown"; it can still be enabled by hand.
        self._attr_entity_registry_enabled_default = not _no_sensor(coordinator, door)

    @property
    def is_on(self) -> bool | None:
        status = self.coordinator.data.status if self.coordinator.data else None
        return status.door_open(self.door) if status else None


class ZKAlarm(ZKEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "alarm"

    def __init__(self, coordinator, door: int) -> None:
        super().__init__(coordinator, f"alarm_{door}")
        self.door = door
        self._attr_translation_placeholders = {"door": str(door)}
        # The alarm (forced open, open too long) relies on the door contact too.
        self._attr_entity_registry_enabled_default = not _no_sensor(coordinator, door)

    @property
    def is_on(self) -> bool | None:
        status = self.coordinator.data.status if self.coordinator.data else None
        if status is None:
            return None
        return status.alarm(self.door) or status.open_timeout(self.door)


class ZKConnected(ZKEntity, BinarySensorEntity):
    """Is the device reachable? Stays available when the device is not, so it can be alerted on."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "connected"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "connected")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success
