# HomeKit Hub WebSocket protocol

All JSON messages **must** include a string **`version`** field. Current protocol version: **`1`**.

The hub may pair **multiple** HomeKit accessories at once. Every `event` includes the **`device_id`** of the accessory that changed; clients should filter by `device_id`. Commands **must** include the target accessory’s `device_id`.

## Client → Hub (`hello`)

```json
{
  "version": "1",
  "action": "hello",
  "client": "udi-poly-ecobee"
}
```

## Hub → Client (`ack`)

```json
{
  "version": "1",
  "action": "ack",
  "protocol": "1"
}
```

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
      "device_id": "<AccessoryPairingID lowercase>"
    }
  ]
}
```

Use `list_devices` + one `snapshot` request per `device_id` to initialize a client with all active devices.

## Example client (`websockets`)

Python 3.9+ using the [`websockets`](https://websockets.readthedocs.io/) library (same dependency as the hub). The hub listens on Custom Params `ws_host` / `ws_port` (default `127.0.0.1:8163`).

```python
import asyncio
import json

import websockets

URI = "ws://127.0.0.1:8163"

async def main() -> None:
    async with websockets.connect(URI) as ws:
        await ws.send(json.dumps({"version": "1", "action": "hello", "client": "example"}))
        ack = json.loads(await ws.recv())
        print("hello ack:", ack)

        await ws.send(json.dumps({"version": "1", "action": "list_devices"}))
        devices = json.loads(await ws.recv())
        print("devices:", devices)

        # Subscribe to events by reading in a loop; send commands on another task as needed.
        while True:
            raw = await ws.recv()
            msg = json.loads(raw)
            if msg.get("action") == "event":
                print("event:", msg)

asyncio.run(main())
```

In production, run the receive loop concurrently with your command/snapshot logic (e.g. `asyncio.create_task`); the hub may push many `event` messages without prior requests.
