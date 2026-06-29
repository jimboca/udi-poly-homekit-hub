"""HAP characteristic → IoX driver classification for HomeKit events."""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Dict, FrozenSet, Optional, Set

# Normalized (32 hex, no dashes) UUIDs for standard HAP characteristics we map to
# IoX drivers. Values align with aiohomekit ``CharacteristicsTypes`` (HAP R2).
_UUID_MAPPED_NORMALIZED: FrozenSet[str] = frozenset(
    {
        '000000110000100080000026bb765291',  # TEMPERATURE_CURRENT
        '000000350000100080000026bb765291',  # TEMPERATURE_TARGET
        '000000120000100080000026bb765291',  # TEMPERATURE_HEATING_THRESHOLD
        '0000000d0000100080000026bb765291',  # TEMPERATURE_COOLING_THRESHOLD
        '0000000f0000100080000026bb765291',  # HEATING_COOLING_CURRENT
        '000000330000100080000026bb765291',  # HEATING_COOLING_TARGET
        '000000100000100080000026bb765291',  # RELATIVE_HUMIDITY_CURRENT
        '000000340000100080000026bb765291',  # RELATIVE_HUMIDITY_TARGET
        '000000af0000100080000026bb765291',  # FAN_STATE_CURRENT
        '000000bf0000100080000026bb765291',  # FAN_STATE_TARGET
        '000000290000100080000026bb765291',  # ROTATION_SPEED
        '000000710000100080000026bb765291',  # OCCUPANCY_DETECTED
        '000000220000100080000026bb765291',  # MOTION_DETECTED
        '000000680000100080000026bb765291',  # BATTERY_LEVEL
        '000000790000100080000026bb765291',  # STATUS_LO_BATT
        '000000250000100080000026bb765291',  # ON
        '000000080000100080000026bb765291',  # BRIGHTNESS
        '0000006a0000100080000026bb765291',  # CONTACT_SENSOR_STATE
    }
)

# Name fragments (substring match on uppercased type string) for hubs that send
# enum-style names instead of UUIDs. Includes legacy aliases.
_MAPPED_NAME_FRAGMENTS: FrozenSet[str] = frozenset(
    {
        'TEMPERATURE_CURRENT',
        'CURRENT_TEMPERATURE',
        'TEMPERATURE_TARGET',
        'TARGET_TEMPERATURE',
        'TEMPERATURE_HEATING_THRESHOLD',
        'TEMPERATURE_COOLING_THRESHOLD',
        'HEATING_COOLING_CURRENT',
        'HEATING_COOLING_TARGET',
        'RELATIVE_HUMIDITY_CURRENT',
        'RELATIVE_HUMIDITY_TARGET',
        'FAN_STATE_CURRENT',
        'FAN_STATE_TARGET',
        'CURRENT_FAN_STATE',
        'TARGET_FAN_STATE',
        'ROTATION_SPEED',
        'OCCUPANCY_DETECTED',
        'MOTION_DETECTED',
        'BATTERY_LEVEL',
        'STATUS_LO_BATT',
        'STATUS_LOW_BATTERY',
        'BRIGHTNESS',
        'CONTACT_SENSOR_STATE',
        'CONTACT_STATE',
    }
)

# Name-style hub labels for informational characteristics (not substring ``NAME`` alone — too broad).
_INFORMATIONAL_NAME_FRAGMENTS: FrozenSet[str] = frozenset(
    {
        'MANUFACTURER',
        'MODEL',
        'SERIAL_NUMBER',
        'SERIALNUMBER',
        'FIRMWARE_REVISION',
        'HARDWARE_REVISION',
        'CONFIGURED_NAME',
        'STATUS_ACTIVE',
        'CHARGING_STATE',
        # Accessory / bridge metadata (no IoX driver); avoids HomeKit "unmapped" notices.
        'PRODUCT_DATA',
        'ACCESSORY_PROPERTIES',
        'TEMPERATURE_UNITS',
        'TEMPERATURE_DISPLAY_UNITS',
    }
)


class CharBucket(Enum):
    MAPPED = 'mapped'
    INFORMATIONAL = 'informational'
    UNKNOWN = 'unknown'


def normalize_hap_uuid(s: str) -> Optional[str]:
    """Return 32 lowercase hex chars, or None if *s* is not a HAP UUID string."""
    if not s or not isinstance(s, str):
        return None
    t = re.sub(r'[^0-9a-fA-F]', '', s.strip())
    if len(t) != 32:
        return None
    return t.lower()


# Metadata / state we do not mirror to IoX drivers: no "unmapped" notices, events are not applied
# to node drivers. "Informational" is not a claim that the HAP value is unimportant—only that
# this plugin does not map it to an IoX control today (see README, HomeKit hub mode).
_UUID_INFORMATIONAL_NORMALIZED: FrozenSet[str] = frozenset(
    {
        x
        for x in (
            normalize_hap_uuid('00000023-0000-1000-8000-0026BB765291'),  # Name
            normalize_hap_uuid('00000020-0000-1000-8000-0026BB765291'),  # Manufacturer
            normalize_hap_uuid('00000021-0000-1000-8000-0026BB765291'),  # Model
            normalize_hap_uuid('00000030-0000-1000-8000-0026BB765291'),  # Serial Number
            normalize_hap_uuid('00000052-0000-1000-8000-0026BB765291'),  # FirmwareRevision
            normalize_hap_uuid('00000053-0000-1000-8000-0026BB765291'),  # HardwareRevision
            normalize_hap_uuid('000000E3-0000-1000-8000-0026BB765291'),  # Configured Name
            normalize_hap_uuid('00000075-0000-1000-8000-0026BB765291'),  # Status Active
            normalize_hap_uuid('0000008F-0000-1000-8000-0026BB765291'),  # Charging State
            normalize_hap_uuid('A8F798E0-4A40-11E6-BDF4-0800200C9A66'),  # vendor (Ecobee / bridge)
            normalize_hap_uuid('BFE61C70-4A40-11E6-BDF4-0800200C9A66'),  # vendor (Ecobee / bridge)
            # Standard HAP (metadata / display; not mirrored to drivers).
            normalize_hap_uuid('00000036-0000-1000-8000-0026BB765291'),  # Temperature Display Units
            normalize_hap_uuid('00000037-0000-1000-8000-0026BB765291'),  # Version
            normalize_hap_uuid('000000A6-0000-1000-8000-0026BB765291'),  # Accessory Properties
            normalize_hap_uuid('00000220-0000-1000-8000-0026BB765291'),  # Product Data (HAP)
            # Vendor / bridge UUIDs seen on Ecobee via udi-poly-homekit-hub (not mapped to IoX).
            normalize_hap_uuid('34AB8811-AC7F-4340-BAC3-FD6A85F9943B'),
            normalize_hap_uuid('4A6AE4F6-036C-495D-87CC-B3702B437741'),
            normalize_hap_uuid('DB7BF261-7042-4194-8BD1-3AA22830AEDD'),
            normalize_hap_uuid('41935E3E-B54D-42E9-B8B9-D33C6319F0AF'),
        )
        if x
    }
)


def classify_uuid_normalized(normalized_uuid: str) -> Optional[CharBucket]:
    """Classify a normalized UUID. Returns None if *normalized_uuid* is not in the table."""
    if normalized_uuid in _UUID_MAPPED_NORMALIZED:
        return CharBucket.MAPPED
    if normalized_uuid in _UUID_INFORMATIONAL_NORMALIZED:
        return CharBucket.INFORMATIONAL
    return None


def mapped_uuids_normalized() -> FrozenSet[str]:
    """Return a copy of the mapped UUID set (for tests and tooling)."""
    return _UUID_MAPPED_NORMALIZED


def informational_uuids_normalized() -> FrozenSet[str]:
    """Normalized HAP UUIDs accepted as informational (metadata / vendor); no IoX driver mapping."""
    return _UUID_INFORMATIONAL_NORMALIZED


def mapped_name_fragments() -> FrozenSet[str]:
    """Fragments used for name-based classification (read-only for tests)."""
    return _MAPPED_NAME_FRAGMENTS


def _strip_noise(name: str) -> str:
    return re.sub(r'[^A-Z0-9]', '', (name or '').upper())


def normalize_characteristic_label(name: str) -> str:
    """
    Turn hub / HAP style labels into an upper snake-ish form for matching.

    Examples: ``CurrentTemperature`` → ``CURRENT_TEMPERATURE``; UUIDs unchanged.
    """
    if not name or not isinstance(name, str):
        return ''
    s = name.strip()
    if normalize_hap_uuid(s):
        return s.upper()
    s = re.sub(r'(.)([A-Z][a-z]+)', r'\1_\2', s)
    s = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s)
    return s.replace('-', '_').upper()


def classify(characteristic: str, aid: int) -> CharBucket:
    """
    Return the bucket for a characteristic type string or UUID.

    *aid* is reserved for per-accessory rules; currently unused.
    """
    del aid  # reserved
    if not characteristic:
        return CharBucket.UNKNOWN

    nu = normalize_hap_uuid(characteristic)
    if nu is not None:
        hit = classify_uuid_normalized(nu)
        if hit is not None:
            return hit
        return CharBucket.UNKNOWN

    u = (characteristic or '').upper()
    if 'VENDOR_ECOBEE' in u:
        return CharBucket.INFORMATIONAL

    norm = normalize_characteristic_label(characteristic or '')
    compact = _strip_noise(characteristic)
    hay = {u, norm, _strip_noise(norm)}
    for frag in _MAPPED_NAME_FRAGMENTS:
        f = frag.upper()
        for h in hay:
            if f in h or f in compact:
                return CharBucket.MAPPED
    # Standalone Accessory Information **Name** labels (avoid ``*_*_NAME`` false positives, e.g. HAP names).
    if norm in ('NAME', 'ACCESSORY_NAME'):
        return CharBucket.INFORMATIONAL
    for frag in _INFORMATIONAL_NAME_FRAGMENTS:
        f = frag.upper()
        for h in hay:
            if f in h or f in compact:
                return CharBucket.INFORMATIONAL
    # HAP **Version** (avoid a bare ``VERSION`` substring fragment — too easy to mis-hit).
    if norm == 'VERSION' or compact == 'VERSION':
        return CharBucket.INFORMATIONAL
    return CharBucket.UNKNOWN


def invert_mapped_uuids() -> Set[str]:
    """
    Invert the static UUID table into a mutable set (e.g. for CLIFS-style dumps).

    Prefer :func:`mapped_uuids_normalized` when immutability is enough.
    """
    return set(_UUID_MAPPED_NORMALIZED)


# HAP Accessory Information — **Name** / **Configured Name** (hub snapshot often uses UUID strings).
_HAP_UUID_ACCESSORY_NAME_NORM = normalize_hap_uuid('00000023-0000-1000-8000-0026BB765291')
_HAP_UUID_CONFIGURED_NAME_NORM = normalize_hap_uuid('000000E3-0000-1000-8000-0026BB765291')

# Normalized UUIDs for picking which ``aid`` carries thermostat controls (multi-aid Ecobee).
_HAP_NORM_HEATING_COOLING_TARGET = normalize_hap_uuid('00000033-0000-1000-8000-0026BB765291')
_HAP_NORM_HEATING_THRESHOLD = normalize_hap_uuid('00000012-0000-1000-8000-0026BB765291')
_HAP_NORM_COOLING_THRESHOLD = normalize_hap_uuid('0000000D-0000-1000-8000-0026BB765291')
_HAP_NORM_TARGET_TEMPERATURE = normalize_hap_uuid('00000035-0000-1000-8000-0026BB765291')
# aiohomekit ``CharacteristicsTypes.VENDOR_ECOBEE_CURRENT_MODE`` (climate program index).
_HAP_NORM_ECOBEE_CURRENT_MODE = normalize_hap_uuid('B7DDB9A3-54BB-4572-91D2-F1F5B0510F8C')
# Motion / occupancy on the Ecobee base often share the thermostat accessory ``aid``.
_HAP_NORM_MOTION_DETECTED = normalize_hap_uuid('00000022-0000-1000-8000-0026BB765291')
_HAP_NORM_OCCUPANCY_DETECTED = normalize_hap_uuid('00000071-0000-1000-8000-0026BB765291')
_HAP_NORM_CURRENT_TEMPERATURE = normalize_hap_uuid('00000011-0000-1000-8000-0026BB765291')
_HAP_NORM_CURRENT_RELATIVE_HUMIDITY = normalize_hap_uuid('00000010-0000-1000-8000-0026BB765291')


def is_builtin_room_sensor_signal(characteristic: str) -> bool:
    """
    Return True for motion / occupancy characteristics that should drive a sensor node even when
    the hub ``aid`` matches the thermostat accessory (Ecobee built-in occupancy / motion).
    """
    if not characteristic or not isinstance(characteristic, str):
        return False
    nu = normalize_hap_uuid(characteristic.strip())
    if nu is not None:
        return nu in (_HAP_NORM_MOTION_DETECTED, _HAP_NORM_OCCUPANCY_DETECTED)
    if classify(characteristic, 0) != CharBucket.MAPPED:
        return False
    norm = normalize_characteristic_label(characteristic)
    u = characteristic.upper()
    if 'MOTION_DETECTED' in norm or 'MOTION_DETECTED' in u:
        return True
    if ('OCCUPANCY' in norm or 'OCCUPANCY' in u) and 'TARGET' not in norm:
        return True
    return False


def builtin_motion_sensor_ambient_mirror(characteristic: str) -> bool:
    """
    Current temperature / relative humidity on the primary thermostat ``aid`` — same physical
    accessory as the built-in motion child, so mirror to the motion sensor node for ST / CLIHUM.
    """
    if not characteristic or not isinstance(characteristic, str):
        return False
    if is_builtin_room_sensor_signal(characteristic):
        return False
    nu = normalize_hap_uuid(characteristic.strip())
    if nu is not None:
        return nu in (_HAP_NORM_CURRENT_TEMPERATURE, _HAP_NORM_CURRENT_RELATIVE_HUMIDITY)
    if classify(characteristic, 0) != CharBucket.MAPPED:
        return False
    norm = normalize_characteristic_label(characteristic)
    if 'CURRENT_TEMPERATURE' in norm or norm.endswith('TEMPERATURE_CURRENT'):
        return True
    if 'RELATIVE_HUMIDITY' in norm and 'TARGET' not in norm:
        return True
    return False


def builtin_motion_sensor_mirror_characteristic(characteristic: str) -> bool:
    """Hub rows / events that should update the built-in motion :class:`HomeKitSensor` child."""
    return is_builtin_room_sensor_signal(characteristic) or builtin_motion_sensor_ambient_mirror(
        characteristic
    )


def _score_row_for_thermostat_control(characteristic: str) -> int:
    """Higher score ⇒ row belongs on the main thermostat accessory (vs room sensor / occupancy)."""
    if not characteristic or not isinstance(characteristic, str):
        return 0
    nu = normalize_hap_uuid(characteristic)
    if nu and nu == _HAP_NORM_HEATING_COOLING_TARGET:
        return 10
    if nu and nu == _HAP_NORM_HEATING_THRESHOLD:
        return 3
    if nu and nu == _HAP_NORM_COOLING_THRESHOLD:
        return 3
    if nu and nu == _HAP_NORM_TARGET_TEMPERATURE:
        return 2
    if nu and nu == _HAP_NORM_ECOBEE_CURRENT_MODE:
        return 4
    norm = normalize_characteristic_label(characteristic)
    if 'CURRENT_HEATING_COOLING' in norm:
        return 0
    if 'HEATING_COOLING_TARGET' in norm or 'TARGET_HEATING_COOLING' in norm:
        return 10
    if 'HEATING_THRESHOLD' in norm or 'HEAT_TARGET' in norm:
        return 3
    if 'COOLING_THRESHOLD' in norm or 'COOL_TARGET' in norm:
        return 3
    if 'TARGET_TEMPERATURE' in norm and 'THRESHOLD' not in norm:
        return 2
    u = characteristic.upper()
    if 'VENDOR_ECOBEE_CURRENT_MODE' in u:
        return 4
    return 0


def thermostat_control_aid_from_snapshot_values(values: Any) -> Optional[int]:
    """
    Return the ``aid`` that exposes heating/cooling controls, inferred from a hub ``snapshot``.

    Hub ``primary_aid`` (udi-poly-homekit-hub ``list_devices``) can point at a child such as
    **Occupancy** when it has the lowest non-bridge ``aid``. Thermostat setpoints and mode live
    on another ``aid``; this function finds that accessory by scoring characteristic rows.
    """
    if not isinstance(values, list) or not values:
        return None
    scores: Dict[int, int] = {}
    for row in values:
        if not isinstance(row, dict):
            continue
        try:
            aid = int(row.get('aid'))
        except (TypeError, ValueError):
            continue
        ch = row.get('characteristic')
        if not ch:
            continue
        sc = _score_row_for_thermostat_control(str(ch))
        if sc:
            scores[aid] = scores.get(aid, 0) + sc
    if not scores:
        return None
    best = max(scores.values())
    candidates = sorted(a for a, s in scores.items() if s == best)
    return candidates[0] if candidates else None


def accessory_display_name_from_snapshot_rows(rows: Any) -> Optional[str]:
    """
    Best-effort accessory label from hub ``snapshot`` / ``get`` rows for one ``aid``.

    udi-poly-homekit-hub (and aiohomekit) typically emit **Name** / **Configured Name** as full UUID
    characteristic ids. Older Ecobee HomeKit code only matched ``'NAME' in characteristic`` and
    missed those, so remote sensors fell back to ``Ecobee - Sensor aid <aid>``.

    Accessories may expose multiple **Name** characteristics (Accessory Information vs service
    labels). Prefer **Configured Name** when present; otherwise use the **Name** row with the
    lowest ``iid`` (matches Accessory Information primary ``NAME``, e.g. snapshot ``…/2``).
    """
    if not isinstance(rows, list) or not rows:
        return None
    configured: Optional[str] = None
    name_pairs: list[tuple[int, str]] = []
    for r in rows:
        if not isinstance(r, dict) or 'value' not in r:
            continue
        val = r.get('value')
        if val is None:
            continue
        s = str(val).strip()
        if not s:
            continue
        lab = str(r.get('characteristic') or '').strip()
        nu = normalize_hap_uuid(lab)
        if _HAP_UUID_CONFIGURED_NAME_NORM and nu == _HAP_UUID_CONFIGURED_NAME_NORM:
            configured = s
            continue
        u = lab.upper()
        if 'VENDOR' in u:
            continue
        norm = normalize_characteristic_label(lab)
        if norm == 'CONFIGURED_NAME' or norm.endswith('_CONFIGURED_NAME'):
            configured = s
            continue
        is_name = False
        if _HAP_UUID_ACCESSORY_NAME_NORM and nu == _HAP_UUID_ACCESSORY_NAME_NORM:
            is_name = True
        elif norm == 'NAME' or norm.endswith('_NAME'):
            is_name = True
        elif 'NAME' in u and 'PROGRAM' not in u:
            is_name = True
        if not is_name:
            continue
        try:
            iid = int(r.get('iid'))
        except (TypeError, ValueError):
            iid = 10**9
        name_pairs.append((iid, s))
    if configured:
        return configured
    name_pairs.sort(key=lambda x: x[0])
    if name_pairs:
        return name_pairs[0][1]
    return None
