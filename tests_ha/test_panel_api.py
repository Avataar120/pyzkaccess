"""Panel WebSocket API: people, categories, credentials, enrollment, events, backups."""
import pytest
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events
from pyzkaccess_native import Template, ZKAccess

from custom_components.zkaccess_native.const import DOMAIN, EVENT_ACCESS


@pytest.fixture
async def setup(hass: HomeAssistant, emulator, hass_ws_client):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="EMU0000000001", title="Front door",
                            data={CONF_HOST: "127.0.0.1", CONF_PORT: emulator.port, CONF_PASSWORD: ""})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    ws = await hass_ws_client(hass)
    n = {"id": 0}

    async def call(type_, **kw):
        n["id"] += 1
        await ws.send_json({"id": n["id"], "type": f"{DOMAIN}/{type_}", "entry_id": entry.entry_id, **kw})
        msg = await ws.receive_json()
        assert msg["success"], msg
        return msg["result"]

    yield entry, call
    await hass.config_entries.async_unload(entry.entry_id)


async def poll(hass, entry):
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_people_categories_and_events(hass: HomeAssistant, emulator, setup):
    entry, call = setup
    await call("categories/save", categories={
        "0": {"type": "open", "name": "Open", "relay": True, "second": 9},
        "3": {"type": "arm", "name": "Arm", "relay": False},
        "9": {"type": "custom", "name": "Close + arm", "relay": False}})
    person = await call("person/save", name="Alice")
    assert person == 1
    await call("credential/set", person=1, digit=0, card=555, code="1234")
    await call("credential/set", person=1, digit=3, code="1234")
    state = await call("state", refresh=True)
    alice = state["snapshot"]["persons"][0]
    assert alice["name"] == "Alice"
    cats = {c["digit"]: c for c in alice["categories"]}
    assert cats[0]["user_id"] == 10 and cats[0]["card"] == 555 and cats[0]["authorized"]
    assert cats[3]["user_id"] == 13 and cats[3]["has_code"] and not cats[3]["authorized"]
    assert emulator.state.data["user"][1]["Name"] == "Alice"     # name copied to the new category

    events = async_capture_events(hass, EVENT_ACCESS)
    emulator.state.push_event(0, user_id=10, card=555)        # card opens (relay category)
    emulator.state.push_event(23, user_id=13)                 # refused by the device: no rights
    await poll(hass, entry)
    first, second = events[-2].data, events[-1].data
    assert first["result"] == "granted" and first["person_name"] == "Alice" and first["method"] == "card"
    assert second["result"] == "action" and second["category"] == "Arm"

    emulator.state.push_event(0, user_id=10, card=555)        # double badge -> second category
    await poll(hass, entry)
    assert events[-1].data["category"] == "Close + arm" and events[-1].data["gesture"] == "double"

    await call("person/enable", person=1, enabled=False)
    assert not any(a["Pin"] == 10 for a in emulator.state.data["userauthorize"])
    emulator.state.push_event(23, user_id=13)
    await poll(hass, entry)
    assert events[-1].data["result"] == "denied"
    await call("person/enable", person=1, enabled=True)
    assert any(a["Pin"] == 10 for a in emulator.state.data["userauthorize"])

    history = await call("history")
    assert history[0]["result"] == "denied"


async def test_enrollment_unassigned_and_fingers(hass: HomeAssistant, emulator, setup):
    entry, call = setup
    await call("categories/save", categories={"0": {"type": "open", "name": "Open", "relay": True},
                                              "8": {"type": "custom", "name": "Garage", "relay": False}})
    await call("person/save", person=12, name="Bob")
    started = await call("enroll/start", person=12, digit=8, physical=6)
    assert started["user_id"] == 128
    # The keypad enrollment, simulated directly on the device.
    with ZKAccess("127.0.0.1", emulator.port) as dev:
        dev.save_template(Template(user_id=128, finger_id=0, template=b"F" * 32))
    await poll(hass, entry)
    bob = (await call("state", refresh=True))["snapshot"]["persons"][0]
    garage = next(c for c in bob["categories"] if c["digit"] == 8)
    assert garage["fingers"] == [{"finger_id": 0, "size": 32, "physical": 6}]

    moved = await call("finger/update", user_id=128, finger_id=0, digit=0)
    assert moved == [120, 0]
    bob = (await call("state"))["snapshot"]["persons"][0]
    assert next(c for c in bob["categories"] if c["digit"] == 0)["fingers"][0]["physical"] == 6

    with ZKAccess("127.0.0.1", emulator.port) as dev:      # a user created on the keypad
        from pyzkaccess_native import User
        dev.save_user(User(user_id=6, name="", card_no=777))
    state = await call("state", refresh=True)
    assert [u["user_id"] for u in state["snapshot"]["unassigned"]] == [6]
    assert await call("unassigned/assign", user_id=6, person=12, digit=8) == 128
    state = await call("state", refresh=True)
    assert state["snapshot"]["unassigned"] == []
    garage = next(c for c in state["snapshot"]["persons"][0]["categories"] if c["digit"] == 8)
    assert garage["card"] == 777

    await call("person/delete", person=12)
    assert (await call("state", refresh=True))["snapshot"]["persons"] == []


async def test_door_and_backups(hass: HomeAssistant, emulator, setup):
    entry, call = setup
    door = await call("door/get")
    assert door["settings"]["relay_seconds"] == 5
    await call("door/set", settings={"relay_seconds": 3})
    assert emulator.state.params["Door1Drivertime"] == "3"
    await call("door/control", action="open", seconds=2)
    assert emulator.state.controls[-1] == (1, 1, 1, 2, 0)

    await call("person/save", name="Carol")
    name = await call("backup/create")
    backups = await call("backup/list")
    assert backups[0]["name"] == name and backups[0]["users"] == 1
    await call("person/delete", person=1)
    await call("backup/restore", name=name, mode="replace")
    assert (await call("state", refresh=True))["snapshot"]["persons"][0]["name"] == "Carol"
