"""Map a Plugins Bonjour browse of ``_hap._tcp`` into hub discover rows.

The hub's own zeroconf scan uses unicast queries because it cannot bind UDP
5353. Those queries are answered by other devices on the LAN. A HomeKit
advertisement published on this same eISY is visible to Plugins' mDNS
responder and is missing from the unicast scan. DISCOVER merges the two.
"""

from __future__ import annotations

from typing import Any, Mapping

# HomeKit status flag: bit 0 set means the accessory is not paired.
_SF_UNPAIRED = 0x01


def rows_from_bonjour_payload(payload: Any) -> list[dict[str, Any]]:
    """Turn one Plugins ``BONJOUR`` payload into discover rows.

    Accepts the PG3 reply ``{mdns: [services]}``, a list of services, or one
    service dict. Services without a HomeKit device id or an IPv4 address are
    skipped.
    """
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in _unwrap(payload):
        if not isinstance(item, Mapping):
            continue
        row = _row_from_service(item)
        if row is None:
            continue
        did = row["id"]
        if did in seen:
            continue
        seen.add(did)
        rows.append(row)
    return rows


def merge_discover_rows(
    zeroconf_rows: list[dict[str, Any]] | None,
    bonjour_rows: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Keep zeroconf rows, then append Plugins-only accessories.

    When both scans saw the same id, copy address and HAP TXT fields from the
    Plugins row onto the zeroconf row if those fields are missing. Pairing can
    then open that address after the unicast cache no longer has the accessory.
    """
    merged: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for raw in zeroconf_rows or []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        did = _norm_id(row.get("id"))
        if did:
            row["id"] = did
            by_id[did] = row
        merged.append(row)
    for raw in bonjour_rows or []:
        if not isinstance(raw, dict):
            continue
        did = _norm_id(raw.get("id"))
        if not did:
            continue
        existing = by_id.get(did)
        if existing is None:
            row = dict(raw)
            row["id"] = did
            by_id[did] = row
            merged.append(row)
            continue
        for key, value in raw.items():
            if key == "id":
                continue
            current = existing.get(key)
            if key == "port":
                if not current:
                    existing[key] = value
                continue
            if current in (None, "", [], ()):
                existing[key] = value
    return merged


def ip_discovery_from_row(ip_controller: Any, row: Mapping[str, Any]) -> Any | None:
    """Build an aiohomekit IP discovery from a saved discover row.

    ``ip_controller`` is the IP transport (``HKController.transports[IP]``),
    the same object a zeroconf discovery would use for pair-setup.
    """
    did = _norm_id(row.get("id"))
    host = str(row.get("host") or "").strip()
    try:
        port = int(row.get("port") or 0)
    except (TypeError, ValueError):
        port = 0
    if not did or not host or port <= 0:
        return None
    addresses = _address_list(row.get("addresses"))
    if host not in addresses:
        addresses.insert(0, host)

    from aiohomekit.controller.ip.discovery import IpDiscovery
    from aiohomekit.model import Categories
    from aiohomekit.model.feature_flags import FeatureFlags
    from aiohomekit.model.status_flags import StatusFlags
    from aiohomekit.zeroconf import HAP_TYPE_TCP, HomeKitService

    description = HomeKitService(
        name=str(row.get("name") or did),
        id=did,
        model=str(row.get("model") or ""),
        feature_flags=FeatureFlags(_int(row.get("feature_flags"), 0)),
        status_flags=StatusFlags(_int(row.get("status_flags"), 0 if row.get("paired") else _SF_UNPAIRED)),
        config_num=_int(row.get("config_num"), 0),
        state_num=_int(row.get("state_num"), 0),
        category=Categories(_int(row.get("category"), 1)),
        protocol_version=str(row.get("protocol_version") or "1.1"),
        type=HAP_TYPE_TCP,
        address=host,
        addresses=addresses,
        port=port,
    )
    return IpDiscovery(ip_controller, description)


def _row_from_service(item: Mapping[str, Any]) -> dict[str, Any] | None:
    txt = item.get("txt") if isinstance(item.get("txt"), Mapping) else {}
    did = _norm_id(txt.get("id") or item.get("id"))
    if not did:
        return None
    addresses = _ipv4_addresses(item.get("addresses"))
    host = addresses[0] if addresses else ""
    if not host:
        raw_host = str(item.get("host") or "").strip().rstrip(".")
        if raw_host and not raw_host.lower().endswith(".local"):
            host = raw_host
    if not host:
        return None
    try:
        port = int(item.get("port") or 0)
    except (TypeError, ValueError):
        port = 0
    if port <= 0:
        return None
    sf = _int(txt.get("sf"), _SF_UNPAIRED if not item.get("paired") else 0)
    name = str(item.get("name") or txt.get("md") or "").strip()
    return {
        "id": did,
        "name": name,
        "paired": (sf & _SF_UNPAIRED) == 0,
        "host": host,
        "port": port,
        "addresses": addresses or [host],
        "model": str(txt.get("md") or ""),
        "feature_flags": _int(txt.get("ff"), 0),
        "status_flags": sf,
        "config_num": _int(txt.get("c#"), 0),
        "state_num": _int(txt.get("s#"), 0),
        "category": _int(txt.get("ci"), 1),
        "protocol_version": str(txt.get("pv") or "1.1"),
        "source": "pg3_bonjour",
    }


def _unwrap(payload: Any) -> list[Any]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, Mapping):
        for key in ("mdns", "services", "bonjour"):
            inner = payload.get(key)
            if isinstance(inner, list):
                return inner
        if "name" in payload or "txt" in payload or "port" in payload:
            return [payload]
    return []


def _ipv4_addresses(raw: Any) -> list[str]:
    found: list[str] = []
    if isinstance(raw, (list, tuple)):
        entries = raw
    elif raw:
        entries = [raw]
    else:
        entries = []
    for entry in entries:
        text = str(entry or "").strip()
        if not _is_usable_ipv4(text) or text in found:
            continue
        found.append(text)
    return found


def _is_usable_ipv4(text: str) -> bool:
    parts = text.split(".")
    if len(parts) != 4:
        return False
    try:
        quads = [int(part) for part in parts]
    except ValueError:
        return False
    if any(quad < 0 or quad > 255 for quad in quads):
        return False
    if quads[0] in (0, 127) or text.startswith("169.254."):
        return False
    return True


def _address_list(raw: Any) -> list[str]:
    if isinstance(raw, (list, tuple)):
        return [str(item).strip() for item in raw if str(item).strip()]
    if raw:
        return [str(raw).strip()]
    return []


def _norm_id(value: Any) -> str:
    return str(value or "").strip().lower()


def _int(value: Any, default: int) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default
