"""Thread-safe store saves, repairs issues, diagnostics."""
import json
import time

import pytest
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zkaccess_native.const import DOMAIN


@pytest.fixture
async def entry(hass: HomeAssistant, emulator):
    e = MockConfigEntry(domain=DOMAIN, unique_id="EMU0000000001", title="Front door",
                        data={CONF_HOST: "127.0.0.1", CONF_PORT: emulator.port, CONF_PASSWORD: ""})
    e.add_to_hass(hass)
    assert await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    yield e
    await hass.config_entries.async_unload(e.entry_id)


async def test_store_save_from_executor_thread(hass: HomeAssistant, entry, hass_storage):
    store = entry.runtime_data.store

    def change_in_thread():
        store.data["notify_keypad"] = False        # as a device operation does, in the executor
        store.save(delay=0)
    await hass.async_add_executor_job(change_in_thread)
    await hass.async_block_till_done()
    await store.async_save_now()
    saved = hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]
    assert saved["notify_keypad"] is False


async def test_repairs_unreachable_and_slow_restarts(hass: HomeAssistant, emulator, entry, monkeypatch):
    coord = entry.runtime_data
    issues = ir.async_get(hass)
    unreachable = f"unreachable_{entry.entry_id}"
    # Unreachable for more than 30 minutes -> issue; it answers again -> issue removed.
    coord._track_reachability(False)
    assert issues.async_get_issue(DOMAIN, unreachable) is None
    coord._unreachable_since = time.monotonic() - 31 * 60
    coord._track_reachability(False)
    issue = issues.async_get_issue(DOMAIN, unreachable)
    assert issue and issue.translation_key == "unreachable" and issue.translation_placeholders["minutes"] == "30"
    await coord.async_refresh()
    await hass.async_block_till_done()
    assert issues.async_get_issue(DOMAIN, unreachable) is None
    # Rebooted for slowness twice in 24 h -> issue.
    slow = f"slow_restarts_{entry.entry_id}"
    monkeypatch.setattr(coord, "_measure_response", lambda: 0.9)
    await coord.async_check_response_time()
    coord._boot_until = None
    assert issues.async_get_issue(DOMAIN, slow) is None
    coord._last_auto_restart = None                     # skip the 6 h cool-down
    await coord.async_check_response_time()
    coord._boot_until = None
    issue = issues.async_get_issue(DOMAIN, slow)
    assert issue and issue.translation_placeholders["count"] == "2"


async def test_diagnostics_has_maintenance(hass: HomeAssistant, entry):
    from custom_components.zkaccess_native.diagnostics import async_get_config_entry_diagnostics
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["maintenance"]["nightly_restart"] is True and "response_time" in diag["maintenance"]
    assert diag["poll_interval"] == 1.0
    json.dumps(diag)


async def test_template_slots_safety_net(hass: HomeAssistant, emulator, entry):
    """A template left on another slot (user moved by the device) is rebound at the next read."""
    from pyzkaccess_native import Template, User, ZKAccess
    with ZKAccess("127.0.0.1", emulator.port) as dev:
        for uid in (101, 109):
            dev.save_user(User(user_id=uid, name="Bob"))
        dev.save_template(Template(user_id=109, finger_id=0, template=b"N" * 40))
        dev.delete_user(101)
        dev.write_records("user", [{"Pin": 109, "Name": "Bob"}])   # raw write: the device moves 109
        assert dev.check_template_slots()
    coord = entry.runtime_data
    await coord.async_refresh_snapshot(silent=True)
    await hass.async_block_till_done()
    with ZKAccess("127.0.0.1", emulator.port) as dev:
        assert dev.check_template_slots() == []
