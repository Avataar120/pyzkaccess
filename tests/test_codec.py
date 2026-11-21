"""Unit tests based on frames captured from real devices (values anonymized)."""
from datetime import datetime

import pytest

from pyzkaccess_native import codec, protocol
from pyzkaccess_native.emulator import DEFAULT_TABLE_CONFIG
from pyzkaccess_native.errors import ProtocolError

TABLES = codec.parse_table_config(DEFAULT_TABLE_CONFIG)
USER = TABLES["user"]
TPL = TABLES["templatev10"]


def test_crc16_arc_check_value():
    # Standard check value of CRC-16/ARC.
    assert protocol.crc16(b"123456789") == 0xBB3D


def test_frame_roundtrip():
    frame = protocol.build_frame(protocol.Command.GET_PARAM, b"FirmVer", session=0x1234, request=0xFEFF)
    assert frame[0] == 0xAA and frame[-1] == 0x55 and frame[1] == 0x01
    parsed = protocol.parse_frame(frame)
    assert parsed.command == protocol.Command.GET_PARAM
    assert parsed.data == bytes.fromhex("3412fffe") + b"FirmVer"


def test_frame_bad_crc():
    frame = bytearray(protocol.build_frame(0x04, b"x"))
    frame[-2] ^= 0xFF
    with pytest.raises(ProtocolError):
        protocol.parse_frame(bytes(frame))


def test_table_config():
    assert USER.index == 1
    assert USER.field("Pin").index == 3 and USER.field("Name").type == "s"
    assert TPL.field("Template").type == "B"


def test_query_with_filter():
    # GET_DATA on "user", all fields, Pin = 899
    payload = codec.encode_query(USER, where={"Pin": 899})
    assert payload == bytes.fromhex("010a0102030405060708090a00" "0103028303")


def test_query_without_filter():
    assert codec.encode_query(TPL) == bytes.fromhex("0a080102030405060708" "0000")


def test_write_user_payload():
    # Same record as a captured SetDeviceData("user", "Pin=899\tCardNo=0\tPassword=\tName=TEST899").
    # The official DLL keeps the caller's field order (3, 2, 4, 8); fields are sorted
    # here, which devices accept as well.
    row = {"Pin": 899, "CardNo": 0, "Password": "", "Name": "TEST899"}
    expected = bytes.fromhex("01" "04" "02030408" "00" "028303" "00") + b"\x07TEST899"
    assert codec.encode_records(USER, [row]) == expected


def test_write_template_payload():
    tpl = bytes(range(256)) * 4 + bytes(62)  # 1086 bytes
    row = {"Size": 1086, "UID": 4, "Pin": 899, "FingerID": 0, "Valid": 1, "Template": tpl, "EndTag": 13}
    expected = (bytes.fromhex("0a0701020304050608" "023e04" "0104" "028303" "00" "0101" "3e04")
                + tpl + bytes.fromhex("010d"))
    assert codec.encode_records(TPL, [row]) == expected


def test_delete_condition():
    assert bytes([TPL.index]) + codec.encode_conditions(TPL, {"Pin": 899}) == bytes.fromhex("0a01030283 03".replace(" ", ""))


def test_decode_template_record():
    tpl = b"JuSS21" + bytes(816)
    data = (bytes.fromhex("0a080102030405060708") + bytes.fromhex("023603" "0101" "0164" "0100" "0101")
            + len(tpl).to_bytes(2, "little") + tpl + bytes.fromhex("0100010d"))
    rows = codec.decode_records(TPL, data)
    assert rows == [{"Size": 822, "UID": 1, "Pin": 100, "FingerID": 0, "Valid": 1, "Template": tpl,
                     "Resverd": 0, "EndTag": 13}]


def test_decode_empty_answer():
    assert codec.decode_records(USER, bytes.fromhex("01020308")) == []


def test_truncated_record():
    with pytest.raises(ProtocolError):
        codec.decode_records(USER, bytes.fromhex("010103") + b"\x05\x01")


def test_zero_int_is_empty():
    assert codec.encode_int(0) == b"\x00"
    assert codec.encode_int(899) == b"\x02\x83\x03"
    assert codec.encode_int(13) == b"\x01\x0d"


def test_strings_roundtrip_unknown_bytes():
    raw = b"\x04\xe9t\xe9!"  # latin-1 bytes, not valid utf-8
    rows = codec.decode_records(USER, bytes.fromhex("010108") + raw)
    assert codec.encode_value(USER.field("Name"), rows[0]["Name"]) == raw


def test_datetime():
    assert codec.encode_datetime(datetime(2026, 11, 21, 16, 57, 40)) == 864233860
    assert codec.decode_datetime(864233860) == datetime(2026, 11, 21, 16, 57, 40)
    assert codec.decode_datetime(0) is None


def test_params():
    assert codec.parse_params(b"~SerialNumber=ABC123,FirmVer=Ver 6.64\x00") == {
        "~SerialNumber": "ABC123", "FirmVer": "Ver 6.64"}
    assert codec.encode_params({"DateTime": 864233860}) == b"DateTime=864233860"
