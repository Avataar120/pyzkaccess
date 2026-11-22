"""Config flow, entities, events and services of the zkaccess_native integration."""
import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.zkaccess_native.const import DOMAIN, EVENT_ACCESS


@pytest.fixture
def search(monkeypatch, emulator):
    """The search finds the emulator (no real network scan in tests)."""
    from pyzkaccess_native import DiscoveredDevice
    calls = []

    def fake_search(networks, port):
        calls.append(networks)
        if "192.168.1.0/24" in networks and len(networks) == 1:
            return []                                     # nothing on Home Assistant's own subnet
        return [DiscoveredDevice("127.0.0.1", None, "EMU0000000001", "Emu", "Emulator 1.0", {})]

    async def fake_own(hass):
        return ["192.168.1.0/24"]
    monkeypatch.setattr("custom_components.zkaccess_native.config_flow._search", fake_search)
    monkeypatch.setattr("custom_components.zkaccess_native.config_flow._own_networks", fake_own)
    return calls


async def test_config_flow_search(hass: HomeAssistant, emulator, search):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"networks": "192.168.5.0/24"})
    assert search == [["192.168.1.0/24", "192.168.5.0/24"]]
    assert result["step_id"] == "select"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"device": "127.0.0.1"})
    assert result["step_id"] == "connect"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: emulator.port, CONF_PASSWORD: ""})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "EMU0000000001"
    await hass.async_block_till_done()
    # Searching again lists it as already configured; picking it stops there.
    again = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    again = await hass.config_entries.flow.async_configure(again["flow_id"], {"networks": ""})
    assert search[-1] == ["192.168.1.0/24", "127.0.0.0/24"]    # the configured device's range is searched
    labels = [o["label"] for o in again["data_schema"].schema["device"].config["options"]]
    assert labels[0].endswith("(already configured)")
    again = await hass.config_entries.flow.async_configure(again["flow_id"], {"device": "127.0.0.1"})
    assert again["type"] is FlowResultType.ABORT and again["reason"] == "already_configured"
    await hass.config_entries.async_unload(result["result"].entry_id)


async def test_config_flow_invalid_range_and_manual(hass: HomeAssistant, emulator, search):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"networks": "192.168.5"})
    assert result["errors"] == {"networks": "invalid_network"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"networks": ""})
    assert result["step_id"] == "user" and result["errors"] == {"base": "no_devices"}   # nothing found: say so
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"networks": ""})
    assert result["step_id"] == "connect"                     # submitted again unchanged: by hand
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: 1, CONF_PASSWORD: ""})
    assert result["errors"] == {"base": "cannot_connect"}


async def test_entities_events_services(hass: HomeAssistant, emulator):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="EMU0000000001", title="Front door",
                            data={CONF_HOST: "127.0.0.1", CONF_PORT: emulator.port, CONF_PASSWORD: ""})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.front_door_door_1").state == "off"
    assert hass.states.get("sensor.front_door_people").state == "0"
    assert hass.states.get("button.front_door_open_door_1") is not None

    device = dr.async_get(hass).async_get_device({(DOMAIN, "EMU0000000001")})
    events = async_capture_events(hass, EVENT_ACCESS)

    await hass.services.async_call(DOMAIN, "set_user", {"device_id": device.id, "user_id": 10, "name": "Alice"},
                                   blocking=True)
    await hass.services.async_call(DOMAIN, "grant", {"device_id": device.id, "user_id": 10}, blocking=True)
    users = await hass.services.async_call(DOMAIN, "list_users", {"device_id": device.id},
                                           blocking=True, return_response=True)
    assert users["users"][0]["name"] == "Alice"

    await entry.runtime_data.async_refresh_snapshot(silent=True)
    emulator.state.push_event(14, user_id=10)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert events[-1].data["user_id"] == 10 and events[-1].data["granted"]
    assert events[-1].data["person_name"] == "Alice" and events[-1].data["method"] == "fingerprint"
    state = hass.states.get("event.front_door_access")
    assert state.attributes["event_type"] == "granted" and state.attributes["person"] == 1
    assert hass.states.get("sensor.front_door_last_person").state == "Alice"

    await hass.services.async_call("button", "press", {"entity_id": "button.front_door_open_door_1"},
                                   blocking=True)
    assert emulator.state.controls[-1] == (1, 1, 1, 5, 0)

    await hass.config_entries.async_unload(entry.entry_id)


async def test_counts_right_after_boot(hass: HomeAssistant, emulator, monkeypatch):
    """Counters must be read on the first poll even when the host booted < 10 min ago."""
    import custom_components.zkaccess_native.coordinator as coordinator

    monkeypatch.setattr(coordinator.time, "monotonic", lambda: 42.0)
    entry = MockConfigEntry(domain=DOMAIN, unique_id="EMU0000000001", title="Front door",
                            data={CONF_HOST: "127.0.0.1", CONF_PORT: emulator.port, CONF_PASSWORD: ""})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.front_door_people").state == "0"
    await hass.config_entries.async_unload(entry.entry_id)


def test_logbook_texts(hass: HomeAssistant):
    from homeassistant.core import Event
    from custom_components.zkaccess_native import logbook
    seen = {}
    for lang in ("en", "fr"):
        hass.config.language = lang
        logbook.async_describe_events(hass, lambda domain, event_type, fn: seen.__setitem__(lang, fn))
    card = {"result": "granted", "person_name": "Alice", "category": "Open", "method": "card", "gesture": "double"}
    denied = {"result": "denied", "card": 42, "method": "card", "event_name": "Unregistered card"}
    assert seen["en"](Event(EVENT_ACCESS, card)) == {"name": "Alice", "message": "Open (card), second presentation"}
    assert seen["fr"](Event(EVENT_ACCESS, card)) == {"name": "Alice", "message": "Open (carte), deuxième présentation"}
    assert seen["fr"](Event(EVENT_ACCESS, denied)) == {"name": "Carte n°42", "message": "refusé : carte inconnue"}
