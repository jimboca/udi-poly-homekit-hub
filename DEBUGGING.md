# HomeKit Hub — debugging issues

Use this guide when pairing fails, **Discover** does not add rows, status shows **Disconnected**, or downstream plugins (for example **udi-poly-ecobee**) cannot talk to the hub.

For normal setup, start with **[CONFIG.md](CONFIG.md)**.

---

## Confirm you have the right plugin

| Plugin | Purpose |
|--------|---------|
| **HomeKit Hub** (`udi-poly-homekit-hub`) | Pairs **to** LAN HomeKit accessories (Ecobee, etc.) — **this document** |
| **HomeKit Bridge** (`udi-poly-homekit-bridge`) | Exports IoX/ISY devices **to** Apple Home — different product |

If you are pairing an Ecobee thermostat, you need **HomeKit Hub**, not HomeKit Bridge.

**No Apple Home hub is required.** You also do not use the Apple Home app to pair accessories for this integration.

---

## Healthy hub checklist

On the **HomeKit Hub** controller node in IoX, check these status names (as shown in the UI):

| Status | Good value | Meaning |
|--------|------------|---------|
| **NodeServer Online** | **Connected** | Node Server is running and talking to Polyglot |
| **Bridge Status** | **Running** | HomeKit bridge and local API are up — **Discover** needs this |
| **MQTT transport** | **Connected** | MQTT broker link is up (when MQTT is enabled in configuration) |
| **Hub error code** | **No error** | Last hub fault cleared |

Also confirm in the Node Server log (`logs/debug.log`):

- Line **`HomeKit Hub ready`** appears after each start/restart.
- After **Discover**, look for **`HomeKit DISCOVER: scan finished`** and how many accessories were found.

If **NodeServer Online** is **Disconnected** or **Failed**, or **Bridge Status** is **Stopped** or **Error**, fix that before pairing. **Discover** is skipped when the bridge is not ready. Check Notices and `logs/debug.log`; if the cause is not obvious, use **Download Log Package** and PM it to the plugin author.

**Professional edition:** device inventory JSON lives under `persistent/` (one file per paired device). **Download Log Package** includes `persistent/` — use it when authoring vendor nodeDefs or escalating support. Inventory export requires **Professional** (trial licenses usually qualify).

Startup can take up to **1–2 minutes** after a restart (configuration must load first).

---

## Where to look for clues

### 1. PG3 Notices

Open the **HomeKit Hub** Node Server **Configuration** page and scroll to the **Custom** section (not a popup on the IoX tree). Important keys:

| Notice key | What it means |
|------------|----------------|
| **HomeKit DISCOVER running** (`discover_progress`) | Scan in progress — shows the current phase (primary scan, alternate-bind probe, or saving settings) and a countdown when applicable |
| **HomeKit bridge restarting** (`discover_bridge_restart`) | After a successful alternate-bind probe, zeroconf Custom Params were saved and the bridge is restarting (may take up to 1–2 minutes; often much faster on Polisy) |
| **HomeKit discover** (`hap_discover`) | Scan results — unpaired vs already paired, whether rows were added, attempt history and suggestions when probes ran |
| **Zeroconf diagnostic** (`zeroconf_diag`) | Output of manual **ZEROCONF_DIAG** command only — full JSON snapshot (not shown during **DISCOVER**) |
| **HomeKit Hub failed to start** | Bridge did not start — check log and zeroconf/port 5353 |
| **HomeKit discover scan failed** | Network/mDNS scan error |
| **HomeKit pairing failed** / **pairing code rejected** | Wrong or expired code, or device not in pairing mode |
| **HomeKit pairing success** | Pairing completed (transient notice) |

Notices are **cleared when the Node Server restarts** — run **DISCOVER** again after each restart to refresh **`hap_discover`**.

### 2. Log file

Path: **`logs/debug.log`** in the Node Server folder (the live process writes here). Do **not** rely on a `debug.log` at the plugin root unless you are running a dev copy that logs there — on Polisy the canonical path is **`logs/debug.log`** (also mirrored to `/var/udx/logs/debug.log` on some installs).

Useful log phrases:

| Log text | Meaning |
|----------|---------|
| `HomeKit Hub ready` | Safe to run **Discover** |
| `HomeKit DISCOVER skipped: bridge not ready` | Hub not started yet — wait and retry |
| `HomeKit DISCOVER: scan finished, N accessory(ies)` | Scan completed; `N=0` means nothing seen on LAN |
| `HomeKit DISCOVER: starting` | Scan began |
| `HomeKit DISCOVER zeroconf diag:` | Compact network/mDNS snapshot at scan start (bind source, interface IPs, 5353 probe) |
| `HomeKit DISCOVER network hint:` | Actionable suggestion logged when the scan context warrants it (especially zero results) |
| `Discovery network context:` | Per-scan bind summary inside `discover_collect` |
| `Bridge start failed` / `zeroconf` / `5353` | mDNS / bridge startup problem |

Increase log detail in Polyglot if needed (Node Server log level).

If the log does not make the problem obvious, use **Download Log Package** on the Node Server page and PM the file to the plugin author.

### Configuration snapshot (support)

On startup and whenever Custom Params / Custom Data / Custom Typed configuration changes, the hub writes a **redacted** configuration report:

| Location | What to look for |
|----------|------------------|
| **`persistent/hub_config_debug.txt`** | Full latest snapshot (pairing codes and passwords are **not** stored in plain text) |
| **`logs/debug.log`** | Same content, one line per row prefixed with **`CONFIG `** (grep for `CONFIG` or `configuration snapshot`) |

The report includes runtime flags (handler readiness, edition, paired device ids), merged Custom Params, typed pairing rows (with friendly labels), **Extra Discovery Networks** rows (when configured), and summarized custom data (pairing slot keys, last DISCOVER preview). Use it when escalating pairing or MQTT issues without pasting secrets into chat.

**Download Log Package** includes `persistent/` when present.

---

## Discover does nothing or adds no rows

### Symptom

You run **Discover** on the **HomeKit Hub** controller, but **HomeKit pairing slots** under **Custom Typed Configuration Parameters** stays empty (even after refresh).

### Common causes

#### A. NodeServer Online is not Connected

**First check:** on the **HomeKit Hub** controller node, confirm **NodeServer Online** shows **Connected**.

If it shows **Disconnected** or **Failed**, the Node Server is not running correctly — **Discover** will not work and pairing rows will not update.

**Upgrade Packages (June 1, 2026 release):** Hub builds from that release onward require the Python package **`aiomqtt`** (listed in `requirements.txt`). If you last ran **Upgrade Packages** before June 1, 2026, startup often fails with `ModuleNotFoundError: No module named 'aiomqtt'` in **`logs/debug.log`**. In IoX, run **Upgrade Packages**, wait for install to finish, then **Re-Install** the Plugin.

1. Open the **HomeKit Hub** Node Server in Polyglot and read **Notices**.
2. Check **`logs/debug.log`** in the Node Server folder for errors at startup (Python exceptions, missing dependencies, install failures).
3. Try **Restart** on the Node Server and wait **1–2 minutes**; check **NodeServer Online** again.

If you still cannot see what is wrong, use **Download Log Package** on the Node Server page in Polyglot, save the file, and **private-message (PM) that package to the plugin author** (include your Polisy/eISY model and a short description of what you tried).

Do not continue with pairing or **Discover** until **NodeServer Online** is **Connected**.

#### B. Bridge Status is not Running

After **NodeServer Online** is **Connected**, confirm **Bridge Status** is **Running** and the log shows **`HomeKit Hub ready`**.

If **Discover** runs before the bridge is ready, it is ignored. Restart the Node Server, wait for **Connected** + **Running**, then **Discover** again.

If **Bridge Status** stays **Stopped** or **Error** while **NodeServer Online** is **Connected**, check Notices and `debug.log` (zeroconf / port 5353 issues are common). Use **Download Log Package** and PM the plugin author if the cause is not clear.

#### C. Wrong refresh action

After **Discover** adds rows, **reload the entire Configuration page** in your browser (not only the typed-table refresh button).

Open: **Configuration → Custom Typed Configuration Parameters → HomeKit pairing slots**.

An empty list is normal until **Discover** finds a device or you click **Add row**.

#### D. Accessory not visible on the LAN (mDNS)

**Discover** only sees HomeKit accessories advertising on your local network.

Check:

- Accessory and Polisy/eISY are on the **same routable LAN** (no guest Wi‑Fi, no AP client isolation) **or** the IoT subnet is listed under **Extra Discovery Networks** (see below).
- Accessory is **actively in HomeKit pairing mode** during the scan (codes expire when that screen closes).
- Run **Discover** while the pairing code is still on the device screen; wait for the full scan window.
- If the device still does not appear, **remove power** (unplug or breaker off), wait **10–30 seconds**, restore power, re-enter pairing mode, wait **30–60 seconds**, then **Discover** again.
- Run **Zeroconf diagnostic** on the controller and read the Notice. When extra networks are configured, confirm **`zeroconf_interface_ips`** lists the expected local addresses (primary Polisy interface plus each IoT VLAN).

If the **HomeKit discover** Notice says **no accessories found**, this is usually a network/mDNS issue, not a UI bug. The Notice includes **Discover attempt history** and **Suggestions** when alternate-bind probes ran. Zeroconf technical details are written to **`debug.log`** only (search `DISCOVER zeroconf diag`); run **ZEROCONF_DIAG** manually if you need the JSON snapshot in a Notice.

When an alternate-bind probe succeeds, the hub **saves the winning `zeroconf_*` Custom Param(s)** and shows **HomeKit bridge restarting** while the bridge recycles. On Polisy this is often **under a few seconds**; other hosts may take up to 1–2 minutes. Discover results and pairing rows are saved immediately — you can enter pairing codes while waiting for **Bridge Status** to return to **Running**. The **HomeKit discover** notice explains why the alternate bind was needed (for example primary scan used `auto_primary` on one interface only).

On FreeBSD/Polisy, when alternate-bind probes are likely, the primary scan uses a **6 second** window instead of 12 seconds before probes run.

After `zeroconf_interfaces=all` is saved, you may see a one-time log warning like `127.0.0.1 … Can't assign requested address` (errno 49). This is harmless on Polisy — discovery still works if accessories appear in the notice.

##### Reading the diagnostic summary

Compact output (in Notices and log) includes fields such as:

| Field | Good / expected | Problem indicator |
|-------|-----------------|-------------------|
| `bind_source=auto_primary` | Single LAN; hub auto-bound Polisy primary interface | `bind_source=none` on FreeBSD — add **Extra Discovery Networks** or check routing |
| `iface_ips=[…]` | Lists local IP(s) used for mDNS | Empty when extra networks are configured — address did not resolve |
| `discover_rows=N` | `0` = no extra networks; `>0` = IoT rows configured | Rows set but `iface_ips` empty — fix address format or routing |
| `5353=bind_udp_5353_failed` | Common on Polisy when unicast mode is on — OK if `unicast=True` | Fails pairing only when `unicast=False` and 5353 is taken |
| `primary_local` / `poly_addr` | Should match your Polisy LAN IP on single-LAN installs | Mismatch with accessory subnet — add **Extra Discovery Networks** |

**Suggestions** in the Notice appear mainly when **zero accessories** were found. If devices are found but all show **Already paired elsewhere**, read that section of the Notice instead — network bind is working.

##### IoT / separate VLAN — Extra Discovery Networks

Use this when accessories live on a **different subnet** than the Polisy primary interface (common for Ecobee on dedicated IoT Wi‑Fi). Same pattern as **udi-poly-kasa**.

1. Open **Configuration → Custom Typed Configuration Parameters → Extra Discovery Networks**.
2. **Add row** with that subnet's **broadcast address** (e.g. `192.168.222.255`), **gateway** (e.g. `192.168.222.1`), or **this host's IP** on that VLAN (e.g. `192.168.222.10`).
3. **Save** — the hub restarts mDNS on the primary interface **plus** each configured subnet.
4. Run **ZEROCONF_DIAG**. The Notice should list your IoT address in **`zeroconf_interface_ips`**. Log line **`HomeKit ZEROCONF_DIAG zeroconf diag:`** should show the same. If it is missing, the subnet hint did not resolve to a local interface — check routing, firewall/mDNS rules between VLANs, and the address format.
5. Run **Discover** again while the accessory is in pairing mode.

Leave **Extra Discovery Networks** empty on typical single-LAN installs — on FreeBSD/Polisy the hub **auto-binds the primary interface** when this list is empty (`bind_source=auto_primary`). Full setup guide: [CONFIG.md — Discovery networks (setup)](CONFIG.md#discovery-networks-setup).

**Power-cycle the accessory:** if **Discover** still finds nothing after checking the list above, remove power from the device (unplug or switch off the circuit), wait **10–30 seconds**, power it back on, put it in **HomeKit pairing mode** again, wait **30–60 seconds** for it to advertise on the LAN, then run **Discover** once more. A cold reboot often clears a stuck mDNS advertisement or pairing-mode state that a soft reset does not fix.

#### E. Accessory still paired elsewhere

**Discover** auto-adds rows for **unpaired** accessories. If the device is still bonded to Apple Home or another controller, the Notice lists it under **Already paired elsewhere** and may not add a row.

Fix:

1. Remove the accessory from **Apple Home** and any other HomeKit controller.
2. Power-cycle the accessory; re-enter HomeKit pairing mode on the device.
3. Wait **30–60 seconds** for mDNS to settle.
4. Run **Discover** again.

Deleting a cloud plugin (for example the old Ecobee cloud integration) does **not** clear a HomeKit bond on the thermostat.

#### F. Ecobee QR vs numeric code

Ecobee may show a **QR code** for Apple Home. This hub needs the **numeric HomeKit pairing code** on the thermostat screen (or sticker/docs), entered in **HomeKit pairing code**.

The QR is not scanned by Polyglot. See [CONFIG.md — Manual rows](CONFIG.md#manual-rows-qr-code-in-vendor-app-only).

---

## Manual pairing (skip Discover)

If you have the 8-digit code, you do not need **Discover**:

1. **Configuration → Custom Typed → HomeKit pairing slots**
2. **Add row**
3. Enter the code in **HomeKit pairing code** (`12345678` or `123-45-678`)
4. Leave **Accessory device id** and **Substring of accessory name** blank if only one unpaired device is on the LAN
5. **Save**

The hub attempts pairing on save. A **Paired HomeKit device** child node should appear; **Paired status** should show **Paired**.

---

## Pairing fails after entering the code

| **Hub error code** | Likely cause | What to try |
|--------------------|--------------|-------------|
| **Pairing: no matching accessory** | Device not found, still paired elsewhere, or wrong id/name filter | **Discover** again; clear **Accessory device id**; reset device HomeKit pairing |
| **Pairing failed** | Wrong code, expired code, or device left pairing mode | Re-open pairing mode on device; enter the **current** code |
| **Custom typed save failed** | Polyglot could not save configuration | Check disk/permissions; retry Save |
| **Pairing rows update failed** | Could not update slots after **Discover** | Check log; retry **Discover** |

Setup codes **expire** when pairing mode ends. Always use the code shown **right now** on the device.

---

## Accessory shows "already paired"

Symptoms: **Discover** Notice lists the device under **Already paired elsewhere**, or **Hub error code** is **Pairing: no matching accessory**.

1. Unpair from **Apple Home** and any other HomeKit controller.
2. Put the accessory in HomeKit pairing mode again.
3. Power-cycle or vendor HomeKit reset if needed.
4. Wait 30–60 seconds; run **Discover** again.
5. Enter the current code in **HomeKit pairing code** and **Save**.

**Unpair device** / **Delete device node** on a **Paired HomeKit device** child clears **this plugin's** saved pairing only. If the physical device still advertises as paired, repeat the steps on the device.

---

## Bridge will not start

| Status | Value | Action |
|--------|-------|--------|
| **Bridge Status** | **Error** | Read Notices and `debug.log` |
| **Hub error code** | **Bridge start failed** | Often mDNS / UDP port **5353** conflict |

On Polisy/eISY, leave **`zeroconf_unicast`** at default **`on`** unless support directs otherwise. See [CONFIG_EXTRA.md — zeroconf parameters](CONFIG_EXTRA.md#zeroconf_unicast).

Run **ZEROCONF_DIAG** and include the **`zeroconf_diag`** or **`hap_discover`** Notice when asking for help. When **Extra Discovery Networks** are configured, the Notice includes **`discover_network_rows`** (what you saved) and **`zeroconf_interface_ips`** (what the hub actually bound for mDNS). Grep **`logs/debug.log`** for `HomeKit DISCOVER zeroconf diag` or `network hint`.

Other failures:

| **Hub error code** | Meaning |
|--------------------|---------|
| **Asyncio loop stopped** | Internal hub thread died — restart Node Server; check log |
| **Bridge stop failed** | Shutdown error (usually when restarting) |

---

## MQTT problems (after pairing, for Ecobee plugin)

Pairing can succeed even when MQTT is down, but **udi-poly-ecobee** needs the hub's MQTT transport (preferred).

| **MQTT transport** | Meaning |
|--------------------|---------|
| **Disabled** | `mqtt_enable` is `false` in configuration |
| **Not connected** | Broker unreachable or wrong host/port |
| **Connected** | Ready for Ecobee plugin |

Defaults: `mqtt_host` = `localhost`, `mqtt_port` = `1884`, `mqtt_hub_slug` = `default`. The Ecobee plugin's hub slug must match.

Notices **MQTT transport lost** or **MQTT transport disabled** explain broker-side issues.

**Connected, but Ecobee logs `hello ack not received`:** the hub is on the broker and is not answering hello. After this build the hub log should show `MQTT inbound hello` and `hello ack sent` within a second of Ecobee's hello. The ack uses the cached pairing list. A thermostat that is not accepting HomeKit connections no longer holds that ack. Restart the hub so this process is the one listening.

---

## Paired device child nodes

Each **HomeKit pairing slots** row can create a **Paired HomeKit device** child under the hub.

| Status | Values | Meaning |
|--------|--------|---------|
| **Paired status** | **Unpaired** / **Paired** | Whether this slot has an active pairing |
| **Slot** | 1, 2, 3, … | Slot number |
| **Health** | **Healthy** / **Degraded** / **Not paired** | Live transport health for paired accessories |

Commands on the child node:

| Command | Purpose |
|---------|---------|
| **Unpair device** | Clears **HomeKit pairing code** for that row; reloads sessions |
| **Delete device node** | Removes the row and child node |

These do not guarantee the physical accessory cleared its HomeKit bond.

---

## Hub error code reference

All values shown on the controller as **Hub error code**:

| Value | Meaning |
|-------|---------|
| **No error** | OK |
| **Bridge start failed** | Hub bridge did not start |
| **Discover scan failed** | **Discover** network scan failed |
| **Discover unexpected error** | Unexpected **Discover** failure |
| **Custom typed save failed** | Could not save typed configuration |
| **Pairing rows update failed** | Could not update **HomeKit pairing slots** after **Discover** |
| **Bridge stop failed** | Error stopping bridge |
| **Status update failed** | Internal status update error |
| **Pairing: no matching accessory** | No unpaired device matched the slot row |
| **Pairing failed** | Pairing attempt rejected or timed out |
| **Asyncio loop stopped** | Hub background loop stopped |
| **Pairing health degraded** | Paired accessory transport unhealthy |

---

## Ecobee-specific quick path

1. Install/start **HomeKit Hub** (not HomeKit Bridge).
2. Wait for **NodeServer Online** = **Connected**, **Bridge Status** = **Running**, log shows **`HomeKit Hub ready`**.
3. **IoT / separate VLAN only:** if the Ecobee is not on the Polisy primary LAN, add that subnet under **Extra Discovery Networks**, **Save**, run **Zeroconf diagnostic** to confirm **`zeroconf_interface_ips`**, then continue.
4. On Ecobee: **Settings → Enable HomeKit pairing** (keep screen open). **Do not** add to Apple Home.
5. On **HomeKit Hub** controller: **Discover**.
6. Read **Notices** → **HomeKit discover**.
7. Reload the full **Configuration** page → **HomeKit pairing slots**.
8. Enter **HomeKit pairing code** → **Save** (run **Discover** before entering the PIN so id/name are prefilled).
9. Confirm **Paired HomeKit device** child appears with **Paired status** = **Paired**.
10. Install **udi-poly-ecobee** (see its CONFIG.md).

---

## Information to collect for support

1. Plugin name and version (Polyglot Node Server page).
2. **NodeServer Online**, **Bridge Status**, **MQTT transport**, **Hub error code** from the **HomeKit Hub** controller.
3. Full text of PG3 **Notices** after **Discover** and after Save.
4. **Download Log Package** from the Node Server page in Polyglot — PM this file to the plugin author (preferred over copying log excerpts by hand).
5. Accessory model, how it was unpaired from any prior controller, and whether an Apple Home hub was ever used (not required for this hub, but relevant if the device was paired to Apple Home via iPhone).
6. Network layout: single LAN vs IoT VLAN; **Extra Discovery Networks** rows (if any); output of **Zeroconf diagnostic** (`zeroconf_interface_ips`, `discover_network_rows`).

---

## Related documentation

- [SUPPORT_CASES.md](SUPPORT_CASES.md) — internal analyzed support cases (log triage notes)
- [CONFIG.md](CONFIG.md) — setup, pairing flow, UI-seeded custom params
- [CONFIG_EXTRA.md](CONFIG_EXTRA.md) — advanced MQTT, WebSocket, and zeroconf parameters
- [PROTOCOL.md](PROTOCOL.md) — WebSocket/MQTT API for integrators
- [udi-poly-ecobee CONFIG.md](https://github.com/UniversalDevicesInc-PG3/udi-poly-ecobee/blob/master/CONFIG.md) — after the hub is paired
