# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.0.7] - 2026-06-25

Edition tags: **(Professional)** = Professional store zip only; **(Standard + Professional)** = both editions.

Beta follow-up (Honeywell T10 program setpoint 74°F → 75°F display):

| Issue | Symptom | Fixed in 2.0.7 |
|-------|---------|----------------|
| Heat setpoint readback | Program `CLISPH` 74°F writes correctly, then HAP echo flips IoX to **75°F** ~200 ms later | **Yes** — ignore ±1 °F `TEMPERATURE_TARGET` echoes for 3 s after a successful write |
| Legacy `minStep` | Nodes paired before 2.0.6 could still wire Ecobee 0.1 °C bins (e.g. 74°F → 23.4°C) | **Yes** — `HKHubThermostat` falls back to **0.5°C** when binding omits `minStep` (74°F → **23.5°C**) |
| °F read path | HAP °C converted with `toF()` round-trip at half-degree boundaries | **Yes** — truncated exact °F display (`int(C*1.8+32)`) on read, matching Honeywell/Ecobee UI |

### Fixed

- **(Professional)** **Honeywell T10 setpoint echo guard:** After a successful `CLISPH`/`CLISPC`/`BRT`/`DIM` write, suppress HAP `TEMPERATURE_TARGET` events that differ by ±1 °F from the value just written (T10 often reports 24.0 °C / 75 °F right after a 23.5 °C / 74 °F write).
- **(Professional)** **`minStep` fallback:** `ThermostatNode` assumes **0.5°C** for `HKHubThermostat` `TARGET_TEMPERATURE` when char bindings lack `minStep` (stale nodes from before 2.0.6).
- **(Professional)** **Truncated °F read path:** `driver_st_from_hap_celsius()` uses truncated exact °F for Fahrenheit thermostats (e.g. 22.5 °C → 72 °F, not 73 °F).

### Added

- **(Professional)** **Tests:** 74°F → 23.5°C with `minStep` 0.5; legacy binding fallback; echo guard; truncated read parity.

### Changed

- **(Standard + Professional)** Version **2.0.7** — `nodes/__init__.py` **`VERSION`** and `profile/version.txt`.

## [2.0.6] - 2026-06-25

Edition tags: **(Professional)** = Professional store zip only; **(Standard + Professional)** = both editions.

Beta follow-up (Honeywell T10 heat setpoint rejected by HAP):

| Issue | Symptom | Fixed in 2.0.6 |
|-------|---------|----------------|
| Heat setpoint on T10 | `CLISPH` 70°F → HAP write `21.2`°C fails; stat stays at 68°F | **Yes** — quantize to characteristic `minStep` (0.5°C); 70°F writes **21.5°C** |
| Program command picker | HomeKit thermostats missing from Admin Console program actions | **Yes** — nodedef `accepts` aligned with Ecobee HomeKit template (`BRT`/`DIM`, Ecobee `GV3` editors) |

### Fixed

- **(Professional)** **Honeywell T10 `minStep` setpoints:** `TEMPERATURE_TARGET` uses **0.5°C** steps; Ecobee-oriented 0.1°C wire values (e.g. 70°F → 21.2°C) are rejected by HAP (-70410). Char bindings now carry HAP `minStep`; setpoint writes quantize to the bound characteristic grid (70°F → **21.5°C**).
- **(Professional)** **HAP write diagnostics:** `hub_write_by_iid` logs the bridge error string (status / message) on failure.

### Added

- **(Professional)** **Thermostat program commands:** `HKHubThermostat` and `HKHubEcobeeThermostat` nodedefs accept `BRT`/`DIM` (optional step editor `I_SETTEMP_F`); `ThermostatNode.set_point()` implements increment/decrement.
- **(Professional)** **Ecobee program profile:** `HKHubEcobeeThermostat` `GV3` status/command editors (`CTA_HK`, `CT_HK`) for Admin Console program picker parity with udi-poly-ecobee HomeKit template.
- **(Professional)** **Tests:** Honeywell `minStep` 0.5°C conversion (70°F → 21.5°C); setpoint write asserts wire value when `minStep` is in bindings.

### Changed

- **(Standard + Professional)** Version **2.0.6** — `nodes/__init__.py` **`VERSION`** and `profile/version.txt`.

## [2.0.5] - 2026-06-25

Edition tags: **(Professional)** = Professional store zip only; **(Standard + Professional)** = both editions.

Beta follow-up (Honeywell T10 / multi-accessory thermostats with **Professional generic nodes**):

| Issue | Symptom | Fixed in 2.0.5 |
|-------|---------|----------------|
| Heat setpoint programming | `CLISPH` does not change physical thermostat (Honeywell T10) | **Yes** — heat/cool mode writes `TARGET_TEMPERATURE` when bound; auto mode uses thresholds |
| Wrong HAP target | Name-only writes hit first matching characteristic across accessories | **Yes** — thermostat writes use node `char_bindings` aid/iid via `hub_write_by_iid` |
| Support log gaps | `persistent/*.json` device inventory not in PG3 log package | **Yes** — full inventory JSON mirrored to log as `INVENTORY` lines (pairing export + config snapshot) |

### Fixed

- **(Professional)** **Honeywell / generic thermostat setpoints:** `CLISPH`/`CLISPC` in heat or cool mode prefer `TARGET_TEMPERATURE` when the device exposes target temp plus heating/cooling thresholds (T10-style). Threshold-only devices still use threshold characteristics; auto mode writes both thresholds.
- **(Professional)** **Binding-aware HAP writes:** `ThermostatNode._hub_write()` resolves the node's bound aid/iid before falling back to global characteristic name lookup — critical for pairings with multiple accessories (e.g. T10 + RedLINK room sensors on aids 1, 5, 6, 7).
- **(Professional)** **Failed write visibility:** thermostat hub writes log at INFO when HAP `put_characteristic` returns failure (aid, iid, characteristic, value).
- **(Professional)** **Ecobee setpoint holds:** `EcobeeThermostatNode` writes heating/cooling thresholds together and activates schedule hold after successful `CLISPH`/`CLISPC` programming.
- **(Professional)** **Ecobee vs generic classification:** Ecobee vendor fingerprint detected pairing-wide; thermostat nodedef migrates when classification changes (`HKHubThermostat` ↔ `HKHubEcobeeThermostat`).

### Added

- **(Professional)** **Device inventory in debug log:** `persistent/<device_id>.json` contents emitted as `INVENTORY begin` … `INVENTORY end` lines when inventory is exported and again on configuration snapshot export — PG3 log packages now include full HAP aid/iid trees for remote support.
- **(Professional)** **Tests:** Honeywell T10 + RedLINK sensor classification fixture; thermostat setpoint write path tests (`TARGET_TEMPERATURE` vs thresholds, binding-aware writes).

### Changed

- **(Standard + Professional)** Version **2.0.5** — `nodes/__init__.py` **`VERSION`** and `profile/version.txt`.

## [2.0.4] - 2026-06-25

Edition tags: **(Professional)** = Professional store zip only.

Beta follow-up (Ecobee thermostats with **Professional generic nodes**) — issues **#1**, **#3**, **#4**:

| Report | Symptom | Fixed in 2.0.4 |
|--------|---------|----------------|
| **#1** | Room sensors without humidity hardware show **0%** humidity | **Yes** — `HKHubSensorDry` nodedef (no `CLIHUM` driver); humid room sensors keep `HKHubSensor` |
| **#3** | Main Floor room sensors stale 15+ min after restart | **Yes** — one HAP snapshot per device, debounced startup refresh, periodic refresh by `device_id` |
| **#4** | Thermostat QUERY does not refresh room sensors | **Yes** — thermostat QUERY runs device-scoped refresh for thermostat **and** all child sensors |

### Fixed

- **(Professional)** **Sensor nodedef split:** `HKHubSensor` (humid room), `HKHubSensorDry` (no HAP humidity hardware), `HKHubMotionSensor` (built-in motion; no battery drivers). Selection uses HAP `char_bindings` at discovery (mirrors ecobee `EcobeeSensorF` / `EcobeeSensorHF` pattern).
- **(Professional)** **Device-scoped refresh:** `refresh_device_generic_nodes()` — one `hub_snapshot_values()` per thermostat device on startup (debounced), sensor/thermostat QUERY, and periodic longPoll refresh.
- **(Professional)** **Deferred driver reporting:** sensor refresh uses `apply_driver_schema(report=True)` instead of raw `reportDrivers()`, so undeferred humidity/battery values are not overwritten with schema zeros.

### Changed

- **(Professional)** **Upgrade / migration:** On first start after updating to 2.0.4, existing sensor nodes with the wrong nodedef are **automatically deleted and re-added at the same address** (no re-pairing; no manual IoX delete). Log: `Recreating sensor IoX node …`. Dry room sensors lose spurious `CLIHUM`; motion children lose spurious `BATLVL`/`BATLOW`. Node names and addresses are preserved; programs keyed by address should be unaffected.
- **(Standard + Professional)** Version **2.0.4** — `nodes/__init__.py` **`VERSION`** and `profile/version.txt`.

## [2.0.3] - 2026-06-25

Edition tags: **(Standard)** = Standard store zip only; **(Professional)** = Professional store zip only; **(Standard + Professional)** = both editions.

Beta sensor evaluation (Ecobee thermostats with **Professional generic nodes**) — what we found and what this release fixes:

| Report | Symptom | Evaluation | Fixed in 2.0.3 |
|--------|---------|------------|----------------|
| **#3** | Thermostat motion child humidity blank | Motion mirror path blocked `CLIHUM` for `motion_sensor` role | **Yes** — humidity mirrored to motion child |
| **#4** | Room sensors show 0% humidity | Many Ecobee room sensors do not expose HAP humidity; plugin published default 0 at addnode | **Partially** — humidity stays blank until first HAP reading; true 0% still shown when reported |
| **#5** | Motion child battery 0% | Mains-powered thermostat has no HAP battery char; motion schema included battery drivers | **Yes** — battery drivers removed from motion child |
| **#6–7** | 15+ min delay; stale temp; 0% battery | Ecobee room sensors report infrequently (~15–25 min) **plus** re-hydrate pushed PG3 default zeros to IoX | **Yes** — re-hydrate no longer wipes live values; periodic snapshot refresh; QUERY on sensor start |
| **#8** | Kitchen occupancy missing | Log showed Kitchen `GV1` updates; likely re-hydrate wipe or viewing Ecobee plugin nodes vs hub `HKHubSensor` children | **Likely** — re-hydrate fix should help; confirm you are viewing hub generic sensor nodes |
| **#9** | Random thermostat notices | `homekit_inventory_export` PG3 Notice on every health/metadata refresh | **Yes** — automatic export unchanged; Notice only on manual **EXPORT_INVENTORY** |

**Not a plugin bug:** GameRoom sensor never received any HAP updates in the submitted log — check Ecobee/HomeKit reachability for that sensor first.

**Operator note:** Hub MQTT transport always runs for paired devices. If you use **udi-poly-ecobee**, leave hub **generic_nodes_enable** off unless you want a second IoX tree (different node addresses under the hub Node Server).

### Fixed

- **(Professional)** **Motion child humidity:** `CLIHUM` added to motion sensor schema; HAP relative humidity applied on mirror path (issue **#3**).
- **(Professional)** **Re-hydrate wipe:** existing sensor nodes re-bind without `apply_driver_schema(report=True)` unless driver UOM/names are stale; snapshot refresh repopulates readings (issues **#6–8**).
- **(Professional)** **Blank vs 0%:** humidity and battery drivers omitted from IoX until first HAP value; motion children no longer expose battery drivers (issues **#4**, **#5**, **#7**).
- **(Professional)** **Sensor refresh:** `QUERY` on sensor node start; staggered longPoll snapshot refresh (~15 cycles) for sparse Ecobee reporters (issue **#6**).
- **(Professional)** **Inventory Notice:** `persistent/<device_id>.json` still exported on pair/health; PG3 Notice only when you run **EXPORT_INVENTORY** manually (issue **#9**).

### Changed

- **(Standard + Professional)** Version **2.0.3** — `nodes/__init__.py` **`VERSION`** and `profile/version.txt`.

## [2.0.2] - 2026-06-30

Edition tags: **(Standard)** = Standard store zip only; **(Professional)** = Professional store zip only; **(Standard + Professional)** = both editions.

### Important — upgrade impact

- **(Professional)** **Existing generic IoX nodes are deleted and re-added on first sync after upgrade** so thermostat and sensor children get the correct parent (`primary` address) and fresh driver metadata. Node **addresses are preserved** where possible, but you may need to re-open IoX Admin Console and re-place nodes in folders. This is required to fix incorrect parent links from earlier 2.0.x builds and stale driver UOMs (e.g. motion **Responding** / **Battery Low** showing raw `0` instead of False/True).

### Added

- **(Professional)** **`HKHubSensor` nodedef** and **`SensorNode`:** room sensors, contact sensors, and Ecobee-style motion children with temperature, humidity (non-motion), occupancy, battery level/low, and **Responding** drivers aligned with **udi-poly-ecobee** patterns.
- **(Professional)** **Sensor sync:** HAP classifier expands per-accessory sensor AIDs; controller `_sync_sensor_nodes` creates motion children under thermostats, mirrors builtin motion from ambient service, and routes HAP events to sensor nodes.
- **(Professional)** **`node_queue` / `wait_for_node_done`:** serializes `addNode` so parent thermostats exist before sensor children register (Ecobee hub pattern).
- **(Professional)** **Redacted config debug export** (`homekit_hub/config_debug.py`) to log and `persistent/hub_config_debug.txt` on bootstrap and config changes.
- **(Professional)** **Tests:** sensor classifier, `hap_apply` sensor paths, controller stale-schema recreate, config debug.

### Changed

- **(Standard + Professional)** **Version 2.0.2** — `nodes/__init__.py` **`VERSION`** and `profile/version.txt` aligned for IoX profile/NLS refresh.
- **(Professional)** **Thermostat IoX parent:** generic thermostat nodes use the hub controller address as `primary` (self-parent) instead of the paired device slot node.
- **(Professional)** **`PLUGIN_AUTHORING.md`**, **`CONFIG.md`**, **`DEBUGGING.md`:** sensor node behavior, motion mirroring, and upgrade notes.

### Fixed

- **(Professional)** **Motion sensor IoX display:** **GV2** (Responding) and **BATLOW** use BOOL UOM (2) with **Occupancy** editor on **GV1**; profile editors and NLS updated. Stale nodes from 2.0.0–2.0.1 are recreated when PG3 driver UOMs/names no longer match the schema.
- **(Professional)** **Motion snapshot:** skip **CLIHUM** writes on motion-role nodes (humidity mirrored from ambient service only where applicable).
- **(Professional)** **Ecobee thermostat + sensor tree:** sensor children parent under the thermostat IoX node; builtin motion sensor ensured after each sensor sync pass.

## [2.0.1] - 2026-06-30

Edition tags: **(Standard)** = Standard store zip only; **(Professional)** = Professional store zip only; **(Standard + Professional)** = both editions.

### Changed

- **(Standard + Professional)** **`CONFIG.md`**, **`README.md`**, **`PLUGIN_AUTHORING.md`:** pairing-first layout, stronger pairing-code guidance, browser reload notes for Custom Typed rows, and corrected **Create generic IoX control nodes (Professional)** label.

### Fixed

- **(Standard + Professional)** **Plugin restart:** `handler_stop` no longer calls `delNode` on paired/generic child nodes; startup re-publishes nodes with `addNode` (Ecobee hub pattern) so IoX Admin Console layout is preserved across restarts.

## [2.0.0] - 2026-06-25

Edition tags: **(Standard)** = Standard store zip only; **(Professional)** = Professional store zip only; **(Standard + Professional)** = both editions.

### Added

- **(Standard + Professional)** **`DEBUGGING.md`:** user troubleshooting guide for pairing and **Discover** — hub health checks, PG3 Notices and logs, LAN/mDNS, manual pairing, Ecobee notes, and **Download Log Package** escalation. Linked from **CONFIG.md** and **README.md**.
- **(Professional)** **Edition tiers:** `dev_settings.py` gates Professional features; store builds via `make production` (Professional) and `make production-standard` (Standard).
- **(Professional)** **Device inventory:** `persistent/<device_id>.json` export on pair, health recovery, and manual **EXPORT_INVENTORY** on paired device nodes.
- **(Professional)** **Generic IoX nodes (opt-in):** `HKHubThermostat`, `HKHubEcobeeThermostat`, `HKHubLight`, `HKHubSwitch`, `HKHubBinarySensor` with HAP classification, `hap_apply`, and controller/pairing flags `generic_nodes_enable` / `generic_nodes`.
- **(Professional)** **`PLUGIN_AUTHORING.md`**, unit tests (`test_dev_settings`, `test_device_inventory`, `test_hap_apply`, `test_node_funcs`), `scripts/strip_standard_zip.py`, and `zip_exclude_professional.lst` for Standard zip stripping.

### Changed

- **(Standard + Professional)** **Version 2.0.0** — `nodes/__init__.py` **`VERSION`** and `profile/version.txt` aligned for IoX profile/NLS refresh.
- **(Standard + Professional)** **Repository / directory:** renamed from **`udi-poly-homekit`** to **`udi-poly-homekit-hub`** (GitHub: `jimboca/udi-poly-homekit-hub`). Runtime entry point, zip name, and MQTT/WebSocket protocol unchanged.
- **(Standard + Professional)** **`CONFIG.md`** and **`README.md`:** document Standard vs Professional editions, generic IoX nodes, and hub-only Ecobee control path on Professional.
- **(Professional)** **Ecobee thermostat IoX parity:** `HKHubEcobeeThermostat` maps HAP characteristics to IoX drivers (mode, fan, HVAC state, comfort **`GV3`**, schedule mode **`CLISMD`**, setpoints).

### Fixed

- **(Professional)** **Ecobee setpoint parity (°F):** outbound heat/cool writes use Ecobee display rounding (`int(C×1.8+32)`) so IoX targets (e.g. 76°F/74°F) match the physical thermostat instead of landing 1°F low.
- **(Professional)** **Schedule mode (`CLISMD`):** manual holds and setpoint changes set **Hold Next** / **Hold Indefinite**; on restart, hold state is inferred from HAP comfort byte plus active vs program setpoints instead of staying **Running**.
- **(Professional)** **Comfort (`GV3`) on manual hold:** HAP temp/hold byte 3 now displays **Temp** (not **Smart1**) when active setpoints do not match a configured program comfort; vacation/smart comforts still resolve by vendor setpoint match.

## [1.0.1] - 2026-05-28

### Fixed

- **Hub client warnings:** hello **`ack`** and **`list_devices`** now always include a **`warnings`** array (empty when healthy) so downstream clients can clear stale PG3 notices after recovery.

## [1.0.0] - 2026-05-28

### Changed

- **Production release:** version **1.0.0** for the Ecobee + HomeKit hub integration path.
- **User setup docs:** restructured **CONFIG.md** as the primary guide with **Ecobee + IoX quick start**, defaults-first pairing steps, consolidated troubleshooting, and demoted reference sections. **README.md** trimmed to a short overview linking to CONFIG.

## [0.2.14] - 2026-05-08

### Changed

- **Pairing notes:** added guidance that some accessories rotate/expire pairing codes and that users should refresh the PG3 **Configuration** page after **DISCOVER** to see new pairing slot rows.
- **Versioning policy:** bumped plugin/runtime version to `0.2.14` without changing `profile/version.txt` (no profile asset changes in this release).

## [0.2.13] - 2026-05-08

### Changed

- **Pairing quick start docs:** expanded the beginning of **`CONFIG.md`** with a concise pairing flow, clarified that HomeKit setup codes can be entered with or without dashes, and noted IoX slot child node status behavior.
- **Compatibility note:** documented that current testing has focused on Ecobee thermostats used with **udi-poly-ecobee**.
- **Versioning policy:** bumped plugin/runtime version to `0.2.13` without changing `profile/version.txt` (no profile asset changes in this release).

## [0.2.12] - 2026-05-07

### Changed

- **CONFIG guidance:** added an explicit note that default parameters are usually correct for most users, with advanced settings mainly for compatibility/troubleshooting and likely to be simplified in future production releases.
- **Version alignment:** `nodes/__init__.py` `VERSION` and `profile/version.txt` are both `0.2.12`.

## [0.2.11] - 2026-05-07

### Changed

- **MQTT transport default:** Custom Param **`mqtt_enable`** now defaults to **`true`** for new installs so the hub subscribes to MQTT ingress unless explicitly disabled.
- **Version alignment:** `nodes/__init__.py` `VERSION` and `profile/version.txt` are both `0.2.11`.

## [0.2.6] - 2026-05-01

### Added

- **Hub RPC error notice:** optional **`hub_rpc_error_notice`** on **`HomeKitHubBridge`**; the controller sets Polyglot **`Notices['homekit_hub_rpc_error']`** when a client **`command`** fails (HAP status / invalid value), including **`device_id`**, transport (**MQTT `client_slug`** or WebSocket), **`characteristic`**, and **`repr(value)`** for support.

### Changed

- **`PROTOCOL.md`:** documents that wire **`characteristic`** tokens are **`aiohomekit` `CharacteristicsTypes`** names or UUIDs—not Apple PascalCase HAP-doc labels; examples updated (**`ON`**, **`TEMPERATURE_CURRENT`**, etc.).
- **`list_devices` metadata:** when HAP omits **Category** (common on some bridges), infer thermostat category from accessory services so clients get **`category` / `category_label`** without an extra full accessory reload when Manufacturer/Model already succeeded.
- **Characteristic resolution:** **`_resolve_aid_iid_detailed`** returns readable errors for unknown or invalid characteristic tokens instead of failing opaquely; debug logging when Category read returns unexpected payloads.

### Fixed

- Invalid characteristic names on **`command` / `get` / `subscribe`** surface clearer hub **`error`** messages for integrators.

## [0.2.5] - 2026-05-03

### Added

- **Optional MQTT transport** (Custom Params `mqtt_enable`, `mqtt_host`, `mqtt_port`, optional `mqtt_username` / `mqtt_password`, `mqtt_hub_slug`): per-client topic tree `udi/homekit/hubs/{hub_slug}/clients/{client_slug}/in` and split egress `out/rpc` / `out/event`, same JSON as WebSocket; hub uses **aiomqtt** on the asyncio loop. **No application-level MQTT secret in v1** (`ws_token` remains WebSocket-only). Documented in **`PROTOCOL.md`** and **`CONFIG.md`**. Unit tests in **`tests/test_mqtt_topics.py`** (optional **`mqtt_integration`** marker for future broker tests).

## [0.2.4] - 2026-05-02

### Added

- **WebSocket request correlation:** optional **`id`** on **`command`**, **`snapshot`**, and **`get`**; the hub echoes it on matching **`ack`**, **`snapshot`**, **`get`**, and **`error`** (`for` those actions) so clients can multiplex concurrent RPCs on one connection. Hello **`ack.capabilities.rpc`** may advertise **`multiplex`**. Documented in **`PROTOCOL.md`**.

### Changed

- **WebSocket hello:** the hub no longer sends an automatic **`list_devices`** frame immediately after hello **`ack`**. Pairing membership and **`devices[]`** metadata are on the **`ack`** (`device_ids` + `devices`). Clients that waited for a second inbound **`list_devices`** should use **`ack["devices"]`** or send **`action: list_devices`**. Proactive **`list_devices`** after pairing changes is unchanged.

## [0.2.3] - 2026-05-01

### Added

- **WebSocket client notices:** hello **`ack`** and **`list_devices`** may include an optional **`warnings`** array (`level`, `code`, `message`, optional **`device_id`** / **`primary_aid`**) when the hub hits metadata or HAP issues (e.g. **`get_characteristics`** failure, incomplete Accessory Information, accessories load failure, hub controller not ready). Integrators can mirror these in Node Server logs or UI. Documented in **`PROTOCOL.md`**.

### Fixed

- **WebSocket hello / `list_devices`:** each **`devices[]`** row now drives **HAP `get_characteristics`** when **Category**, **Manufacturer**, or **Model** is missing from the cached accessory model (not only when Manufacturer was empty). If **category** is still missing while the pairing has accessories, the hub **refreshes `/accessories` once** and retries; a **WARNING** logs **`device_id`** and context instead of silently returning only **`device_id`**.

## [0.2.2] - 2026-05-01

### Added

- **`ws_debug_client`**: **`--max-messages`** / **`--oneshot`** for bounded runs from **`Makefile`** / scripts; **`snapshot_all`** can fall back to per-device **`snapshot`** requests when **`list_devices`** is empty but **`event`** frames carry **`device_id`**.
- **Tests:** unit coverage for **`ws_debug_client`** (including extracted **`snapshot_all_handle_inbound`**); optional **`integration`**-marked live hub exercises in **`tests/test_ws_live.py`** with **`live_hub`** fixture / **`HOMEKIT_WS_*`** env (**`pytest -m integration`**).
- **`Makefile`**: restores **`beta`**, **`production`**, **`release`**, **`zip`**, **`xml-check`**, **`check`**, **`clean`**, **`format-check`**, **`black-check`**, plus **`test-unit`**, **`test-integration`**, **`help`**, and **`ws-*`** smoke targets alongside **`ruff check .`**.

### Changed

- **Releases / PG3 install:** delivery is **`beta`** and **`production`** git branches (**`make beta`**, **`make production`**, **`make release`** updates **`production`** + annotated tag). **`make release`** does not build **`HomeKitHub.zip`**; **`make zip`** is optional.

### Fixed

- **Hub WebSocket `list_devices` / pairing resolution:** collect active pairing ids from pairing values and listener aliases; **`_pairing_for_device_id`** resolves by accessory **`id`** when the map key differs; extend short settle retries before treating the paired list as empty.
- **Paired device Health (GV1):** **`Healthy`** / **`Degraded`** only apply while the slot is paired. When there is no active pairing (including after **UNPAIR** once custom data syncs), **GV1** is **`Not paired`** (index **2**; profile **`HKHLTH-2`** / editor subset updated). **`update_health`** no longer forces **`Healthy`** on unpaired nodes.
- **`zip_exclude.lst`**: exclude Polyglot **`*.cert`**, **`*.key`**, **`*.lock`**, and **`snapshot-all.txt`** so store zips do not bundle host secrets or debug artifacts. **`make zip`** removes an existing **`HomeKitHub.zip`** first so **`zip -r`** cannot leave stale entries from older builds.

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
