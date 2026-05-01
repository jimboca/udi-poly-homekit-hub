# HomeKit Hub WebSocket protocol

All JSON messages **must** include a string **`version`** field. Current protocol version: **`1`** (`PROTOCOL_VERSION` in the hub).

The hub may pair **multiple** HomeKit accessories at once. Every `event` includes the **`device_id`** of the accessory that changed; clients should filter by `device_id`. Commands **must** include the target accessory’s `device_id`.

## HAP identifiers used in this protocol

- **`device_id`**: HomeKit pairing identifier (AccessoryPairingID), represented as lowercase text in this protocol. It selects which paired HomeKit device/bridge session the message targets.
- **`aid`** (**Accessory ID**): HomeKit accessory identifier within a pairing's accessory database. In many single-accessory pairings this is `1`, but bridge pairings often have multiple accessories with different `aid` values.
- **`iid`** (**Instance ID**): HomeKit characteristic identifier within an accessory (`aid`). Together, `(aid, iid)` identifies one characteristic within the selected `device_id`.

When this protocol includes all three keys (`device_id`, `aid`, `iid`), they identify one concrete HomeKit characteristic endpoint.

## Protocol version policy

- **`version`** (per message) and the hello **`ack.protocol`** field track the wire format. They stay aligned with the hub’s `PROTOCOL_VERSION` constant.
- **Bump `PROTOCOL_VERSION`** when a change is **not** backward compatible for existing clients (removed/renamed actions, changed required fields, different semantics, or stricter validation that rejects previously valid payloads).
- **Do not bump** for additive, optional fields (extra keys on `ack`, new optional message keys, new actions clients can ignore) or for documentation-only clarifications.
- When bumping: update this document, the hub constant, client examples, and release notes; prefer supporting **one previous** version briefly only if you explicitly document a migration window (this project currently expects clients to match **`1`**).

## Optional WebSocket auth (`ws_token`)

When the Node Server Custom Param **`ws_token`** is **non-empty**, the hub requires:

1. The first client message must be **`hello`** with a matching secret in field **`token`** or **`ws_token`** (string, compared in constant time).
2. Any other message before a successful hello is rejected with **`error`** and the connection is closed.

When **`ws_token`** is empty or unset, behavior matches older hubs: any action may be sent without hello ordering (though clients should still send **`hello`** to receive the bootstrap stream and capability advertisement).

## Client → Hub (`hello`)

```json
{
  "version": "1",
  "action": "hello",
  "client": "udi-poly-ecobee",
  "token": "<optional when hub ws_token is set>"
}
```

## Hub → Client (`ack` for hello)

```json
{
  "version": "1",
  "action": "ack",
  "for": "hello",
  "protocol": "1",
  "device_ids": ["<AccessoryPairingID lowercase>", "..."],
  "devices": [
    {
      "device_id": "<AccessoryPairingID lowercase>",
      "name": "Thermostat",
      "manufacturer": "ecobee Inc.",
      "model": "EBSTATE",
      "serial_number": "…",
      "firmware_revision": "…",
      "hardware_revision": "…",
      "category": 9,
      "category_label": "THERMOSTAT",
      "primary_aid": 1
    }
  ],
  "capabilities": {
    "actions": ["hello", "command", "snapshot", "list_devices", "get", "subscribe", "unsubscribe"],
    "auth": "none",
    "events": {
      "mode": "filtered_after_subscribe",
      "description": "By default all HAP events are forwarded. After at least one successful subscribe, only matching (device_id, aid, iid) events are sent until the subscription set becomes empty (then defaults are restored)."
    }
  }
}
```

- **`device_ids`**: sorted list of paired accessories (same membership as `list_devices`), included for quick client startup without waiting for the bootstrap `list_devices` frame.
- **`devices`**: same objects as in **`list_devices`** (see below): HAP **Accessory Information** metadata when the hub has loaded `/accessories` for that pairing. Optional keys may be omitted if unknown or not yet fetched.
- **`capabilities.auth`**: **`none`** if the hub does not require a token; **`token`** when `ws_token` is configured.

After this `ack`, the hub sends one **`list_devices`** message (current active pairings) on the same connection. It does **not** auto-send **`snapshot`** / **`get`**; clients request those for the devices they care about.

## Hub → Client (`event`)

Emitted when a subscribed HomeKit characteristic changes.

```json
{
  "version": "1",
  "action": "event",
  "device_id": "<AccessoryPairingID lowercase>",
  "characteristic": "<CharacteristicsTypes enum name or normalized type UUID>",
  "aid": 1,
  "iid": 10,
  "value": null
}
```

`aid` and `iid` in each `event` point to the exact characteristic instance that changed for that `device_id`.

### Event filtering (`subscribe` / `unsubscribe`)

- By default, connected clients receive **all** `event` messages for all paired accessories.
- After a client successfully sends at least one **`subscribe`** for this connection, the hub **only** forwards `event` messages whose `(device_id, aid, iid)` was subscribed.
- **`unsubscribe`** removes one subscription. When the last subscription is removed, filtering is cleared and the client again receives **all** events.

HomeKit accessory subscriptions/listeners are managed by the hub per pairing; `subscribe` / `unsubscribe` only affect **WebSocket fan-out**, not HAP notify registration.

## Client → Hub (`command`)

```json
{
  "version": "1",
  "action": "command",
  "device_id": "<AccessoryPairingID lowercase>",
  "characteristic": "<enum name or UUID>",
  "value": true
}
```

## Hub → Client (`ack` / `error` for command)

Success:

```json
{
  "version": "1",
  "action": "ack",
  "for": "command"
}
```

Failure:

```json
{
  "version": "1",
  "action": "error",
  "message": "reason",
  "for": "command"
}
```

Mismatch or unknown `version` may result in `error` and connection close.

## Client → Hub (`snapshot`)

Request current readable values for a paired accessory (use at client startup to initialize state).

```json
{
  "version": "1",
  "action": "snapshot",
  "device_id": "<AccessoryPairingID lowercase>"
}
```

## Hub → Client (`snapshot` / `error` for snapshot)

Success:

```json
{
  "version": "1",
  "action": "snapshot",
  "device_id": "<AccessoryPairingID lowercase>",
  "values": [
    {
      "characteristic": "CurrentTemperature",
      "aid": 1,
      "iid": 10,
      "value": 21.5
    }
  ]
}
```

Each `values[]` item may also include `status` when HomeKit returns an error/status for that characteristic read.

Failure:

```json
{
  "version": "1",
  "action": "error",
  "for": "snapshot",
  "message": "reason"
}
```

## Client → Hub (`get`)

Read **selected** characteristics in one round trip (response shape matches `snapshot` `values`).

```json
{
  "version": "1",
  "action": "get",
  "device_id": "<AccessoryPairingID lowercase>",
  "characteristics": ["On", "Brightness"]
}
```

Either **`characteristics`** (array of strings) or a single **`characteristic`** (string) is required. Names follow the same rules as `command` / `snapshot` (enum name or UUID).

## Hub → Client (`get` / `error` for get)

Success:

```json
{
  "version": "1",
  "action": "get",
  "device_id": "<AccessoryPairingID lowercase>",
  "values": [
    {
      "characteristic": "On",
      "aid": 1,
      "iid": 10,
      "value": true
    }
  ]
}
```

Failure: `action` = `error`, `for` = `get`.

## Client → Hub (`subscribe`)

Restrict **WebSocket** `event` delivery to one characteristic (after the first successful `subscribe` on this connection, only subscribed keys are forwarded until the set is cleared — see above).

Use **`aid`** and **`iid`** together, or **`characteristic`** (string), plus **`device_id`**:

```json
{
  "version": "1",
  "action": "subscribe",
  "device_id": "<AccessoryPairingID lowercase>",
  "characteristic": "On"
}
```

## Hub → Client (`ack` / `error` for `subscribe`)

Success: `action` `ack`, `for` `subscribe`, echoing `device_id`, `aid`, `iid`.

## Client → Hub (`unsubscribe`)

Remove one `(device_id, aid, iid)` from the WebSocket filter (same body shape as `subscribe`).

## Hub → Client (`ack` / `error` for `unsubscribe`)

Success: `action` `ack`, `for` `unsubscribe`, echoing `device_id`, `aid`, `iid`.

## Client → Hub (`list_devices`)

Request the set of currently active paired accessories.

```json
{
  "version": "1",
  "action": "list_devices"
}
```

## Hub → Client (`list_devices`)

```json
{
  "version": "1",
  "action": "list_devices",
  "devices": [
    {
      "device_id": "<AccessoryPairingID lowercase>",
      "name": "Thermostat",
      "manufacturer": "ecobee Inc.",
      "model": "EBSTATE",
      "serial_number": "…",
      "firmware_revision": "…",
      "hardware_revision": "…",
      "category": 9,
      "category_label": "THERMOSTAT",
      "primary_aid": 1
    }
  ]
}
```

Each element **always** includes **`device_id`**. The hub adds optional discovery fields from the HAP **Accessory Information** service (values from the last successful `/accessories` load for that pairing):

| Field | Meaning |
|-------|---------|
| `name` | Accessory **Name** |
| `manufacturer` | **Manufacturer** (clients often match vendor here, e.g. Ecobee) |
| `model` | **Model** |
| `serial_number` | **SerialNumber** |
| `firmware_revision` | **FirmwareRevision** |
| `hardware_revision` | **HardwareRevision** |
| `category` | HAP **Category** id (integer) |
| `category_label` | aiohomekit **Categories** enum name when known (e.g. `THERMOSTAT`) |
| `primary_aid` | Accessory **aid** whose information is shown (see below) |

**Bridge pairings:** one WebSocket row is still one **pairing** (`device_id`). If the pairing exposes multiple HAP accessories, the hub picks a **representative** accessory for metadata: the first non-**Bridge** category when possible, otherwise the lowest `aid`. Use `primary_aid` to interpret which accessory the strings refer to; `command` / `snapshot` / `get` still use the pairing’s `device_id` and real `aid`/`iid` from the full layout.

If the hub has not yet fetched the accessory database for a pairing, rows may contain only `device_id` until the next successful layout refresh.

When the layout exists but Accessory Information strings are still empty in the cached `/accessories` model (common for some IP accessories), the hub performs a **read** of the Accessory Information characteristics while building `list_devices` so **Manufacturer** (and related fields) can populate without requiring a separate `snapshot`/`get` first.

The hub may also send `list_devices` proactively after pairing/unpairing state changes so connected clients can refresh membership without polling.

The hub sends `list_devices` after `hello`/`ack` (bootstrap). Clients should request `snapshot` or `get` only for device(s) they care about.

## Client guide: device type + capability discovery

Clients that want "all thermostats", "all lights", "all switches/plugs", etc. should use a two-stage approach:

1. **Membership + identity** (`hello` -> bootstrap `list_devices`):
   - Use `device_id` as the stable pairing key.
   - Use optional `manufacturer`, `model`, `name` for vendor/model filtering.
   - Use `category` / `category_label` as the first-pass device class hint.

2. **Real capabilities** (`snapshot` or targeted `get`):
   - Determine what the device can actually do by the characteristics it exposes.
   - Build capability flags from characteristic names/UUIDs (and optionally value shape/range).

`category` identifies a coarse accessory class, but HomeKit capability is characteristic-driven. For robust routing, treat category as a hint and characteristics as authority.

### Practical classification pattern

- **Thermostat**: `CurrentTemperature`, `TargetTemperature`, `TargetHeatingCoolingState`, `CurrentHeatingCoolingState`.
- **Light**: `On` plus one or more of `Brightness`, `Hue`, `Saturation`, `ColorTemperature`.
- **Switch / plug / outlet**: `On` without light-only controls; outlets often expose `OutletInUse`.
- **Contact / occupancy / motion sensors**: state characteristics such as `ContactSensorState`, `OccupancyDetected`, `MotionDetected`.
- **Locks / covers / fans / valves**: use their service-specific characteristics (`LockTargetState`, `TargetPosition`, `RotationSpeed`, `Active`, etc.).

### Suggested startup flow

1. Send `hello`.
2. Read bootstrap `list_devices`.
3. For each candidate row, send `snapshot` (or `get` for a small characteristic set) and cache:
   - characteristic -> `(aid, iid)` mapping,
   - supported commandable characteristics,
   - read-only telemetry characteristics.
4. Apply app rules (examples):
   - "All ecobee thermostats": `manufacturer` contains `ecobee` AND thermostat characteristic set present.
   - "All lights": light characteristic signature present, regardless of `category`.
   - "All switches/plugs": `On` present and no light-only controls.
5. Optionally send `subscribe` only for `(device_id, aid, iid)` keys your app uses.

### Notes and edge cases

- `list_devices` metadata fields are optional; always tolerate missing `manufacturer`/`model`/`category`.
- Bridge pairings expose one row per pairing; `primary_aid` tells you which accessory supplied row metadata, but capability detection should come from full `snapshot`/`get` data.
- Characteristic names in this protocol are `aiohomekit` enum names when known; otherwise normalized UUID strings. Client matchers should support both.
- New HomeKit types can appear without protocol changes; clients should classify by observed characteristics and ignore unknown fields safely.

## Example client (`websockets`)

Python 3.10+ using the [`websockets`](https://websockets.readthedocs.io/) library (same dependency as the hub). The hub listens on Custom Params `ws_host` / `ws_port` (default `127.0.0.1:8163`).

```python
import asyncio
import json

import websockets

URI = "ws://127.0.0.1:8163"

async def main() -> None:
    async with websockets.connect(URI) as ws:
        hello = {"version": "1", "action": "hello", "client": "example"}
        # If the hub Custom Param ws_token is set, add: hello["token"] = "your-secret"
        await ws.send(json.dumps(hello))
        ack = json.loads(await ws.recv())
        print("hello ack:", ack)

        # Hub auto-sends list_devices after ack
        devices = json.loads(await ws.recv())
        print("bootstrap devices:", devices)

        # Request snapshots only for device(s) you care about.
        for item in devices.get("devices", []):
            did = (item.get("device_id") or "").strip().lower()
            if not did:
                continue
            await ws.send(json.dumps({"version": "1", "action": "snapshot", "device_id": did}))

        # Subscribe to incoming messages; send commands on another task as needed.
        while True:
            raw = await ws.recv()
            msg = json.loads(raw)
            print(msg.get("action"), msg)

asyncio.run(main())
```

In production, run the receive loop concurrently with your command/snapshot logic (e.g. `asyncio.create_task`); the hub may push many `event` messages without prior requests.
