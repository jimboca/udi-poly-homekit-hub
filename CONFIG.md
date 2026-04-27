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
| **HomeKit pairing code** (`hap_pin`) | Code shown on the accessory (e.g. `123-45-678`). **Leave empty** on that row to disassociate only that accessory (Hub removes pairing when possible and clears saved data for that slot). |
| **Accessory device id** (`accessory_id`) | **Optional.** If you leave it (and the name) **empty**, the Hub uses the **most recent DISCOVER** snapshot in custom data (`last_hap_discover`) to pick the target — you do not copy ids by hand. If **several** accessories are unpaired at once, set this field (or **name** below) on the row to choose one. |
| **Substring of accessory name** (`accessory_name`) | **Optional** extra filter (same as id); use when you must disambiguate multiple unpaired devices. |

- There is **no fixed maximum** number of rows; use as many as you need.
- **Remove** a row or **clear** its pairing code in the editor to disassociate that slot; running **DISCOVER** again will add rows only for unpaired devices **not** already listed (same **Accessory id**).

## Persisted custom data

Pairing keys and session metadata are stored under **`homekit_pairings`** in Polyglot **custom data** (object keyed by slot index string, e.g. `"1"`, `"12"`). You normally do not edit this by hand.

## Pairing workflow

1. Put the accessory in HomeKit pairing mode (unpaired).
2. Run the **DISCOVER** command on the **HomeKit Hub** controller in the ISY / PG3 admin UI (same as other Node Servers). The hub stores **`last_hap_discover`**, and **appends the Custom Typed** **HomeKit pairing slots** list with a row per new unpaired accessory (id and name set for you; pairing code **empty** until you fill it).
3. In **Custom Typed** configuration, find the new row and enter the **HomeKit pairing code** (eight digits, often shown as `123-45-678`). The accessory shows it on a label, screen, or in its vendor app while it is in **HomeKit pairing mode**—it is not in Polyglot. **Save** (and restart the Node Server if the admin UI requires it). You only need to edit id/name on that row if you are correcting a **DISCOVER** mistake or disambiguating before pairing; otherwise those fields are already set by **DISCOVER**.

### Manual rows (e.g. vendor app only offers a QR code)

You do **not** have to rely on **DISCOVER** auto-adding rows. **Custom Typed** **HomeKit pairing slots** is the manual configuration: use **add row** in that list, enter **HomeKit pairing code** and (recommended) **Accessory id** / **name** so the hub targets the right device.

Some products (e.g. **Ecobee**) steer you to scan a **QR code** in their app to add the device to **Apple Home**. That is separate from this hub: for **Local** control, the accessory must be pairable by **this** Node Server—usually **not** paired to Apple Home at the same time—and you still need the **numeric** setup code the HomeKit spec uses (often on a sticker or in documentation; a QR is an encoding of that payload, not a substitute typed into Polyglot). Use the hub’s **DISCOVER** on the controller while the device is in **HomeKit pairing mode** to populate **`last_hap_discover`** and to fill **id** / **name** automatically, or type those fields yourself if you already know them.

## WebSocket protocol

See `PROTOCOL.md`. All messages require `"version": "1"`. Events for all paired accessories share one connection; clients filter by `device_id`.

## Security

The WebSocket server binds to `127.0.0.1` by default so only local clients can connect.
