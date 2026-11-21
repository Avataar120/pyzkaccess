"""Polling coordinator: one TCP session per device, shared by every entity and service."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pyzkaccess_native import AccessEvent, DeviceInfo, DoorStatus, ZKAccess, ZKError

from .const import (CONF_POLL_INTERVAL, CONF_SYNC_CLOCK, COUNT_REFRESH_SECONDS,
                    DEFAULT_POLL_INTERVAL, DEFAULT_SYNC_CLOCK, DOMAIN, EVENT_ACCESS)

_LOGGER = logging.getLogger(__name__)


@dataclass
class ZKData:
    """Snapshot published to entities after each poll."""

    status: Optional[DoorStatus] = None
    events: list[AccessEvent] = field(default_factory=list)
    users: Optional[int] = None
    templates: Optional[int] = None


class ZKCoordinator(DataUpdateCoordinator[ZKData]):
    """Polls the real-time log and keeps the connection alive."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        interval = entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry,
                         update_interval=timedelta(seconds=interval), always_update=False)
        self.client = ZKAccess(entry.data[CONF_HOST], entry.data.get(CONF_PORT, 4370),
                               entry.data.get(CONF_PASSWORD, ""))
        self.info: Optional[DeviceInfo] = None
        self._counted_at: Optional[float] = None  # None = count at the next poll
        self._counts: tuple[Optional[int], Optional[int]] = (None, None)

    # Runs in the executor ---------------------------------------------------
    def _connect(self) -> None:
        self.client.connect()
        self.info = self.client.info()
        if self.config_entry.options.get(CONF_SYNC_CLOCK, DEFAULT_SYNC_CLOCK):
            self.client.set_time()

    def _poll(self) -> ZKData:
        if not self.client.connected:
            self._connect()
        records = self.client.poll()
        if self._counted_at is None or time.monotonic() - self._counted_at > COUNT_REFRESH_SECONDS:
            tables = self.client.tables()
            self._counts = (self.client.count("user") if "user" in tables else None,
                            self.client.count("templatev10") if "templatev10" in tables else None)
            self._counted_at = time.monotonic()
        status = next((r for r in reversed(records) if isinstance(r, DoorStatus)), None)
        return ZKData(status=status or (self.data.status if self.data else None),
                      events=[r for r in records if isinstance(r, AccessEvent)],
                      users=self._counts[0], templates=self._counts[1])

    def refresh_counts_soon(self) -> None:
        self._counted_at = None

    # Event loop -------------------------------------------------------------
    async def _async_update_data(self) -> ZKData:
        try:
            data = await self.hass.async_add_executor_job(self._poll)
        except (ZKError, OSError) as ex:
            await self.hass.async_add_executor_job(self.client.close)
            raise UpdateFailed(f"cannot reach {self.client.host}: {ex}") from ex
        for ev in data.events:
            self.hass.bus.async_fire(EVENT_ACCESS, self.event_payload(ev))
        return data

    def event_payload(self, ev: AccessEvent) -> dict[str, Any]:
        device = dr.async_get(self.hass).async_get_device({(DOMAIN, self.config_entry.unique_id)})
        return {
            "device_id": device.id if device else None,
            "event": int(ev.event),
            "event_name": ev.label,
            "pin": ev.pin,
            "card": ev.card,
            "door": ev.door,
            "verify_mode": int(ev.verify_mode),
            "granted": ev.granted,
            "denied": ev.denied,
            "time": ev.time.isoformat() if ev.time else None,
        }

    async def async_shutdown(self) -> None:
        await super().async_shutdown()
        await self.hass.async_add_executor_job(self.client.disconnect)
