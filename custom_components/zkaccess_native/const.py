"""Constants of the ZKAccess Native integration."""
from __future__ import annotations

DOMAIN = "zkaccess_native"

CONF_POLL_INTERVAL = "poll_interval"
CONF_OPEN_SECONDS = "open_seconds"
CONF_SYNC_CLOCK = "sync_clock"

DEFAULT_PORT = 4370
DEFAULT_POLL_INTERVAL = 1.0  # seconds between two real-time log polls
POLL_INTERVAL_MIN = 0.3
POLL_INTERVAL_MAX = 3.0
DEFAULT_OPEN_SECONDS = 5
DEFAULT_SYNC_CLOCK = True

#: How often the user / template / authorization tables are re-read (seconds).
SNAPSHOT_REFRESH_SECONDS = 30
RETRY_MIN_SECONDS = 5  # first wait after a failed connection, doubled after each failure
RETRY_MAX_SECONDS = 60
#: How long the enrollment wizard waits for a new fingerprint (seconds).
ENROLL_TIMEOUT_SECONDS = 300
IDENTIFY_TIMEOUT_SECONDS = 60  # per step of a finger identification
# Temporary UserIDs used to tell apart the fingers of one UserID during an identification:
# each template gets its own UserID, without access rights. Hidden from the panel.
IDENTIFY_PINS = range(9990, 10000)
# The device reports a door opened for a phone as a remote opening within this delay.
PHONE_ECHO_SECONDS = 10
#: Number of access events kept for the activity log.
HISTORY_SIZE = 500

#: Fired on the event bus for every access event.
EVENT_ACCESS = f"{DOMAIN}_access"

# Event entity types / ``result`` field of the access event.
RESULT_GRANTED = "granted"   # the device released the door
RESULT_ACTION = "action"     # a known credential of a category that does not drive the relay
RESULT_DENIED = "denied"
RESULT_OTHER = "other"       # remote opening, alarm, door status...

# Numbering modes.
NUMBERING_CATEGORY = "category"  # UserID = person number + category digit
NUMBERING_PLAIN = "plain"        # one UserID per person, no categories

# Category types (the digit 0 is always "open").
CATEGORY_TYPES = ("open", "close", "lock", "unlock", "arm", "disarm", "custom", "toggle")
#: Entity states that count as "inactive" for a toggle category (anything else is "active").
TOGGLE_INACTIVE_STATES = ("off", "unlocked", "disarmed", "closed", "not_home", "idle", "unavailable", "unknown")
DEFAULT_CATEGORIES = {"0": {"type": "open", "name": "Open", "relay": True, "second": None}}
DEFAULT_SECOND_WINDOW = 5.0

#: How long a phone pairing waits for a tag scan (seconds).
PHONE_PAIR_TIMEOUT_SECONDS = 120

PANEL_URL = "zkaccess-native"
STATIC_URL = f"/{DOMAIN}_static"
