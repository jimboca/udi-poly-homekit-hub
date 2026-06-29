"""HAP accessory tree → IoX generic / vendor nodeDef classification."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from aiohomekit.model.characteristics import CharacteristicsTypes
from aiohomekit.model.services.service_types import ServicesTypes
from aiohomekit.uuid import normalize_uuid

from homekit_hub.char_map import normalize_hap_uuid

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

_SENSOR_SERVICE_UUIDS: Set[str] = set()
for _name in ('CONTACT_SENSOR', 'MOTION_SENSOR', 'OCCUPANCY_SENSOR'):
    if hasattr(ServicesTypes, _name):
        try:
            _SENSOR_SERVICE_UUIDS.add(normalize_uuid(getattr(ServicesTypes, _name)))
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
)


def _service_uuid(svc: Any) -> str:
    try:
        return normalize_uuid(getattr(svc, 'type', '') or '')
    except Exception:
        return ''


def _char_type_uuid(ch: Any) -> str:
    try:
        return normalize_uuid(getattr(ch, 'type', '') or '')
    except Exception:
        return ''


def _char_name_for_type(type_uuid: str) -> Optional[str]:
    nu = normalize_hap_uuid(type_uuid)
    if nu == _ECOBEE_CURRENT_MODE_UUID:
        return 'VENDOR_ECOBEE_CURRENT_MODE'
    for attr in dir(CharacteristicsTypes):
        if attr.startswith('_'):
            continue
        try:
            if normalize_uuid(getattr(CharacteristicsTypes, attr)) == nu:
                return attr
        except Exception:
            continue
    return None


def _find_char(aid: int, svc: Any, *names: str) -> Optional[Dict[str, int]]:
    want = {n.upper() for n in names}
    for ch in getattr(svc, 'characteristics', None) or []:
        label = (_char_name_for_type(getattr(ch, 'type', '')) or '').upper()
        nu = normalize_hap_uuid(getattr(ch, 'type', ''))
        if label in want or any(w in label for w in want):
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
    return out


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
                continue
            if su in _SENSOR_SERVICE_UUIDS:
                bindings = _bind_service_chars(aid, svc, _CHAR_BINDINGS_SENSOR)
                if bindings:
                    rows.append(
                        {
                            'aid': aid,
                            'role': 'binary_sensor',
                            'node_def_id': 'HKHubBinarySensor',
                            'service_iid': svc_iid,
                            'char_bindings': bindings,
                            'vendor': None,
                        }
                    )
    return rows


def detected_roles(accessories: Any) -> List[str]:
    return sorted({r['role'] for r in classify_accessories(accessories)})


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
