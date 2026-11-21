# Changelog

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
