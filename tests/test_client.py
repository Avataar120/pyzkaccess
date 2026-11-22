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
    dev.save_user(User(user_id=101, name="Alice", password="4321", card_no=555))
    dev.save_user(User(user_id=101, name="Alice B."))  # rename only
    user = dev.get_user(101)
    assert (user.name, user.password, user.card_no) == ("Alice B.", "4321", 555)
    assert user.slot == 1
    dev.grant(101)
    assert dev.get_authorizations(101)[0].door_mask == 1
    dev.delete_user(101)
    assert dev.get_user(101) is None and dev.get_authorizations(101) == []


def test_template_and_buffered_read(dev):
    for uid in (1, 2, 3):
        dev.save_user(User(user_id=uid, name=f"user {uid}"))
        dev.save_template(Template(user_id=uid, template=bytes([uid]) * 500))
    tpls = dev.get_templates()  # > buffer_threshold: goes through the device buffer
    assert [t.user_id for t in tpls] == [1, 2, 3]
    assert tpls[2].template == bytes([3]) * 500 and tpls[2].slot == 3 and tpls[2].size == 500
    assert dev.count("templatev10") == 3
    dev.delete_templates(2)
    assert dev.count("templatev10") == 2


def test_template_refused_on_duplicate_slot(dev, emu):
    dev.save_user(User(user_id=1, name="a"))
    dev.save_user(User(user_id=2, name="b"))
    emu.state.data["user"][1]["UID"] = emu.state.data["user"][0]["UID"]
    with pytest.raises(SafetyError):
        dev.save_template(Template(user_id=2, template=b"x" * 10))


def test_template_needs_user(dev):
    with pytest.raises(SafetyError):
        dev.save_template(Template(user_id=9, template=b"x"))


def test_wipe_needs_flag(dev):
    with pytest.raises(SafetyError):
        dev.delete_records("user", None)
    dev.delete_records("user", None, allow_wipe=True)


def test_events_and_control(dev, emu):
    emu.state.push_event(EventType.FINGERPRINT_OPEN, user_id=100)
    recs = dev.poll()
    assert isinstance(recs[0], AccessEvent) and recs[0].user_id == 100
    dev.open_door(1, 3)
    assert emu.state.controls[-1] == (1, 1, 1, 3, 0)
    assert dev.poll()[0].event == EventType.REMOTE_OPENING
    assert dev.door_status().door_open(1) is False


def test_backup_restore(dev, emu):
    dev.write_records("timezone", [{"TimezoneId": 1, "SunTime1": 2359}])
    for uid in (10, 20):
        dev.save_user(User(user_id=uid, name=f"n{uid}"))
        dev.save_template(Template(user_id=uid, template=bytes([uid]) * 50))
        dev.grant(uid)
    snap = backup.backup(dev)
    dev.delete_user(10)
    result = backup.restore(dev, snap, wipe=True)
    assert result["user"] == 2 and result["templatev10"] == 2
    assert {t.user_id: t.template for t in dev.get_templates()} == {10: bytes([10]) * 50, 20: bytes([20]) * 50}
    users = {u.user_id: u.slot for u in dev.get_users()}
    assert {t.user_id: t.slot for t in dev.get_templates()} == users


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


def test_light_template_read_and_move(dev):
    from pyzkaccess_native import Template, User
    for uid in (120, 123):
        dev.save_user(User(user_id=uid, name="Alice"))
    dev.save_template(Template(user_id=120, finger_id=0, template=b"A" * 40))
    dev.save_template(Template(user_id=120, finger_id=1, template=b"B" * 40))
    light = dev.get_templates(120, with_data=False)
    assert [(t.finger_id, t.template, t.size) for t in light] == [(0, b"", 40), (1, b"", 40)]
    slot = dev.move_template(120, 1, 123)
    assert slot == 0
    assert [t.template for t in dev.get_templates(123)] == [b"B" * 40]
    assert [t.finger_id for t in dev.get_templates(120)] == [0]


def test_revoke_returns_and_restores(dev):
    from pyzkaccess_native import User
    dev.save_user(User(user_id=5, name="x"))
    dev.grant(5, 1, 1)
    removed = dev.revoke(5)
    assert dev.get_authorizations(5) == [] and len(removed) == 1
    dev.restore_authorizations(removed)
    assert dev.get_authorizations(5)[0].timezone_id == 1


def test_door_settings(dev):
    from pyzkaccess_native import DoorSettings
    s = dev.get_door_settings(1)
    assert s.relay_seconds == 5 and s.close_and_lock == 1
    dev.set_door_settings(DoorSettings(door=1, relay_seconds=3))
    assert dev.get_door_settings(1).relay_seconds == 3


def test_deprecated_aliases():
    import warnings
    from pyzkaccess_native import User
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        assert User(user_id=7, slot=2).pin == 7 and User(user_id=7, slot=2).uid == 2
    assert any(issubclass(x.category, DeprecationWarning) for x in w)


def test_single_finger_delete_without_multi_condition_support(dev, emu):
    # The emulator, like the MA500, ignores a delete with two conditions (Pin + FingerID).
    assert emu.state.multi_condition_delete is False
    dev.save_user(User(user_id=7, name="x"))
    for slot, byte in ((0, b"A"), (1, b"B"), (2, b"C")):
        dev.save_template(Template(user_id=7, finger_id=slot, template=byte * 40))
    dev.delete_templates(7, 1)
    assert [(t.finger_id, t.template) for t in dev.get_templates(7)] == [(0, b"A" * 40), (2, b"C" * 40)]
    dev.delete_templates(7, 5)  # absent slot: nothing happens
    assert dev.count("templatev10") == 2
    dev.delete_templates(7)
    assert dev.count("templatev10") == 0


def test_move_last_template_removes_source(dev):
    for uid in (400, 401):
        dev.save_user(User(user_id=uid, name="Alice"))
    dev.save_template(Template(user_id=400, finger_id=0, template=b"A" * 40))
    dev.save_template(Template(user_id=401, finger_id=0, template=b"B" * 40))
    assert dev.move_template(401, 0, 400) == 1
    assert dev.get_templates(401) == []
    assert [t.template for t in dev.get_templates(400)] == [b"A" * 40, b"B" * 40]
    assert dev.count("templatev10") == 2


def test_move_templates_all_at_once(dev):
    for uid in (500, 501):
        dev.save_user(User(user_id=uid, name="Bob"))
    dev.save_template(Template(user_id=500, finger_id=0, template=b"A" * 40))
    dev.save_template(Template(user_id=501, finger_id=0, template=b"B" * 40))
    dev.save_template(Template(user_id=501, finger_id=3, template=b"C" * 40))
    assert dev.move_templates(501, 500) == {0: 1, 3: 2}
    assert dev.get_templates(501) == []
    assert [(t.finger_id, t.template) for t in dev.get_templates(500)] == [
        (0, b"A" * 40), (1, b"B" * 40), (2, b"C" * 40)]
    assert dev.move_templates(501, 500) == {}


def test_move_templates_refuses_without_room(dev):
    for uid in (600, 601):
        dev.save_user(User(user_id=uid, name="Eve"))
    for slot in range(9):
        dev.save_template(Template(user_id=600, finger_id=slot, template=bytes([slot]) * 20))
    dev.save_template(Template(user_id=601, finger_id=0, template=b"X" * 20))
    dev.save_template(Template(user_id=601, finger_id=1, template=b"Y" * 20))
    with pytest.raises(SafetyError):
        dev.move_templates(601, 600)
    assert len(dev.get_templates(601)) == 2 and len(dev.get_templates(600)) == 9


def test_backup_skips_legacy_template_table(dev, emu):
    from pyzkaccess_native import backup as zk_backup
    dev.save_user(User(user_id=1, name="a"))
    dev.save_template(Template(user_id=1, template=b"T" * 40))
    emu.state.data["template"].append({"Size": 40, "Pin": 1, "FingerID": 0, "Valid": 1, "Template": "x"})
    snap = zk_backup.backup(dev)
    assert "templatev10" in snap["tables"] and "template" not in snap["tables"]
    snap["tables"]["template"] = [{"Size": 1, "Pin": 9, "FingerID": 0, "Valid": 1, "Template": "y"}]
    zk_backup.restore(dev, snap, wipe=True)                   # the firmware cache is left alone
    assert emu.state.data["template"][0]["Pin"] == 1


def test_scan_finds_device_over_tcp(emu):
    from pyzkaccess_native import scan
    found = scan(["127.0.0.1/32"], port=emu.port)
    assert [(d.ip, d.serial_number) for d in found] == [("127.0.0.1", "EMU0000000001")]
    emu.state.password = "1234"
    found = scan(["127.0.0.1/32"], port=emu.port)
    assert found[0].serial_number is None and found[0].raw["auth"] == "required"
    assert scan(["127.0.0.1/32"], port=1) == []               # nothing listening


def test_scan_refuses_bad_ranges():
    from pyzkaccess_native import scan
    with pytest.raises(ValueError):
        scan(["10.0.0.0/16"])                                 # too large
    with pytest.raises(ValueError):
        scan(["not-a-network"])


def test_rename_keeps_slot_and_templates(dev):
    """MA500: a record rewritten without its slot moves to the lowest free one while its
    templates stay behind (a renamed user's finger then matched someone else)."""
    for uid in (101, 102, 109):
        dev.save_user(User(user_id=uid, name="Bob"))
    dev.save_template(Template(user_id=109, finger_id=0, template=b"N" * 40))
    slot = dev.get_user(109).slot
    dev.delete_user(101)                                  # frees a lower slot
    dev.save_user(User(user_id=109, name="Robert"))       # rename
    assert dev.get_user(109).slot == slot
    assert [t.slot for t in dev.get_templates(109)] == [slot]
    assert dev.check_template_slots() == []
    # The device itself moves a record written without its slot (raw write):
    dev.write_records("user", [{"Pin": 109, "Name": "raw"}])
    moved = dev.get_user(109).slot
    assert moved != slot and dev.check_template_slots() == [(109, slot, moved)]
    assert dev.repair_template_slots() == [109]
    assert [t.slot for t in dev.get_templates(109)] == [moved] and dev.check_template_slots() == []
    assert dev.get_templates(109)[0].template == b"N" * 40
