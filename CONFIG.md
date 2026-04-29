# HomeKit Hub — configuration

## Custom Configuration Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `ws_host` | No | WebSocket bind address. Default `127.0.0.1`. |
| `ws_port` | No | WebSocket port. Default `8163`. |

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

## WebSocket protocol

See `PROTOCOL.md`. All messages require `"version": "1"`. Events for all paired accessories share one connection; clients filter by `device_id`.

## Security

The WebSocket server binds to `127.0.0.1` by default so only local clients can connect.

## Environment (optional)

These apply to the Node Server process (see `homekit-poly.py` for defaults such as unicast zeroconf):

| Variable | Values | Purpose |
|----------|--------|---------|
| `HOMEKIT_HUB_ZEROCONF_UNICAST` | `1` / `true` / `yes` | Use python-zeroconf unicast mode (avoids binding UDP 5353 when another mDNS stack owns it). |
| `HOMEKIT_HUB_ZEROCONF_INTERFACES` | `default` / `all` | Narrow or widen interface selection for zeroconf (BSD/macOS unicast may need `default` to reduce `sendto` errno 49 warnings). |
| `HOMEKIT_HUB_ZEROCONF_IP_VERSION` | `v4` / `v6` / `all` | Force IP stack for zeroconf; on BSD/macOS in unicast mode the hub may default to IPv4-only unless you set `all`. |

## PG3 Bonjour vs in-process zeroconf

**Runtime:** Pairing and live HomeKit traffic use **`aiohomekit` with `AsyncZeroconf`** (mDNS listeners and the transport discovery cache). That path is required for stock `aiohomekit`; PG3’s **`polyglot.bonjour()`** results are **not** the same objects the library expects, so Bonjour data is **not** a drop-in replacement for zeroconf without a large custom adapter.

**Diagnostics / UDI:** `polyglot.bonjour()` can still return useful LAN service rows (including HAP) for **comparison, evidence to UDI, or experiments**. What the first argument should be (`"_hap"`, `"hap"`, or `None`) and how `protocol` behaves are **defined by the PG3 server**, not only by `udi_interface`. Broad queries (`type=None`) have been observed to return more complete snapshots than a narrow type on some builds.

**Branches:** **Main** may ship **zeroconf-only** to limit supported code paths. A **side branch** (e.g. Bonjour compare tooling) can keep PG3-specific diagnostics without committing to maintaining them in every release.
