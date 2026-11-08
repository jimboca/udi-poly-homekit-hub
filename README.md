# udi-poly-homekit-hub

## What is this?

**udi-poly-homekit-hub** is a **HomeKit hub plugin for Universal Devices IoX (PG3 / PG3x)**. It runs on your **eisy / Polisy**, pairs directly with HomeKit-compatible accessories on your LAN, and exposes them to IoX and other PG3 Node Servers.

**No iPhone, iPad, Mac, Apple TV, or HomePod is required.** You do not use the Apple Home app to pair accessories for this integration.

The hub is commonly used with **[udi-poly-ecobee](https://github.com/UniversalDevicesInc-PG3/udi-poly-ecobee)**: pair Ecobee thermostats here first; the Ecobee Node Server then reads and controls them over local **MQTT** (preferred) or **WebSocket**. The hub answers that client's hello from the accessories it already has cached, then sends an updated device list when a live HomeKit read finishes. You can also use the hub **without** the Ecobee plugin on **Professional** — see [Editions](#editions) below.

**DISCOVER** also asks Plugins to browse HomeKit advertisements. That is how the hub finds an **IoX HomeKit Bridge** running on this same eISY. Turn the bridge's **Advertise** on, run **DISCOVER**, then enter the bridge setup code. See [CONFIG.md](CONFIG.md).

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
| Color light | **HKHubColorLight** |
| Fan | **HKHubFan** |
| Switch / outlet | **HKHubSwitch** |
| Valve | **HKHubValve** |
| Garage door | **HKHubGarageDoor** |
| Contact, motion, occupancy, … | **HKHubBinarySensor** |

Generic **light**, **color light**, **fan**, **switch**, **valve**, and **garage door** node types are supported. They sit under the pairing node, which IoX keeps as its own parent so those children can be added. A motion-only accessory is a **HomeKit Motion Sensor**. A valve reports **Closed** or **Open**. **Open** and **Close** write the HomeKit valve. A HomeKit garage door (for example ratgdo32 firmware) reports **Door State**, **Obstruction**, and **Lock** when the accessory has a lock. **Open** and **Close** move the door. A color light reports **Brightness**, **Hue**, **Saturation**, and **Color Temperature**. When a device's HomeKit type changes, the existing IoX node is replaced with the new type at the same address. See **[PLUGIN_AUTHORING.md](PLUGIN_AUTHORING.md)** for further node types.

Honeywell T9 remote sensors and the built-in motion child report **Temperature** and **Humidity** from HomeKit. Fahrenheit rounding matches the Ecobee plugin. See **[CONFIG.md — supported generic nodes](CONFIG.md#supported-generic-iox-nodes-included-devices)**.

Generic nodes are **off by default**. Enable **`generic_nodes_enable`** on the controller and **Create generic IoX control nodes (Professional)** on the pairing row in Custom Typed configuration (see **[CONFIG.md — Professional edition](CONFIG.md#professional-edition)**). Reload the **Configuration** page in your browser if those controls do not appear after upgrade. Existing sites that use **udi-poly-ecobee** or other vendor plugins can leave both off and keep using those plugins for control.

Professional also exports a full HAP capability snapshot per device (auto on pair/reconnect, or **Export device inventory** on a paired device node). See **[PLUGIN_AUTHORING.md](PLUGIN_AUTHORING.md)** for using that JSON to add vendor-specific node types over time.

## Start here

**[CONFIG.md](CONFIG.md)** — setup in order: **[Pairing accessories](CONFIG.md#pairing-accessories)** (all editions), then **[Professional edition](CONFIG.md#professional-edition)** or **[Ecobee + udi-poly-ecobee](CONFIG.md#ecobee--udi-poly-ecobee)**.

That guide covers **DISCOVER**, entering the HomeKit pairing code (use the code shown **when you enter it**), verifying the hub is ready, and linking to the Ecobee plugin or hub-only generic nodes on Professional. **Unpair device** on a pairing node deletes the nodes under it, then the pairing node, and removes that slot from configuration.

**[DEBUGGING.md](DEBUGGING.md)** — when pairing or **Discover** does not work as expected.

## Requirements

- **Python 3.10+** on the Polyglot host
- Polyglot runs **`install.sh`** and **`requirements.txt`** on install; you do not install Python packages manually for a normal store install

## For maintainers

- **[DEVELOPMENT.md](DEVELOPMENT.md)** — tests, lint, `make beta` / `make production` / `make production-standard` / `make release`
- **[PROTOCOL.md](PROTOCOL.md)** — WebSocket / MQTT API for integrators
- **[PLUGIN_AUTHORING.md](PLUGIN_AUTHORING.md)** — Professional device inventory + generic IoX nodes
- Hub status codes, full parameter tables, and troubleshooting: **[CONFIG.md](CONFIG.md)**

Store builds: **`make production`** → `HomeKitHub-Production-Professional-<VERSION>.zip`; **`make production-standard`** → `HomeKitHub-Production-Standard-<VERSION>.zip`.

## License

MIT — see `LICENSE`.
