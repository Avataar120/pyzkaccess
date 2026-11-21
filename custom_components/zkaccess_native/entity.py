"""Base entity."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ZKCoordinator


class ZKEntity(CoordinatorEntity[ZKCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: ZKCoordinator, key: str) -> None:
        super().__init__(coordinator)
        serial = coordinator.config_entry.unique_id
        info = coordinator.info
        self._attr_unique_id = f"{serial}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            manufacturer="ZKTeco",
            model=f"Machine type {info.machine_type}" if info and info.machine_type else None,
            name=coordinator.config_entry.title,
            serial_number=serial,
            sw_version=info.firmware if info else None,
        )
