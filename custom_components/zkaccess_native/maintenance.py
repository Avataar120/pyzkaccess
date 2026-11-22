"""Maintenance: response time, reboot (button, nightly, slow device)."""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import (RESTART_WAIT_SECONDS, SLOW_RESTART_COOLDOWN_SECONDS, SLOW_THRESHOLD_SECONDS)
from .helpers import signal_changed

_LOGGER = logging.getLogger(__package__ + ".coordinator")


class MaintenanceMixin:
    """Part of ZKCoordinator (mixed in); uses its client, store, manager and snapshot."""

    # ---------------------------------------------------------------- maintenance
    @property
    def maintenance(self) -> dict[str, Any]:
        return self.store.data.setdefault("maintenance", {"nightly_restart": True, "slow_restart": True})

    def _measure_response(self) -> float:
        """Median of 3 clock reads (a cheap request): the device's own response time."""
        times = []
        for _ in range(3):
            start = time.monotonic()
            self.client.get_time()
            times.append(time.monotonic() - start)
        return sorted(times)[1]

    async def async_check_response_time(self) -> Optional[float]:
        """Measure the response time; reboot a slow device when allowed (at most every 6 h)."""
        try:
            value = await self.async_run(self._measure_response, refresh=False, background=True)
        except HomeAssistantError as ex:
            _LOGGER.debug("Response time not measured: %s", ex)
            return None
        self.response_time = round(value, 3)
        self.async_update_listeners()
        if (value > SLOW_THRESHOLD_SECONDS and self.maintenance.get("slow_restart", True)
                and (self._last_auto_restart is None
                     or time.monotonic() - self._last_auto_restart > SLOW_RESTART_COOLDOWN_SECONDS)):
            _LOGGER.warning("%s answers in %.2f s (over %.1f s): rebooting it",
                            self.client.host, value, SLOW_THRESHOLD_SECONDS)
            self._last_auto_restart = time.monotonic()
            await self.async_restart_device("slow")
        return self.response_time

    async def async_restart_device(self, reason: str) -> None:
        """Reboot the device (about 25 s). Data and lock are untouched; the integration
        reconnects by itself once the device is back."""
        def job() -> None:
            self.client.restart()                      # closes the connection
            # No reconnection attempt while it boots: hammering a booting device froze it once.
            self._retry_at = self._boot_until = time.monotonic() + RESTART_WAIT_SECONDS
        await self.async_run(job, refresh=False, background=reason in ("nightly", "slow"))
        self.store.data["last_restart"] = {"time": dt_util.now().isoformat(timespec="seconds"), "reason": reason}
        self.store.save()
        _LOGGER.info("%s rebooted (%s)", self.client.host, reason)
        async_dispatcher_send(self.hass, signal_changed(self.config_entry.entry_id))
