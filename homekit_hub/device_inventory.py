"""Professional device inventory JSON export (full HAP tree + plugin hints)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from homekit_hub.char_map import classify, normalize_hap_uuid
from homekit_hub.device_classifier import classify_accessories, collect_vendor_uuids, detected_roles
from homekit_hub.hap_labels import characteristic_label
from homekit_hub.paths import inventory_json_path

SCHEMA_VERSION = 1


def _value_json(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, Enum):
        return val.value
    if isinstance(val, bytes):
        try:
            return val.decode('utf-8', errors='replace')
        except Exception:
            return str(val)
    if isinstance(val, (dict, list, str, int, float, bool)):
        return val
    return str(val)


def _char_row(aid: int, svc_iid: int, ch: Any) -> Dict[str, Any]:
    type_uuid = getattr(ch, 'type', '')
    label = characteristic_label(type_uuid)
    nu = normalize_hap_uuid(type_uuid)
    bucket = classify(label or type_uuid, 0)
    row: Dict[str, Any] = {
        'aid': aid,
        'service_iid': svc_iid,
        'iid': int(getattr(ch, 'iid', 0) or 0),
        'type': label or type_uuid,
        'uuid': nu,
        'perms': [str(p) for p in (getattr(ch, 'perms', None) or [])],
        'format': str(getattr(ch, 'format', '') or ''),
        'value': _value_json(getattr(ch, 'value', None)),
        'char_bucket': bucket.value,
    }
    for key in ('minValue', 'maxValue', 'minStep', 'unit'):
        v = getattr(ch, key, None)
        if v is not None:
            row[key] = _value_json(v)
    return row


def build_device_inventory(
    *,
    device_id: str,
    alias: str,
    pairing: Any,
    reason: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    accessories_out: List[Dict[str, Any]] = []
    accessories = getattr(pairing, 'accessories', None)
    if accessories:
        for acc in accessories:
            aid = int(getattr(acc, 'aid', 0) or 0)
            acc_row: Dict[str, Any] = {'aid': aid, 'services': []}
            for svc in getattr(acc, 'services', None) or []:
                svc_row: Dict[str, Any] = {
                    'iid': int(getattr(svc, 'iid', 0) or 0),
                    'type': characteristic_label(getattr(svc, 'type', '')),
                    'characteristics': [],
                }
                for ch in getattr(svc, 'characteristics', None) or []:
                    svc_row['characteristics'].append(_char_row(aid, svc_row['iid'], ch))
                acc_row['services'].append(svc_row)
            accessories_out.append(acc_row)

    roles = detected_roles(accessories)
    classification = classify_accessories(accessories)
    vendor_uuids = collect_vendor_uuids(accessories)
    plugin_hints: Dict[str, Any] = {
        'detected_roles': roles,
        'classification': classification,
        'vendor_characteristics': vendor_uuids,
        'ai_prompt': (
            'HomeKit Hub device inventory. Use vendor_characteristics and unmapped UUIDs '
            'to author vendor-specific nodeDefs. Runtime sync uses classify_accessories(), '
            'not this file.'
        ),
    }

    return {
        'schema_version': SCHEMA_VERSION,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'reason': reason,
        'device_id': str(device_id).strip().lower(),
        'alias': alias,
        'metadata': metadata or {},
        'accessories': accessories_out,
        'detected_roles': roles,
        'plugin_hints': plugin_hints,
    }


def write_device_inventory(payload: Dict[str, Any], device_id: str) -> Path:
    path = inventory_json_path(device_id)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding='utf-8')
    return path


def export_device_inventory(
    *,
    device_id: str,
    alias: str,
    pairing: Any,
    reason: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Path:
    payload = build_device_inventory(
        device_id=device_id,
        alias=alias,
        pairing=pairing,
        reason=reason,
        metadata=metadata,
    )
    return write_device_inventory(payload, device_id)
