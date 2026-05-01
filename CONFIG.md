# HomeKit Hub — configuration

## Custom Configuration Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `ws_host` | No | WebSocket bind address. Default `127.0.0.1`. |
| `ws_port` | No | WebSocket port. Default `8163`. |
| `zeroconf_unicast` | No | `on` (default), `auto`, or `off`. **`on`** uses python-zeroconf unicast mode (typical on eISY and other hosts where UDP **5353** is already owned). **`auto`** tries multicast first, then falls back on “address in use”. **`off`** forces multicast only (fails if 5353 is taken). **Most installs never change this.** |
| `zeroconf_interfaces` | No | `default`, `all`, or leave empty. Optional narrowing for BSD/macOS unicast quirks (errno **49**). **Usually leave empty.** |
| `zeroconf_ip_version` | No | `v4`, `v6`, `all`, or leave empty. **Usually leave empty.** |

**Zeroconf parameters:** On a normal Polisy / eISY deployment you can ignore the three `zeroconf_*` keys entirely. Defaults match the supported production setup (unicast-friendly when mDNS is shared). Change them only for troubleshooting or unusual networks; the controller command **Zeroconf diagnostic** (`ZEROCONF_DIAG`) logs a snapshot (mode, transports, library versions). After changing `zeroconf_*` or WebSocket bind settings, save configuration; the hub restarts the asyncio bridge automatically when those values change. (Environment variables below still **override** Custom Params when set for the Node Server process.)

## Custom Typed Configuration Parameters

Same pattern as **udi-poly-notification** (e.g. Pushover / Messages lists): one typed section with **multiple rows**; each row is one pairing slot.

### HomeKit pairing slots (`pairing_slots`)

In the Polyglot UI, open **Custom Typed Configuration Parameters** and use the list **“HomeKit pairing slots”** (the Node Server registers it at startup; the list supports **add row** / **remove** in the editor). **DISCOVER** automatically **adds a row** for each newly seen **unpaired** accessory (with **Accessory id** and **name** filled in; you add the **HomeKit pairing code** and save). You can also add or remove rows manually in that list.

Each row has a **Slot** (optional) plus the pairing and filter fields:

| Field | Description |
|-------|-------------|
| **Slot** (`slot`) | Positive integer **1, 2, 3, …** identifying this row’s pairing in saved data and hub logs. **Optional:** if you leave it empty, the Hub picks the **smallest unused** slot number (reuses a gap if you remove a row). If two rows request the same slot, the first wins and the other is reassigned automatically. |
| **HomeKit pairing code** (`hap_pin`) | Code shown on the accessory (e.g. `123-45-678`). You may enter **eight digits without dashes** (`12345678`); the hub normalizes to `123-45-678` before pairing. **Leave empty** on that row to disassociate only that accessory (Hub removes pairing when possible and clears saved data for that slot). |
| **Accessory device id** (`accessory_id`) | **Optional.** If you leave it (and the name) **empty**, the Hub uses the **most recent DISCOVER** snapshot in custom data (`last_hap_discover`) to pick the target — you do not copy ids by hand. If **several** accessories are unpaired at once, set this field (or **name** below) on the row to choose one. |
| **Substring of accessory name** (`accessory_name`) | **Optional** extra filter (same as id); use when you must disambiguate multiple unpaired devices. |
| **Node key** (`node_key`) | Stable plugin-managed key used for IoX child node identity/address. Auto-assigned if missing. Leave it unchanged to keep the same IoX node address across unpair/re-pair, including when replacing with a different physical device in that row, so existing programs/scenes/references continue to target the same node. Auto-generated keys are monotonic and are not automatically reused later, even if old rows are deleted. |
| **LAN host:port** (`discover_endpoint`) | Filled from **DISCOVER** when applicable; also **updated automatically** when a degraded IP pairing **recovers** after reboot or LAN/IP/port change (informational). |

- There is **no fixed maximum** number of rows; use as many as you need.
- **Remove** a row or **clear** its pairing code in the editor to disassociate that slot.
- If you removed a row by mistake, run **DISCOVER** again: the hub will repopulate rows from current discover results (prefers unpaired devices, but can also recreate missing rows from paired discoveries so id/name are available again).

## Persisted custom data

Pairing keys and session metadata are stored under **`homekit_pairings`** in Polyglot **custom data** (object keyed by slot index string, e.g. `"1"`, `"12"`). You normally do not edit this by hand.

## Pairing workflow

1. Put the accessory in HomeKit pairing mode (unpaired).
2. Run the **DISCOVER** command on the **HomeKit Hub** controller in the ISY / PG3 admin UI or in the PG3 UI. The hub stores **`last_hap_discover`**, and **appends the Custom Typed** **HomeKit pairing slots** list with a row per new unpaired accessory (id and name set for you; pairing code **empty** until you fill it).
3. In **Custom Typed** configuration, find the new row and enter the **HomeKit pairing code** (eight digits, often shown as `123-45-678`). The accessory shows it on a label, screen, or in its vendor app while it is in **HomeKit pairing mode**—it is not in Polyglot. **Save** (and restart the Node Server if the admin UI requires it). You only need to edit id/name on that row if you are correcting a **DISCOVER** mistake or disambiguating before pairing; otherwise those fields are already set by **DISCOVER**.

### Manual rows (e.g. vendor app only offers a QR code)

You do **not** have to rely on **DISCOVER** auto-adding rows. **Custom Typed** **HomeKit pairing slots** is the manual configuration: use **add row** in that list, enter **HomeKit pairing code** and (recommended) **Accessory id** / **name** so the hub targets the right device.

Some products (e.g. **Ecobee**) steer you to scan a **QR code** in their app to add the device to **Apple Home**. That is separate from this hub: for **Local** control, the accessory must be pairable by **this** Node Server—usually **not** paired to Apple Home at the same time—and you still need the **numeric** setup code the HomeKit spec uses (often on a sticker or in documentation; a QR is an encoding of that payload, not a substitute typed into Polyglot). Use the hub’s **DISCOVER** on the controller while the device is in **HomeKit pairing mode** to populate **`last_hap_discover`** and to fill **id** / **name** automatically, or type those fields yourself if you already know them.

### Troubleshooting: "Already paired" after unpair

If DISCOVER or pairing still reports the accessory as **already paired** after you unpaired it, use this recovery sequence:

1. Remove/unpair the accessory from **Apple Home** (and any other HomeKit controller).
2. Power-cycle the accessory (or perform a HomeKit factory reset on the accessory if the vendor requires it).
3. Wait 30-60 seconds for mDNS/HomeKit state to settle.
4. Run **DISCOVER** again to refresh `last_hap_discover` and repopulate/update the row.
5. Enter the HomeKit pairing code on that row and save.

Notes:
- HomeKit paired state in discovery can lag briefly after unpair.
- Deleting a row also removes the saved pairing slot data for that row; if that happens, re-pairing is a fresh pairing flow.

## Controller commands

| Command | Purpose |
|---------|---------|
| **DISCOVER** | Scan for HAP accessories; refreshes `last_hap_discover` and updates Custom Typed rows. |
| **ZEROCONF_DIAG** | Notice with zeroconf mode, transport discovery counts, and library versions. |

## Paired device node commands

Each pairing slot row is exposed as its own node (including DISCOVER candidates), with:

- **ST** = paired status (`1` while the slot is currently paired, `0` for discovered/candidate slots not paired yet),
- **GV0** = slot number.
- Node address = `hkp_<node_key>` (stable per row; not tied to slot/device id/name).
- Default node name = `HK Device <NODE_KEY>` (state-independent to avoid node churn on pair/unpair).
- Because address is keyed by `node_key`, IoX references to that node address remain valid even if slot assignment changes or a new accessory is paired into the same row.

| Command | Purpose |
|---------|---------|
| **UNPAIR** | Clears only that slot row's `hap_pin` in Custom Typed data and reloads hub sessions. This removes plugin-side pairing configuration for that slot. |
| **DELETE** | Removes that slot row from Custom Typed data, removes that slot entry from saved custom data (`homekit_pairings`), then deletes the node. |

Important:

- `UNPAIR` / `DELETE` are plugin-side cleanup and do **not** guarantee the accessory itself has cleared HomeKit bonds.
- If a device still advertises as `paired=True` after plugin-side unpair/delete, also unpair/reset it from Apple Home or vendor workflow, then rediscover.

## HomeKit setup URI (`X-HM://`)

Vendor apps often show a **QR code** or share link whose payload starts with **`X-HM://`**. The hub still needs the **numeric** setup code (e.g. `123-45-678`) in Custom Typed; the URI encodes that code plus metadata.

- **Decode helper (dev machine):** from the Node Server repo root, run  
  `python3 tools/decode_x_hm_setup.py 'X-HM://…'`  
  (or pipe the URI on stdin). JSON output includes `setup_code` in `XXX-XX-XXX` form.
- **Library:** `homekit_hub.x_hm_uri.decode_x_hm_setup_uri` returns the same fields for tests or tooling.

## Troubleshooting

### UNPAIR slot was run, but re-pair still fails

Symptom:

- You run **UNPAIR** for a slot (or clear the slot `hap_pin`), then save a new pairing code.
- Pairing fails with a notice like:
  - `HomeKit pairing: no matching accessory`
  - `Slot N: no unpaired accessory matched id=... name=...`
- A fresh **DISCOVER** may show the device under **Already paired elsewhere**.

Why this happens:

- The slot unpair path clears this plugin's saved pairing state and asks the hub to remove its session.
- If the accessory is still paired to another HomeKit controller (Apple Home or another bridge), or has not fully returned to pairing mode yet, discovery reports it as `paired=True`.
- The hub intentionally refuses to run SRP pairing against discoveries marked paired, so it reports "no matching accessory" for that slot.

Recovery sequence:

1. Remove/unpair the accessory from Apple Home (and any other HomeKit controller).
2. Put the accessory into HomeKit pairing mode again.
3. Power-cycle the accessory (or perform vendor HomeKit reset/factory reset if required).
4. Wait ~30-60 seconds for mDNS/HomeKit state to settle.
5. Run **DISCOVER** again and confirm the target appears as unpaired (not in "Already paired elsewhere").
6. Enter/save the pairing code in the slot row and let the hub pair.

If DISCOVER continues to show `paired=True` after those steps, the accessory still has an active HomeKit pairing and usually needs the vendor-specific HomeKit reset/factory reset workflow.

## WebSocket protocol

See `PROTOCOL.md`. All messages require `"version": "1"`. Events for all paired accessories share one connection; clients filter by `device_id`.

## Security

The WebSocket server binds to `127.0.0.1` by default so only local clients can connect.

## Environment (optional)

These apply to the **Node Server process**. When set, they **override** the corresponding Custom Params (`zeroconf_*`). Host operators use them for support or automation; **typical users rely on Custom Param defaults and do not set these.**

| Variable | Values | Purpose |
|----------|--------|---------|
| `HOMEKIT_HUB_ZEROCONF_UNICAST` | `1` / `true` / `yes` / `on` or `0` / `false` / `off` | Force unicast or force multicast regardless of Custom Params. |
| `HOMEKIT_HUB_ZEROCONF_INTERFACES` | `default` / `all` | Interface selection for zeroconf (BSD/macOS: `default` can reduce errno **49** warnings in unicast mode). |
| `HOMEKIT_HUB_ZEROCONF_IP_VERSION` | `v4` / `v6` / `all` | IP stack for zeroconf. |
