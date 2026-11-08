# HomeKit Hub — support case notes

Internal reference for recurring support patterns and analyzed logs. For user-facing steps see **[DEBUGGING.md](DEBUGGING.md)**.

---

## Case: eISY DISCOVER empty — WiFi then ethernet (2026-07-05)

**Symptom:** User cannot pair; **DISCOVER** finds no accessories. Initially on WiFi; later moved eISY to ethernet — still zero results.

**Plugin:** HomeKit Hub **2.0.12**, Professional edition, eISY (FreeBSD 14).

**Log source:** `logs/debug.log` (on device). Dev copy may appear at `plugins/udi-poly-homekit-hub/debug.log`.

### Timeline

| Time (local) | Event | eISY primary IP |
|--------------|-------|-----------------|
| 08:33 | Node server start (WiFi) | `192.168.1.3` |
| 08:35 | Manual DISCOVER | `.3` → **0** accessories |
| 08:36 | Auto-discover after typed save | `.3` → **0** |
| 12:56 | Node server restart (ethernet) | `192.168.1.25` |
| 13:03–13:04 | Manual DISCOVER | `.25` → **0** accessories |

Ethernet move **confirmed** in log: `Connect: Network Interface: {'addr': '192.168.1.25', ...}` at 12:56:56.

### Hub health (good throughout)

- ST=1, GV0=1, GV1=2, ERR=0
- `bridge_loop_running = true`, `HomeKit Hub ready`
- `hap_browser_count: 2` (`_hap._tcp`, `_hap._udp`)
- Scan runs full 12s window without exception

### Zeroconf snapshot (representative — ethernet session)

```
mode=param_on; unicast=True; bind_source=auto_primary; explicit_ifaces=True;
iface_ips=['192.168.1.25']; discover_rows=0;
primary_bc=192.168.1.255; primary_local=192.168.1.25;
poly_addr=192.168.1.25; outbound_ip=192.168.1.25;
5353=bind_udp_5353_failed errno=48 Address already in use; platform=freebsd14
```

**Interpretation:**

- Auto-primary bind succeeded on `192.168.1.0/24`.
- `errno=48` on UDP 5353 is **expected** on eISY with `zeroconf_unicast=on` — not a failure mode.
- `discover_rows=0` = no Extra Discovery Networks (fine for single-LAN).
- `transports_discovery_counts: COAP=0, IP=0` = no HAP advertisements received.

### DISCOVER result (all attempts)

```
Discovery window ended: 0 unique HAP accessory(ies) in this window
HomeKit DISCOVER: scan finished, 0 accessory(ies) in result
```

### Configuration state (blocking pairing)

From `export_config_debug` after latest DISCOVER:

- **pairing_slots row 1:** `hap_pin` set, `node_key=b`, **no** `accessory_id`, `accessory_name`, `discover_endpoint`, `slot`
- **customdata:** `last_hap_discover: []`, `last_hap_discover_count = 0`
- **paired_device_ids:** empty

**Notice:**

> HomeKit pairing skipped: missing saved data — Slot 1: PIN-only row cannot be restored because customdata has no saved pairings and no last_hap_discover snapshot. Run DISCOVER, then enter/save the pairing code on the correct row.

User entered pairing code **before** DISCOVER prefilled the row.

### Conclusion

1. **Not a WiFi vs ethernet plugin bug** — bind and hub healthy on both `.3` and `.25`; zero mDNS on both.
2. **Not a zeroconf bind failure** — `auto_primary` correct each time.
3. **Root cause unsettled in log** — most likely accessory/network: not in pairing mode, still in Apple Home, on different VLAN/IoT WiFi, or mDNS blocked (AP isolation).
4. **Workflow issue confirmed** — PIN without DISCOVER data prevents pairing even if scan later succeeds.

### User guidance given / to give

1. Clear `hap_pin` on the row.
2. Remove accessory from Apple Home if applicable; power-cycle; enter HomeKit pairing mode.
3. Run **DISCOVER** on controller while pairing mode active (12s window).
4. Only after row shows id/name, enter the **current** on-device code and Save.
5. Confirm accessory is on **same routable subnet** as eISY (`192.168.1.x`). If on IoT VLAN, add subnet under **Extra Discovery Networks**, Save, **ZEROCONF_DIAG**, then **DISCOVER**.
6. Disable AP/client isolation on WiFi SSID used by accessory.
7. Sanity check: Bonjour browser on a PC on the **accessory's** network — look for `_hap._tcp`.
8. Optional: `zeroconf_unicast=auto` or `zeroconf_interfaces=all` in custom params (see CONFIG_EXTRA.md), restart NS, retry.

### Open questions for user

- Thermostat/accessory make and model (likely Ecobee given `thermostat_profile_rev=2` in typed data)?
- Accessory IP/subnet vs eISY `192.168.1.25`?
- Still paired in Apple Home?
- On guest or IoT WiFi while eISY is on main LAN ethernet?

---

## Log analysis cheat sheet

| Search term | Purpose |
|-------------|---------|
| `Connect: Network Interface` | Primary IP at NS start (detect WiFi→ethernet change) |
| `HomeKit DISCOVER: starting` | DISCOVER invoked |
| `zeroconf diag:` / `Discovery network context:` | Bind + network snapshot |
| `Discovery window ended` | Result count |
| `pairing skipped` / `PIN-only row` | PIN-before-DISCOVER |
| `export_config_debug: CONFIG` | Full config snapshot |
| `Already paired` | Accessory bonded elsewhere (if appears in discover output) |

Canonical log path on Polisy/eISY: **`logs/debug.log`** in the Node Server folder.

---

## Related releases

- **v2.0.13:** CONFIG_EXTRA split, slim UI custom params, one-time default prune. Does not change discovery mechanics; improves docs/param UX.
