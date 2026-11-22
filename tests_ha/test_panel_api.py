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

    await call("finger/update", user_id=128, finger_id=0, physical=7)   # integration-side change only
    bob = (await call("state"))["snapshot"]["persons"][0]
    assert next(c for c in bob["categories"] if c["digit"] == 8)["fingers"][0]["physical"] == 7
    await call("finger/update", user_id=128, finger_id=0, physical=None)  # unplace
    bob = (await call("state"))["snapshot"]["persons"][0]
    assert next(c for c in bob["categories"] if c["digit"] == 8)["fingers"][0]["physical"] is None
    await call("finger/update", user_id=128, finger_id=0, physical=6)

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


async def test_phones_toggle_and_connectivity(hass: HomeAssistant, emulator, setup):
    entry, call = setup
    await call("categories/save", categories={
        "0": {"type": "open", "name": "Open", "relay": True, "second": 9},
        "3": {"type": "arm", "name": "Arm", "relay": False},
        "4": {"type": "disarm", "name": "Disarm", "relay": False},
        "5": {"type": "toggle", "name": "Alarm toggle", "relay": False,
              "entity_id": "alarm_control_panel.home", "on": 4, "off": 3},
        "9": {"type": "custom", "name": "Close + arm", "relay": False}})
    await call("person/save", person=12, name="Alice")
    await call("tags/save", tags={"tag-entrance": {"door": 1}})
    await call("phone/pair_start", person=12, digit=0)

    events = async_capture_events(hass, EVENT_ACCESS)
    hass.bus.async_fire("tag_scanned", {"tag_id": "tag-entrance", "device_id": "phone-1"})
    await hass.async_block_till_done()
    alice = (await call("state"))["snapshot"]["persons"][0]
    assert alice["phones"][0]["device_id"] == "phone-1" and alice["phones"][0]["digit"] == 0
    assert events == []                                       # pairing scan is not an access

    hass.bus.async_fire("tag_scanned", {"tag_id": "tag-entrance", "device_id": "phone-1"})
    await hass.async_block_till_done()
    ev = events[-1].data
    assert ev["result"] == "granted" and ev["method"] == "phone" and ev["person_name"] == "Alice"
    assert emulator.state.controls[-1][:3] == (1, 1, 1)       # relay category: the door is released

    hass.bus.async_fire("tag_scanned", {"tag_id": "tag-entrance", "device_id": "phone-1"})
    await hass.async_block_till_done()
    assert events[-1].data["category"] == "Close + arm" and events[-1].data["gesture"] == "double"

    await call("phone/update", device_id="phone-1", digit=5)  # toggle category
    hass.states.async_set("alarm_control_panel.home", "disarmed")
    hass.bus.async_fire("tag_scanned", {"tag_id": "tag-entrance", "device_id": "phone-1"})
    await hass.async_block_till_done()
    assert events[-1].data["category"] == "Arm" and events[-1].data["toggle"] is True
    hass.states.async_set("alarm_control_panel.home", "armed_away")
    entry.runtime_data._repeat._last.clear()
    hass.bus.async_fire("tag_scanned", {"tag_id": "tag-entrance", "device_id": "phone-1"})
    await hass.async_block_till_done()
    assert events[-1].data["category"] == "Disarm" and events[-1].data["result"] == "action"

    await call("person/enable", person=12, enabled=False)
    entry.runtime_data._repeat._last.clear()
    hass.bus.async_fire("tag_scanned", {"tag_id": "tag-entrance", "device_id": "phone-1"})
    hass.bus.async_fire("tag_scanned", {"tag_id": "tag-entrance", "device_id": "stranger"})
    await hass.async_block_till_done()
    assert [e.data["result"] for e in events[-2:]] == ["denied", "denied"]
    hass.bus.async_fire("tag_scanned", {"tag_id": "other-tag", "device_id": "phone-1"})
    await hass.async_block_till_done()
    assert len(events) == 6                                   # tags not declared are ignored

    await call("phone/remove", device_id="phone-1")
    assert (await call("state"))["snapshot"]["persons"][0]["phones"] == []

    assert hass.states.get("binary_sensor.front_door_connected").state == "on"
    from custom_components.zkaccess_native.diagnostics import async_get_config_entry_diagnostics
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["settings"]["tags"] == 1 and diag["entry"]["data"]["host"] == "**REDACTED**"


async def test_toggle_needs_valid_targets(hass: HomeAssistant, emulator, setup, hass_ws_client):
    entry, call = setup
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": f"{DOMAIN}/categories/save", "entry_id": entry.entry_id,
                        "categories": {"0": {"type": "open", "name": "Open", "relay": True},
                                       "5": {"type": "toggle", "name": "T", "entity_id": "x.y", "on": 6, "off": 0}}})
    msg = await ws.receive_json()
    assert not msg["success"]


async def test_identify_finger(hass: HomeAssistant, emulator, setup):
    entry, call = setup
    coord = entry.runtime_data
    await call("categories/save", categories={"0": {"type": "open", "name": "Open", "relay": True},
                                              "8": {"type": "custom", "name": "Garage", "relay": True}})
    await call("person/save", person=12, name="Bob")
    with ZKAccess("127.0.0.1", emulator.port) as dev:
        from pyzkaccess_native import User
        for uid in (120, 128):
            dev.save_user(User(user_id=uid, name="Bob"))
        dev.save_template(Template(user_id=120, finger_id=0, template=b"A" * 32))
        dev.save_template(Template(user_id=120, finger_id=1, template=b"B" * 32))
        dev.save_template(Template(user_id=128, finger_id=0, template=b"G" * 32))
    await call("state", refresh=True)
    events = async_capture_events(hass, EVENT_ACCESS)
    notices = []
    from homeassistant.helpers.dispatcher import async_dispatcher_connect
    from custom_components.zkaccess_native.coordinator import signal_changed
    async_dispatcher_connect(hass, signal_changed(entry.entry_id),
                             lambda msg=None: msg and "identify" in msg and notices.append(msg["identify"]))

    def templates():
        with ZKAccess("127.0.0.1", emulator.port) as dev:
            return sorted((t.user_id, t.finger_id, t.template[:1]) for t in dev.get_templates())

    def fingers(state, uid):
        cat = next(c for c in state["snapshot"]["persons"][0]["categories"] if c["user_id"] == uid)
        return {f["finger_id"]: f["physical"] for f in cat["fingers"]}

    # A UserID with one fingerprint: identified at once, no write on the device.
    assert (await call("identify/start", person=12, physical=3))["state"] == "waiting"
    emulator.state.push_event(14, user_id=128)
    await poll(hass, entry)
    assert notices[-1]["state"] == "found" and notices[-1]["user_id"] == 128 and notices[-1]["placed"]
    assert fingers(await call("state"), 128) == {0: 3}

    # Several fingerprints: each is isolated on a temporary UserID, then put back.
    before = templates()
    await call("identify/start", person=12, physical=6)
    emulator.state.push_event(14, user_id=120)
    await poll(hass, entry)
    await hass.async_block_till_done()
    assert notices[-1]["state"] == "again"
    isolated = templates()
    assert [t for t in isolated if t[0] == 120] == []
    temp = next(t[0] for t in isolated if t[0] >= 9990 and t[2] == b"B")
    assert coord.store.data["identify_pending"]["user_id"] == 120
    state = await call("state", refresh=True)       # temporary UserIDs stay hidden
    assert all(c["user_id"] < 9990 for p in state["snapshot"]["persons"] for c in p["categories"])
    assert state["snapshot"]["unassigned"] == []
    emulator.state.push_event(23, user_id=temp)     # recognized, no rights
    await poll(hass, entry)
    await hass.async_block_till_done()
    assert notices[-1]["state"] == "found" and (notices[-1]["user_id"], notices[-1]["finger_id"]) == (120, 1)
    assert templates() == before
    assert "identify_pending" not in coord.store.data
    assert fingers(await call("state", refresh=True), 120) == {0: None, 1: 6}

    # Cancelled while isolated: everything is put back.
    await call("identify/start", person=12)
    emulator.state.push_event(14, user_id=120)
    await poll(hass, entry)
    await hass.async_block_till_done()
    assert templates() != before
    await call("identify/cancel")
    assert templates() == before and "identify_pending" not in coord.store.data

    # Identification never reaches the automations; normal events do again afterwards.
    assert events == []
    emulator.state.push_event(14, user_id=128)
    await poll(hass, entry)
    assert len(events) == 1 and events[0].data["user_id"] == 128


async def test_identify_restored_after_restart(hass: HomeAssistant, emulator, setup):
    entry, call = setup
    await call("person/save", person=12, name="Bob")
    with ZKAccess("127.0.0.1", emulator.port) as dev:
        from pyzkaccess_native import User
        dev.save_user(User(user_id=120, name="Bob"))
        dev.save_template(Template(user_id=120, finger_id=0, template=b"A" * 32))
        dev.save_template(Template(user_id=120, finger_id=2, template=b"C" * 32))
    await call("state", refresh=True)
    await call("identify/start", person=12)
    emulator.state.push_event(14, user_id=120)
    await poll(hass, entry)
    await hass.async_block_till_done()
    assert entry.runtime_data.store.data.get("identify_pending")
    # Home Assistant stops in the middle: unloading puts the fingerprints back.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    with ZKAccess("127.0.0.1", emulator.port) as dev:
        assert sorted((t.user_id, t.finger_id) for t in dev.get_templates()) == [(120, 0), (120, 2)]
        assert [u.user_id for u in dev.get_users() if u.user_id >= 9990] == []


async def test_poll_interval_setting(hass: HomeAssistant, emulator, setup):
    entry, call = setup
    coord = entry.runtime_data
    assert (await call("state"))["settings"]["poll_interval"] == 1.0
    await call("settings/save", poll_interval=0.3)
    await hass.async_block_till_done()
    assert entry.runtime_data is coord                  # applied live, no reload
    assert coord.update_interval.total_seconds() == 0.3
    assert entry.options["poll_interval"] == 0.3
    assert (await call("state"))["settings"]["poll_interval"] == 0.3
    await call("settings/save", poll_interval=3)
    await hass.async_block_till_done()
    assert coord.update_interval.total_seconds() == 3
    for bad in (0.2, 3.5):                              # out of range: refused
        with pytest.raises(AssertionError):
            await call("settings/save", poll_interval=bad)
    assert coord.update_interval.total_seconds() == 3
