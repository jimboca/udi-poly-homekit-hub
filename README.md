# udi-poly-homekit-hub

## What is this?

**udi-poly-homekit-hub** is a **HomeKit hub plugin for Universal Devices IoX (PG3 / PG3x)**. It runs on your **eisy / Polisy**, pairs directly with HomeKit-compatible accessories on your LAN, and exposes them to IoX and other PG3 Node Servers.

**No iPhone, iPad, Mac, Apple TV, or HomePod is required.** You do not use the Apple Home app to pair accessories for this integration.

The hub is commonly used with **[udi-poly-ecobee](https://github.com/UniversalDevicesInc-PG3/udi-poly-ecobee)**: pair Ecobee thermostats here first; the Ecobee Node Server then reads and controls them over local **MQTT** (preferred) or **WebSocket**. You can also use the hub **without** the Ecobee plugin on **Professional** — see [Editions](#editions) below.

## Editions

PG3 sets the licensed edition from your store license (`Standard` or `Professional`). Trial licenses typically report as **Professional** for pre-purchase testing.

| Edition | Features |
|---------|----------|
| **Standard** | Multi-slot pairing, **DISCOVER**, WebSocket/MQTT transport, and **HKHubPairedDevice** child nodes (pairing slots). Enough for hub + vendor plugins (e.g. Ecobee) that talk to the hub over MQTT/WebSocket. |
| **Professional** | Everything in **Standard**, plus **device inventory** JSON (`persistent/<device_id>.json`) for support and plugin authoring, and **optional generic IoX control nodes** created directly from HomeKit — no separate vendor plugin required when you opt in. |

### Professional: generic IoX nodes (opt-in)

On **Professional**, the hub can create IoX child nodes from standard HomeKit profiles after pairing:

| Device type | IoX node (examples) |
|-------------|---------------------|
| Thermostat | **HKHubThermostat** (generic HAP) |
| Ecobee thermostat | **HKHubEcobeeThermostat** (full comfort / `GV3` when hub owns control) |
| Light | **HKHubLight** |
| Switch / outlet | **HKHubSwitch** |
| Contact, motion, occupancy, … | **HKHubBinarySensor** |

For now, only **generic** light and switch node types are supported (**HKHubLight**, **HKHubSwitch**). Capability-specific variants (e.g. on/off-only vs dimmer vs color temperature vs full color lights, or dimmable outlets) are not separate node types yet; see **[PLUGIN_AUTHORING.md](PLUGIN_AUTHORING.md)** for the planned incremental approach from device inventory.

Generic nodes are **off by default**. Enable **`generic_nodes_enable`** on the controller and **Create generic IoX control nodes (Professional)** on the pairing row in Custom Typed configuration (see **[CONFIG.md — Professional edition](CONFIG.md#professional-edition)**). Reload the **Configuration** page in your browser if those controls do not appear after upgrade. Existing sites that use **udi-poly-ecobee** or other vendor plugins can leave both off and keep using those plugins for control.

Professional also exports a full HAP capability snapshot per device (auto on pair/reconnect, or **Export device inventory** on a paired device node). See **[PLUGIN_AUTHORING.md](PLUGIN_AUTHORING.md)** for using that JSON to add vendor-specific node types over time.

## Start here

**[CONFIG.md](CONFIG.md)** — setup in order: **[Pairing accessories](CONFIG.md#pairing-accessories)** (all editions), then **[Professional edition](CONFIG.md#professional-edition)** or **[Ecobee + udi-poly-ecobee](CONFIG.md#ecobee--udi-poly-ecobee)**.

That guide covers **DISCOVER**, entering the HomeKit pairing code (use the code shown **when you enter it**), verifying the hub is ready, and linking to the Ecobee plugin or hub-only generic nodes on Professional.

**[DEBUGGING.md](DEBUGGING.md)** — when pairing or **Discover** does not work as expected.

## Requirements

- **Python 3.10+** on the Polyglot host
- Polyglot runs **`install.sh`** and **`requirements.txt`** on install; you do not install Python packages manually for a normal store install

## For maintainers

- **[DEVELOPMENT.md](DEVELOPMENT.md)** — tests, lint, `make beta` / `make production` / `make production-standard` / `make release`
- **[PROTOCOL.md](PROTOCOL.md)** — WebSocket / MQTT API for integrators
- **[PLUGIN_AUTHORING.md](PLUGIN_AUTHORING.md)** — Professional device inventory + generic IoX nodes
- Hub status codes, full parameter tables, and troubleshooting: **[CONFIG.md](CONFIG.md)**

Store builds: **`make production`** produces the Professional zip; **`make production-standard`** produces a Standard zip with Professional-only source stripped.

## License

MIT — see `LICENSE`.
