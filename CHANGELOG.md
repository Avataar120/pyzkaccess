# Changelog

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
