"""Exceptions raised by the library."""
from __future__ import annotations


class ZKError(Exception):
    """Base class for every error raised by this library."""


class ProtocolError(ZKError):
    """A malformed frame or an unexpected answer was received."""


class ZKConnectionError(ZKError, ConnectionError):
    """The TCP connection could not be opened or was lost."""


class DeviceError(ZKError):
    """The device answered with an error code."""

    def __init__(self, code: int, message: str):
        super().__init__(f"device error {code}: {message}")
        self.code = code


class SafetyError(ZKError):
    """An operation was refused by one of the library's safety checks."""
