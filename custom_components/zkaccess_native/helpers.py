"""Helpers shared by the coordinator and its parts: signals, event classification, data."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from pyzkaccess_native import AccessEvent, DoorStatus

from .const import CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL, DOMAIN, POLL_INTERVAL_MAX, POLL_INTERVAL_MIN

#: Event codes meaning "credential not registered": never an action.
UNKNOWN_CREDENTIAL = {27, 34}
FINGERPRINT_EVENTS = {14, 15, 16, 17, 18, 19, 31, 32, 33, 34, 35, 103, 203}
CODE_EVENTS = {4, 30, 101}
REMOTE_EVENTS = {7, 8, 9, 10, 11, 12, 13, 205}


def poll_interval(options: Mapping[str, Any]) -> float:
    """The configured poll interval, kept within the supported range."""
    value = float(options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL))
    return min(max(value, POLL_INTERVAL_MIN), POLL_INTERVAL_MAX)


def signal_event(entry_id: str) -> str:
    return f"{DOMAIN}_{entry_id}_event"


def signal_changed(entry_id: str) -> str:
    return f"{DOMAIN}_{entry_id}_changed"


def access_method(ev: AccessEvent) -> str:
    code = int(ev.event)
    if code in REMOTE_EVENTS:
        return "remote"
    if code in FINGERPRINT_EVENTS:
        return "fingerprint"
    if code in CODE_EVENTS:
        return "code"
    if ev.card:
        return "card"
    return {1: "fingerprint", 3: "code", 4: "card"}.get(int(ev.verify_mode), "other")


@dataclass
class ZKData:
    """Snapshot published to entities and to the panel after each poll."""

    status: Optional[DoorStatus] = None
    events: list[dict[str, Any]] = field(default_factory=list)
    snapshot: Optional[dict[str, Any]] = None


#: Fields of an access event that are credentials (card numbers): kept in the integration's
#: own history for administrators (the panel), never put on the event bus, in entity
#: attributes (recorder) or sent to other users. ``card_known`` tells automations enough.
PRIVATE_EVENT_FIELDS = ("card",)


def public_event(ev: dict[str, Any]) -> dict[str, Any]:
    """An access event without its credential fields."""
    return {k: v for k, v in ev.items() if k not in PRIVATE_EVENT_FIELDS}
