# Plugin authoring with device inventory (Professional)

Professional edition exports a full HomeKit capability snapshot per paired device under `persistent/<device_id>.json`. Use this file as the spec when authoring vendor-specific IoX nodeDefs.

## Workflow

1. Pair the accessory on a **Professional** hub (trial license works).
2. Open `persistent/<device_id>.json` in your editor (or use **Export device inventory** on the paired device node).
3. Inspect `plugin_hints.vendor_characteristics` and `plugin_hints.classification` for unmapped vendor UUIDs.
4. Add a fingerprint rule in `homekit_hub/device_classifier.py`, a nodeDef in `profile/nodedef/generic_nodedefs.xml`, a node module under `nodes/`, and any vendor branch in `homekit_hub/hap_apply.py`.
5. Enable **`generic_nodes_enable`** on the controller and **Create generic IoX control nodes (Professional)** on the pairing row, then re-pair or save config to sync nodes. Reload the **Configuration** page in your browser if the new Custom Typed column does not appear after upgrade.

Runtime node creation uses the **live** HAP tree via `device_classifier` — the JSON file is for discovery and support, not read at runtime.

## Generic IoX nodes (Professional)

| Control | Location | Default |
|---------|----------|---------|
| `generic_nodes_enable` | Controller Custom Params | `false` |
| **Create generic IoX control nodes (Professional)** (`generic_nodes`) | Custom Typed → HomeKit pairing slots | `false` |

When both are true, the hub creates child nodes (`HKHubThermostat`, `HKHubEcobeeThermostat`, `HKHubLight`, `HKHubSwitch`, `HKHubBinarySensor`) from HAP classification.

If **Create generic IoX control nodes (Professional)** is missing from **HomeKit pairing slots**, reload the **Configuration** page in your browser (full page reload, not only the table refresh button).

## Ecobee coexistence

Leave both switches **off** if `udi-poly-ecobee` already drives the thermostat. Enable both for hub-only Ecobee HomeKit control via `HKHubEcobeeThermostat` (includes comfort / `GV3`).
