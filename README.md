# udi-poly-homekit

## What is this?

**udi-poly-homekit** is a **HomeKit hub plugin for Universal Devices IoX (PG3 / PG3x)**. It runs on your **eisy / Polisy**, pairs directly with HomeKit-compatible accessories on your LAN, and exposes them to IoX and other PG3 Node Servers.

**No iPhone, iPad, Mac, Apple TV, or HomePod is required.** You do not use the Apple Home app to pair accessories for this integration.

The hub is commonly used with **[udi-poly-ecobee](https://github.com/UniversalDevicesInc-PG3/udi-poly-ecobee)**: pair Ecobee thermostats here first; the Ecobee Node Server then reads and controls them over local **MQTT** (preferred) or **WebSocket**.

## Start here

**[CONFIG.md](CONFIG.md)** — step-by-step setup, especially **[Ecobee + IoX quick start](CONFIG.md#ecobee--iox-quick-start)**.

That guide covers **DISCOVER**, entering the HomeKit pairing code, verifying the hub is ready, and linking to the Ecobee plugin.

## Requirements

- **Python 3.10+** on the Polyglot host
- Polyglot runs **`install.sh`** and **`requirements.txt`** on install; you do not install Python packages manually for a normal store install

## For maintainers

- **[DEVELOPMENT.md](DEVELOPMENT.md)** — tests, lint, `make beta` / `make production` / `make release`
- **[PROTOCOL.md](PROTOCOL.md)** — WebSocket / MQTT API for integrators
- Hub status codes, full parameter tables, and troubleshooting: **[CONFIG.md](CONFIG.md)**

## License

MIT — see `LICENSE`.
