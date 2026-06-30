"""HAP accessory tree → IoX generic / vendor nodeDef classification."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from aiohomekit.model.characteristics import CharacteristicsTypes
from aiohomekit.model.services.service_types import ServicesTypes
from aiohomekit.uuid import normalize_uuid

from homekit_hub.char_map import normalize_hap_uuid, thermostat_control_aid_from_snapshot_values

_ECOBEE_CURRENT_MODE_UUID = normalize_hap_uuid('B7DDB9A3-54BB-4572-91D2-F1F5B0510F8C')

_THERMOSTAT_SERVICE_UUIDS: Set[str] = set()
for _name in ('THERMOSTAT', 'HEATER_COOLER'):
    if hasattr(ServicesTypes, _name):
        try:
            _THERMOSTAT_SERVICE_UUIDS.add(normalize_uuid(getattr(ServicesTypes, _name)))
        except Exception:
            pass

_LIGHT_SERVICE_UUIDS: Set[str] = set()
if hasattr(ServicesTypes, 'LIGHTBULB'):
    try:
        _LIGHT_SERVICE_UUIDS.add(normalize_uuid(ServicesTypes.LIGHTBULB))
    except Exception:
        pass

_SWITCH_SERVICE_UUIDS: Set[str] = set()
for _name in ('SWITCH', 'OUTLET'):
    if hasattr(ServicesTypes, _name):
        try:
            _SWITCH_SERVICE_UUIDS.add(normalize_uuid(getattr(ServicesTypes, _name)))
        except Exception:
            pass

_ACCESSORY_INFO_SERVICE_UUID: Optional[str] = None
if hasattr(ServicesTypes, 'ACCESSORY_INFORMATION'):
    try:
        _ACCESSORY_INFO_SERVICE_UUID = normalize_uuid(ServicesTypes.ACCESSORY_INFORMATION)
    except Exception:
        pass

_NAME_CHAR_UUID: Optional[str] = None
if hasattr(CharacteristicsTypes, 'NAME'):
    try:
        _NAME_CHAR_UUID = normalize_uuid(CharacteristicsTypes.NAME)
    except Exception:
        pass

_CHAR_BINDINGS_THERMOSTAT = (
    'CURRENT_TEMPERATURE',
    'TARGET_TEMPERATURE',
    'HEATING_THRESHOLD',
    'COOLING_THRESHOLD',
    'TARGET_HEATING_COOLING_STATE',
    'CURRENT_HEATING_COOLING_STATE',
    'TARGET_FAN_STATE',
    'CURRENT_FAN_STATE',
    'RELATIVE_HUMIDITY',
    'TARGET_RELATIVE_HUMIDITY',
    'TEMPERATURE_DISPLAY_UNITS',
    'VENDOR_ECOBEE_CURRENT_MODE',
    'VENDOR_ECOBEE_SET_HOLD_SCHEDULE',
    'VENDOR_ECOBEE_CLEAR_HOLD',
)

_CHAR_BINDINGS_LIGHT = ('ON', 'BRIGHTNESS', 'COLOR_TEMPERATURE')
_CHAR_BINDINGS_SWITCH = ('ON',)
_CHAR_BINDINGS_SENSOR = (
    'CONTACT_STATE',
    'MOTION_DETECTED',
    'OCCUPANCY_DETECTED',
    'CURRENT_TEMPERATURE',
    'RELATIVE_HUMIDITY',
    'BATTERY_LEVEL',
    'STATUS_LO_BATT',
)

_MOTION_SENSOR_CHAR_NAMES = frozenset({'MOTION_DETECTED', 'OCCUPANCY_DETECTED'})


def _service_uuid(svc: Any) -> str:
    raw = getattr(svc, 'type', '') or ''
    try:
        return normalize_uuid(raw)
    except Exception:
        pass
    name = str(raw).strip().upper()
    if name and hasattr(ServicesTypes, name):
        try:
            return normalize_uuid(getattr(ServicesTypes, name))
        except Exception:
            pass
    return ''


def _char_type_uuid(ch: Any) -> str:
    try:
        return normalize_uuid(getattr(ch, 'type', '') or '')
    except Exception:
        return ''


def _char_name_for_type(type_uuid: str) -> Optional[str]:
    raw = str(type_uuid or '').strip()
    if not raw:
        return None
    u = raw.upper()
    if hasattr(CharacteristicsTypes, u):
        return u
    nu = normalize_hap_uuid(type_uuid)
    if not nu:
        return None
    if nu == _ECOBEE_CURRENT_MODE_UUID:
        return 'VENDOR_ECOBEE_CURRENT_MODE'
    for attr in dir(CharacteristicsTypes):
        if attr.startswith('_'):
            continue
        try:
            if normalize_hap_uuid(getattr(CharacteristicsTypes, attr)) == nu:
                return attr
        except Exception:
            continue
    return None


_CHAR_ALIAS_TO_BINDING: Dict[str, str] = {
    'TEMPERATURE_CURRENT': 'CURRENT_TEMPERATURE',
    'RELATIVE_HUMIDITY_CURRENT': 'RELATIVE_HUMIDITY',
    'HEATING_COOLING_TARGET': 'TARGET_HEATING_COOLING_STATE',
    'HEATING_COOLING_CURRENT': 'CURRENT_HEATING_COOLING_STATE',
    'TEMPERATURE_HEATING_THRESHOLD': 'HEATING_THRESHOLD',
    'TEMPERATURE_COOLING_THRESHOLD': 'COOLING_THRESHOLD',
    'TEMPERATURE_TARGET': 'TARGET_TEMPERATURE',
    'RELATIVE_HUMIDITY_TARGET': 'TARGET_RELATIVE_HUMIDITY',
    'TEMPERATURE_UNITS': 'TEMPERATURE_DISPLAY_UNITS',
    'STATUS_LOW_BATTERY': 'STATUS_LO_BATT',
}


def _binding_key_for_label(label: str) -> Optional[str]:
    u = (label or '').upper()
    if not u:
        return None
    if u in _CHAR_BINDINGS_THERMOSTAT or u in _CHAR_BINDINGS_SENSOR:
        return u
    return _CHAR_ALIAS_TO_BINDING.get(u)


def _find_char(aid: int, svc: Any, *names: str) -> Optional[Dict[str, int]]:
    want = {n.upper() for n in names}
    for ch in getattr(svc, 'characteristics', None) or []:
        label = (_char_name_for_type(getattr(ch, 'type', '')) or '').upper()
        binding_key = _binding_key_for_label(label) or label
        nu = normalize_hap_uuid(getattr(ch, 'type', ''))
        if binding_key in want or label in want or any(w in label for w in want):
            return {'aid': int(aid), 'iid': int(ch.iid)}
        if 'VENDOR_ECOBEE' in label and any('VENDOR_ECOBEE' in w for w in want):
            return {'aid': int(aid), 'iid': int(ch.iid)}
        if nu == _ECOBEE_CURRENT_MODE_UUID and 'VENDOR_ECOBEE_CURRENT_MODE' in want:
            return {'aid': int(aid), 'iid': int(ch.iid)}
    return None


def _accessory_has_ecobee_fingerprint(acc: Any) -> bool:
    for svc in getattr(acc, 'services', None) or []:
        for ch in getattr(svc, 'characteristics', None) or []:
            nu = normalize_hap_uuid(getattr(ch, 'type', ''))
            if nu == _ECOBEE_CURRENT_MODE_UUID:
                return True
            label = (_char_name_for_type(getattr(ch, 'type', '')) or '').upper()
            if 'VENDOR_ECOBEE' in label:
                return True
    return False


def _bind_service_chars(aid: int, svc: Any, names: tuple[str, ...]) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = {}
    for name in names:
        hit = _find_char(aid, svc, name)
        if hit:
            out[name] = hit
            continue
        for ch in getattr(svc, 'characteristics', None) or []:
            label = (_char_name_for_type(getattr(ch, 'type', '')) or '').upper()
            binding_key = _binding_key_for_label(label)
            if binding_key == name.upper():
                out[name] = {'aid': int(aid), 'iid': int(ch.iid)}
                break
    return out


def _bind_accessory_chars(aid: int, acc: Any, names: tuple[str, ...]) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = {}
    for svc in getattr(acc, 'services', None) or []:
        out.update(_bind_service_chars(aid, svc, names))
    return out


def _accessory_information_name(acc: Any) -> Optional[str]:
    for svc in getattr(acc, 'services', None) or []:
        if _ACCESSORY_INFO_SERVICE_UUID and _service_uuid(svc) != _ACCESSORY_INFO_SERVICE_UUID:
            continue
        for ch in getattr(svc, 'characteristics', None) or []:
            nu = normalize_hap_uuid(getattr(ch, 'type', ''))
            label = (_char_name_for_type(getattr(ch, 'type', '')) or '').upper()
            if _NAME_CHAR_UUID and nu == _NAME_CHAR_UUID:
                val = getattr(ch, 'value', None)
                if isinstance(val, str) and val.strip():
                    return val.strip()
            if label == 'NAME':
                val = getattr(ch, 'value', None)
                if isinstance(val, str) and val.strip():
                    return val.strip()
    return None


def _accessory_has_thermostat_service(acc: Any) -> bool:
    for svc in getattr(acc, 'services', None) or []:
        if _service_uuid(svc) in _THERMOSTAT_SERVICE_UUIDS:
            return True
    return False


def _thermostat_control_aid_from_accessories(accessories: Any) -> Optional[int]:
    """Lowest ``aid`` that exposes a thermostat / heater-cooler service."""
    candidates: List[int] = []
    for acc in accessories or []:
        try:
            aid = int(getattr(acc, 'aid', 0) or 0)
        except (TypeError, ValueError):
            continue
        if aid > 0 and _accessory_has_thermostat_service(acc):
            candidates.append(aid)
    if not candidates:
        return None
    return min(candidates)


def resolve_control_aid(
    accessories: Any,
    *,
    control_aid: Optional[int] = None,
    snapshot_values: Any = None,
) -> Optional[int]:
    if control_aid is not None:
        try:
            return int(control_aid)
        except (TypeError, ValueError):
            pass
    if snapshot_values:
        from_snapshot = thermostat_control_aid_from_snapshot_values(snapshot_values)
        if from_snapshot is not None:
            return int(from_snapshot)
    return _thermostat_control_aid_from_accessories(accessories)


def classify_sensor_aids(
    accessories: Any,
    *,
    control_aid: Optional[int] = None,
    snapshot_values: Any = None,
) -> List[Dict[str, Any]]:
    """
    Return per-aid sensor rows (room sensors, built-in motion child) for generic IoX nodes.

    One ``HKHubSensor`` child per non-control ``aid`` with sensor signals; optional
  ``motion_sensor`` child when motion/occupancy appears on the control ``aid``.
    """
    if not accessories:
        return []
    ctrl = resolve_control_aid(
        accessories,
        control_aid=control_aid,
        snapshot_values=snapshot_values,
    )
    rows: List[Dict[str, Any]] = []
    for acc in accessories:
        try:
            aid = int(getattr(acc, 'aid', 0) or 0)
        except (TypeError, ValueError):
            continue
        if aid <= 0:
            continue
        bindings = _bind_accessory_chars(aid, acc, _CHAR_BINDINGS_SENSOR)
        acc_name = _accessory_information_name(acc)
        if ctrl is not None and aid == ctrl:
            motion_bindings = {
                k: v for k, v in bindings.items() if k in _MOTION_SENSOR_CHAR_NAMES
            }
            if motion_bindings:
                rows.append(
                    {
                        'aid': aid,
                        'role': 'motion_sensor',
                        'node_def_id': 'HKHubSensor',
                        'service_iid': 0,
                        'char_bindings': motion_bindings,
                        'accessory_name': acc_name,
                        'vendor': None,
                    }
                )
            continue
        if bindings or acc_name:
            rows.append(
                {
                    'aid': aid,
                    'role': 'sensor',
                    'node_def_id': 'HKHubSensor',
                    'service_iid': 0,
                    'char_bindings': bindings,
                    'accessory_name': acc_name,
                    'vendor': None,
                }
            )
    return rows


def classification_diagnostic_summary(accessories: Any) -> str:
    """Explain why ``classify_accessories`` may return no rows (support logging)."""
    if not accessories:
        return 'no accessories'
    parts: List[str] = []
    for acc in accessories:
        try:
            aid = int(getattr(acc, 'aid', 0) or 0)
        except (TypeError, ValueError):
            continue
        svc_types: List[str] = []
        thermostat_hits = 0
        for svc in getattr(acc, 'services', None) or []:
            su = _service_uuid(svc)
            label = str(getattr(svc, 'type', '') or su or '?')
            if len(label) > 36:
                label = su or label[:36]
            svc_types.append(label)
            if su in _THERMOSTAT_SERVICE_UUIDS:
                thermostat_hits += 1
                bindings = _bind_service_chars(aid, svc, _CHAR_BINDINGS_THERMOSTAT)
                parts.append(
                    f'aid={aid} thermostat svc iid={getattr(svc, "iid", "?")} '
                    f'chars={len(getattr(svc, "characteristics", None) or [])} '
                    f'bindings={len(bindings)}'
                )
        if thermostat_hits == 0:
            parts.append(f'aid={aid} services={svc_types or ["none"]}')
    return '; '.join(parts) if parts else 'no accessory rows'


def classify_accessories(accessories: Any) -> List[Dict[str, Any]]:
    """Return role rows for generic IoX node creation from a live HAP tree."""
    if not accessories:
        return []
    rows: List[Dict[str, Any]] = []
    for acc in accessories:
        aid = int(getattr(acc, 'aid', 0) or 0)
        ecobee = _accessory_has_ecobee_fingerprint(acc)
        for svc in getattr(acc, 'services', None) or []:
            su = _service_uuid(svc)
            svc_iid = int(getattr(svc, 'iid', 0) or 0)
            if su in _THERMOSTAT_SERVICE_UUIDS:
                node_def = 'HKHubEcobeeThermostat' if ecobee else 'HKHubThermostat'
                bindings = _bind_service_chars(aid, svc, _CHAR_BINDINGS_THERMOSTAT)
                if bindings:
                    rows.append(
                        {
                            'aid': aid,
                            'role': 'thermostat',
                            'node_def_id': node_def,
                            'service_iid': svc_iid,
                            'char_bindings': bindings,
                            'vendor': 'ecobee' if ecobee else None,
                        }
                    )
                continue
            if su in _LIGHT_SERVICE_UUIDS:
                bindings = _bind_service_chars(aid, svc, _CHAR_BINDINGS_LIGHT)
                if bindings.get('ON'):
                    rows.append(
                        {
                            'aid': aid,
                            'role': 'light',
                            'node_def_id': 'HKHubLight',
                            'service_iid': svc_iid,
                            'char_bindings': bindings,
                            'vendor': None,
                        }
                    )
                continue
            if su in _SWITCH_SERVICE_UUIDS:
                bindings = _bind_service_chars(aid, svc, _CHAR_BINDINGS_SWITCH)
                if bindings.get('ON'):
                    rows.append(
                        {
                            'aid': aid,
                            'role': 'switch',
                            'node_def_id': 'HKHubSwitch',
                            'service_iid': svc_iid,
                            'char_bindings': bindings,
                            'vendor': None,
                        }
                    )
    return rows


def detected_roles(accessories: Any) -> List[str]:
    roles = {r['role'] for r in classify_accessories(accessories)}
    roles.update(r['role'] for r in classify_sensor_aids(accessories))
    return sorted(roles)


def collect_vendor_uuids(accessories: Any) -> List[str]:
    """Vendor / private UUID strings for plugin_hints (discovery pipeline)."""
    seen: Set[str] = set()
    out: List[str] = []
    for acc in accessories or []:
        for svc in getattr(acc, 'services', None) or []:
            for ch in getattr(svc, 'characteristics', None) or []:
                label = _char_name_for_type(getattr(ch, 'type', '')) or ''
                nu = normalize_hap_uuid(getattr(ch, 'type', ''))
                if 'VENDOR' in label.upper() or (nu and not label.startswith('000000')):
                    key = label or (nu or '')
                    if key and key not in seen:
                        seen.add(key)
                        out.append(key)
    return out
