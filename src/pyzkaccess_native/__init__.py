"""Pure-Python client for ZKTeco PullSDK / ZKAccess access-control devices.

No DLL, no Windows: talks the device's TCP protocol directly.
"""
from .client import DeviceInfo, ZKAccess
from .codec import Field, Table, decode_datetime, encode_datetime
from .discovery import DiscoveredDevice, discover
from .errors import DeviceError, ProtocolError, SafetyError, ZKConnectionError, ZKError
from .events import (AccessEvent, DoorSensor, DoorStatus, EventType, InOutState, VerifyMode,
                     event_label)
from .models import Authorization, Template, User

__version__ = "0.1.0"

__all__ = [
    "ZKAccess", "DeviceInfo", "discover", "DiscoveredDevice",
    "User", "Template", "Authorization",
    "AccessEvent", "DoorStatus", "DoorSensor", "EventType", "VerifyMode", "InOutState", "event_label",
    "Table", "Field", "encode_datetime", "decode_datetime",
    "ZKError", "ZKConnectionError", "ProtocolError", "DeviceError", "SafetyError",
    "__version__",
]
