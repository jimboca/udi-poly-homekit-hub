"""Plugins Bonjour browse rows used when unicast zeroconf misses a local bridge."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from homekit_hub.pg3_hap import (
    ip_discovery_from_row,
    merge_discover_rows,
    rows_from_bonjour_payload,
)


def _bridge_payload() -> dict:
    return {
        "command": "bonjour",
        "success": True,
        "mdns": [
            {
                "name": "Office Hallway",
                "host": "ecobee-artemis.local",
                "port": 38553,
                "addresses": ["192.168.1.175"],
                "txt": {"id": "6B:FF:37:1F:F1:09", "sf": "0", "ci": "9", "md": "Ecobee", "ff": "0", "pv": "1.1"},
            },
            {
                "name": "IoX Bridge 5E6BDE",
                "host": "IoX-Bridge-5E6BDE.local",
                "port": 51826,
                "addresses": ["192.168.1.89", "fe80::1"],
                "txt": {
                    "id": "72:68:00:5E:6B:DE",
                    "sf": 1,
                    "ci": 2,
                    "md": "IoX Bridge",
                    "ff": 0,
                    "pv": "1.1",
                    "c#": 4,
                    "s#": 1,
                },
            },
        ],
    }


def test_rows_keep_unpaired_local_bridge_and_skip_mdns_hostname():
    rows = rows_from_bonjour_payload(_bridge_payload())
    by_id = {row["id"]: row for row in rows}
    bridge = by_id["72:68:00:5e:6b:de"]
    assert bridge["paired"] is False
    assert bridge["host"] == "192.168.1.89"
    assert bridge["port"] == 51826
    assert bridge["category"] == 2
    assert "fe80::1" not in bridge["addresses"]
    hallway = by_id["6b:ff:37:1f:f1:09"]
    assert hallway["paired"] is True
    assert hallway["host"] == "192.168.1.175"


def test_merge_appends_plugins_only_accessory():
    zeroconf = [
        {
            "id": "6b:ff:37:1f:f1:09",
            "name": "Office Hallway",
            "paired": True,
            "host": "192.168.1.175",
            "port": 38553,
        }
    ]
    bonjour = rows_from_bonjour_payload(_bridge_payload())
    merged = merge_discover_rows(zeroconf, bonjour)
    ids = [row["id"] for row in merged]
    assert ids == ["6b:ff:37:1f:f1:09", "72:68:00:5e:6b:de"]
    assert merged[0]["host"] == "192.168.1.175"
    assert merged[1]["source"] == "pg3_bonjour"


def test_merge_fills_address_fields_onto_existing_id():
    zeroconf = [{"id": "72:68:00:5E:6B:DE", "name": "IoX Bridge 5E6BDE", "paired": False, "host": "", "port": 0}]
    bonjour = rows_from_bonjour_payload(_bridge_payload())
    merged = merge_discover_rows(zeroconf, bonjour)
    bridge = next(row for row in merged if row["id"] == "72:68:00:5e:6b:de")
    assert bridge["host"] == "192.168.1.89"
    assert bridge["port"] == 51826
    assert bridge["status_flags"] == 1
    assert any(row["id"] == "6b:ff:37:1f:f1:09" for row in merged)


def test_ip_discovery_uses_saved_ipv4():
    row = next(
        item
        for item in rows_from_bonjour_payload(_bridge_payload())
        if item["id"] == "72:68:00:5e:6b:de"
    )

    async def build():
        return ip_discovery_from_row(MagicMock(), row)

    discovery = asyncio.run(build())
    assert discovery.description.address == "192.168.1.89"
    assert discovery.description.port == 51826
    assert discovery.paired is False
