"""Diagnostics download (Settings > Devices > ... > Download diagnostics), with personal data removed."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import ZKConfigEntry

REDACT = {"password", "host", "name", "person_name", "ha_person", "card", "phone", "device_id",
          "serial", "unique_id", "title", "entity_id"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ZKConfigEntry) -> dict[str, Any]:
    coord = entry.runtime_data
    info = coord.info
    store = coord.store.data
    snapshot = coord.snapshot or {}
    return async_redact_data({
        "entry": {"data": dict(entry.data), "options": dict(entry.options)},
        "device": {"firmware": info.firmware if info else None, "machine_type": info.machine_type if info else None,
                   "lock_count": info.lock_count if info else None, "connected": coord.last_update_success,
                   "clock_offset": coord.clock_offset},
        "settings": {"numbering": store["numbering"], "second_window": store.get("second_window"),
                     "categories": store["categories"], "backup": store["backup"],
                     "tags": len(store["tags"]), "phones": len(store["phones"]),
                     "disabled_persons": len(store["disabled"])},
        "maintenance": {**coord.maintenance, "response_time": coord.response_time,
                        "last_restart": store.get("last_restart"),
                        "automatic_reboots_24h": len(coord._slow_restarts),
                        "unreachable_issue": coord._unreachable_issue},
        "poll_interval": coord.update_interval.total_seconds() if coord.update_interval else None,
        "counts": snapshot.get("counts"),
        "persons": [{"num": p["num"], "enabled": p["enabled"],
                     "categories": [{"digit": c["digit"], "fingers": len(c["fingers"]), "card": bool(c["card"]),
                                     "code": c["has_code"], "authorized": c["authorized"]} for c in p["categories"]],
                     "phones": len(p.get("phones", []))} for p in snapshot.get("persons", [])],
        "unassigned": len(snapshot.get("unassigned", [])),
        "recent_events": store["history"][-20:],
    }, REDACT)
