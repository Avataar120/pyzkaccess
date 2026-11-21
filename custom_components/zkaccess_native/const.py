"""Constants of the ZKAccess Native integration."""
from __future__ import annotations

DOMAIN = "zkaccess_native"

CONF_POLL_INTERVAL = "poll_interval"
CONF_OPEN_SECONDS = "open_seconds"
CONF_SYNC_CLOCK = "sync_clock"

DEFAULT_PORT = 4370
DEFAULT_POLL_INTERVAL = 1.0  # seconds between two real-time log polls
DEFAULT_OPEN_SECONDS = 5
DEFAULT_SYNC_CLOCK = True

#: Users/templates are counted every N seconds (they rarely change).
COUNT_REFRESH_SECONDS = 600

#: Fired on the event bus for every access event.
EVENT_ACCESS = f"{DOMAIN}_access"

EVENT_TYPE_GRANTED = "granted"
EVENT_TYPE_DENIED = "denied"
EVENT_TYPE_OTHER = "other"
