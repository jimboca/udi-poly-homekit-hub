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
