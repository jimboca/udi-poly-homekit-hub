# HomeKit Hub — configuration

<a href="https://github.com/jimboca/udi-poly-homekit-hub/blob/master/DEBUGGING.md" target="_blank" rel="noopener noreferrer">Debugging issues</a> — pairing failures, **Discover** not adding rows, status **Disconnected**, logs, and what to send support.

*Advanced flat parameters (MQTT, WebSocket, zeroconf) →* <a href="https://github.com/jimboca/udi-poly-homekit-hub/blob/master/CONFIG_EXTRA.md" target="_blank" rel="noopener noreferrer">CONFIG_EXTRA.md</a>

**Upgrading to 2.0.13+:** on first start after upgrade, a **one-time** cleanup removes advanced Custom Params still at shipped defaults from the Configuration table (runtime behavior unchanged). Customized values are kept. Re-adding a param manually afterward is never auto-removed.

---

## Start here

This guide is ordered for every install:

1. **[Pairing accessories](#pairing-accessories)** — required for **Standard** and **Professional** (no Apple Home app).
2. **[Professional edition](#professional-edition)** — optional hub-only IoX control and device inventory (skip if you use a vendor plugin below).
3. **[Ecobee + udi-poly-ecobee](#ecobee--udi-poly-ecobee)** — pair on this hub first, then install the Ecobee Node Server.

On a typical Polisy / eISY install, **leave MQTT, WebSocket, and zeroconf at their defaults** — see <a href="https://github.com/jimboca/udi-poly-homekit-hub/blob/master/CONFIG_EXTRA.md" target="_blank" rel="noopener noreferrer">CONFIG_EXTRA.md</a> only when you need multi-hub slugs, custom broker bind, or mDNS troubleshooting.

---

## Pairing accessories

Applies to **Standard** and **Professional**. **No iPhone, iPad, Mac, or Apple Home app is required** — and the accessory must **not** be paired to Apple Home (or another HomeKit controller) while you pair it here.

### Quick pairing (DISCOVER)

1. Add **HomeKit Hub** from the PG3 store and start the Node Server.
2. Put the accessory in **HomeKit pairing mode** (see vendor docs). Confirm it is **unpaired** from Apple Home and other controllers.
3. **IoT / separate VLAN (optional):** only when accessories are **not** on the Polisy primary LAN — see [Discovery networks (setup)](#discovery-networks-setup) below. Skip on typical single-LAN installs.
4. On the **HomeKit Hub** controller node, run **DISCOVER**. Read **Notices** on the Node Server **Configuration** page (Custom section): **HomeKit discover** includes scan results and a **Zeroconf / hub diagnostic** summary when the new build is installed.
5. Open **Configuration** → **Custom Typed Configuration Parameters** → **HomeKit pairing slots**. **Reload the Configuration page in your browser** if the new row does not appear yet (the table refresh button alone may not be enough).
6. Find the row for your device (id and name are filled in by **DISCOVER**). In **HomeKit pairing code** (`hap_pin`), enter the **8-digit code currently shown on the accessory** while it is in pairing mode (`12345678` or `123-45-678` — either format works). **Save**.
7. Wait for pairing to finish. A **Paired HomeKit device** child node should appear; **ST** should show paired/connected. Check PG3 **Notices** or `logs/debug.log` if pairing fails.

**Order matters:** run **DISCOVER** before entering **hap_pin** so id/name are prefilled. If you already typed a PIN, clear it, run **DISCOVER** with the device in pairing mode, then re-enter the current code.

### Discovery networks (setup)

Use this when HomeKit accessories live on a **different subnet/VLAN** than the Polisy primary interface (common for Ecobee on dedicated IoT Wi‑Fi). Same idea as **udi-poly-kasa** **Extra Discovery Networks**.

| When | Action |
|------|--------|
| Accessories on the **same LAN** as Polisy | Leave **Extra Discovery Networks** **empty** — the hub uses the primary interface automatically (on FreeBSD/Polisy it auto-binds from `poly.network_interface` when no extra rows are configured). |
| Accessories on **IoT / guest / VLAN** | Add one row per subnet under **Configuration → Custom Typed Configuration Parameters → Extra Discovery Networks**. |

**What to enter in each row** (`address` field):

| Value type | Example | When to use |
|------------|---------|-------------|
| Subnet **broadcast** | `192.168.222.255` | Preferred when you know the IoT subnet mask |
| **Gateway** on that VLAN | `192.168.222.1` | Works when broadcast is unknown |
| **This host's IP** on that VLAN | `192.168.222.10` | When Polisy has a routed address on the IoT network |

**After adding or changing rows:**

1. **Save** typed configuration (hub restarts mDNS).
2. Run **ZEROCONF_DIAG** on the controller — the Notice should list your IoT address in **`zeroconf_interface_ips`**.
3. Run **DISCOVER** while the accessory is in HomeKit pairing mode.

**Verify without extra rows (single LAN):** run **DISCOVER** or **ZEROCONF_DIAG** and check the Notice or `logs/debug.log` for `bind_source=auto_primary` and `iface_ips=[…]` matching the Polisy primary IP.

**Still no devices?** Confirm routing and firewall rules allow mDNS (UDP 5353) between VLANs, disable AP client isolation on Wi‑Fi, and see <a href="https://github.com/jimboca/udi-poly-homekit-hub/blob/master/DEBUGGING.md#iot--separate-vlan--extra-discovery-networks" target="_blank" rel="noopener noreferrer">DEBUGGING.md — IoT / separate VLAN</a>.

Full field reference: [Extra Discovery Networks](#extra-discovery-networks-networks).

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
| Fan | **HKHubFan** |
| Switch / outlet | **HKHubSwitch** |
| Contact, motion, occupancy (standalone accessory) | **HKHubSensor** (per HAP `aid`) |
| Ecobee room sensors (separate `aid`s) | **HKHubSensor** child per sensor |
| Built-in motion on thermostat `aid` | **HKHubSensor** · motion child |

Generic **light**, **fan**, and **switch** node types are supported (**HKHubLight**, **HKHubFan**, **HKHubSwitch**). Capability-specific light variants (color, etc.) are not separate node types yet; see **[PLUGIN_AUTHORING.md](PLUGIN_AUTHORING.md)**.

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
- If the Ecobee is on a **separate IoT VLAN**, add that subnet under **Extra Discovery Networks** (see [Quick pairing](#quick-pairing-discover) step 3) before **DISCOVER**.

### After pairing on the hub

1. Confirm the hub is ready (**ST** `1`, **GV0** `1`, **GV1** `2` on the controller) — see [Verify the hub is ready](#verify-the-hub-is-ready).
2. Leave MQTT at defaults unless you run multiple hubs on one broker — see <a href="https://github.com/jimboca/udi-poly-homekit-hub/blob/master/CONFIG_EXTRA.md#mqtt_hub_slug" target="_blank" rel="noopener noreferrer">CONFIG_EXTRA.md — mqtt_hub_slug</a>.
3. Install **udi-poly-ecobee** and follow its [CONFIG.md — Ecobee quick start](https://github.com/UniversalDevicesInc-PG3/udi-poly-ecobee/blob/master/CONFIG.md#ecobee-quick-start-homekit).

On **Professional**, leave **generic_nodes_enable** and **Create generic IoX control nodes (Professional)** **off** on Ecobee rows unless you intentionally want duplicate thermostat nodes in IoX.

---

## Troubleshooting

See <a href="https://github.com/jimboca/udi-poly-homekit-hub/blob/master/DEBUGGING.md" target="_blank" rel="noopener noreferrer">DEBUGGING.md</a> for step-by-step diagnosis (hub not ready, **Discover** with no rows, LAN/mDNS, Ecobee pairing, logs, and support checklist).

### DISCOVER finds no accessories (empty pairing row)

Symptoms: **DISCOVER** completes but Notices say **no accessories found**; typed **HomeKit pairing slots** stay empty or only show a manual row without id/name.

1. Confirm the accessory is in **HomeKit pairing mode** and **unpaired** from Apple Home.
2. Confirm Polisy and the accessory are on the **same routable LAN** (or add the IoT subnet under **Extra Discovery Networks** — see [Quick pairing](#quick-pairing-discover) step 3).
3. Run **ZEROCONF_DIAG**; check `zeroconf_interface_ips` when extra networks are configured.
4. If still empty, try advanced zeroconf params — see <a href="https://github.com/jimboca/udi-poly-homekit-hub/blob/master/CONFIG_EXTRA.md#zeroconf_unicast" target="_blank" rel="noopener noreferrer">CONFIG_EXTRA.md</a> (`zeroconf_interfaces=all` or `zeroconf_unicast=auto`), save, wait for bridge restart, then **DISCOVER** again.
5. Enter **hap_pin** only **after** a successful **DISCOVER** prefilled id/name (or type **accessory_id** / **accessory_name** manually on a manual row).

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

## Custom params you may change

These three flat params are shown in **Custom Configuration Parameters**. Advanced transport and mDNS keys are in <a href="https://github.com/jimboca/udi-poly-homekit-hub/blob/master/CONFIG_EXTRA.md" target="_blank" rel="noopener noreferrer">CONFIG_EXTRA.md</a>.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `generic_nodes_enable` | `false` | **Professional:** master switch for generic IoX child nodes. Also requires **Create generic IoX control nodes (Professional)** on the pairing row in Custom Typed. See [PLUGIN_AUTHORING.md](PLUGIN_AUTHORING.md). |
| `change_node_names` | `true` | When `true`, IoX **renames** paired-device child nodes so titles track **`last_hap_discover`** and Custom Typed pairing rows. When `false`, the plugin keeps the IoX database name if it differs. Same idea as **udi-poly-kasa**. |
| `hk_heat_cool_min_delta` | `3` | **Professional:** minimum heat/cool gap in °F when writing thermostat thresholds (default `3`). |

**Multi-hub / Ecobee slug match:** when several HomeKit hubs share one MQTT broker, set **`mqtt_hub_slug`** manually per <a href="https://github.com/jimboca/udi-poly-homekit-hub/blob/master/CONFIG_EXTRA.md#mqtt_hub_slug" target="_blank" rel="noopener noreferrer">CONFIG_EXTRA.md</a> (default `default`; not shown in the UI unless you add it).

**Professional device inventory:** JSON files are written to `persistent/<device_id>.json` on pair and health recovery. Use **Export device inventory** on a paired device node or include `persistent/` via **Download Log Package** (not excluded from support zips).

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

### Extra Discovery Networks (`networks`)

Same pattern as **udi-poly-kasa**. Use when HomeKit accessories live on a **different LAN/VLAN** than the Polisy primary interface (common for IoT Wi‑Fi). Each row is one subnet; the hub binds mDNS to the matching local interface IP before **DISCOVER** and pairing.

| Field | Description |
|-------|-------------|
| **Broadcast address** (`address`) | Subnet broadcast (e.g. `192.168.222.255`), gateway (e.g. `192.168.222.1`), or this host's IP on that VLAN (e.g. `192.168.222.10`). |

- Leave empty on typical single-LAN installs. On **FreeBSD/Polisy**, when this list is empty the hub **auto-binds** the Polisy primary interface for mDNS (`bind_source=auto_primary` in **DISCOVER** / **ZEROCONF_DIAG** output).
- After adding or changing rows, **Save** typed configuration; the hub restarts mDNS automatically.
- Run **ZEROCONF_DIAG** to confirm `zeroconf_interface_ips` lists the expected addresses.
- Setup walkthrough: [Discovery networks (setup)](#discovery-networks-setup).

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

See `PROTOCOL.md`. When **`mqtt_enable`** is `true` (default), the hub exposes the same JSON on MQTT and WebSocket. WebSocket remains available in parallel. Transport and zeroconf tuning: <a href="https://github.com/jimboca/udi-poly-homekit-hub/blob/master/CONFIG_EXTRA.md" target="_blank" rel="noopener noreferrer">CONFIG_EXTRA.md</a>.

### Security

WebSocket binds to `127.0.0.1` by default. **MQTT (v1):** no application-level secret like **`ws_token`**; use broker authentication, ACLs, and a private LAN. Details in CONFIG_EXTRA.md.

### Multiple WebSocket clients

Other Node Servers (e.g. **udi-poly-ecobee**) connect as clients. The hub fan-outs HAP events; each client filters by `device_id`. **`hello` `ack`** and **`list_devices`** include accessory **category** metadata (e.g. **9** = thermostat) for downstream filtering.
