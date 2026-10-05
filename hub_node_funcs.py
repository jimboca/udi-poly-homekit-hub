"""Shared helpers for HomeKit Hub IoX generic nodes (climate map, names, spans)."""

from __future__ import annotations

import re
from typing import Any, Dict, Mapping, Optional


def ltom(items):
    out: Dict[str, int] = {}
    for i, name in enumerate(items):
        out[name] = i
    return out


climateList = [
    'away',
    'home',
    'sleep',
    'smart1',
    'smart2',
    'smart3',
    'smart4',
    'smart5',
    'smart6',
    'smart7',
    'vacation',
    'smartAway',
    'smartHome',
    'demandResponse',
    'unknown',
    'wakeup',
]
climateMap = ltom(climateList)


def hap_event_matches_node(aid: int, iid: int, node: Any) -> bool:
    """True when a HAP event targets a characteristic bound to *node*.

    When ``char_bindings`` are present, match **only** those ``aid``/``iid`` pairs so
    multi-service accessories (e.g. fan + light on the same ``aid``) do not cross-apply
    events. Without bindings, fall back to primary ``aid`` equality.
    """
    try:
        ev_aid = int(aid)
        ev_iid = int(iid)
    except (TypeError, ValueError):
        return False
    bindings = getattr(node, 'char_bindings', None) or {}
    if bindings:
        for binding in bindings.values():
            if not isinstance(binding, dict):
                continue
            try:
                if int(binding.get('aid', -1)) == ev_aid and int(binding.get('iid', -1)) == ev_iid:
                    return True
            except (TypeError, ValueError):
                continue
        return False
    try:
        return ev_aid == int(getattr(node, 'aid', ev_aid))
    except (TypeError, ValueError):
        return False



def hub_write_bound_characteristic(
    controller: Any,
    device_id: str,
    char_bindings: Mapping[str, Any] | None,
    binding_key: str,
    value: Any,
    *,
    hap_name_fallback: str | None = None,
) -> bool:
    """Write a HAP characteristic using bound ``aid``/``iid`` when available.

    Falls back to ``controller.hub_write(device_id, hap_name_fallback, value)`` when
    no binding is present (or iid write is unavailable). Preferring iid avoids
    ambiguous name resolution when an accessory exposes multiple characteristics
    with the same HAP type (e.g. fan **On** + light **On**).
    """
    key = str(binding_key or '').strip().upper()
    bindings = char_bindings if isinstance(char_bindings, Mapping) else {}
    ref = bindings.get(key) if key else None
    if isinstance(ref, dict):
        try:
            aid = int(ref.get('aid', 0) or 0)
            iid = int(ref.get('iid', 0) or 0)
        except (TypeError, ValueError):
            aid = iid = 0
        if aid > 0 and iid > 0:
            by_iid = getattr(controller, 'hub_write_by_iid', None)
            if callable(by_iid):
                return bool(by_iid(str(device_id), aid, iid, value))
    if hap_name_fallback:
        by_name = getattr(controller, 'hub_write', None)
        if callable(by_name):
            return bool(by_name(str(device_id), str(hap_name_fallback), value))
    return False


def get_valid_node_name(name: str) -> str:
    name = bytes(str(name or ''), 'utf-8').decode('utf-8', 'ignore')
    return re.sub(r"[<>`~!@#$%^&*(){}[\]?/\\;:\"']+", '', name)


def toF(tempC: float) -> int:
    return int(round(float(tempC) * 1.8) + 32)


def toC(tempF: float) -> float:
    return round(((float(tempF) - 32) / 1.8) * 2) / 2


def getMapName(map_dict: Mapping[str, int], val: int) -> Optional[str]:
    val = int(val)
    for name in map_dict:
        if int(map_dict[name]) == val:
            return name
    return None


def heat_cool_min_span_degrees(use_celsius: bool, params: Optional[Mapping[str, Any]] = None) -> float:
    """Minimum heat/cool separation when writing both thresholds (matches Ecobee HK default 3 °F)."""
    raw = None
    if params:
        raw = params.get('hk_heat_cool_min_delta')
    try:
        delta = float(raw) if raw not in (None, '') else 3.0
    except (TypeError, ValueError):
        delta = 3.0
    if use_celsius:
        return max(0.5, round(delta / 1.8 * 2) / 2)
    return max(1.0, delta)
