"""Journaled fingerprint operations: verified result, put back on error, on an unexpected
result, and after an interruption (journal left on disk)."""
import pytest
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pyzkaccess_native import Template, User, ZKAccess

from custom_components.zkaccess_native import coordinator as zk_coordinator
from custom_components.zkaccess_native import journal as zk_journal
from custom_components.zkaccess_native.const import DOMAIN

CATEGORIES = {"0": {"type": "open", "name": "Open", "relay": True},
              "9": {"type": "custom", "name": "Close + arm", "relay": True}}


@pytest.fixture
async def setup(hass: HomeAssistant, emulator, hass_ws_client):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="EMU0000000001", title="Front door",
                            data={CONF_HOST: "127.0.0.1", CONF_PORT: emulator.port, CONF_PASSWORD: ""})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    ws = await hass_ws_client(hass)
    n = {"id": 0}

    async def call(type_, ok=True, **kw):
        n["id"] += 1
        await ws.send_json({"id": n["id"], "type": f"{DOMAIN}/{type_}", "entry_id": entry.entry_id, **kw})
        msg = await ws.receive_json()
        assert msg["success"] is ok, msg
        return msg.get("result") if ok else msg["error"]["message"]

    await call("categories/save", categories=CATEGORIES)
    with ZKAccess("127.0.0.1", emulator.port) as dev:
        dev.save_user(User(user_id=100, name="Alice"))
        dev.grant(100)
        for f, b in enumerate((b"A", b"B", b"C", b"D")):
            dev.save_template(Template(user_id=100, finger_id=f, template=b * 600))
        dev.save_user(User(user_id=130, name="Bob"))
        dev.save_template(Template(user_id=130, finger_id=0, template=b"Z" * 600))
    await call("state", refresh=True)
    yield entry, call
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.fixture
def notes(monkeypatch):
    """Persistent notifications created by the integration: {notification_id: message}."""
    seen = {}
    real = zk_coordinator.persistent_notification.async_create

    def create(hass, message, title=None, notification_id=None):
        seen[notification_id] = message
        real(hass, message, title=title, notification_id=notification_id)
    monkeypatch.setattr(zk_coordinator.persistent_notification, "async_create", create)
    return seen


def users(emulator):
    with ZKAccess("127.0.0.1", emulator.port) as dev:
        return {u.user_id for u in dev.get_users()}


def device(emulator):
    """{UserID: {finger: first byte}} and the slot check, read from the emulator."""
    with ZKAccess("127.0.0.1", emulator.port) as dev:
        out = {}
        for t in dev.get_templates():
            out.setdefault(t.user_id, {})[t.finger_id] = t.template[:1]
        return out, dev.check_template_slots()


BEFORE = {100: {0: b"A", 1: b"B", 2: b"C", 3: b"D"}, 130: {0: b"Z"}}


async def poll(hass, entry):
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_move_to_another_category_is_verified(hass, emulator, setup):
    entry, call = setup
    await call("finger/update", user_id=100, finger_id=2, digit=9, physical=6)
    templates, wrong = device(emulator)
    assert templates == {100: {0: b"A", 1: b"B", 3: b"D"}, 109: {0: b"C"}, 130: {0: b"Z"}}
    assert wrong == []
    coord = entry.runtime_data
    assert not coord.manager.journal.exists()
    assert coord.store.physical_finger(109, 0) == 6


async def test_move_interrupted_is_put_back(hass, emulator, setup, monkeypatch):
    """The device drops the connection after the source fingerprints were deleted, before
    they are written back: the journal puts everything back (at once, or at the next poll)."""
    entry, call = setup
    state = emulator.state
    armed = {"deleted": False, "fired": False}
    real_delete, real_set = state.delete_data, state.set_data

    def delete_data(payload):
        real_delete(payload)
        if state.table_by_index(payload[0]).name == "templatev10":
            armed["deleted"] = True

    def set_data(payload):
        if armed["deleted"] and not armed["fired"] and state.table_by_index(payload[0]).name == "templatev10":
            armed["fired"] = True
            raise ConnectionError("device froze")
        real_set(payload)
    monkeypatch.setattr(state, "delete_data", delete_data)
    monkeypatch.setattr(state, "set_data", set_data)

    await call("finger/update", ok=False, user_id=100, finger_id=2, digit=9)
    assert armed["fired"]
    coord = entry.runtime_data
    for _ in range(3):        # put back during the failed operation, or by the next poll
        if not coord.manager.journal.exists():
            break
        coord._journal_retry_at = 0
        await poll(hass, entry)
    assert not coord.manager.journal.exists()
    templates, wrong = device(emulator)
    assert templates == BEFORE and wrong == []
    assert 109 not in users(emulator)          # created by the operation: removed again


async def test_unexpected_result_is_put_back(hass, emulator, setup, monkeypatch):
    """A firmware that silently drops a fingerprint while rewriting: detected, put back."""
    entry, call = setup
    client = entry.runtime_data.client
    real = client.move_template

    def lossy(user_id, finger_id, to_user_id, to_finger_id=None):
        slot = real(user_id, finger_id, to_user_id, to_finger_id)
        client.delete_templates(user_id, 0)                     # loss in scope: must be detected
        return slot
    monkeypatch.setattr(client, "move_template", lossy)
    error = await call("finger/update", ok=False, user_id=100, finger_id=2, digit=9)
    assert "put back" in error
    templates, wrong = device(emulator)
    assert templates[100] == BEFORE[100] and 109 not in templates and wrong == []
    assert not entry.runtime_data.manager.journal.exists()


async def test_journal_left_by_a_crash_is_settled(hass, emulator, setup, notes):
    """Home Assistant stopped in the middle of an operation: the journal is on disk, the
    device half changed. At the next poll the device is put back exactly as before."""
    entry, call = setup
    coord = entry.runtime_data
    with ZKAccess("127.0.0.1", emulator.port) as dev:
        before = zk_journal.capture(dev, [100, 109])
        coord.manager.journal.write("move fingerprint 100/2 to 109", before)
        dev.delete_records("templatev10", {"Pin": 100})               # half done
        dev.save_user(User(user_id=109, name="Alice"))
        dev.save_template(Template(user_id=109, finger_id=0, template=b"C" * 600))
    await call("identify/start", ok=False, person=10)                   # refused meanwhile
    await poll(hass, entry)
    assert not coord.manager.journal.exists()
    templates, wrong = device(emulator)
    assert templates == BEFORE and wrong == [] and 109 not in users(emulator)
    assert any("put back" in m for k, m in notes.items() if k.endswith("_journal_done"))


async def test_identify_checked_against_journal(hass, emulator, setup):
    entry, call = setup
    coord = entry.runtime_data
    await call("identify/start", person=10)
    emulator.state.push_event(14, user_id=100)
    await poll(hass, entry)
    await hass.async_block_till_done()
    assert coord.manager.journal.exists()          # isolated: the journal holds the originals
    await call("identify/cancel")
    assert not coord.manager.journal.exists()
    templates, wrong = device(emulator)
    assert templates == BEFORE and wrong == []


async def test_identify_restore_falls_back_to_journal(hass, emulator, setup):
    """Temporary copies lost (device fault) during an identification: the originals come
    back from the journal on disk."""
    entry, call = setup
    coord = entry.runtime_data
    await call("identify/start", person=10)
    emulator.state.push_event(14, user_id=100)
    await poll(hass, entry)
    await hass.async_block_till_done()
    coord._identify_cache = None                   # as after a restart
    with ZKAccess("127.0.0.1", emulator.port) as dev:
        for pin in range(9990, 10000):
            dev.delete_records("templatev10", {"Pin": pin})
    await call("identify/cancel")
    assert not coord.manager.journal.exists()
    templates, wrong = device(emulator)
    assert templates == BEFORE and wrong == []


async def test_lost_fingerprints_alert(hass, emulator, setup, notes):
    entry, call = setup
    # Changes made through the panel do not alert.
    await call("finger/delete", user_id=100, finger_id=3)
    await entry.runtime_data.async_refresh_snapshot()
    await hass.async_block_till_done()
    assert not any(k.endswith("_lost") for k in notes)
    # A fingerprint deleted behind Home Assistant's back does.
    with ZKAccess("127.0.0.1", emulator.port) as dev:
        dev.delete_templates(130)
    await entry.runtime_data.async_refresh_snapshot()
    await hass.async_block_till_done()
    message = next(m for k, m in notes.items() if k.endswith("_lost"))
    assert "UserID 130, finger slot 0" in message and "100" not in message
