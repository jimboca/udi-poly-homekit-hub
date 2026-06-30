# HomeKit Hub — configuration

**[Debugging issues](DEBUGGING.md)** — pairing failures, **Discover** not adding rows, status **Disconnected**, logs, and what to send support.

---

## Start here

This guide is ordered for every install:

1. **[Pairing accessories](#pairing-accessories)** — required for **Standard** and **Professional** (no Apple Home app).
2. **[Professional edition](#professional-edition)** — optional hub-only IoX control and device inventory (skip if you use a vendor plugin below).
3. **[Ecobee + udi-poly-ecobee](#ecobee--udi-poly-ecobee)** — pair on this hub first, then install the Ecobee Node Server.

On a typical Polisy / eISY install, **leave MQTT, WebSocket, and zeroconf settings at their defaults**. You only need to change **`mqtt_hub_slug`** if multiple HomeKit hubs share one MQTT broker.

---

## Pairing accessories

Applies to **Standard** and **Professional**. **No iPhone, iPad, Mac, or Apple Home app is required** — and the accessory must **not** be paired to Apple Home (or another HomeKit controller) while you pair it here.

### Quick pairing (DISCOVER)

1. Add **HomeKit Hub** from the PG3 store and start the Node Server.
2. Put the accessory in **HomeKit pairing mode** (see vendor docs). Confirm it is **unpaired** from Apple Home and other controllers.
3. On the **HomeKit Hub** controller node, run **DISCOVER**.  PG3 will show Notices about what is happening and what it found.
4. Open **Configuration** → **Custom Typed Configuration Parameters** → **HomeKit pairing slots**. **Reload the Configuration page in your browser** if the new row does not appear yet (the table refresh button alone may not be enough).
5. Find the row for your device (id and name are filled in by **DISCOVER**). In **HomeKit pairing code** (`hap_pin`), enter the **8-digit code currently shown on the accessory** while it is in pairing mode (`12345678` or `123-45-678` — either format works). **Save**.
6. Wait for pairing to finish. A **Paired HomeKit device** child node should appear; **ST** should show paired/connected. Check PG3 **Notices** or `logs/debug.log` if pairing fails.

### Pairing code can change

HomeKit setup codes are **not permanent**. Many accessories issue a **new code** each time pairing mode starts, and codes **expire** when pairing mode ends.

- Enter the code **shown on the device at the moment you type it** — not an old sticker, email, screenshot, or code from an earlier attempt.
- If pairing fails or the code is rejected, put the accessory back in pairing mode and use the **new** code on screen before **Save**.
- Polyglot does not display the code for you; it comes from the accessory label, screen, or vendor app while pairing mode is active.

### More pairing options

**Several unpaired devices:** use **accessory_id** or **accessory_name** on the row to pick the right one (usually **DISCOVER** already set these).

**Manual row:** you can **add row** in **HomeKit pairing slots** instead of waiting for **DISCOVER**. **Reload the Configuration page in your browser** if the new row does not appear after you save.

**QR / `X-HM://` only:** some products (e.g. **Ecobee**) show a QR in their app for **Apple Home**. This hub needs the **numeric** setup code. Run **DISCOVER** while the device is in pairing mode to fill **id** / **name**, or type them yourself.

**Browser refresh:** after **DISCOVER**, **add row**, or a plugin upgrade that adds new columns, **reload the entire Configuration page in your browser** before editing typed rows—the table refresh button alone often is not enough.

### Verify the hub is ready

On the **HomeKit Hub** controller node:

| Driver | Good value | Meaning |
|--------|------------|---------|
| **ST** | `1` | Node Server connected to Polyglot. |
| **GV0** | `1` | Bridge running (HomeKit + WebSocket server up). |
| **GV1** | `2` | MQTT connected (when **`mqtt_enable`** is `true`). |

If **GV0** is not `1` or **GV1** is not `2`, check PG3 **Notices** and `logs/debug.log` before connecting a client plugin or enabling Professional generic nodes.

---

## Professional edition

If your PG3 license includes **Professional**, the hub adds features on top of the [pairing flow](#pairing-accessories) above. **Standard** behavior is unchanged: multi-slot pairing, **DISCOVER**, WebSocket/MQTT transport, and **HKHubPairedDevice** child nodes.

PG3 sets the edition from your license (`Standard` or `Professional`). A **trial license** typically reports as **Professional** so you can evaluate before purchase. The plugin does not expose a separate “mode” toggle — edition comes from the store license at runtime.

You do **not** need Professional to use **udi-poly-ecobee** or other hub client plugins on **Standard**.

### What Professional adds

| Feature | What it does | Default |
|---------|----------------|---------|
| **Device inventory** | On pair and HAP health recovery, writes `persistent/<device_id>.json` — full HAP layout, values, and `plugin_hints` for plugin authoring and support. | Always on when licensed Professional |
| **Export device inventory** | Command on a paired device node to refresh that JSON and show a Notice with the file path. | Manual trigger |
| **Generic IoX nodes** | Optional child nodes driven directly from HomeKit in this plugin — no separate vendor Node Server when you opt in. | **Off** until you opt in |

Inventory files are included in **Download Log Package** (`persistent/` is not excluded from support zips). See [PLUGIN_AUTHORING.md](PLUGIN_AUTHORING.md) for using the JSON to design vendor nodeDefs.

### Supported generic IoX nodes (included devices)

When generic control is enabled (below), the hub can create these child node types from HomeKit after pairing:

| Device type | IoX node |
|-------------|----------|
| Thermostat (generic HAP) | **HKHubThermostat** |
| Ecobee thermostat | **HKHubEcobeeThermostat** (comfort / `GV3`, schedule mode, setpoints) |
| Light | **HKHubLight** |
| Switch / outlet | **HKHubSwitch** |
| Contact, motion, occupancy (standalone accessory) | **HKHubSensor** (per HAP `aid`) |
| Ecobee room sensors (separate `aid`s) | **HKHubSensor** child per sensor |
| Built-in motion on thermostat `aid` | **HKHubSensor** · motion child |

For now, only **generic** light and switch node types are supported (**HKHubLight**, **HKHubSwitch**). Capability-specific variants (dimmer vs color, etc.) are not separate node types yet; see **[PLUGIN_AUTHORING.md](PLUGIN_AUTHORING.md)**.

### Opt-in generic control (Professional)

Generic nodes are **not** created automatically. Complete [pairing](#pairing-accessories) first, then enable both:

1. **Custom Configuration Parameters:** set **`generic_nodes_enable`** to `true` (hub master switch; seeded as `false` on upgrade). Reload the **Configuration** page in your browser if this parameter does not appear after a plugin upgrade.
2. **Custom Typed → HomeKit pairing slots:** on the row for that pairing, set **Create generic IoX control nodes (Professional)** to **true** (internal key `generic_nodes`). Reload the **Configuration** page in your browser if that column does not appear yet (common after a plugin upgrade).

Both must be **true** for that device. Defaults stay **off** so existing sites that use **udi-poly-ecobee** (or other plugins) are not given duplicate thermostats.

| Your setup | Settings |
|------------|----------|
| Use **udi-poly-ecobee** (or similar) | Leave both **off** — hub transports HomeKit; the other plugin drives IoX. Inventory export still works on Professional. |
| **Hub-only** control (no Ecobee plugin) | Enable both on that pairing — Ecobee pairings get **HKHubEcobeeThermostat**; other thermostats get **HKHubThermostat** until a vendor-specific nodeDef is added. |

After changing either flag, save configuration; the hub re-syncs generic children for affected pairings.

---

## Ecobee + udi-poly-ecobee

Use this path when **udi-poly-ecobee** drives your thermostats over the hub’s MQTT/WebSocket API. **Pair on this hub first**, then install the Ecobee plugin.

This hub flow has been tested primarily with **Ecobee thermostats**. Other HomeKit accessories use the same [pairing steps](#pairing-accessories).

### Before you start

- Complete **[Pairing accessories](#pairing-accessories)** for each Ecobee **before** installing **udi-poly-ecobee**.
- **Critical:** the Ecobee must **not** be in **Apple Home** while you pair here. Remove it from Apple Home first if needed.
- Ecobee may prompt you to add the thermostat to Apple Home during setup — **skip that** for this integration.

### After pairing on the hub

1. Confirm the hub is ready (**ST** `1`, **GV0** `1`, **GV1** `2` on the controller) — see [Verify the hub is ready](#verify-the-hub-is-ready).
2. Leave **`mqtt_enable`** `true` and **`mqtt_hub_slug`** `default` unless you run multiple hubs on one broker.
3. Install **udi-poly-ecobee** and follow its [CONFIG.md — Ecobee quick start](https://github.com/UniversalDevicesInc-PG3/udi-poly-ecobee/blob/master/CONFIG.md#ecobee-quick-start-homekit).

On **Professional**, leave **generic_nodes_enable** and **Create generic IoX control nodes (Professional)** **off** on Ecobee rows unless you intentionally want duplicate thermostat nodes in IoX.

---

## Defaults you can ignore

Most users never change these. Only touch them when you have a specific reason (multiple hubs, custom broker, or support asked you to).

| Parameter | Default | Change only when… |
|-----------|---------|-------------------|
| `mqtt_enable` | `true` | You intentionally want WebSocket-only (not recommended for Ecobee). |
| `mqtt_host` / `mqtt_port` | `localhost` / `1884` | Your MQTT broker is not the Polisy/eISY general broker. |
| `mqtt_hub_slug` | `default` | Multiple HomeKit hubs share one broker (must match Ecobee **`hk_mqtt_hub_slug`**). |
| `ws_host` / `ws_port` | `127.0.0.1` / `8163` | WebSocket fallback client needs a non-default bind or port. |
| `ws_token` | *(empty)* | You want a shared secret on the WebSocket API. |
| `zeroconf_*` | shipped defaults | mDNS / discover troubleshooting only. |

---

## Troubleshooting

See **[DEBUGGING.md](DEBUGGING.md)** for step-by-step diagnosis (hub not ready, **Discover** with no rows, LAN/mDNS, Ecobee pairing, logs, and support checklist).

### Accessory shows "already paired"

Symptoms: **DISCOVER** lists the device under **Already paired elsewhere**, or pairing fails with notices like **no matching accessory** / **no unpaired accessory matched**.

1. Remove/unpair the accessory from **Apple Home** and any other HomeKit controller.
2. Put the accessory into HomeKit pairing mode again.
3. Power-cycle the accessory (or vendor HomeKit reset if required).
4. Wait 30–60 seconds for mDNS to settle.
5. Run **DISCOVER** again; confirm the target is **unpaired**.
6. Enter the pairing code **currently shown on the accessory** (re-open pairing mode if needed) on the slot row and **Save**.

**UNPAIR** / **DELETE** on a slot row clears **this plugin's** pairing data only. If the accessory still advertises `paired=True`, repeat the steps above on the device side.

Other notes:

- Paired state in discovery can lag briefly after unpair.
- Deleting a typed row removes saved slot data; re-pairing is a fresh flow.

### Pairing code rejected or expired

Put the accessory back in HomeKit pairing mode and enter the **new** code shown on the device **at that moment** — codes change between sessions. See [Pairing code can change](#pairing-code-can-change).

---

## Reference: Hub status and errors

The controller exposes **ST** (Node Server connection), **GV0** (**Bridge Status**), **GV1** (**MQTT transport**), and **ERR** (last error code). Polyglot **Notices** carry human-readable text for the same events.

| Driver | Values |
|--------|--------|
| **ST** `0` / `1` / `2` | Disconnected / Connected / Failed |
| **GV0** `0` / `1` / `2` | Bridge stopped / running / error |
| **GV1** `0` / `1` / `2` | MQTT disabled / reconnecting / connected |

**ERR** codes (profile NLS `ERRC-*`):

| Code | Label |
|------|--------|
| 0 | No error |
| 1 | Bridge start failed |
| 2 | Discover scan failed |
| 3 | Discover unexpected error |
| 4 | Custom typed save failed |
| 5 | Pairing rows update failed |
| 6 | Bridge stop failed |
| 7 | Status update failed |
| 8 | Pairing: no matching accessory |
| 9 | Pairing failed |
| 10 | Asyncio loop stopped |

On Node Server start, the controller clears all Notices before loading.

---

## Reference: Custom Configuration Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `ws_host` | No | WebSocket bind address. Default `127.0.0.1`. |
| `ws_port` | No | WebSocket port. Default `8163`. |
| `ws_token` | No | Optional shared secret for the WebSocket API. **Leave empty** (default) for no auth. When set, clients must send the same value on `hello` as JSON field **`token`** or **`ws_token`** before any other action; see `PROTOCOL.md`. **Does not apply to MQTT** (v1: no application-level MQTT secret; use broker ACLs). |
| `mqtt_enable` | No | `true` / `false` (string). When `true`, the hub connects to the LAN MQTT broker and subscribes to per-client ingress topics (see `PROTOCOL.md` MQTT section). Default `true`. |
| `mqtt_host` | No | MQTT broker hostname or IP. Default `localhost`. |
| `mqtt_port` | No | MQTT broker port. Default `1884` (Polisy/eISY general MQTT / PG3-style broker). |
| `mqtt_username` | No | Optional broker username (when the broker requires authentication). |
| `mqtt_password` | No | Optional broker password (when the broker requires authentication). |
| `mqtt_hub_slug` | No | Topic segment after `udi/homekit/hubs/` identifying this hub instance (broker-safe slug). Default `default`. Change when multiple hubs share one broker. |
| `zeroconf_unicast` | No | `on` (default), `auto`, or `off`. **`on`** uses python-zeroconf unicast mode (typical on eISY and other hosts where UDP **5353** is already owned). **`auto`** tries multicast first, then falls back on “address in use”. **`off`** forces multicast only (fails if 5353 is taken). **Most installs never change this.** |
| `zeroconf_interfaces` | No | `default`, `all`, or leave empty. Optional narrowing for BSD/macOS unicast quirks (errno **49**). **Usually leave empty.** |
| `zeroconf_ip_version` | No | `v4`, `v6`, `all`, or leave empty. **Usually leave empty.** |
| `change_node_names` | No | `true` (default) or `false` (string). When `true`, IoX **renames** paired-device child nodes so titles track **`last_hap_discover`** and Custom Typed pairing rows. When `false`, the plugin keeps the IoX database name if it differs. Same idea as **udi-poly-kasa**. |
| `generic_nodes_enable` | No | **Professional:** `false` (default) or `true`. Master switch for generic IoX child nodes. Also requires **Create generic IoX control nodes (Professional)** on the pairing row in Custom Typed. See [PLUGIN_AUTHORING.md](PLUGIN_AUTHORING.md). |
| `hk_heat_cool_min_delta` | No | **Professional:** minimum heat/cool gap in °F when writing thermostat thresholds (default `3`). |

**Professional device inventory:** JSON files are written to `persistent/<device_id>.json` on pair and health recovery. Use **Export device inventory** on a paired device node or include `persistent/` via **Download Log Package** (not excluded from support zips).

**Zeroconf parameters:** On a normal Polisy / eISY deployment you can ignore the three `zeroconf_*` keys entirely. The controller command **Zeroconf diagnostic** (`ZEROCONF_DIAG`) logs a snapshot for support. After changing `zeroconf_*` or WebSocket bind settings, save configuration; the hub restarts the asyncio bridge automatically.

---

## Reference: Custom Typed Configuration Parameters

Same pattern as **udi-poly-notification**: one typed section with **multiple rows**; each row is one pairing slot.

### HomeKit pairing slots (`pairing_slots`)

In the Polyglot UI, open **Custom Typed Configuration Parameters** and use the list **“HomeKit pairing slots”**. **DISCOVER** automatically **adds a row** for each newly seen **unpaired** accessory. You can also **add row** / **remove** manually.

**Browser refresh:** After **DISCOVER**, **add row**, or a plugin upgrade that adds new columns (e.g. **Create generic IoX control nodes (Professional)**), **reload the entire Configuration page in your browser** if rows or fields are missing—the typed-table refresh button alone is often not enough.

| Field | Description |
|-------|-------------|
| **Slot** (`slot`) | Positive integer **1, 2, 3, …** Optional: if empty, the Hub picks the smallest unused slot. |
| **HomeKit pairing code** (`hap_pin`) | **8-digit code on the accessory while pairing mode is active** (e.g. `123-45-678`; dashes optional). Codes can **change** each time pairing mode starts — enter what the device shows **when you save**, not an older code. **Leave empty** to disassociate that slot. |
| **Accessory device id** (`accessory_id`) | Optional. Usually filled by **DISCOVER**. Use to disambiguate multiple unpaired devices. |
| **Substring of accessory name** (`accessory_name`) | Optional extra filter. |
| **Node key** (`node_key`) | Stable IoX child node identity (`hkp_<node_key>`). Auto-assigned; leave unchanged to keep the same IoX address across re-pair. |
| **LAN host:port** (`discover_endpoint`) | Filled from **DISCOVER**; updated when IP pairing recovers after reboot (informational). |
| **Create generic IoX control nodes (Professional)** (`generic_nodes`) | **Professional:** default **false**. Set **true** (and enable hub **`generic_nodes_enable`**) to manage this device with generic IoX nodes in this plugin instead of a separate vendor plugin. |

- No fixed maximum number of rows.
- If you removed a row by mistake, run **DISCOVER** again to repopulate.

### Persisted custom data

Pairing keys live under **`homekit_pairings`** in Polyglot custom data. Do not edit by hand.

---

## Advanced

### Controller commands

| Command | Purpose |
|---------|---------|
| **DISCOVER** | Scan for HAP accessories; refreshes discover snapshot and updates Custom Typed rows. |
| **ZEROCONF_DIAG** | Notice with zeroconf mode, transport discovery counts, and library versions. |

### Paired device node commands

Each pairing slot row is exposed as its own node:

- **ST** = paired status (`1` paired, `0` candidate)
- **GV0** = slot number
- Node address = `hkp_<node_key>`

| Command | Purpose |
|---------|---------|
| **UNPAIR** | Clears that row's `hap_pin` and reloads hub sessions. |
| **DELETE** | Removes the row, clears saved slot data, deletes the node. |

`UNPAIR` / `DELETE` do **not** guarantee the physical accessory cleared its HomeKit bond.

### HomeKit setup URI (`X-HM://`)

Vendor QR codes often encode **`X-HM://`**. The hub still needs the **numeric** setup code in **hap_pin**.

- **Decode helper (dev machine):** `python3 tools/decode_x_hm_setup.py 'X-HM://…'`
- **Library:** `homekit_hub.x_hm_uri.decode_x_hm_setup_uri`

### WebSocket and MQTT protocol

See `PROTOCOL.md`. When **`mqtt_enable`** is `true`, the hub exposes the same JSON on MQTT and WebSocket. WebSocket remains available in parallel.

### Security

WebSocket binds to `127.0.0.1` by default. **MQTT (v1):** no application-level secret like **`ws_token`**; use broker authentication, ACLs, and a private LAN.

### Environment (optional)

Override Custom Params `zeroconf_*` for the Node Server process. Typical users do not set these.

| Variable | Values | Purpose |
|----------|--------|---------|
| `HOMEKIT_HUB_ZEROCONF_UNICAST` | `1` / `true` / `yes` / `on` or `0` / `false` / `off` | Force unicast or multicast. |
| `HOMEKIT_HUB_ZEROCONF_INTERFACES` | `default` / `all` | Interface selection for zeroconf. |
| `HOMEKIT_HUB_ZEROCONF_IP_VERSION` | `v4` / `v6` / `all` | IP stack for zeroconf. |

### Multiple WebSocket clients

Other Node Servers (e.g. **udi-poly-ecobee**) connect as clients. The hub fan-outs HAP events; each client filters by `device_id`. **`hello` `ack`** and **`list_devices`** include accessory **category** metadata (e.g. **9** = thermostat) for downstream filtering.
