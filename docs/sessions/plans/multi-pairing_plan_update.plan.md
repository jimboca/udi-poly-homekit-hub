---
name: Multi-pairing plan update
overview: Clarify in [ecobee_homekit_implementation_plan.md](c:\Users\jimse\OneDrive\Documents\GitHub\ecobee_homekit_implementation_plan.md) that the HomeKit Hub must support **multiple simultaneous pairings** from day one, define persistence and UX for pairing slots, and note the required follow-up refactor of [udi-poly-homekit](c:\Users\jimse\OneDrive\Documents\GitHub\udi-poly-homekit) (today single `default` / `homekit_pairing`).
todos:
  - id: edit-implementation-plan
    content: "Revise ecobee_homekit_implementation_plan.md: Goals/Architecture + Phase 1.2–1.5, Phase 4, Risks, diagram for explicit N pairings, slot params + homekit_pairings customData"
    status: completed
  - id: hub-refactor-followup
    content: "After doc merge: refactor udi-poly-homekit bridge + Controller + CONFIG/README for multi-slot pairings (separate task)"
    status: completed
isProject: false
---

# Multi-pairing: plan document + implementation alignment

## Gap analysis

- [ecobee_homekit_implementation_plan.md](c:\Users\jimse\OneDrive\Documents\GitHub\ecobee_homekit_implementation_plan.md) **§1.3** already says “for **that slot**” and “no active pairing for **that slot**” but never defines **N concurrent accessories** or how slots map to aiohomekit aliases. The architecture diagram and exit criteria read as **one** Ecobee.
- [udi-poly-homekit/homekit_hub/bridge.py](c:\Users\jimse\OneDrive\Documents\GitHub\udi-poly-homekit\homekit_hub\bridge.py) implements **one** pairing: `PAIRING_ALIAS = "default"`, `DATA_KEY_PAIRING = "homekit_pairing"`, one `_stop_listening`, one `_pairing()`.
- [PROTOCOL.md](c:\Users\jimse\OneDrive\Documents\GitHub\udi-poly-homekit\PROTOCOL.md) is already multi-device friendly: every `event` / `command` carries **`device_id`** (AccessoryPairingID). No protocol change required for multi-pairing beyond optional hub messages (e.g. `pairing_list`) if desired later.

## 1. Updates to `ecobee_homekit_implementation_plan.md`

Add an explicit **“Multi-pairing (hub scope)”** subsection under **Goals** or **Architecture** stating:

- The Hub maintains **multiple independent HAP sessions** (one aiohomekit pairing / alias per accessory).
- Each accessory is identified on the WebSocket API by **`device_id`** = normalized **AccessoryPairingID** (lowercase), as today.
- **Fan-out**: every connected client receives events for **all** paired accessories; clients **filter** by `device_id` (already how `register_callback(device_id, ...)` is described in Phase 2).

**Phase 1** edits (concrete):

- **§1.2 Discovery**: Log/list accessories for operator setup; optionally note **child nodes per paired device** as a later UX enhancement (not required for MVP if slot-based params exist).
- **§1.3 Pairing and persistence**: Replace implied single blob with a defined model, for example:
  - **Pairing slots** `1..N` (fixed N in v1, e.g. 8–16) exposed as custom params: `hap_pin_<n>`, `accessory_id_<n>`, `accessory_name_<n>` (empty PIN for slot *n* ⇒ disassociate **only** that slot: `remove_pairing` for that alias, remove that entry from persisted data).
  - **customData** structure: `homekit_pairings` as a **JSON object** keyed by **stable slot id** (string `"1"`…`"N"`) whose value is the aiohomekit pairing dict (`AccessoryPairingID`, keys, addresses, etc.); after successful pair, reconcile **alias** in the controller with slot (e.g. alias `slot_3`).
  - Clarify: clearing PIN on one slot must **not** affect other slots.
- **§1.4**: For **each** loaded pairing: `list_accessories_and_characteristics`, subscribe all `ev` characteristics, attach **per-pairing** `dispatcher_connect` listener that broadcasts with that pairing’s `device_id`.
- **§1.5**: Command routing: resolve `device_id` → **the correct pairing object** then `put_characteristics` (already implied; make explicit).
- **Phase 4 / README-style bullets**: Document slot table for operators; discovery + id/name per slot.

**Phase 2–3**: No change to `HomeKitClient` API beyond documentation: clients already pass `device_id` per thermostat.

**Risks**: Add row for **resource / asyncio load** (many accessories) and **alias / slot bookkeeping** bugs; mitigation = bounded N, tests, clear logs per slot.

**Diagram** (optional): extend mermaid to show Hub with `Pairing1`, `Pairing2`, … to Ecobee and other accessories.

## 2. Follow-up code work (after plan approval)

Not part of the markdown edit unless you want it in the same PR:

- Refactor [bridge.py](c:\Users\jimse\OneDrive\Documents\GitHub\udi-poly-homekit\homekit_hub\bridge.py) to:
  - Track `dict[slot_id, { pairing, stop_listening }]`.
  - Loop slots in `_sync_pairing_from_params`: load from `homekit_pairings[slot]` or run discover+pair for slots with PIN.
  - On WS command, `controller.pairings[device_id]` or map `device_id` → pairing.
- Update [nodes/Controller.py](c:\Users\jimse\OneDrive\Documents\GitHub\udi-poly-homekit\nodes\Controller.py) `handler_params` snapshot to include all slot keys (or hash of serialized pairing config) so changes to **any** slot trigger restart.
- Update [CONFIG.md](c:\Users\jimse\OneDrive\Documents\GitHub\udi-poly-homekit\CONFIG.md) / [README.md](c:\Users\jimse\OneDrive\Documents\GitHub\udi-poly-homekit\README.md) to remove “one pairing” language.

## 3. Out of scope for this plan

- Changing Ecobee Phase 3 beyond noting **one Ecobee node server may use one `device_id` per thermostat** if multiple tstats are paired to the hub.
- PyPI extraction of `pg3_homekit` (unchanged).
