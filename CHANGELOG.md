# Changelog

## 0.6.3 - 2026-11-24

- Home Assistant integration: **two cards per category** (category numbering). The device
  keeps one card number per UserID, so card 2 is stored on UserID 9000 + the category's UserID
  (9120 for 120), with the same name and access rights. It follows the person and category
  switches, renaming and deletion; its events carry the person and category of card 1. The
  panel shows Card 1 / Card 2 per category, and "Learn a card" lets you choose the slot (first
  free one by default). Not available for person 99 (9990-9999 are the identification's
  temporary UserIDs).
- Home Assistant integration, finger identification: the panel no longer redraws the hands
  while the fingerprints are isolated on the temporary UserIDs (they showed as empty); the view
  is re-read once everything is back in place. A finger of another person is refused at the
  first reading, without isolating its fingerprints and asking for it again.

## 0.6.2 - 2026-11-24

- Home Assistant integration, security: card numbers are no longer in the
  `zkaccess_native_access` event, in the attributes of the event entity (recorder history) nor
  in the activity sent to users who are not administrators; a new `card_known` field tells
  automations whether the card is known. The logbook says "Unknown card" instead of its number.
- Home Assistant integration, security: the raw actions `set_user`, `delete_user`, `grant`,
  `revoke` and `list_users` are reserved to administrators.
- Home Assistant integration: the check for a pending fingerprint journal no longer touches the
  disk on the event loop at every poll (state kept in memory, read once at setup).
- README: installation from the HACS search first.

## 0.6.1 - 2026-11-23

- Home Assistant integration, panel and activity card: dates and times follow the user's Home
  Assistant profile (12 h / 24 h, and the server or browser time zone), as in the rest of HA.
- Home Assistant integration, settings: the *Used by* column of the categories lists every
  person (it stopped at the first four, without saying so).
- Home Assistant integration, finger identification: a finger of another person than the one
  being edited is refused (the panel says whose finger it is, and which one) and nothing is
  changed; the panel shows the current finger on the hand and the category; the finger is no
  longer placed at once, *Confirm* records it, by default on the current finger (or the one
  chosen to start). The category can be changed from the same dialog (the fingerprint
  moves to the UserID of that category).
- Home Assistant integration: journaled fingerprint operations. Identification, moving a
  fingerprint to another category, deleting one, assigning an unassigned UserID, renaming,
  card/code, enrollment preparation and slot repair write the state of the UserIDs they touch
  (templates with their data) to a journal on disk first, then check the device byte for byte
  (finger slots, data, own and unshared internal slot). Any difference or error writes these
  UserIDs back as they were; an interrupted operation (restart, frozen device) is settled at
  the next poll and retried every minute, with a notification. The end of an identification
  is checked against the journal, and falls back to it when the temporary copies are lost.
- Home Assistant integration: notification when fingerprints or UserIDs disappear from the
  device without an operation of Home Assistant.
- Home Assistant integration: response time history in the panel (Settings): chart of the
  hourly measurements read from the recorder (24 h, 7 days, 30 days, 1 year; hourly or daily
  average with min-max band beyond 7 days), with the reboot threshold and the reader reboots,
  which are now kept for a year.

## 0.6.0 - 2026-11-22

- Home Assistant integration: the store is saved safely from device operations, which run
  outside the event loop (Home Assistant's delayed save was scheduled from that thread, and the
  file was written from data an operation could still be changing).
- Home Assistant integration: during the 25 s a device takes to reboot, an action started by the
  user (panel, action, button, switch) stops at once with "the reader is rebooting, try again in
  N s"; internal operations (schedules, identification) wait for the end of the boot.
- Home Assistant integration: **Repairs** issues, removed by themselves: device unreachable for
  more than 30 minutes; device rebooted for slowness at least twice in 24 hours.
- Home Assistant integration: icons in `icons.json` (entities and actions), `PARALLEL_UPDATES`
  declared, diagnostics include the maintenance settings, last response time and reboot.
- **Fix (MA500): finger identification reported "not the same finger".** The MA500 takes about
  20 to 45 s to match a template copied to a new UserID (measured). A reading that does not
  conclude (unknown finger, the former owner, another UserID seen once) now asks for the finger
  again instead of reporting another finger, and the second step waits up to 90 s. The restore also checks that the templates are bound to
  the user's slot, and rebinds them otherwise.
- **Fix (MA500): fingerprints recognized as another person.** Rewriting a user's record
  (rename, card, code) without its internal slot made the MA500 move the user to the lowest
  free slot while its fingerprint templates stayed bound to the old one: they failed, or matched
  the next user given that slot. The library now rewrites a user with its own slot, checks it,
  and rebinds the templates if the device moved it anyway (`rebind_templates`,
  `check_template_slots`, `repair_template_slots`). The emulator now behaves like the MA500.
- Home Assistant integration: safety net, every read of the device checks that each template is
  bound to its own user's slot, and rebinds it otherwise (with a notification).
- Home Assistant integration: each reading during a finger identification is logged (step,
  UserID reported, event code).
- Code: the coordinator is split into `identify.py`, `phones.py`, `maintenance.py` and
  `helpers.py` (no behaviour change).

## 0.5.3 - 2026-11-22

- Home Assistant integration: the automatic reboot of a slow device could not happen during
  the first 6 hours after the host started (cool-down measured from the host's boot).

## 0.5.2 - 2026-11-22

- Home Assistant integration: faster finger identification on a UserID with several
  fingerprints. Fewer exchanges with the device (4 fingerprints: 11 instead of 18 to separate
  them, 14 instead of 26 to put them back), and the result shows as soon as the finger is
  recognized while the fingerprints are put back in the background (an action started
  meanwhile, such as deleting that fingerprint, waits for it).
- Home Assistant integration: **maintenance**. Reboot button and `restart` action; nightly
  reboot at 04:00 after the clock sync and backup; hourly response-time measure (diagnostic
  sensor) with a reboot when it is over 0.5 s (at most every 6 h). Both options can be turned
  off in Settings, where the response time can also be measured on demand. No reconnection
  attempt while the device boots.
- Protocol: reboot (control operation 3) verified on the MA500.
- Panel Settings: changes are saved at once (no Save button); the General / Maintenance card
  is at the top of the right column.
- Panel and activity card: their URL carries a hash of the file, so an update is always
  loaded (it could stay cached when the version number did not change).

## 0.5.1 - 2026-11-22

- README: supported devices made explicit (PullSDK / ZKAccess access control devices; time
  attendance terminals such as MA300, F22, SpeedFace use another protocol), with a quick check.
- README: security advice, do not wire the door lock to the relay of a reader mounted
  outside (tearing it off would expose the wires); keep the lock relay indoors and let Home
  Assistant open the door.

## 0.5.0 - 2026-11-22

- Home Assistant integration: **Reconfigure** a device (new address, port or password)
  without losing anything; a refused communication password asks for the new one (reauth).
- Home Assistant integration: ZKTeco devices (MAC prefix 00:17:61) found by DHCP are offered
  for setup, and a configured device's new address is picked up.
- Home Assistant integration: **device triggers** for the automation editor: category
  presented (optionally by one person), access denied, unknown card, unknown fingerprint.
- Home Assistant integration: one **switch per person** (access enabled), and the actions
  `set_access` (person or one of their categories) and `rename_person`. The raw UserID
  actions are marked advanced.
- Home Assistant integration: the activity card works for any logged-in user (reading the
  history no longer needs an administrator).
- Home Assistant integration: door sensor and door alarm disabled by default when no door
  contact is wired; buttons use the same connection path as everything else; errors are
  translated (English, French).
- Panel: "Device relay" renamed "Reader opens its lock", with an explanation.
- Home Assistant integration: a refusal for lack of rights (event 23, a category without the
  device lock) gives the real method (fingerprint, card or code, deduced from the UserID's
  credentials) instead of "other": the MA500 sends no verify mode for it.
- Blueprints: run actions for a category, open and disarm, lock and arm when leaving, alert
  on repeated refusals (`blueprints/automation/zkaccess_native`).
- Emulator: `DeviceState.door_sensors` sets the door contacts of the status record.
- README: manual installation without HACS (download a release, copy
  `custom_components/zkaccess_native`, restart).

## 0.4.2 - 2026-11-22

- Panel: no browser error when its script is loaded again (e.g. after renaming it).
- Home Assistant integration: dashboard card `custom:zkaccess-activity-card`, loaded
  automatically: the access activity with the same rows as the panel's Activity tab (person,
  category, fingerprint / card / phone, second presentation, refusals), live; options
  `title`, `limit`, `filter` (all / open / actions / denied), `person`, `device`.
- Home Assistant integration: logbook entries in French when Home Assistant is in French
  (method, second presentation, refusals).

## 0.4.1 - 2026-11-22

- Home Assistant integration: the sidebar name of the *Access control* page can be changed
  in its Settings (empty = default); applied at once, kept across restarts.

## 0.4.0 - 2026-11-22

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
