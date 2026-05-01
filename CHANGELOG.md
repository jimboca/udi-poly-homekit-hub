# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Releases / PG3 install:** delivery is **`beta`** and **`production`** git branches (**`make beta`**, **`make production`**, **`make release`** updates **`production`** + annotated tag). **`make release`** no longer builds **`HomeKitHub.zip`**; **`make zip`** is kept for optional local archives only.

### Fixed

- **`zip_exclude.lst`**: exclude Polyglot **`*.cert`**, **`*.key`**, **`*.lock`**, and **`snapshot-all.txt`** so store zips do not bundle host secrets or debug artifacts. **`make zip`** now removes an existing **`HomeKitHub.zip`** first so **`zip -r`** cannot leave stale entries from older builds.

## [0.2.0] - 2026-05-01

### Added

- **`make release`**: build **`HomeKitHub.zip`**, create annotated **`v`<version>**, **`git push`** current branch + tag to **`origin`** (or **`GIT_REMOTE`**), then write **`release-pg3-store.txt`** (versions, **`zip_path`**, branch/remote/tag hints). Requires clean tree and a checked-out branch (not detached **`HEAD`**).
- **WebSocket `list_devices`** (and hello **`ack`**): each paired row may include HAP **Accessory Information** metadata (**`name`**, **`manufacturer`**, **`model`**, **`serial_number`**, **`firmware_revision`**, **`hardware_revision`**, **`category`**, **`category_label`**, **`primary_aid`**) so clients can filter by vendor or category (e.g. Ecobee thermostats) without out-of-band config. When the cached accessory model has no **Manufacturer** yet, the hub issues a **read** of those characteristics so metadata can fill in on the same response.
- **`PROTOCOL.md`**: added a client playbook for selecting device types/capabilities (switch, light, plug, thermostat, sensors, etc.) using `list_devices` metadata as a first-pass hint and `snapshot`/`get` characteristic sets as the authoritative capability model.
- **WebSocket**: optional Custom Param **`ws_token`**. When non-empty, clients must complete **`hello`** with a matching **`token`** / **`ws_token`** field before other actions; hello **`ack`** includes **`device_ids`** and **`capabilities`** (supported actions, auth mode, event-filter semantics).
- **WebSocket** actions **`get`** (partial read), **`subscribe`** / **`unsubscribe`** (per-connection `event` fan-out filtering). Documented in **`PROTOCOL.md`** with **`PROTOCOL_VERSION` bump policy**.
- When a **degraded** IP pairing slot **recovers** (health probe), the hub reports the live **LAN host:port** on the existing `pairing_health_notice` callback; the controller persists it to the matching Custom Typed row **`discover_endpoint`** (so the UI matches the resolved endpoint after reboot or IP/port change).
- **Dev ergonomics (P4):** `pyproject.toml` (**ruff**, **black**, **pytest** settings), optional **`.pre-commit-config.yaml`** (ruff + ruff-format, aligned with CI), **`Makefile`** targets `lint`, `format-check`, `black-check`, `test`, `clean`, and `xml-check` (profile **`check`** aliases `xml-check`; removed debug `echo` of XML globs). **`install.sh`** no longer runs `pip install --upgrade pip`, uses **`pip3 install --no-input`**, and prints **python3** / **pip3** / **udi_interface** diagnostics. Pytest config moved from **`pytest.ini`** into **`pyproject.toml`**. Python sources were run through **`ruff format`** once so `make format-check` stays green.

### Changed

- **Profile / Node Server version** **0.2.0** (`profile/version.txt` and `nodes/__init__.py` **`VERSION`**) so IoX receives updated profile/NLS and matches the running server.

- **Paired device nodes**: NLS **`ND-HKHubPairedDevice-ICON`** is **`GenericRspCtl`** ([Appendix: Icons](https://wiki.universal-devices.com/Polisy_Developers:ISY:API:Appendix:Icons)). The **admin-console tree** icon is driven by the Polyglot **`addnode` `hint`**, not by that NLS key alone; **`PairedDeviceNode`** now sets **`hint`** to **`0x01040200`** (home · Relay · On/Off Power Switch · n/a per [UniversalDevicesInc/hints](https://github.com/UniversalDevicesInc/hints/blob/master/hint.yaml)) so new nodes are not created with the default **`[0,0,0,0]`** “unknown” / bulb glyph. Nodes already in IoX keep their stored hint until you remove them and let the plugin add them again (same address after sync).

- **Python minimum 3.10**: **`aiohomekit` ≥3.2** and current **`udi_interface`** publish wheels that require Python **3.10+**, so **`pip install -r requirements.txt`** cannot succeed on 3.9. **`pyproject.toml`** **`requires-python`**, entry-point version check, **CI matrix** (**3.10** / **3.11**), and docs now match.

### Fixed

- **Paired device tree icon**: previous builds left **`hint`** at the **`udi_interface`** default, so IoX showed the bulb/unknown glyph even after NLS **`ND-*-ICON`** changes.

- **GitHub Actions CI**: bump `actions/checkout`, `actions/setup-python`, `astral-sh/ruff-action` (pin **`v4.0.0`** — immutable tags, no floating `v4`), and `actions/upload-artifact` to versions that run on **Node.js 24**, avoiding deprecated Node 20 action runtimes on `ubuntu-latest`.

- **Controller**: `_get_node_key_next_index` tolerates instances built with `__new__` (unit tests) that never run `__init__`, so discover-append helpers do not raise on a missing `_node_key_next_index_cache`.

- **WebSocket `snapshot`**: if the pairing has no in-memory accessory layout yet (e.g. after reload or before a successful HAP fetch), the hub now **calls `/accessories` first** instead of returning **0 characteristics** with no explanation. If the accessory is unreachable, the client receives an **`error`** frame with the underlying message instead of an empty snapshot.
- Pairing health probes now **force a HAP DNS-SD refresh and close the IP session** after a failed probe, then **retry with backoff and additional DNS-SD bumps** (accessories often need time to listen, and the HAP port can change twice during boot). This fixes **Degraded** sticking after power-cycle when a single immediate retry races the accessory or stale mDNS.
- If soft recovery still fails, the hub now **reloads the slot’s saved pairing blob into a fresh aiohomekit `IpPairing`** (clears wedged connector tasks) and retries, with **settle delays** to avoid `CancelledError` races during reconnect.

## [0.1.14] - 2026-04-30

### Added

- New per-row typed field **`node_key`** (plugin-managed) to anchor child-node identity.
- Paired-device child node command **`DELETE`** (remove typed row + saved pairing entry + node).
- Automatic child node creation for DISCOVER/typed candidate rows (`ST=0`) before pairing completes.

### Changed

- Child-node address is now stable and row-based: **`hkp_<node_key>`** (no longer derived from slot/device id/name).
- Child-node default naming is now static (`HK Device <NODE_KEY>`) and no longer toggles between Candidate/Paired.
- Child-node `UNPAIR` / `DELETE` flows now resolve by **`node_key`**, so IoX references can remain stable when slot assignments or physical devices change.
- Auto-generated `node_key` allocation is now persistent/monotonic (`customdata` cursor), so keys are not automatically reused after row deletion.
- Added periodic pairing health probes that detect accessory reconnects and refresh listeners/subscriptions after transient offline/reboot periods.
- Pairing health degradation now surfaces in the controller via **ERRC-11** and clears automatically when all probed pairings recover.
- Per-device node health is now exposed on **GV1** (`Healthy`/`Degraded`) so operators can identify which paired slot is failing probes.
- Removed controller-level `UNPAIR` profile command exposure; unpair/delete actions are now on each paired-device node.
- Documentation updated to clarify that `node_key` preserves IoX node-address continuity across unpair/re-pair and replacement devices.

## [0.1.13] - 2026-04-29

### Added

- **UNPAIR (slot 1–16)** controller command: clears the pairing code on the Custom Typed row that resolves to the chosen slot and reloads hub sessions (same semantics as clearing **hap_pin** in the editor).
- **`homekit_hub.x_hm_uri.decode_x_hm_setup_uri`** and **`tools/decode_x_hm_setup.py`**: decode HomeKit **`X-HM://`** setup URIs to the numeric **`XXX-XX-XXX`** code (27-bit HAP payload).
- **`PROTOCOL.md`**: example **`websockets`** client (hello → list_devices → event loop).
- **`CONFIG.md`**: controller command summary, **`X-HM://`** / decode helper pointer.

### Changed

- **WebSocket fan-out**: each client has a bounded outbound queue and sender task; **`_broadcast`** no longer awaits **`ws.send`** for every peer on one slow connection. Overflow drops the **oldest** queued line per client and logs a warning.
- **HAP events**: characteristic updates go through a bounded hub queue and a single broadcast worker instead of unbounded **`loop.create_task`** per characteristic.
- **`homekit-poly.py`**: **`checkProfile()`** instead of **`updateProfile()`** (PG3x profile install semantics).
- **Transport discovery**: **`_iter_transport_discoveries`** (and zeroconf diag) tolerate **`aiohomekit`** internal API drift with a one-time warning; module docstring documents supported **aiohomekit** 3.2.x–3.x range.

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

- Pytest harness (`tests/`, `pyproject.toml` / `pytest` settings, `requirements-dev.txt`) for pure helpers in `homekit_hub.bridge` and `nodes.Controller`.
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
