"""Fixtures for the Home Assistant integration tests (run against the bundled emulator)."""
import pytest
from pyzkaccess_native.emulator import DeviceState, Emulator


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def emulator(socket_enabled):
    with Emulator(state=DeviceState()) as emu:
        yield emu
