"""Buttons: open each door, sync the clock."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pyzkaccess_native import ZKError

from . import ZKConfigEntry
from .const import CONF_OPEN_SECONDS, DEFAULT_OPEN_SECONDS
from .entity import ZKEntity


async def async_setup_entry(hass: HomeAssistant, entry: ZKConfigEntry,
                            add: AddEntitiesCallback) -> None:
    coord = entry.runtime_data
    doors = (coord.info.lock_count if coord.info else None) or 1
    add([ZKOpenDoor(coord, d) for d in range(1, doors + 1)] + [ZKSyncClock(coord)])


class _ZKButton(ZKEntity, ButtonEntity):
    def _action(self) -> None:
        raise NotImplementedError

    async def async_press(self) -> None:
        def run():
            if not self.coordinator.client.connected:
                self.coordinator.client.connect()
            self._action()
        try:
            await self.hass.async_add_executor_job(run)
        except ZKError as ex:
            raise HomeAssistantError(str(ex)) from ex


class ZKOpenDoor(_ZKButton):
    _attr_translation_key = "open_door"

    def __init__(self, coordinator, door: int) -> None:
        super().__init__(coordinator, f"open_door_{door}")
        self.door = door
        self._attr_translation_placeholders = {"door": str(door)}

    def _action(self) -> None:
        seconds = self.coordinator.config_entry.options.get(CONF_OPEN_SECONDS, DEFAULT_OPEN_SECONDS)
        self.coordinator.client.open_door(self.door, seconds)


class ZKSyncClock(_ZKButton):
    _attr_translation_key = "sync_clock"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "sync_clock")

    def _action(self) -> None:
        self.coordinator.client.set_time()
