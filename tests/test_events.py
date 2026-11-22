from pyzkaccess_native.events import (AccessEvent, DoorSensor, DoorStatus, EventType, VerifyMode,
                                     event_label, parse_rtlog)


def test_status_and_event():
    event = bytes.fromhex("00000000" "64000000" "01" "01" "0e" "00" "84d38333")
    status = bytes.fromhex("00000000" "01000000" "00" "00" "ff" "00" "84d38333")
    recs = parse_rtlog(event + status)
    assert isinstance(recs[0], AccessEvent)
    assert recs[0].user_id == 100 and recs[0].event == EventType.FINGERPRINT_OPEN
    assert recs[0].verify_mode == VerifyMode.FINGERPRINT and recs[0].granted
    assert isinstance(recs[1], DoorStatus)
    assert recs[1].sensors[0] == DoorSensor.CLOSED and recs[1].door_open(1) is False
    assert recs[1].time.year == 2026


def test_unknown_event_code_kept():
    rec = parse_rtlog(bytes.fromhex("00000000" "00000000" "00" "01" "cf" "00" "00000000"))[0]
    assert rec.event == 207 and event_label(207) == "Event 207" and rec.time is None


def test_denied():
    rec = parse_rtlog(bytes.fromhex("00000000" "00000000" "01" "01" "22" "00" "84d38333"))[0]
    assert rec.event == EventType.UNREGISTERED_FINGERPRINT and rec.denied
