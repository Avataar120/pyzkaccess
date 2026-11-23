"""Maintenance: response time, reboot (button, nightly, slow device)."""
from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any, Optional

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import (DOMAIN, RESTART_WAIT_SECONDS, SLOW_RESTART_COOLDOWN_SECONDS, SLOW_RESTARTS_ISSUE_COUNT,
                    SLOW_THRESHOLD_SECONDS, UNREACHABLE_ISSUE_SECONDS)
from .helpers import signal_changed

#: reboots kept for the response time chart
RESTART_HISTORY_DAYS = timedelta(days=366)
RESTART_HISTORY_MAX = 1000

_LOGGER = logging.getLogger(__package__ + ".coordinator")


class MaintenanceMixin:
    """Part of ZKCoordinator (mixed in); uses its client, store, manager and snapshot."""

    # ------------------------------------------------------------------- repairs
    def _issue_id(self, kind: str) -> str:
        return f"{kind}_{self.config_entry.entry_id}"

    def _issue_placeholders(self) -> dict[str, str]:
        return {"name": self.config_entry.title.strip() or str(self.config_entry.unique_id),
                "host": self.client.host}

    @callback
    def _track_reachability(self, reachable: bool) -> None:
        """Repairs: "device unreachable" after 30 min without an answer, removed as soon as it
        answers again (also after a restart of Home Assistant)."""
        if reachable:
            if self._unreachable_since is not None or self._unreachable_issue:
                self._unreachable_since = None
                self._unreachable_issue = False
                ir.async_delete_issue(self.hass, DOMAIN, self._issue_id("unreachable"))
            return
        now = time.monotonic()
        if self._unreachable_since is None:
            self._unreachable_since = now
        elif not self._unreachable_issue and now - self._unreachable_since > UNREACHABLE_ISSUE_SECONDS:
            self._unreachable_issue = True
            ir.async_create_issue(
                self.hass, DOMAIN, self._issue_id("unreachable"), is_fixable=False,
                severity=ir.IssueSeverity.WARNING, translation_key="unreachable",
                translation_placeholders={**self._issue_placeholders(),
                                          "minutes": str(UNREACHABLE_ISSUE_SECONDS // 60)})

    @callback
    def _track_slow_restarts(self, restarted: bool) -> None:
        """Repairs: "rebooted for slowness N times in 24 h", removed after 24 h without one."""
        now = time.time()
        if restarted:
            self._slow_restarts.append(now)
        self._slow_restarts = [t for t in self._slow_restarts if now - t < 24 * 3600]
        if len(self._slow_restarts) >= SLOW_RESTARTS_ISSUE_COUNT:
            ir.async_create_issue(
                self.hass, DOMAIN, self._issue_id("slow_restarts"), is_fixable=False,
                severity=ir.IssueSeverity.WARNING, translation_key="slow_restarts",
                translation_placeholders={**self._issue_placeholders(),
                                          "count": str(len(self._slow_restarts))})
        elif not self._slow_restarts:
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id("slow_restarts"))

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
            self._track_slow_restarts(True)
        else:
            self._track_slow_restarts(False)
        return self.response_time

    async def async_restart_device(self, reason: str) -> None:
        """Reboot the device (about 25 s). Data and lock are untouched; the integration
        reconnects by itself once the device is back."""
        def job() -> None:
            self.client.restart()                      # closes the connection
            # No reconnection attempt while it boots: hammering a booting device froze it once.
            self._retry_at = self._boot_until = time.monotonic() + RESTART_WAIT_SECONDS
        await self.async_run(job, refresh=False, background=reason in ("nightly", "slow"))
        entry = {"time": dt_util.now().isoformat(timespec="seconds"), "reason": reason}
        self.store.data["last_restart"] = entry
        # Kept for the response time chart of the panel (one year, bounded).
        history = self.store.data.setdefault("restart_history", [])
        history.append(entry)
        cutoff = (dt_util.now() - RESTART_HISTORY_DAYS).isoformat(timespec="seconds")
        self.store.data["restart_history"] = [r for r in history if r["time"] >= cutoff][-RESTART_HISTORY_MAX:]
        self.store.save()
        _LOGGER.info("%s rebooted (%s)", self.client.host, reason)
        async_dispatcher_send(self.hass, signal_changed(self.config_entry.entry_id))
