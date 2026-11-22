"""Reconfigure / reauth / DHCP, device triggers, person switch, model services, door entities."""
import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service
from pyzkaccess_native import Template, User, ZKAccess

from custom_components.zkaccess_native.const import DOMAIN, EVENT_ACCESS


@pytest.fixture
async def entry(hass: HomeAssistant, emulator):
    e = MockConfigEntry(domain=DOMAIN, unique_id="EMU0000000001", title="Front door",
                        data={CONF_HOST: "127.0.0.1", CONF_PORT: emulator.port, CONF_PASSWORD: ""})
    e.add_to_hass(hass)
    with ZKAccess("127.0.0.1", emulator.port) as dev:
        for uid in (120, 128):
            dev.save_user(User(user_id=uid, name="Bob"))
        dev.grant(120, 1, 1)
        dev.save_template(Template(user_id=120, finger_id=0, template=b"A" * 32))
    assert await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    coord = e.runtime_data
    await coord.async_run(coord.manager.categories_save,
                          {"0": {"type": "open", "name": "Open", "relay": True},
                           "8": {"type": "custom", "name": "Garage", "relay": False}}, 5.0)
    await coord.async_refresh_snapshot(silent=True)
    await hass.async_block_till_done()
    yield e
    await hass.async_block_till_done()                        # a reauth / reconfigure reloads the entry
    if e.state is config_entries.ConfigEntryState.LOADED:
        await hass.config_entries.async_unload(e.entry_id)


async def test_reconfigure_keeps_entry(hass: HomeAssistant, emulator, entry):
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: 1, CONF_PASSWORD: ""})
    assert result["errors"] == {"base": "cannot_connect"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: emulator.port, CONF_PASSWORD: ""})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert hass.config_entries.async_get_entry(entry.entry_id) is not None


async def test_reauth_when_password_changes(hass: HomeAssistant, emulator, entry):
    emulator.state.password = "1234"
    coord = entry.runtime_data
    await hass.async_add_executor_job(coord.client.close)
    await coord.async_refresh()
    await hass.async_block_till_done()
    flows = [f for f in hass.config_entries.flow.async_progress() if f["context"]["source"] == "reauth"]
    assert flows, "a reauth flow is started"
    result = await hass.config_entries.flow.async_configure(flows[0]["flow_id"], {CONF_PASSWORD: "bad"})
    assert result["errors"] == {"base": "invalid_auth"}
    result = await hass.config_entries.flow.async_configure(flows[0]["flow_id"], {CONF_PASSWORD: "1234"})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "1234"


async def test_dhcp_updates_known_device_address(hass: HomeAssistant, emulator, entry, monkeypatch):
    from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
    from custom_components.zkaccess_native import config_flow
    info = await hass.async_add_executor_job(config_flow._probe, "127.0.0.1", emulator.port, "")
    monkeypatch.setattr(config_flow, "_probe", lambda host, port, pw: info)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_DHCP},
        data=DhcpServiceInfo(ip="10.0.0.9", hostname="ma500", macaddress="001761aabbcc"))
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "10.0.0.9"


async def test_device_triggers(hass: HomeAssistant, emulator, entry):
    from homeassistant.components.device_automation import DeviceAutomationType, async_get_device_automations
    device = dr.async_get(hass).async_get_device({(DOMAIN, "EMU0000000001")})
    triggers = (await async_get_device_automations(hass, DeviceAutomationType.TRIGGER, [device.id]))[device.id]
    kinds = {(t["type"], t.get("subtype")) for t in triggers}
    assert ("category", "8 · Garage") in kinds and ("denied", None) in kinds and ("unknown_card", None) in kinds
    calls = async_mock_service(hass, "test", "automation")
    assert await async_setup_component(hass, "automation", {"automation": [
        {"triggers": {"trigger": "device", "domain": DOMAIN, "device_id": device.id, "type": "category",
                      "subtype": "8 · Garage", "person": 12},
         "actions": {"action": "test.automation", "data": {"who": "{{ trigger.event.data.person_name }}"}}},
        {"triggers": {"trigger": "device", "domain": DOMAIN, "device_id": device.id, "type": "unknown_card"},
         "actions": {"action": "test.automation", "data": {"who": "unknown"}}}]})
    emulator.state.push_event(14, user_id=120)              # Open: not Garage
    emulator.state.push_event(23, user_id=128)              # Garage, no relay -> action
    emulator.state.push_event(27, card=777)                 # unknown card
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert [c.data["who"] for c in calls] == ["Bob", "unknown"]


async def test_person_switch_and_services(hass: HomeAssistant, emulator, entry):
    ent = er.async_get(hass)
    switch_id = ent.async_get_entity_id("switch", DOMAIN, "EMU0000000001_person_12_access")
    assert switch_id and hass.states.get(switch_id).state == "on"
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch_id}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(switch_id).state == "off"
    with ZKAccess("127.0.0.1", emulator.port) as dev:
        assert dev.get_authorizations(120) == []
    device = dr.async_get(hass).async_get_device({(DOMAIN, "EMU0000000001")})
    await hass.services.async_call(DOMAIN, "set_access", {"device_id": device.id, "person": 12, "enabled": True},
                                   blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(switch_id).state == "on"
    await hass.services.async_call(DOMAIN, "set_access", {"device_id": device.id, "person": 12, "category": 0,
                                                          "enabled": False}, blocking=True)
    with ZKAccess("127.0.0.1", emulator.port) as dev:
        assert dev.get_authorizations(120) == []
    await hass.services.async_call(DOMAIN, "rename_person", {"device_id": device.id, "person": 12, "name": "Robert"},
                                   blocking=True)
    await hass.async_block_till_done()
    with ZKAccess("127.0.0.1", emulator.port) as dev:
        assert {u.name for u in dev.get_users()} == {"Robert"}
    assert "Robert" in hass.states.get(switch_id).attributes["friendly_name"]
    from homeassistant.exceptions import ServiceValidationError
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "set_access", {"device_id": device.id, "person": 77, "enabled": True},
                                       blocking=True)


async def test_door_entities_disabled_without_sensor(hass: HomeAssistant, emulator):
    emulator.state.door_sensors = [0, 0, 0, 0]                # no door contact wired
    e = MockConfigEntry(domain=DOMAIN, unique_id="EMU0000000001", title="Front door",
                        data={CONF_HOST: "127.0.0.1", CONF_PORT: emulator.port, CONF_PASSWORD: ""})
    e.add_to_hass(hass)
    assert await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    ent = er.async_get(hass)
    for uid in ("EMU0000000001_door_1", "EMU0000000001_alarm_1"):
        entity_id = ent.async_get_entity_id("binary_sensor", DOMAIN, uid)
        assert ent.async_get(entity_id).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    await hass.config_entries.async_unload(e.entry_id)


async def test_door_entities_enabled_with_sensor(hass: HomeAssistant, emulator, entry):
    ent = er.async_get(hass)
    entity_id = ent.async_get_entity_id("binary_sensor", DOMAIN, "EMU0000000001_door_1")
    assert ent.async_get(entity_id).disabled_by is None


async def test_method_deduced_for_refusal_without_rights(hass: HomeAssistant, emulator, entry):
    from pytest_homeassistant_custom_component.common import async_capture_events
    with ZKAccess("127.0.0.1", emulator.port) as dev:
        dev.save_template(Template(user_id=128, finger_id=0, template=b"G" * 32))
    coord = entry.runtime_data
    await coord.async_refresh_snapshot(silent=True)
    events = async_capture_events(hass, EVENT_ACCESS)
    emulator.state.push_event(23, user_id=128, verify=0)    # MA500: no verify mode for event 23
    await coord.async_refresh()
    await hass.async_block_till_done()
    assert events[-1].data["result"] == "action" and events[-1].data["method"] == "fingerprint"


async def test_restart_and_response_time(hass: HomeAssistant, emulator, entry, monkeypatch):
    coord = entry.runtime_data
    ent = er.async_get(hass)
    button = ent.async_get_entity_id("button", DOMAIN, "EMU0000000001_restart")
    sensor = ent.async_get_entity_id("sensor", DOMAIN, "EMU0000000001_response_time")
    await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    assert emulator.state.controls[-1][0] == 3                      # control op 3 = reboot
    assert coord.store.data["last_restart"]["reason"] == "button"
    assert not coord.client.connected                               # waits for the device to boot

    # Monotonic clock just after boot (CI runners, a freshly started host): the cool-down must
    # not block the first automatic reboot.
    monkeypatch.setattr("custom_components.zkaccess_native.coordinator.time.monotonic", lambda: 60.0)
    # Hourly check: fast device -> measured, no reboot.
    n = len(emulator.state.controls)
    assert await coord.async_check_response_time() is not None
    await hass.async_block_till_done()
    assert float(hass.states.get(sensor).state) >= 0 and len(emulator.state.controls) == n
    # Slow device -> one reboot, then none within the cool-down.
    monkeypatch.setattr(coord, "_measure_response", lambda: 0.8)
    await coord.async_check_response_time()
    assert len(emulator.state.controls) == n + 1 and coord.store.data["last_restart"]["reason"] == "slow"
    await coord.async_check_response_time()
    assert len(emulator.state.controls) == n + 1
    # Option off -> no reboot even when slow.
    coord._last_auto_restart = None
    coord.maintenance["slow_restart"] = False
    await coord.async_check_response_time()
    assert len(emulator.state.controls) == n + 1
