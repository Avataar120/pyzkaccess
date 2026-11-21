"""Config flow, entities, events and services of the zkaccess_native integration."""
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.zkaccess_native.const import DOMAIN, EVENT_ACCESS


async def test_config_flow(hass: HomeAssistant, emulator):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: emulator.port, CONF_PASSWORD: ""})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "EMU0000000001"
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(result["result"].entry_id)


async def test_config_flow_cannot_connect(hass: HomeAssistant, socket_enabled):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
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
    assert hass.states.get("sensor.front_door_users").state == "0"
    assert hass.states.get("button.front_door_open_door_1") is not None

    device = dr.async_get(hass).async_get_device({(DOMAIN, "EMU0000000001")})
    events = async_capture_events(hass, EVENT_ACCESS)

    await hass.services.async_call(DOMAIN, "set_user", {"device_id": device.id, "pin": 101, "name": "Alice"},
                                   blocking=True)
    await hass.services.async_call(DOMAIN, "grant", {"device_id": device.id, "pin": 101}, blocking=True)
    users = await hass.services.async_call(DOMAIN, "list_users", {"device_id": device.id},
                                           blocking=True, return_response=True)
    assert users["users"][0]["name"] == "Alice"

    emulator.state.push_event(14, pin=101)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert events[-1].data["pin"] == 101 and events[-1].data["granted"]
    state = hass.states.get("event.front_door_access")
    assert state.attributes["event_type"] == "granted" and state.attributes["pin"] == 101
    assert hass.states.get("sensor.front_door_last_user").state == "101"

    await hass.services.async_call("button", "press", {"entity_id": "button.front_door_open_door_1"},
                                   blocking=True)
    assert emulator.state.controls[-1] == (1, 1, 1, 5, 0)

    await hass.config_entries.async_unload(entry.entry_id)
