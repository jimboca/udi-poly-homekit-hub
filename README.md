# udi-poly-homekit

## What is this?

**udi-poly-homekit** is a **HomeKit plugin for Universal Devices IoX (PG3 / PG3x)**. It runs **on your eisy / Polisy as its own HomeKit hub** and pairs directly with HomeKit-compatible accessories on your LAN — **you do not pair them with the Apple Home app or any Apple device**. **No iPhone, iPad, Mac, Apple TV, or HomePod is required** to install or operate this plugin.

This Node Server speaks the same HomeKit Accessory Protocol (HAP) as Apple's Home app, so accessories that advertise HomeKit support (thermostats, plugs, sensors, locks, etc.) can be added to IoX through PG3. Pairings are persisted in PG3 custom data, so accessories stay paired across restarts.

The hub exposes local **WebSocket** and **MQTT** interfaces so other PG3 Node Servers running on the same controller (for example `udi-poly-ecobee` in Local mode) can read and control characteristics on those paired accessories without going through Ecobee. Those other plugins can then publish their own nodes, drivers, and commands to PG3 / IoX. Each paired accessory is identified on the WebSocket API by its **`device_id`** (HAP `AccessoryPairingID`, lowercase).

The hub supports **multiple simultaneous HomeKit pairings** (each row has an optional **slot** number, or the Hub assigns the next free slot).

## Requirements

**Python 3.10+** on the Polyglot host (**`aiohomekit` 3.x** and current **`udi_interface`** require it).

**Polyglot** runs **`install.sh`** on the Node Server host to install **`requirements.txt`**; you do not need to install those packages by hand for a normal install.

## Configuration

See **`CONFIG.md`**: **Custom Typed Configuration Parameters** — list **HomeKit pairing slots** ( **DISCOVER** adds rows with id/name; you enter the pairing code), optional **slot**, same idea as typed lists in **udi-poly-notification**. Flat **Custom Configuration Parameters** include `ws_host` / `ws_port` and optional **`zeroconf_*`** knobs (defaults match typical Polisy/eISY installs; most users never change them — details in CONFIG).

On the **HomeKit Hub** controller node, **Discover** runs a LAN HAP scan; **`ZEROCONF_DIAG`** (shown in the admin UI as **Zeroconf diagnostic**) posts a one-shot Notice with zeroconf mode, transport discovery counts, a UDP 5353 probe, and library versions for support.

Persisted pairing payloads live in custom data under **`homekit_pairings`**.

## Hub status and errors (ISY / eisy)

The controller node exposes **ST** (Polyglot / NodeServer connection — same idea as **udi-poly-kasa** `NodeServer Online`), **GV0** (**Bridge Status**: asyncio HomeKit bridge + WebSocket), and **ERR** (last reported error code). **ST** and **GV0** use ISY **UOM 25** (*index*) with profile NLS **`CST-*`** / **`BRST-*`**. **ERR** uses **`ERRC-*`**. Polyglot **Notices** carry human-readable titles and exception text for the same events.

| Driver | Meaning |
|--------|---------|
| **ST** `0` | Disconnected (Node Server stopped / not reporting to Polyglot). |
| **ST** `1` | Connected. |
| **ST** `2` | Failed. |
| **GV0** `0` | Bridge stopped (starting or Node Server stopping). |
| **GV0** `1` | Bridge running (aiohomekit + WebSocket server up). |
| **GV0** `2` | Bridge error — failed start, failed config-driven **full_restart**, or the asyncio loop thread exited while the hub was running (see **ERR** 10). Other faults (discover, typed save, etc.) update **ERR** and Notices only; they leave **GV0** unchanged. |

**ERR** codes (UOM 25; see profile NLS `ERRC-*`):

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

**`report_error`** (in `nodes/Controller.py`) centralizes failure reporting: it logs with **`LOGGER.exception`** when an exception is passed, otherwise **`LOGGER.error`**; sets a Polyglot Notice under a fixed key (`homekit_bridge`, `homekit_err_discover`, `homekit_err_config`, or `homekit_meta`); and sets **ERR** to the code. Hub-fatal conditions (**GV0** = 2) include start failure, failed **full_restart**, and unexpected asyncio loop thread exit (longPoll watchdog). After a **successful** bridge start, **`clear_hub_error_indicators`** clears those hub error Notice keys and sets **ERR** back to 0 (it does not clear DISCOVER-related state).

On **Node Server start**, the controller clears **all** Notices before loading.

## Packaging (git branches)

Point PG3 at this repository and the **`production`** or **`beta`** branch. Polyglot clones that branch and runs **`install.sh`** / **`requirements.txt`** on the host like other git-based Node Servers. Maintainer-side details on how those branches are produced are in **[DEVELOPMENT.md](DEVELOPMENT.md)**.

## Logs

Runtime logs under `logs/` are **local-only**: the directory is listed in `.gitignore` and is not part of the published zip unless you add files there manually.

## Multiple WebSocket clients

- **`handler_params`** only applies to **this** Node Server’s Polyglot **Custom Configuration Parameters**. It does not register remote plugins.
- **Other Node Servers** connect as **WebSocket clients**. The hub **fan-outs** each HAP `event` to **all** connected clients. Each client filters by `device_id` (and characteristic) in its own code.
- **`hello` `ack`** and proactive **`list_devices`** **`devices[]`** rows include HAP **Accessory Information** when the accessory responds: **`category`** (integer, e.g. **9** = thermostat) and **`category_label`** (HAP enum name when known), plus **`manufacturer`**, **`model`**, **`name`**, **`serial_number`**, etc. The hub issues **reads** and may **refresh `/accessories`** so **category** is populated for healthy pairings; downstream Node Servers (e.g. **udi-poly-ecobee** in HomeKit mode) can rely on **`category`** / **`category_label`** for device-type filtering after pairing succeeds.

## Development

Setup, tests, lint, and the **`make beta` / `make production` / `make release`** flow live in **[DEVELOPMENT.md](DEVELOPMENT.md)**, along with the source-tree layout. End users do not need this file — Polyglot runs `install.sh` on the Node Server host for you.

## References

- **PG3 Python interface (udi_interface)**: [API.md](https://github.com/UniversalDevicesInc/udi_python_interface/blob/master/API.md) — `Interface` events (`CUSTOMTYPEDDATA`, **Custom** class, `load(data, save)` for persisting custom / typed data to Polyglot, etc.).

## License

MIT — see `LICENSE`.
