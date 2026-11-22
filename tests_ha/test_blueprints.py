"""The automation blueprints shipped in blueprints/automation/zkaccess_native load and run."""
import asyncio
import shutil
from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.zkaccess_native.const import EVENT_ACCESS

SRC = Path(__file__).parent.parent / "blueprints" / "automation" / "zkaccess_native"


@pytest.fixture
def blueprints(hass: HomeAssistant):
    dest = Path(hass.config.path("blueprints/automation/zkaccess_native"))
    dest.mkdir(parents=True, exist_ok=True)
    for f in SRC.glob("*.yaml"):
        shutil.copy(f, dest / f.name)
    yield
    shutil.rmtree(Path(hass.config.path("blueprints")), ignore_errors=True)


def use(name, **inputs):
    return {"use_blueprint": {"path": f"zkaccess_native/{name}.yaml", "input": {"reader": "dev1", **inputs}}}


def access(hass, **data):
    hass.bus.async_fire(EVENT_ACCESS, {"device_id": "dev1", **data})


async def test_blueprints(hass: HomeAssistant, blueprints):
    calls = async_mock_service(hass, "test", "automation")
    unlock = async_mock_service(hass, "lock", "unlock")
    lock = async_mock_service(hass, "lock", "lock")
    disarm = async_mock_service(hass, "alarm_control_panel", "alarm_disarm")
    arm = async_mock_service(hass, "alarm_control_panel", "alarm_arm_away")
    switch_on = async_mock_service(hass, "switch", "turn_on")
    switch_off = async_mock_service(hass, "switch", "turn_off")
    assert await async_setup_component(hass, "automation", {"automation": [
        {"id": "a1", **use("category_actions", category=8, person=12,
                           actions=[{"action": "test.automation", "data": {"what": "garage"}}])},
        {"id": "a2", **use("open_and_disarm", category=0, lock=["lock.front"], strike=["switch.strike"], pulse=1,
                           alarm=["alarm_control_panel.home"], alarm_code="1234")},
        {"id": "a3", **use("leave_lock_and_arm", category=9, lock=["lock.front"],
                           alarm=["alarm_control_panel.home"])},
        {"id": "a4", **use("refusals_alert", count=2, window=1,
                           actions=[{"action": "test.automation", "data": {"what": "alert"}}])},
    ]})
    await hass.async_block_till_done()
    for state in hass.states.async_all("automation"):
        assert state.state == "on", state

    access(hass, result="action", category_digit=8, person=13)     # other person: ignored
    access(hass, result="action", category_digit=8, person=12)
    access(hass, result="granted", category_digit=0, person=12)
    access(hass, result="action", category_digit=9, person=12)
    access(hass, result="granted", category_digit=0, person=12, device_id="other")   # other reader
    await hass.async_block_till_done()
    assert [c.data["what"] for c in calls] == ["garage"]
    assert len(unlock) == 1 and disarm[0].data.get("code") == "1234"
    assert len(lock) == 1 and len(arm) == 1 and "code" not in arm[0].data
    assert len(switch_on) == 1

    access(hass, result="denied", event=34)
    for _ in range(20):                                             # the run now waits for the next refusal
        await asyncio.sleep(0)
    assert [c.data["what"] for c in calls] == ["garage"]            # one refusal: no alert yet
    access(hass, result="denied", event=27)
    await hass.async_block_till_done()
    assert [c.data["what"] for c in calls] == ["garage", "alert"]
