"""Pure-Python client for ZKTeco PullSDK / ZKAccess access-control devices.

No DLL, no Windows: talks the device's TCP protocol directly.
"""
from .client import DeviceInfo, DoorSettings, ZKAccess
from .codec import Field, Table, decode_datetime, encode_datetime
from .discovery import DiscoveredDevice, discover, scan
from .errors import DeviceError, ProtocolError, SafetyError, ZKConnectionError, ZKError
from .events import (AccessEvent, DoorSensor, DoorStatus, EventType, InOutState, RepeatDetector,
                     VerifyMode, event_label)
from .models import Authorization, Template, User
from .people import CategoryScheme

__version__ = "0.5.3"

__all__ = [
    "ZKAccess", "DeviceInfo", "DoorSettings", "discover", "scan", "DiscoveredDevice", "CategoryScheme",
    "RepeatDetector",
    "User", "Template", "Authorization",
    "AccessEvent", "DoorStatus", "DoorSensor", "EventType", "VerifyMode", "InOutState", "event_label",
    "Table", "Field", "encode_datetime", "decode_datetime",
    "ZKError", "ZKConnectionError", "ProtocolError", "DeviceError", "SafetyError",
    "__version__",
]
