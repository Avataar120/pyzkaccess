"""Two cards per category: the second one lives on UserID 9000 + the category's UserID."""
import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.zkaccess_native.const import DOMAIN, EVENT_ACCESS

from test_panel_api import poll, setup  # noqa: F401 - fixture


def pins(emulator, table="user"):
    return {r["Pin"] for r in emulator.state.data[table]}


async def test_second_card(hass: HomeAssistant, emulator, setup, hass_ws_client):  # noqa: F811
    entry, call = setup
    await call("categories/save", categories={
        "0": {"type": "open", "name": "Open", "relay": True},
        "3": {"type": "arm", "name": "Arm", "relay": False}})
    assert await call("person/save", name="Alice") == 1
    assert await call("credential/set", person=1, digit=0, card=555, code="1234") == 10
    assert await call("credential/set", person=1, digit=0, card=777, slot=None) == 9010   # first free slot
    assert await call("credential/set", person=1, digit=3, card=888, slot=1) == 9013

    state = await call("state", refresh=True)
    snap = state["snapshot"]
    assert snap["max_cards"] == 2 and snap["unassigned"] == [] and len(snap["persons"]) == 1
    cats = {c["digit"]: c for c in snap["persons"][0]["categories"]}
    assert set(cats) == {0, 3}
    assert [(k["slot"], k["user_id"], k["card"]) for k in cats[0]["cards"]] == [(0, 10, 555), (1, 9010, 777)]
    assert [(k["slot"], k["user_id"], k["card"]) for k in cats[3]["cards"]] == [(0, 13, None), (1, 9013, 888)]
    users = {u["Pin"]: u for u in emulator.state.data["user"]}
    assert users[9010]["Name"] == "Alice" and not users[9010].get("Password")
    assert 9010 in pins(emulator, "userauthorize") and 9013 not in pins(emulator, "userauthorize")

    # Errors: card already used, all slots used, code on card 2.
    ws = await hass_ws_client(hass)
    for i, kw in enumerate([{"card": 555, "slot": 1, "digit": 3}, {"card": 999, "slot": None, "digit": 0},
                            {"code": "12", "slot": 1, "digit": 0}], start=1):
        await ws.send_json({"id": i, "type": f"{DOMAIN}/credential/set", "entry_id": entry.entry_id, "person": 1, **kw})
        msg = await ws.receive_json()
        assert not msg["success"], kw

    # Events of card 2: same person and category; refusal without rights = card action.
    events = async_capture_events(hass, EVENT_ACCESS)
    emulator.state.push_event(0, user_id=9010, card=777)
    emulator.state.push_event(23, user_id=9013, verify=0)   # as the MA500 sends it
    await poll(hass, entry)
    first, second = events[-2].data, events[-1].data
    assert (first["result"], first["person"], first["category_digit"], first["method"]) == ("granted", 1, 0, "card")
    assert (second["result"], second["person"], second["category_digit"], second["method"]) == ("action", 1, 3, "card")

    # Switches follow: category off, person off, then back on.
    await call("person/category_enable", person=1, digit=0, enabled=False)
    assert not {10, 9010} & pins(emulator, "userauthorize")
    await call("person/category_enable", person=1, digit=0, enabled=True)
    assert {10, 9010} <= pins(emulator, "userauthorize")
    await call("person/enable", person=1, enabled=False)
    assert not {10, 9010} & pins(emulator, "userauthorize")
    await call("person/enable", person=1, enabled=True)
    assert {10, 9010} <= pins(emulator, "userauthorize") and 9013 not in pins(emulator, "userauthorize")

    # Renaming reaches the extra UserIDs; removing card 2 deletes its UserID.
    await call("person/save", person=1, name="Alicia")
    assert {u["Pin"]: u["Name"] for u in emulator.state.data["user"]}[9013] == "Alicia"
    await call("credential/set", person=1, digit=0, card=None, slot=1)
    assert 9010 not in pins(emulator) and 9010 not in pins(emulator, "userauthorize")
    assert {u["Pin"]: u.get("CardNo") for u in emulator.state.data["user"]}[10] == 555   # card 1 kept

    # Deleting the person deletes its extra card UserIDs too.
    await call("person/delete", person=1)
    assert not pins(emulator) & {10, 13, 9013}


async def test_no_second_card_for_person_99(hass: HomeAssistant, emulator, setup, hass_ws_client):  # noqa: F811
    entry, call = setup
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": f"{DOMAIN}/credential/set", "entry_id": entry.entry_id,
                        "person": 99, "digit": 0, "card": 4242, "slot": 1})
    msg = await ws.receive_json()
    assert not msg["success"] and "reserved" in msg["error"]["message"]
    assert 9990 not in pins(emulator)


async def test_plain_numbering_has_one_card(hass: HomeAssistant, emulator, setup, hass_ws_client):  # noqa: F811
    entry, call = setup
    await call("categories/save", categories={"0": {"type": "open", "name": "Open", "relay": True}}, numbering="plain")
    state = await call("state", refresh=True)
    assert state["snapshot"]["max_cards"] == 1
