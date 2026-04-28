# udi-poly-homekit

Polyglot **PG3x** Node Server: HomeKit Accessory Protocol (HAP) **controller** hub with a local **WebSocket** API for other Node Servers (e.g. `udi-poly-ecobee` in Local mode).

The hub supports **multiple simultaneous HomeKit pairings** (each row has an optional **slot** number, or the Hub assigns the next free slot). Each paired accessory is identified on the WebSocket API by its **`device_id`** (AccessoryPairingID, lowercase).

## Requirements

**Polyglot** runs **`install.sh`** on the Node Server host to install **`requirements.txt`**; you do not need to install those packages by hand for a normal install.

## Layout

- `homekit-poly.py` — entry point
- `homekit_hub/bridge.py` — aiohomekit + WebSocket (default port **8163**), multi-slot pairing
- `nodes/Controller.py` — PG3 lifecycle and custom params/data
- `PROTOCOL.md` — JSON message contract (`version` **1**)

## Configuration

See **`CONFIG.md`**: **Custom Typed Configuration Parameters** — list **HomeKit pairing slots** ( **DISCOVER** adds rows with id/name; you enter the pairing code), optional **slot**, same idea as typed lists in **udi-poly-notification**. Flat **Custom Configuration Parameters** hold `ws_host` / `ws_port` only.

Persisted pairing payloads live in custom data under **`homekit_pairings`**.

## Hub status and errors (ISY / eisy)

The controller node exposes **ST** (hub run state) and **ERR** (last reported error code). **ERR** uses ISY **UOM 25** (*index*) with profile NLS **`ERRC-*`** labels. Polyglot **Notices** carry human-readable titles and exception text for the same events.

| Driver | Meaning |
|--------|---------|
| **ST** `0` | Stopped |
| **ST** `1` | Running (bridge started successfully) |
| **ST** `2` | Error — bridge failed to start. Other faults (discover, typed save, etc.) update **ERR** and Notices only; they leave **ST** unchanged. |

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

**`report_error`** (in `nodes/Controller.py`) centralizes failure reporting: it logs with **`LOGGER.exception`** when an exception is passed, otherwise **`LOGGER.error`**; sets a Polyglot Notice under a fixed key (`homekit_bridge`, `homekit_err_discover`, `homekit_err_config`, or `homekit_meta`); and sets **ERR** to the code. Only hub-fatal start failures also set **ST** to 2. After a **successful** bridge start, **`clear_hub_error_indicators`** clears those hub error Notice keys and sets **ERR** back to 0 (it does not clear DISCOVER-related state).

On **Node Server start**, the controller clears **all** Notices before loading.

## Packaging (zip for Polyglot)

From the repo root on a Unix host (or WSL) with `zip` and optional `xmllint`:

```bash
chmod +x install.sh
./install.sh              # optional: local test install (Polyglot runs this on the host)
make check                # validate profile XML
make zip                  # produces HomeKitHub.zip (see zip_exclude.lst)
```

Install the zip via the Polyglot dashboard like other Node Servers. The archive includes `requirements.txt` and `install.sh` for Polyglot to run on the host.

## Multiple WebSocket clients

- **`handler_params`** only applies to **this** Node Server’s Polyglot **Custom Configuration Parameters**. It does not register remote plugins.
- **Other Node Servers** connect as **WebSocket clients**. The hub **fan-outs** each HAP `event` to **all** connected clients. Each client filters by `device_id` (and characteristic) in its own code.

## References

- **PG3 Python interface (udi_interface)**: [API.md](https://github.com/UniversalDevicesInc/udi_python_interface/blob/master/API.md) — `Interface` events (`CUSTOMTYPEDDATA`, **Custom** class, `load(data, save)` for persisting custom / typed data to Polyglot, etc.).

## License

MIT — see `LICENSE`.
