"""Security review (HACS default list): card numbers stay private, raw device actions are
admin-only, the journal check does not touch the disk on the event loop."""
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events
from pyzkaccess_native import User, ZKAccess

from custom_components.zkaccess_native.const import DOMAIN, EVENT_ACCESS


@pytest.fixture
async def entry(hass: HomeAssistant, emulator):
    e = MockConfigEntry(domain=DOMAIN, unique_id="EMU0000000001", title="Front door",
                        data={CONF_HOST: "127.0.0.1", CONF_PORT: emulator.port, CONF_PASSWORD: ""})
    e.add_to_hass(hass)
    with ZKAccess("127.0.0.1", emulator.port) as dev:
        dev.save_user(User(user_id=120, name="Bob", card_no=555))
        dev.grant(120, 1, 1)
    assert await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    yield e
    await hass.config_entries.async_unload(e.entry_id)
    await hass.async_block_till_done()


async def _present_cards(hass, emulator, entry):
    emulator.state.push_event(0, user_id=120, card=555)     # known card opens
    emulator.state.push_event(27, card=777)                 # unknown card
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_card_numbers_not_on_bus_nor_in_entity(hass: HomeAssistant, emulator, entry):
    events = async_capture_events(hass, EVENT_ACCESS)
    await _present_cards(hass, emulator, entry)
    assert [(e.data["method"], e.data["card_known"]) for e in events] == [("card", True), ("card", False)]
    assert all("card" not in e.data for e in events)
    entity_id = er.async_get(hass).async_get_entity_id("event", DOMAIN, "EMU0000000001_access")
    state = hass.states.get(entity_id)
    assert state.attributes["card_known"] is False and "card" not in state.attributes
    assert "777" not in str(state.attributes) and "555" not in str(state.attributes)
    # The integration's own history keeps them for the administrators' panel.
    assert {ev.get("card") for ev in entry.runtime_data.store.data["history"]} >= {555, 777}


async def test_history_and_live_events_without_card_for_non_admins(
        hass: HomeAssistant, emulator, entry, hass_ws_client, hass_read_only_access_token):
    admin = await hass_ws_client(hass)
    user = await hass_ws_client(hass, hass_read_only_access_token)
    for n, ws in enumerate((admin, user), start=1):
        await ws.send_json({"id": n, "type": f"{DOMAIN}/subscribe", "entry_id": entry.entry_id})
        assert (await ws.receive_json())["success"]
    await _present_cards(hass, emulator, entry)
    live = {}
    for name, ws in (("admin", admin), ("user", user)):
        live[name] = [(await ws.receive_json())["event"]["event"] for _ in range(2)]
    assert [e.get("card") for e in live["admin"]] == [555, 777]
    assert all("card" not in e for e in live["user"]) and [e["card_known"] for e in live["user"]] == [True, False]
    history = {}
    for name, ws in (("admin", admin), ("user", user)):
        await ws.send_json({"id": 10, "type": f"{DOMAIN}/history", "entry_id": entry.entry_id})
        history[name] = (await ws.receive_json())["result"]
    assert {e.get("card") for e in history["admin"]} >= {555, 777}
    assert history["user"] and all("card" not in e for e in history["user"])


@pytest.mark.parametrize("service, data", [
    ("set_user", {"user_id": 130, "name": "Intruder", "card": 999}),
    ("grant", {"user_id": 120}),
    ("revoke", {"user_id": 120}),
    ("delete_user", {"user_id": 120}),
    ("list_users", {}),
])
async def test_raw_device_actions_are_admin_only(hass: HomeAssistant, emulator, entry, hass_read_only_user,
                                                 service, data):
    device = dr.async_get(hass).async_get_device({(DOMAIN, "EMU0000000001")})
    response = service == "list_users"
    with pytest.raises(Unauthorized):
        await hass.services.async_call(DOMAIN, service, {"device_id": device.id, **data}, blocking=True,
                                       context=Context(user_id=hass_read_only_user.id), return_response=response)
    with ZKAccess("127.0.0.1", emulator.port) as dev:
        assert [u.user_id for u in dev.get_users()] == [120] and dev.get_authorizations(120)
    # An administrator still can (list_users: read only, nothing changed).
    if response:
        result = await hass.services.async_call(DOMAIN, service, {"device_id": device.id}, blocking=True,
                                                return_response=True)
        assert [u["user_id"] for u in result["users"]] == [120]


async def test_journal_check_does_not_touch_the_disk(hass: HomeAssistant, emulator, entry):
    coord = entry.runtime_data
    journal = coord.manager.journal.path
    real_exists = Path.exists
    calls = []

    def exists(self, *args, **kwargs):
        if self == journal:
            calls.append(self)
        return real_exists(self, *args, **kwargs)

    with patch.object(Path, "exists", exists):
        for _ in range(3):
            await coord.async_refresh()
            await hass.async_block_till_done()
        assert coord.manager.journal.exists() is False
    assert calls == []
    # Written and cleared by the journal itself: the state in memory follows.
    coord.manager.journal.write("test", {})
    assert coord.manager.journal.exists() and real_exists(journal)
    coord.manager.journal.clear()
    assert not coord.manager.journal.exists() and not real_exists(journal)
