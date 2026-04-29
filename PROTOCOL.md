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
