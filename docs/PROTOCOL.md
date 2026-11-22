# ZKTeco PullSDK ("C3") TCP protocol

This document describes the protocol spoken by ZKTeco access-control devices that are
managed by **ZKAccess** and the **PullSDK** (`plcommpro.dll`): C3-100/200/400 panels,
inBio panels and standalone readers such as the MA500.

It was reverse-engineered by capturing the traffic of the official DLL and of
ZKAccess 3.5, then checked by replaying the frames from Linux.

Status markers:

- **[verified]**: seen in a capture and reproduced on a real device;
- **[assumed]**: deduced, not tested yet. Reports are welcome.

Reference hardware: ZKTeco MA500 (fingerprint + RFID reader, one door), firmware
`Ver 6.64.0012 Jun 25 2014`, `MachineType=101`.

---

## 1. Transport

- TCP, port **4370** by default **[verified]**.
- Discovery: a UDP broadcast to port **65535** **[assumed on the MA500, documented for C3]**.
- The reference device accepted **two simultaneous sessions** **[verified]**. Real-time
  log events are delivered **to every open session** **[verified]**: two programs polling
  the same device both see each event (keep that in mind if one of them acts on events).
  Write operations from two programs at the same time are not coordinated: avoid them.
- Older firmware may accept only one session [assumed].

## 2. Frame

Requests and replies share the same envelope:

```
AA | ver | cmd | len_lo | len_hi | data[len] | crc_lo | crc_hi | 55
```

| Field | Meaning |
|---|---|
| `AA` / `55` | start / end markers |
| `ver` | `01` in requests, `00` in replies **[verified]** |
| `cmd` | command (request) or reply code (reply) |
| `len` | length of `data`, little-endian |
| `crc` | CRC-16/ARC (polynomial `0xA001` reflected, initial value 0) over `ver .. data` **[verified]** |

### Session

Once a session is open, `data` starts with four bytes:

```
session_lo session_hi request_lo request_hi
```

The device echoes them back in its reply.

- The connect frame carries `FE FE FE FE`. The reply data begins with the new session id
  (2 bytes, LE) **[verified]**.
- The request number then increases by one per request on 16 bits: `FEFF`, `FF00`,
  `FF01`... **[verified]**. This library skips the value 0 on wrap-around.
- Replies longer than one TCP segment are common (a 5 KB buffer chunk arrives in several
  segments): read exactly `len + 3` bytes after the 5-byte header **[verified]**.

## 3. Reply codes

| Code | Meaning |
|---|---|
| `C8` | OK, data (if any) follows **[verified]** |
| `C9` | error; last data byte is a signed code: `-13` unknown command, `-14` wrong password **[verified]** |
| `0D` | answer too large, **stored in a device buffer**; see §6 **[verified]** |

## 4. Commands

| Cmd | Name | Request data | Status |
|---|---|---|---|
| `76` | connect (session) | communication password (ASCII, may be empty) | **[verified]** |
| `01` | connect (session-less) | password | [assumed], older firmware |
| `02` | disconnect | none | **[verified]** |
| `03` | set parameters | text `Name=value,Name2=value2` | **[verified]** |
| `04` | get parameters | text `Name1,Name2`; unknown names are omitted from the answer | **[verified]** |
| `05` | control (doors, aux outputs, alarm, reboot) | 5 bytes, see §8 | **[verified]** (output, cancel alarm) |
| `06` | table layout | none; answer = one text line per table | **[verified]** |
| `07` | write records (`SetDeviceData`) | §5.3 | **[verified]** |
| `08` | read records (`GetDeviceData`) | §5.2 | **[verified]** |
| `09` | delete records (`DeleteDeviceData`) | §5.4 | **[verified]** |
| `0A` | count records (`GetDeviceDataCount`) | `<table index> 00`; answer = 4 bytes LE | **[verified]** |
| `0B` | real-time log, binary | none; §7 | **[verified]** |
| `0E` | read a buffer chunk | offset, 4 bytes LE | **[verified]** |
| `0F` | free the buffer | none | **[verified]** |
| `14` | discovery (UDP) | `CallSecurityDevice` | [assumed] |
| `79` | real-time log, key/value | none | [assumed], firmware without `0B` |

## 5. Tables

### 5.1 Layout (`06`)

One line per table:

```
user=1,UID=i1,CardNo=i2,Pin=i3,Password=s4,Group=i5,StartTime=i6,EndTime=i7,Name=s8,Privilege=i9,SuperAuthorize=i10
```

`name=index`, then `Field=<type><number>` with type `i` (integer), `s` (string) or
`B` (binary). Tables seen on the reference device:

| # | Table | Fields |
|---|---|---|
| 1 | `user` | UID, CardNo, Pin, Password, Group, StartTime, EndTime, Name, Privilege, SuperAuthorize |
| 2 | `userauthorize` | Pin, AuthorizeTimezoneId, AuthorizeDoorId |
| 3 | `holiday` | Holiday, HolidayType, Loop |
| 4 | `timezone` | TimezoneId + 3 intervals for each day of the week and 3 holiday types |
| 5 | `transaction` | Cardno, Pin, Verified, DoorID, EventType, InOutState, Time_second |
| 6 | `firstcard` | Pin, DoorID, TimezoneID |
| 7 | `multimcard` | Index, DoorId, Group1..5 |
| 8 | `inoutfun` | Index, EventType, InAddr, OutType, OutAddr, OutTime, Reserved |
| 9 | `template` | Size, Pin, FingerID, Valid, Template (old format; empty on the reference device) |
| 10 | `templatev10` | Size, UID, Pin, FingerID, Valid, **Template (B)**, Resverd, EndTag |
| 11 | `oplog` | Admin, OP, time, Objs1..5 |

### 5.2 Read (`08`)

```
<table index> <field count> <field number>... 00 <condition count> [<field number> <len> <value>]...
```

- All fields of `templatev10`, no filter: `0a 08 01 02 03 04 05 06 07 08 00 00` **[verified]**.
- Filter `Pin = 899`: `... 00 01 03 02 83 03` (field 3, 2 bytes, 899 LE) **[verified]**.
- The `00` byte before the condition count has an unknown role and is always 0 in captures.
- No match: the answer only contains the header (table, field count, field numbers) **[verified]**.

Answer (direct, or rebuilt from the buffer):

```
<table index> <field count> <field number>... <record><record>...
```

Each value is `<length><bytes>`:

- `i` and `s`: **1-byte** length; integers are little-endian, strings are raw bytes;
- `B`: **2-byte** length (LE) **[verified]**.

Example, start of a `templatev10` record:

```
02 36 03 | 01 01 | 01 64 | 01 00 | 01 01 | 36 03 <822 bytes> | 01 00 | 01 0d
Size=822   UID=1   Pin=100 Finger=0 Valid=1  Template          Resverd=0 EndTag=13
```

### 5.3 Write (`07`)

```
<table index> <field count> <field number>... <values, same encoding as above>
```

| DLL call | `07` data |
|---|---|
| `user`, `Pin=899 CardNo=0 Password= Name=TEST899` | `01 04 03 02 04 08 \| 02 83 03 \| 00 \| 00 \| 07 "TEST899"` |
| `templatev10`, Size, UID, Pin, FingerID, Valid, Template, EndTag | `0a 07 01 02 03 04 05 06 08 \| 02 3e 04 \| 01 04 \| 02 83 03 \| 00 \| 01 01 \| 3e 04 <1086 bytes> \| 01 0d` |

**[verified]** findings:

- The same command **creates or updates**; the key of `user` is `Pin`.
- **A `user` write replaces the whole record.** Fields that are not sent are reset
  (sending only `Pin` + `Name` erases the password and card number). Always send the full
  record. The library's `save_user()` merges with the stored record first.
- Integer 0 may be sent with length 0 (the DLL does), while reads return `01 00`.
  Both are accepted.
- Fields may be sent in any order (the DLL uses the caller's order; this library sorts them).
- A fingerprint template fits in **one frame**; no buffered upload is needed.
- Writing `timezone`, `templatev10` and `userauthorize` this way is verified, as well as `user`.
- Several records in one `07` frame: [assumed], not tested. The library sends one per frame.

#### Internal slot (`UID`) allocation

`UID` is an internal slot number that links a user and its templates (the library calls it
`slot`; the user's number, typed on the keypad, is the **UserID**, stored in the field `Pin`). **The device ignores the UID
sent in a `user` write and allocates one itself** **[verified]**. On a device that had seen
many deletions, newly created users received UIDs **already used by other users** **[verified]**.
A template written for the new user would then share its UID with another person.

Recommendations (implemented by `save_template()`):

1. create the user without UID;
2. read it back to learn its UID;
3. refuse to write a template if another user has the same UID;
4. write the template with that UID.

On an empty device (after a wipe) UIDs are allocated sequentially without duplicates **[verified]**.

### 5.4 Delete (`09`)

```
<table index> <condition count> [<field number> <len> <value>]...
```

| DLL call | `09` data |
|---|---|
| `templatev10`, `Pin=899` | `0a 01 03 02 83 03` |
| `user`, `Pin=899` | `01 01 03 02 83 03` |

- **Without any condition (`09 <table> 00`) the whole table is erased** **[verified]**.
  ZKAccess does this when a device is added with "clear data in device" checked.
- Deleting a user does not delete its templates nor its authorizations: delete
  `templatev10` and `userauthorize` too.

## 6. Large answers (buffer)

When an answer is too large (typically `templatev10`), the device replies `0D` **[verified]**:

```
00 | total(4, LE) | total(4, LE) | checksum(4) | chunk_size(4, LE)
```

1. Send `0E` with the offset (0, then +chunk_size...). The answer is the offset (4 bytes)
   followed by at most `chunk_size` bytes.
2. Repeat until `total` bytes are received.
3. Send `0F` to free the buffer.

The rebuilt buffer has exactly the format of a direct `08` answer. The two `total` values
were always identical. The checksum algorithm is unknown (not needed).

## 7. Real-time log (`0B`)

The answer is a sequence of 16-byte records **[verified]**:

| Bytes | Access event | Door/alarm status (code 255) |
|---|---|---|
| 0-3 | card number (LE) | alarm state, one byte per door |
| 4-7 | UserID (LE) | door sensor, one byte per door (0 none, 1 closed, 2 open) |
| 8 | verification mode | |
| 9 | door number | |
| 10 | event code | 255 |
| 11 | in/out state | |
| 12-15 | time (ZK date) | time (ZK date) |

A status record is returned on every poll. Each event is returned once; poll often (e.g.
every 300 ms to 1 s) to get them promptly.

Event codes observed on the reference device: **0** normal open (card), **7** cancel alarm,
**8** remote opening, **14** fingerprint open, **27** unregistered card (UserID 0, the card
number is in bytes 0-3), **34** unregistered fingerprint (UserID 0), **255** status.

The `transaction` table keeps every event (tens of thousands of rows). A filtered read
(`08` with a condition) on it can take more than 5 seconds, during which the device answers
no other session **[verified]**: avoid it while another program is polling the device. The full list from the PullSDK
documentation is in `pyzkaccess_native.events.EventType`.

## 8. Control (`05`)

Five bytes: `operation p1 p2 p3 p4`; the answer is an empty `C8`:

| Operation | p1 | p2 | p3 |
|---|---|---|---|
| `1` output | door or aux number | `1` door lock, `2` aux output | `0` off, `1-254` seconds, `255` on until closed |
| `2` cancel alarm | | | |
| `3` reboot | | | |
| `4` normal-open state for today | door | `1` enable, `0` disable | |

Verified on the reference device:

| Request | `05` data | Real-time log |
|---|---|---|
| open door 1 for 1 s | `01 01 01 01 00` | event **8** (remote opening), UserID 0, door 1, verify mode 200, in/out 2 |
| cancel alarm | `02 00 00 00 00` | event **7** (cancel alarm), UserID 0, door 0, verify mode 200 |

Reboot and the normal-open state were not tested.

## 9. Parameters and clock

Useful parameters (`04`): `~SerialNumber`, `FirmVer`, `MachineType`, `LockCount`,
`ReaderCount`, `AuxInCount`, `AuxOutCount`, `IPAddress`, `NetMask`, `GATEIPAddress`,
`~ZKFPVersion`, `~MaxUserCount`, `~MaxUserFingerCount`, `MThreshold`, `DateTime`, and the
door settings `Door1CloseAndLock`, `Door1SensorType`, `Door1Drivertime`,
`Door1Detectortime`, `Door1VerifyType`, `Door1ValidTZ`, ... Unknown names are silently
omitted from the answer.

### ZK date format **[verified]**

Every month counts as 31 days:

```
value = ((year-2000)*372 + (month-1)*31 + (day-1)) * 86400 + hour*3600 + minute*60 + second
```

It is device local time. Example: `864233860` = 2026-11-21 16:57:40.

Set the clock with `03 DateTime=<value>`, as ZKAccess does on every connection. Read it
with `04 DateTime`.

## 10. ZKAccess behaviour worth knowing

Observed with ZKAccess 3.5.5:

- Adding a device with **"Clear data in device"** checked erases every table (`09` without
  condition) **before** checking the model. On a device whose `MachineType` it does not
  know (`101`, MA500), ZKAccess then reports *"Unknown Device Model!"*: the device ends up
  empty and is not added.
- On connection ZKAccess only reads (and sets the clock).
- *"Get personnel data from device"* reads `user` and `templatev10` but **not
  `userauthorize`**. Synchronizing its database back to the device would therefore push
  users without door rights.

## 11. Readers with a keypad (MA500)

Keypad enrollment **[verified]**: menu `1` (add) only creates a **new** user and refuses an
existing UserID (red LED, beeps). Menu `3` (backup fingerprint) adds a fingerprint to an
**existing** user: the device stores it with the next free `FingerID` (0, 1, 2... in
enrollment order, not a physical finger). Menu `3` also works for a user created over the
network that has no fingerprint yet (`FingerID` 0).

A fingerprint recognized for a user **without authorization** gives event **23** (access
denied) with the UserID **[verified]**: an application can use it as a credential that does not
release the door.

Users enrolled on the device keypad get no name, and an automatic authorization
(time zone 1, door 1). An RFID card enrolled on the keypad fills `CardNo` even when the
`~RFCardOn` parameter reads 0.

## 12. Open questions

- Several records per `07` frame.
- The `00` byte before the conditions of `08`.
- Command `0C`.
- Remote fingerprint enrollment (no command found so far: enrollment is done on the device).
- Buffer checksum algorithm.
