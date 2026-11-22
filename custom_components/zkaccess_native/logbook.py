"""Readable logbook entries for access events."""
from __future__ import annotations

from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN, EVENT_ACCESS

METHODS = {"fingerprint": "fingerprint", "card": "card", "code": "keypad code", "remote": "remote"}


@callback
def async_describe_events(hass: HomeAssistant, async_describe_event) -> None:
    @callback
    def describe(event: Event) -> dict[str, str]:
        d = event.data
        who = d.get("person_name") or (f"UserID {d['user_id']}" if d.get("user_id") else None)
        if not who:
            who = f"Card #{d['card']}" if d.get("card") else "Access control"
        method = METHODS.get(d.get("method"), d.get("method") or "")
        if d.get("result") in ("granted", "action"):
            what = d.get("category") or d.get("event_name")
            message = f"{what} ({method})" if method else what
            if d.get("gesture") == "double":
                message += ", second presentation"
        elif d.get("result") == "denied":
            message = f"denied: {d.get('event_name', '').lower()}"
        else:
            message = d.get("event_name", "")
        return {"name": who, "message": message}

    async_describe_event(DOMAIN, EVENT_ACCESS, describe)
