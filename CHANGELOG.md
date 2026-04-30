# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.12] - 2026-04-29

### Changed

- **START** no longer blocks the PG3 thread on ``time.sleep`` while waiting for custom config. The asyncio hub starts from **CONFIGDONE** (with short delayed retries if CUSTOM* handlers are still finishing), plus a **75s fallback** if CONFIGDONE is missing. **STOP** clears ``mainloop`` / ``bridge`` / ``_loop_thread`` so a restart can bootstrap again.

## [0.1.11] - 2026-04-29

### Fixed

- **longPoll** asyncio-loop watchdog no longer sets **ST**; **ST** remains the Polyglot / Node Server connection driver only. Bridge thread death still sets **GV0** = Error, **ERR** = 10, and a Notice.

## [0.1.10] - 2026-04-29

### Added

- **longPoll** watchdog: if the asyncio event-loop thread exits while the hub is still marked ready, set **GV0** = Error, **ERR** = 10, and post a Notice (restart Node Server). (**ST** is not changed — Polyglot connection only.)

### Changed

- Profile **ERR** editor subset extended to include code **10** (`ERRC-10`).

## [0.1.9] - 2026-04-29

### Added

- Pytest harness (`tests/`, `pytest.ini`, `requirements-dev.txt`) for pure helpers in `homekit_hub.bridge` and `nodes.Controller`.
- GitHub Actions CI: Python 3.9 / 3.11 matrix, `ruff`, `pytest`, `xmllint` on profile XML, tag-only `HomeKitHub.zip` artifact.
- This `CHANGELOG.md`.

### Fixed

- `asyncio.get_running_loop()` instead of deprecated `get_event_loop()` in `discover_collect` and `_wait_for_pairing_discovery`.
- Import `InterfaceChoice` / `IPVersion` from the public `zeroconf` package (not `zeroconf._utils.net`).

### Changed

- Controller profile: **ST** is **NodeServer Online** (Polyglot connection — Disconnected / Connected / Failed), matching **udi-poly-kasa**. **GV0** is **Bridge Status** (Stopped / Running / Error) for the asyncio HomeKit + WebSocket hub. New editor **`brst`** and NLS **`BRST-*`**.

## [0.1.8] - 2026-04-28

### Added

- Auto-discover on typed update when a row has a pairing PIN but no accessory id/name and no cached `last_hap_discover`.
- Normalize 8-digit HAP setup codes to `XXX-XX-XXX` in typed data; pairing failures surfaced via Polyglot Notices.

### Fixed

- Typed auto-discover reads current `TypedData`; empty auto-scan no longer wipes prior discover state.
- Zeroconf on BSD/macOS unicast: narrow interfaces / IPv4 to reduce `sendto` errno 49.
- Unicast zeroconf: pre-create HAP `_hap._tcp` / `_hap._udp` browsers; default `HOMEKIT_HUB_ZEROCONF_UNICAST` in entrypoint; retry unicast when UDP 5353 is `EADDRINUSE`.

### Changed

- DISCOVER typed-slot handling, WebSocket `list_devices` fix, PIN format persistence (`1512e4c`).

## [0.1.0] - 2026-04-27

### Added

- Initial PG3x Node Server: HomeKit hub (`homekit_hub`), controller (`nodes/Controller.py`), WebSocket protocol (`PROTOCOL.md`), multi-slot pairing, DISCOVER via runCmd, centralized `report_error` / ERR driver, profile and docs.

Earlier development history before this file: see `git log`.
