"""End-to-end tests against the bundled emulator."""
import pytest

from pyzkaccess_native import (AccessEvent, DeviceError, EventType, SafetyError, Template, User,
                               ZKAccess)
from pyzkaccess_native import backup
from pyzkaccess_native.emulator import DeviceState, Emulator


@pytest.fixture
def emu():
    with Emulator(state=DeviceState(buffer_threshold=600, chunk_size=128)) as server:
        yield server


@pytest.fixture
def dev(emu):
    with ZKAccess("127.0.0.1", emu.port) as client:
        yield client


def test_info(dev):
    info = dev.info()
    assert info.serial_number == "EMU0000000001" and info.lock_count == 1
    assert dev.get_time() is not None


def test_wrong_password(emu):
    emu.state.password = "1234"
    with pytest.raises(DeviceError) as err:
        ZKAccess("127.0.0.1", emu.port, password="0000").connect()
    assert err.value.code == -14


def test_user_lifecycle(dev):
    dev.save_user(User(pin=101, name="Alice", password="4321", card_no=555))
    dev.save_user(User(pin=101, name="Alice B."))  # rename only
    user = dev.get_user(101)
    assert (user.name, user.password, user.card_no) == ("Alice B.", "4321", 555)
    assert user.uid == 1
    dev.grant(101)
    assert dev.get_authorizations(101)[0].door_mask == 1
    dev.delete_user(101)
    assert dev.get_user(101) is None and dev.get_authorizations(101) == []


def test_template_and_buffered_read(dev):
    for pin in (1, 2, 3):
        dev.save_user(User(pin=pin, name=f"user {pin}"))
        dev.save_template(Template(pin=pin, template=bytes([pin]) * 500))
    tpls = dev.get_templates()  # > buffer_threshold: goes through the device buffer
    assert [t.pin for t in tpls] == [1, 2, 3]
    assert tpls[2].template == bytes([3]) * 500 and tpls[2].uid == 3 and tpls[2].size == 500
    assert dev.count("templatev10") == 3
    dev.delete_templates(2)
    assert dev.count("templatev10") == 2


def test_template_refused_on_duplicate_uid(dev, emu):
    dev.save_user(User(pin=1, name="a"))
    dev.save_user(User(pin=2, name="b"))
    emu.state.data["user"][1]["UID"] = emu.state.data["user"][0]["UID"]
    with pytest.raises(SafetyError):
        dev.save_template(Template(pin=2, template=b"x" * 10))


def test_template_needs_user(dev):
    with pytest.raises(SafetyError):
        dev.save_template(Template(pin=9, template=b"x"))


def test_wipe_needs_flag(dev):
    with pytest.raises(SafetyError):
        dev.delete_records("user", None)
    dev.delete_records("user", None, allow_wipe=True)


def test_events_and_control(dev, emu):
    emu.state.push_event(EventType.FINGERPRINT_OPEN, pin=100)
    recs = dev.poll()
    assert isinstance(recs[0], AccessEvent) and recs[0].pin == 100
    dev.open_door(1, 3)
    assert emu.state.controls[-1] == (1, 1, 1, 3, 0)
    assert dev.poll()[0].event == EventType.REMOTE_OPENING
    assert dev.door_status().door_open(1) is False


def test_backup_restore(dev, emu):
    dev.write_records("timezone", [{"TimezoneId": 1, "SunTime1": 2359}])
    for pin in (10, 20):
        dev.save_user(User(pin=pin, name=f"n{pin}"))
        dev.save_template(Template(pin=pin, template=bytes([pin]) * 50))
        dev.grant(pin)
    snap = backup.backup(dev)
    dev.delete_user(10)
    result = backup.restore(dev, snap, wipe=True)
    assert result["user"] == 2 and result["templatev10"] == 2
    assert {t.pin: t.template for t in dev.get_templates()} == {10: bytes([10]) * 50, 20: bytes([20]) * 50}
    users = {u.pin: u.uid for u in dev.get_users()}
    assert {t.pin: t.uid for t in dev.get_templates()} == users


def test_reconnect_after_close(emu):
    client = ZKAccess("127.0.0.1", emu.port)
    client.connect()
    client.close()
    client.connect()
    assert client.count("user") == 0
    client.disconnect()


def test_backup_file_keeps_raw_bytes(dev, tmp_path):
    dev.write_records("user", [{"Pin": 7, "Name": b"\xe9t\xe9".decode("utf-8", "surrogateescape")}])
    backup.save(backup.backup(dev), tmp_path / "b.json")
    dev.delete_user(7)
    backup.restore(dev, backup.load(tmp_path / "b.json"))
    assert dev.get_user(7).name.encode("utf-8", "surrogateescape") == b"\xe9t\xe9"
