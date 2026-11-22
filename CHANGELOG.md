# Changelog

## Unreleased

- New logo (integration brand icons regenerated from `docs/assets/logo.svg`).
- Home Assistant integration: the access event no longer uses the deprecated
  `DeviceRegistry.async_get_device` (removed in Home Assistant 2027.8).
- Home Assistant integration: no more "Unable to remove unknown job listener" error
  when the entry is reloaded (listeners were removed twice on shutdown).
- Library: removing or moving a single fingerprint no longer relies on a delete with two
  conditions (`Pin` + `FingerID`). The MA500 (firmware 6.64) acknowledges such a delete
  but removes nothing, so moved fingers stayed on the source UserID. A single finger is
  now removed by deleting the user's templates (`Pin` only) and writing the others back.
- Library: new `move_templates()` moves all the fingers of a UserID in one go (copies
  first, then one delete); new `lock` property to run several calls as one sequence.
- Emulator: like the MA500, ignores deletes with several conditions
  (`DeviceState(multi_condition_delete=True)` restores the old behaviour).
- Home Assistant integration: when the device stops answering, reconnection attempts
  back off (5 s, doubling up to 60 s) instead of retrying on every poll; panel actions
  still try at once. A panel operation now runs as one sequence, without polls in between.
- Home Assistant integration: merging a UserID into a category moves its fingers in one
  go and keeps their physical finger.

- Home Assistant integration: **identify a finger** on the reader from the panel (on a
  person, or from a free finger). The page shows the person, UserID and fingerprint slot,
  places it on the hand and offers to delete it; meanwhile the reader's events run no
  automation. A UserID with several fingerprints is separated for a moment on temporary
  UserIDs (9990-9999) without rights, then restored and checked (also after a restart).
- Home Assistant integration: the reader polling interval can be set in the panel's
  Settings (0.3 to 3 s; also in the integration options) and applies without a reload.
- Backups no longer fail with "truncated record in table 'template'": the legacy
  `template` table is left out when `templatev10` exists (the MA500 fills it by itself with
  pre-v10 templates keyed by internal slot, a firmware cache), at backup and at restore.
- Home Assistant integration: a phone access that releases the door no longer adds a
  second "Remote opening" line (the device's echo of that command is ignored), and events
  of the temporary UserIDs of an identification never reach the history.
- Panel: fingers are aligned whether enrolled or not, and a vertical line separates the
  two hands on wide screens.
- Home Assistant integration: each category can be switched off for one person (*Active*
  column of the person's categories). The device refuses its fingers, card and code, and
  the integration reports them as denied, phones included; a second presentation or a
  toggle that leads to a switched-off category is denied too. Kept when the whole person
  is disabled and enabled again.
- Panel: *Learn a card* wrote card 0 (refused: "not a valid value at 'card'") when the
  number field was empty; the card presented on the reader is used then, and a missing
  number gives a clear message.
- Second presentation: the gap is measured with the device's time of each event, not when
  Home Assistant receives it (polling no longer adds to it), and the default delay is
  5 s instead of 3 s (`RepeatDetector(window=5.0)`; `check(key, when=...)` takes a timestamp).
- Library: `scan(networks)` finds devices on any subnet by trying TCP port 4370 on each
  address of the ranges (in parallel, at most /22 each) and reading their identity;
  CLI `zkaccess scan 192.168.5.0/24`. UDP `discover()` only reaches the same subnet.
- Home Assistant integration: adding a device starts with a search (UDP broadcast, plus a
  TCP scan of Home Assistant's subnet and of the ranges you type), then the device is
  picked from a list or entered by hand. The ranges of the devices already set up are searched
  too, and those devices are listed as such; when nothing is found the search form comes
  back, and submitting it again goes to the manual entry. A blank
  device name no longer gives a blank entry title.
- Panel: no more confirmation when a category's *Device relay* is unchecked (it only
  changes the device's own output; automations receive `action` instead of `granted`).
- README and `examples/home_assistant_automations.yaml`: automations for fingerprints,
  cards and phones (relay driven by Home Assistant, alarm, garage, rules by method, per
  person, double badge, repeated refusals, reader offline). `granted` and `action` only
  differ by the device relay: react to both.

## 0.3.0 - 2026-11-22

### Home Assistant integration
- **Phones as credentials**: Home Assistant NFC tags declared as readers, phone pairing from a
  person's page, per-phone category and on/off; scans produce the usual access event
  (`method: phone`), with second presentation and toggle; the door relay is released when the
  category drives it.
- **Toggle** category type: the category is chosen from the state of an entity.
- `binary_sensor.<device>_connected` (connectivity) and `sensor.<device>_clock_offset`.
- Diagnostics download with personal data redacted.
- Access event: new `toggle` and `phone` fields.
- Panel: confirmation before a category change removes the right to open from existing
  UserIDs; instant updates for integration-side changes (finger on the hand, phones);
  busy indicator; a finger can be un-placed from the hand; device clock and device info moved
  to Settings; "Reload from the device" button; NFC tags labelled explicitly; door choice
  only shown on multi-door devices.

## 0.2.1 - 2026-11-22

- Enrollment wizard: the keypad menu is `3` (backup fingerprint), not `1` (add user). The
  page creates the UserID first and menu `1` refuses an existing UserID (verified on the
  MA500, as well as several fingers per UserID with menu `3`).
- Docs: keypad enrollment, event 23 for a known credential without access rights, events
  27 / 34 for unknown cards and fingerprints, slow filtered reads of the `transaction` table.

## 0.2.0 - 2026-11

### Home Assistant integration
- **Access control** page in the sidebar (administrators only), English and French:
  people, two-hand fingerprint view, enrollment wizard, card learning, cards and keypad
  codes per category, unassigned UserIDs, activity log, door relay and settings, action
  categories, backups.
- People and **action categories**: UserID = person number + category digit; per-category
  relay rights; refused credentials of no-relay categories reported as `action`.
- **Second presentation** (double badge) per category.
- Enable / disable a person with remembered rights; link to a Home Assistant person.
- Richer access event (`result`, `person`, `person_name`, `category`, `method`,
  `gesture`...), logbook entries, activity history, new counters and "last person" sensor.
- Automatic backups (on change and nightly, with retention), restore, import, download.
- Notification when a user is added on the device keypad; nightly clock sync.
- Breaking: action and event fields use `user_id` instead of `pin`; `last_user` sensor
  replaced by `last_person`; `users`/`templates` counters replaced by `people`,
  `fingerprints` and `cards`.

### Library
- Vocabulary: **UserID** (`user_id`) instead of PIN, **slot** instead of UID. The old names
  still work with a deprecation warning.
- `CategoryScheme` (person + category numbering), `RepeatDetector` (double badge).
- `DoorSettings`, `get_door_settings()` / `set_door_settings()`.
- `move_template()`, `get_templates(with_data=False)`, `revoke()` returns the removed rights,
  `restore_authorizations()`.
- Python 3.10+.

## 0.2.0 - 2026-11

### Library
- Vocabulary: users are identified by their **UserID** (`user_id`, device field `Pin`); the internal
  slot that binds templates is `slot` (device field `UID`). `pin` and `uid` still work but are
  deprecated.
- `CategoryScheme`: "person + category" numbering of UserIDs.
- `RepeatDetector`: detects a credential presented twice within a delay (double badge).
- `get_door_settings` / `set_door_settings` (`DoorSettings`).
- `move_template`, `get_templates(with_data=False)` (fast, no biometric data),
  `revoke` now returns the removed rights and `restore_authorizations` puts them back.

### Home Assistant integration
- **Access control page** in the sidebar (administrators only): people, hand view of the
  fingers, enrollment wizard, card learning, one card and one code per category, unassigned
  UserIDs, live activity log, door relay and settings, action categories, backups.
- People and action categories, with per-category device relay and second presentation.
- Access events enriched with person, category, method and gesture; new `action` result;
  logbook entries.
- Automatic backups on every change and nightly; restore by merge or exact copy.
- Notification when a user is added on the device keypad.
- Breaking: services take `user_id` (was `pin`) and `code` (was `password`); event attributes
  `pin` became `user_id`; sensors are `people`, `fingerprints`, `cards` and `last_person`.
- English and French.

## 0.1.1 - 2026-11-22

- Home Assistant integration logo (shipped in `brand/`, shown from Home Assistant 2026.3).
- Logo in the README.
- No functional change in the library.

## 0.1.0 - 2026-11

First public release.

- Pure-Python PullSDK / ZKAccess client: session, parameters, clock, tables
  (read with buffered transfers, write, delete, count), real-time log, control commands.
- Typed helpers for users, fingerprint templates and door authorizations, with safety
  checks (record merge, UID collision, no accidental table wipe, cascade delete).
- JSON backup / restore, byte-exact for fingerprint templates.
- `zkaccess` command line tool.
- Device emulator for tests.
- Home Assistant integration (HACS): access event entity, bus events, door sensors,
  counters, open-door buttons, user management services.
