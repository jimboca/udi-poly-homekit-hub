# PG3 BONJOUR vs python-zeroconf: feasibility notes

These are research notes for evaluating whether PG3's [`Interface.bonjour()`](https://github.com/UniversalDevicesInc/udi_python_interface/blob/master/udi_interface/interface.py)
can replace or supplement this plugin's in-process [`AsyncZeroconf`](https://python-zeroconf.readthedocs.io/) +
[`aiohomekit`](https://github.com/Jc2k/aiohomekit) discovery.

The companion runtime tool is the `BONJOUR_COMPARE` admin command (see `nodes/Controller.py`).
That command captures a real, three-way sample (PG3 BONJOUR, raw zeroconf, aiohomekit-normalized) on
the actual LAN, and writes the result to `logs/bonjour_compare_<ts>.json`. **Conclusions in
"Decision matrix" below are tentative until at least one real run is available.**

## 1. PG3 BONJOUR API surface (confirmed)

From [`udi_interface/interface.py`](https://github.com/UniversalDevicesInc/udi_python_interface/blob/master/udi_interface/interface.py)
in the open-source Python interface:

```python
def bonjour(self, type, subtypes, protocol):
    if type is not None and not isinstance(type, str):
        raise TypeError('type must be a string')
    if subtypes is not None and not isinstance(subtypes, list):
        raise TypeError('subtypes must be an array')
    if protocol not in ['tcp', 'udp', None]:
        raise ValueError('protocol can be either "tcp", "udp"')
    message = {
        'bonjour': [{ 'type': type, 'subtypes': subtypes, 'protocol': protocol }]
    }
    self.send(message, 'command')
```

- **`type`**: service base name without leading underscore or `.local.` (e.g. `_hap`).
  PG3 (closed source) presumably appends `_protocol.local.` server-side.
- **`subtypes`**: list of DNS-SD subtypes; we pass `None`/`[]` for HAP.
- **`protocol`**: `"tcp"` | `"udp"` | `None`. `None` may or may not query both;
  the safe approach is **two calls**, one per protocol.
- **Single-shot RPC**: `bonjour()` does not return data directly. PG3 publishes the
  result to the `bonjour` MQTT topic; the interface re-publishes that as a
  `BONJOUR` pub/sub event. There is **no documented continuous browse** equivalent.

The `BONJOUR` event delivers exactly what the MQTT `bonjour` payload contains
(see [`interface.py` line ~1109](https://github.com/UniversalDevicesInc/udi_python_interface/blob/master/udi_interface/interface.py)):

```python
elif key == 'bonjour':
    pub.publish(self.BONJOUR, None, item)
```

`item` is whatever PG3 sent; the schema is **undocumented**. The handler runs on
its own thread (`Thread(target=item[0], args=[*argv]).start()`).

## 2. PG3 BONJOUR event payload (unknown - measure with `BONJOUR_COMPARE`)

The `udi_python_interface` repository has no tests or examples that exercise
`bonjour()`, and the PG3 server is not open source. Likely shapes (we will know
after the first real run):

```json
[
  { "type": "_hap._tcp.local.",
    "name": "Device-Name._hap._tcp.local.",
    "host": "device.local.",
    "port": 51826,
    "addresses": ["192.0.2.42"],
    "txt":  { "id": "AA:BB:CC:DD:EE:FF", "c#": "5", "sf": "1",
              "md": "Device", "pv": "1.1", "s#": "1", "ff": "0",
              "ci": "5" } }
]
```

The runCmd captures the **raw** payload verbatim into `logs/bonjour_compare_<ts>.json`
and into `Custom('compare')['bonjour_compare_last']` so we can iterate on the schema
without guessing.

## 3. What the plugin actually needs from zeroconf

[`HomeKitHubBridge`](homekit_hub/bridge.py) leans on `aiohomekit`, which leans on
[`python-zeroconf`](https://python-zeroconf.readthedocs.io/) in three concrete ways:

1. **`HKController(async_zeroconf_instance=AsyncZeroconf)`** — required for the IP
   transport.
2. **`ZeroconfController.async_start`** calls
   [`find_brower_for_hap_type`](https://github.com/Jc2k/aiohomekit/blob/main/aiohomekit/zeroconf.py)
   to grab a **live** `AsyncServiceBrowser`, then
   `browser.service_state_changed.register_handler(self._handle_service)` so it
   gets ongoing add/update/remove callbacks.
3. **`async_find(device_id)`** awaits a future fed by those live callbacks; the
   pairing flow (`_pair_with_pin`) and reconnection logic depend on it.

`HomeKitService.from_service_info` (called for every record found) needs:

- A `zeroconf.AsyncServiceInfo` instance (not a dict) with:
  - `service.name`, `service.type`, `service.port`
  - `service.ip_addresses_by_version(IPVersion.All)` returning at least one
    non-link-local, non-unspecified address
  - `service.decoded_properties` containing TXT key `id` (required) and
    typically `c#`, `s#`, `sf`, `md`, `pv`, `ff`, `ci`

In other words, **`aiohomekit` is tightly bound to the `zeroconf.Zeroconf`
record cache**. A drop-in replacement of that cache from a foreign source is
non-trivial.

## 4. Decision matrix (provisional)

| Strategy | Verdict | Reason |
|----------|---------|--------|
| **Replace `AsyncZeroconf` entirely with `polyglot.bonjour()`** | **Hard / not feasible against stock `aiohomekit`** | aiohomekit needs a `zeroconf.Zeroconf` cache + live `AsyncServiceBrowser`. PG3 BONJOUR is a single-shot RPC. We would have to either (a) fork aiohomekit's `ZeroconfController` to accept synthetic discoveries, or (b) feed `Zeroconf.cache` ourselves which is internal API. Both are large changes. |
| **Hybrid: BONJOUR feeds `last_hap_discover` for the UI** | **Likely feasible** | `last_hap_discover` rows only need `{id, name, paired, host, port}`. `paired` can be derived from TXT `sf` bit 0 ("Accessory has not been paired" = sf bit 0). If PG3 returns TXT keys, we can build rows. Pairing/runtime stays on `AsyncZeroconf`. Useful as a fallback when in-process mDNS is blocked. |
| **Decline** | **Possible** | If PG3 BONJOUR is missing TXT, or returns nothing for `_hap._tcp` (e.g. PG3 filters known services), there is nothing to merge. |

We cannot pick a strategy yet because (2) is unknown. The plan is:

1. Land `BONJOUR_COMPARE` so we can capture real data.
2. Run it on at least one host (eISY/Polisy/macOS) with at least one HomeKit
   accessory in pairing mode.
3. Update this document with the verdict and the concrete TXT/SRV/A coverage
   measured.

## 5. What full replacement would actually take

If a future run shows BONJOUR returns a complete record set, the work to remove
`AsyncZeroconf` from the runtime path is:

- **Patch / fork `aiohomekit`**:
  [`ZeroconfController`](https://github.com/Jc2k/aiohomekit/blob/main/aiohomekit/zeroconf.py)
  hardcodes `find_brower_for_hap_type` and reads `zc.cache.async_all_by_details(...)`.
  We would need an `AbstractController` subclass that accepts injected
  `HomeKitService` records (no `AsyncServiceInfo`, no `AsyncServiceBrowser`, no
  `Zeroconf.cache`).
- **Implement a `BonjourBrowser`** in this plugin that calls `polyglot.bonjour()`
  on a periodic schedule (long-poll) and pushes records into the custom
  controller's `discoveries` dict.
- **Pairing / `async_find`**: the new controller would await on a future fed by
  `BonjourBrowser` updates instead of zeroconf service-state callbacks.
- **`async_reachable`**: today it falls back to
  `info.async_request(zc, _TIMEOUT_MS)` — without zeroconf we would have to
  re-query via PG3 `bonjour()` and accept higher latency.

This is a large change (custom transport ~ 300-500 LOC, plus tests, plus
upstream coordination if we want it merged in `aiohomekit`). It is only worth
doing if the multicast contention story remains painful.

## 6. References

- [API.md — `polyglot.bonjour()` / `BONJOUR`](https://github.com/UniversalDevicesInc/udi_python_interface/blob/master/API.md)
- [`udi_interface/interface.py`](https://github.com/UniversalDevicesInc/udi_python_interface/blob/master/udi_interface/interface.py)
- [`aiohomekit/zeroconf.py`](https://github.com/Jc2k/aiohomekit/blob/main/aiohomekit/zeroconf.py)
- [HAP DNS-SD TXT keys](https://developer.apple.com/homekit/specification/) (Apple, Section 5)
- Companion plan: [`pg3_bonjour_vs_zeroconf`](../../.cursor/plans/pg3_bonjour_vs_zeroconf_c143bc3f.plan.md)
