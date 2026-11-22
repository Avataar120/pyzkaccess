"""One switch per person: access enabled / disabled (the panel's "Access enabled" toggle).

Off removes all the person's rights on the device (remembered) and Home Assistant treats
their credentials as denied; on puts the rights back. Usable in automations (schedules),
dashboards and voice assistants.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZKConfigEntry
from .coordinator import ZKCoordinator, signal_changed
from .entity import ZKEntity

# Entities one device action at a time (device access is serialized by the coordinator anyway).
PARALLEL_UPDATES = 1


async def async_setup_entry(hass: HomeAssistant, entry: ZKConfigEntry,
                            add: AddEntitiesCallback) -> None:
    coord = entry.runtime_data
    known: set[int] = set()

    @callback
    def add_new(_extra: Any = None) -> None:
        persons = (coord.snapshot or {}).get("persons", [])
        new = [p["num"] for p in persons if p["num"] not in known]
        if new:
            known.update(new)
            add([ZKPersonAccess(coord, num) for num in new])

    add_new()
    entry.async_on_unload(async_dispatcher_connect(hass, signal_changed(entry.entry_id), add_new))


#: Name prefix by Home Assistant language. The name is built here rather than from a
#: translation placeholder: Home Assistant does not recompute a translated name, and the
#: person can be renamed while the entity exists.
PREFIX = {"fr": "Accès", "de": "Zugang", "es": "Acceso", "it": "Accesso", "nl": "Toegang"}


class ZKPersonAccess(ZKEntity, SwitchEntity):
    # translation_key gives the icon (icons.json); the name is set at runtime (_attr_name).
    _attr_translation_key = "person_access"

    def __init__(self, coordinator: ZKCoordinator, person: int) -> None:
        super().__init__(coordinator, f"person_{person}_access")
        self.person = person
        self._sync_name()

    def _person(self) -> dict[str, Any] | None:
        return next((p for p in (self.coordinator.snapshot or {}).get("persons", [])
                     if p["num"] == self.person), None)

    def _sync_name(self) -> None:
        p = self._person()
        name = (p["name"] if p else "") or f"#{self.person}"
        lang = (self.coordinator.hass.config.language or "en").split("-")[0]
        self._attr_name = f"{PREFIX.get(lang, 'Access')} {name}"

    @property
    def available(self) -> bool:
        return super().available and self._person() is not None

    @property
    def is_on(self) -> bool | None:
        p = self._person()
        return bool(p["enabled"]) if p else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"person": self.person}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_run(self.coordinator.manager.person_enable, self.person, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_run(self.coordinator.manager.person_enable, self.person, False)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def changed(_extra: Any = None) -> None:
            self._sync_name()
            self.async_write_ha_state()
        self.async_on_remove(async_dispatcher_connect(
            self.hass, signal_changed(self.coordinator.config_entry.entry_id), changed))
