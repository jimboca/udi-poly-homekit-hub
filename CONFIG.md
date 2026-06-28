# HomeKit Hub — configuration

**[Debugging issues](DEBUGGING.md)** — pairing failures, **Discover** not adding rows, status **Disconnected**, logs, and what to send support.

**Start here.** This file is the main setup guide for pairing accessories and connecting other PG3 plugins (for example **udi-poly-ecobee**).

On a typical Polisy / eISY install, **leave MQTT, WebSocket, and zeroconf settings at their defaults**. You only need to change **`mqtt_hub_slug`** if multiple HomeKit hubs share one MQTT broker.

---

## Ecobee + IoX quick start

Pair your Ecobee on this hub **before** installing **udi-poly-ecobee**. No iPhone, iPad, Mac, or Apple Home app is required.

**Critical:** The Ecobee must **not** be paired to **Apple Home** (or any other HomeKit controller) while you pair it here. Remove it from Apple Home first if it was added there.

1. Add **HomeKit Hub** from the PG3 store and start the Node Server.
2. On the Ecobee: put it in **HomeKit pairing mode** (see Ecobee docs). Confirm it is **not** in Apple Home.
3. On the **HomeKit Hub** controller node in PG3 / IoX, run **DISCOVER**.
4. Open **Configuration** → **Custom Typed Configuration Parameters** → **HomeKit pairing slots**. **Refresh the Configuration page** if the new row does not appear yet.
5. Find the row for your thermostat (id and name are filled in by **DISCOVER**). Enter the 8-digit HomeKit code in **hap_pin** (`12345678` or `123-45-678` — either format works). **Save**.
6. Wait for pairing to finish. A child node should appear; its **ST** driver should show paired/connected. Check PG3 **Notices** or `logs/debug.log` if pairing fails.
7. Leave **`mqtt_enable`** `true` and **`mqtt_hub_slug`** `default` unless you run multiple hubs on one broker.
8. **Next:** install **udi-poly-ecobee** and follow its [CONFIG.md — Ecobee quick start](https://github.com/UniversalDevicesInc-PG3/udi-poly-ecobee/blob/master/CONFIG.md#ecobee-quick-start-homekit).

This hub flow has been tested primarily with **Ecobee thermostats** for use with **udi-poly-ecobee**. Other HomeKit accessories may work; pairing steps are the same.

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

## Pairing details

### Standard flow (DISCOVER)

1. Put the accessory in HomeKit pairing mode (unpaired).
2. Run **DISCOVER** on the **HomeKit Hub** controller. The hub stores a discover snapshot and **adds a row** per new unpaired accessory to **HomeKit pairing slots** (id and name prefilled; **hap_pin** empty).
3. Enter the **HomeKit pairing code** on that row and **Save**. The code appears on the device label, screen, or vendor app while in pairing mode — it is not shown in Polyglot.
4. If several unpaired devices appear at once, use **accessory_id** or **accessory_name** on the row to pick the right one (usually **DISCOVER** already set these).

**Tips:**

- Setup codes often **expire** when pairing mode ends; re-open pairing mode and use the **current** code if pairing fails.
- After **DISCOVER**, refresh the PG3 **Configuration** page before editing typed rows.

### Manual rows (QR code in vendor app only)

You can **add row** manually in **HomeKit pairing slots** instead of waiting for **DISCOVER**.

Some products (e.g. **Ecobee**) show a **QR code** in their app for **Apple Home**. That path is separate: for this hub you need the **numeric** setup code (often on a sticker or in docs). Run **DISCOVER** while the device is in pairing mode to fill **id** / **name**, or type them yourself.

### Ecobee and Apple Home

Ecobee may prompt you to add the thermostat to Apple Home. **Skip that** for this setup. The thermostat should be pairable only to **this** hub (unpaired from Apple Home).

---

## Verify hub is ready for Ecobee

On the **HomeKit Hub** controller node:

| Driver | Good value | Meaning |
|--------|------------|---------|
| **ST** | `1` | Node Server connected to Polyglot. |
| **GV0** | `1` | Bridge running (HomeKit + WebSocket server up). |
| **GV1** | `2` | MQTT connected (when **`mqtt_enable`** is `true`). |

If **GV0** is not `1` or **GV1** is not `2`, check PG3 **Notices** and `logs/debug.log` before installing Ecobee.

Continue with [udi-poly-ecobee CONFIG.md](https://github.com/UniversalDevicesInc-PG3/udi-poly-ecobee/blob/master/CONFIG.md#ecobee-quick-start-homekit).

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
6. Enter the current pairing code on the slot row and **Save**.

**UNPAIR** / **DELETE** on a slot row clears **this plugin's** pairing data only. If the accessory still advertises `paired=True`, repeat the steps above on the device side.

Other notes:

- Paired state in discovery can lag briefly after unpair.
- Deleting a typed row removes saved slot data; re-pairing is a fresh flow.

### Pairing code rejected or expired

Re-open HomeKit pairing mode on the accessory and enter the **new** code shown on the device.

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

**Zeroconf parameters:** On a normal Polisy / eISY deployment you can ignore the three `zeroconf_*` keys entirely. The controller command **Zeroconf diagnostic** (`ZEROCONF_DIAG`) logs a snapshot for support. After changing `zeroconf_*` or WebSocket bind settings, save configuration; the hub restarts the asyncio bridge automatically.

---

## Reference: Custom Typed Configuration Parameters

Same pattern as **udi-poly-notification**: one typed section with **multiple rows**; each row is one pairing slot.

### HomeKit pairing slots (`pairing_slots`)

In the Polyglot UI, open **Custom Typed Configuration Parameters** and use the list **“HomeKit pairing slots”**. **DISCOVER** automatically **adds a row** for each newly seen **unpaired** accessory. You can also **add row** / **remove** manually.

| Field | Description |
|-------|-------------|
| **Slot** (`slot`) | Positive integer **1, 2, 3, …** Optional: if empty, the Hub picks the smallest unused slot. |
| **HomeKit pairing code** (`hap_pin`) | Code shown on the accessory (e.g. `123-45-678`). Eight digits without dashes work. **Leave empty** to disassociate that slot. |
| **Accessory device id** (`accessory_id`) | Optional. Usually filled by **DISCOVER**. Use to disambiguate multiple unpaired devices. |
| **Substring of accessory name** (`accessory_name`) | Optional extra filter. |
| **Node key** (`node_key`) | Stable IoX child node identity (`hkp_<node_key>`). Auto-assigned; leave unchanged to keep the same IoX address across re-pair. |
| **LAN host:port** (`discover_endpoint`) | Filled from **DISCOVER**; updated when IP pairing recovers after reboot (informational). |

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
