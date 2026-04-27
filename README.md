# udi-poly-homekit

Polyglot **PG3x** Node Server: HomeKit Accessory Protocol (HAP) **controller** hub with a local **WebSocket** API for other Node Servers (e.g. `udi-poly-ecobee` in Local mode).

The hub supports **multiple simultaneous HomeKit pairings** (each row has an optional **slot** number, or the Hub assigns the next free slot). Each paired accessory is identified on the WebSocket API by its **`device_id`** (AccessoryPairingID, lowercase).

## Requirements

Install dependencies **on the Polyglot host** (see `requirements.txt`). Do not rely on a dev machine having these packages.

- Python **3.9+**
- `udi_interface`, `aiohomekit`, `websockets`

## Layout

- `homekit-poly.py` — entry point
- `homekit_hub/bridge.py` — aiohomekit + WebSocket (default port **8163**), multi-slot pairing
- `nodes/Controller.py` — PG3 lifecycle and custom params/data
- `PROTOCOL.md` — JSON message contract (`version` **1**)

## Configuration

See **`CONFIG.md`**: **Custom Typed Configuration Parameters** — list **HomeKit pairing slots** ( **DISCOVER** adds rows with id/name; you enter the pairing code), optional **slot**, same idea as typed lists in **udi-poly-notification**. Flat **Custom Configuration Parameters** hold `ws_host` / `ws_port` only.

Persisted pairing payloads live in custom data under **`homekit_pairings`**.

## Packaging (zip for Polyglot)

From the repo root on a Unix host (or WSL) with `zip` and optional `xmllint`:

```bash
chmod +x install.sh
./install.sh              # on the Polyglot machine: install requirements.txt
make check                # validate profile XML
make zip                  # produces HomeKitHub.zip (see zip_exclude.lst)
```

Install the zip via the Polyglot dashboard like other Node Servers. The archive includes `requirements.txt` and `install.sh` for the host.

## Multiple WebSocket clients

- **`handler_params`** only applies to **this** Node Server’s Polyglot **Custom Configuration Parameters**. It does not register remote plugins.
- **Other Node Servers** connect as **WebSocket clients**. The hub **fan-outs** each HAP `event` to **all** connected clients. Each client filters by `device_id` (and characteristic) in its own code.

## References

- **PG3 Python interface (udi_interface)**: [API.md](https://github.com/UniversalDevicesInc/udi_python_interface/blob/master/API.md) — `Interface` events (`CUSTOMTYPEDDATA`, **Custom** class, `load(data, save)` for persisting custom / typed data to Polyglot, etc.).

## License

MIT — see `LICENSE`.
