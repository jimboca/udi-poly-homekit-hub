"""Apply HomeKit hub characteristic values to :class:`nodes.backends.homekit.Thermostat.HomeKitThermostat` drivers."""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple, TYPE_CHECKING

from hub_node_funcs import climateMap, getMapName, hap_event_matches_node, toF

from homekit_hub.char_map import CharBucket, classify, normalize_characteristic_label, normalize_hap_uuid

if TYPE_CHECKING:
    from typing import Any as HomeKitThermostat

_LOG = logging.getLogger(__name__)

# aiohomekit ``CharacteristicsTypes.VENDOR_ECOBEE_CURRENT_MODE`` — Ecobee comfort index on the wire.
_ECOBEE_CURRENT_MODE_UUID_NORM = normalize_hap_uuid('B7DDB9A3-54BB-4572-91D2-F1F5B0510F8C')

# Ecobee HAP vendor comfort bytes (``VENDOR_ECOBEE_CURRENT_MODE`` / ``VENDOR_ECOBEE_SET_HOLD_SCHEDULE``):
# 0 = Home, 1 = Sleep, 2 = Away, 3 = Temp (thermostat “Temp” / custom comfort on the device).
# IoX ``GV3`` uses the same indices as cloud: ``node_funcs.climateMap`` / ``climateList`` order.
_ECOBEE_HK_COMFORT_HOME = 0
_ECOBEE_HK_COMFORT_SLEEP = 1
_ECOBEE_HK_COMFORT_AWAY = 2
ECOBEE_HK_COMFORT_TEMP = 3

# Comfort refs mapped directly by HAP bytes 0–2; byte 3 (Temp) covers all other configured comforts.
_HAP_DIRECT_COMFORT_REFS = frozenset({'home', 'away', 'sleep'})

# Thermostat ``TargetFanState`` (HAP UUID …BF…). Ecobee uses **0 = On, 1 = Auto**; IoX ``CLIFS`` matches
# cloud ``fanMap`` (**auto = 0**, **on = 1**).
_HAP_TARGET_FAN_STATE_UUID_NORM = normalize_hap_uuid('000000BF-0000-1000-8000-0026BB765291')
_HAP_CURRENT_FAN_STATE_UUID_NORM = normalize_hap_uuid('000000AF-0000-1000-8000-0026BB765291')
_HAP_HEATING_COOLING_TARGET_UUID_NORM = normalize_hap_uuid('00000033-0000-1000-8000-0026BB765291')
_HAP_CURRENT_HEATING_COOLING_UUID_NORM = normalize_hap_uuid('0000000F-0000-1000-8000-0026BB765291')
_HAP_ON_UUID_NORM = normalize_hap_uuid('00000025-0000-1000-8000-0026BB765291')
_HAP_BRIGHTNESS_UUID_NORM = normalize_hap_uuid('00000008-0000-1000-8000-0026BB765291')
_HAP_CONTACT_SENSOR_STATE_UUID_NORM = normalize_hap_uuid('0000006A-0000-1000-8000-0026BB765291')


def is_ecobee_current_mode_characteristic(characteristic: str) -> bool:
    """True for Ecobee vendor ``VENDOR_ECOBEE_CURRENT_MODE`` (name or UUID); value is remapped to IoX ``GV3``."""
    if not characteristic:
        return False
    if 'VENDOR_ECOBEE_CURRENT_MODE' in (characteristic or '').upper():
        return True
    nu = normalize_hap_uuid(characteristic)
    return bool(nu and nu == _ECOBEE_CURRENT_MODE_UUID_NORM)


def ecobee_hk_comfort_to_gv3(hub_byte: int) -> int:
    """Map Ecobee HAP comfort byte → IoX ``GV3`` (``climateMap`` index). Unknown high values pass through."""
    b = int(hub_byte)
    if b == _ECOBEE_HK_COMFORT_HOME:
        return int(climateMap['home'])
    if b == _ECOBEE_HK_COMFORT_SLEEP:
        return int(climateMap['sleep'])
    if b == _ECOBEE_HK_COMFORT_AWAY:
        return int(climateMap['away'])
    if b == ECOBEE_HK_COMFORT_TEMP:
        return int(climateMap['smart1'])
    return b


def comfort_setpoint_key(heat_sp: float, cool_sp: float) -> Tuple[float, float]:
    """Round setpoints for stable (heat, cool) signatures when disambiguating HAP Temp mode."""
    return (round(float(heat_sp), 1), round(float(cool_sp), 1))


def hk_temp_mode_extra_refs(configured_refs: Sequence[str]) -> Tuple[str, ...]:
    """Configured comfort refs that share HAP byte 3 (Temp), in thermostat order."""
    out: list[str] = []
    for ref in configured_refs:
        r = str(ref or '').strip()
        if r and r not in _HAP_DIRECT_COMFORT_REFS:
            out.append(r)
    return tuple(out)


def resolve_hk_comfort_gv3(
    hub_byte: int,
    *,
    heat_sp: Optional[float] = None,
    cool_sp: Optional[float] = None,
    configured_refs: Optional[Sequence[str]] = None,
    sp_sig_to_gv3: Optional[Mapping[Tuple[float, float], int]] = None,
    vendor_comfort_sp: Optional[Mapping[str, Tuple[float, float]]] = None,
) -> Tuple[int, Dict[Tuple[float, float], int]]:
    """
    Map Ecobee HAP comfort byte → IoX ``GV3``.

    Bytes 0–2 are fixed (home / sleep / away). Byte 3 (Temp) covers manual holds and extra
    comforts; active setpoints are matched against vendor program targets, then a learned
    signature cache. Unmatched manual holds map to ``unknown`` (display **Temp** on Ecobee).
    """
    b = int(hub_byte)
    if b != ECOBEE_HK_COMFORT_TEMP:
        return ecobee_hk_comfort_to_gv3(b), dict(sp_sig_to_gv3 or {})

    temp_gv3 = int(climateMap['unknown'])
    cache: Dict[Tuple[float, float], int] = dict(sp_sig_to_gv3 or {})
    if heat_sp is None or cool_sp is None:
        return temp_gv3, cache

    sig = comfort_setpoint_key(heat_sp, cool_sp)
    vendor = dict(vendor_comfort_sp or {})
    refs = [str(r or '').strip() for r in (configured_refs or ()) if str(r or '').strip()]

    for ref in refs:
        if ref in vendor and sig == comfort_setpoint_key(*vendor[ref]):
            return int(climateMap[ref]), cache

    if sig in cache:
        cached = int(cache[sig])
        if not vendor:
            return cached, cache
        if cached == temp_gv3:
            return cached, cache
        ref = getMapName(climateMap, cached)
        if ref in vendor:
            if sig == comfort_setpoint_key(*vendor[ref]):
                return cached, cache
        elif cached == int(climateMap['smart1']):
            if 'smart1' in vendor and sig == comfort_setpoint_key(*vendor['smart1']):
                return cached, cache
        elif ref and ref not in _HAP_DIRECT_COMFORT_REFS:
            return cached, cache

    return temp_gv3, cache


# aiohomekit ``VENDOR_ECOBEE_*_TARGET_HEAT/COOL`` → ``climateRef`` (HomeKit snapshot only exposes these three).
_VENDOR_COMFORT_TARGET_PREFIXES: Tuple[Tuple[str, str], ...] = (
    ('VENDOR_ECOBEE_HOME_TARGET_HEAT', 'home'),
    ('VENDOR_ECOBEE_HOME_TARGET_COOL', 'home'),
    ('VENDOR_ECOBEE_SLEEP_TARGET_HEAT', 'sleep'),
    ('VENDOR_ECOBEE_SLEEP_TARGET_COOL', 'sleep'),
    ('VENDOR_ECOBEE_AWAY_TARGET_HEAT', 'away'),
    ('VENDOR_ECOBEE_AWAY_TARGET_COOL', 'away'),
)

# Comfort refs whose IoX ``GV3`` maps to HAP Away (2) but need explicit setpoints (not plain Away).
_GV3_AWAY_COLLISION_REFS = frozenset({'vacation', 'smartAway', 'demandResponse'})


def parse_ecobee_vendor_comfort_target(characteristic: str) -> Optional[Tuple[str, str]]:
    """
    Parse Ecobee vendor comfort target characteristic → ``(climateRef, 'heat'|'cool')``.

    Returns ``None`` when *characteristic* is not a known ``VENDOR_ECOBEE_*_TARGET_*`` name.
    """
    u = (characteristic or '').upper().replace('-', '_')
    if 'VENDOR_ECOBEE' not in u or 'TARGET' not in u:
        return None
    for prefix, ref in _VENDOR_COMFORT_TARGET_PREFIXES:
        if u == prefix or u.endswith(prefix):
            band = 'heat' if prefix.endswith('_HEAT') else 'cool'
            return ref, band
    return None


def gv3_to_comfort_ref(gv3: int, configured_refs: Optional[Sequence[str]] = None) -> Optional[str]:
    """
    Map IoX ``GV3`` index → Ecobee ``climateRef`` for setpoint lookup.

    When the HK command editor sends catalog index **smart1** (3) but the thermostat has no
    ``smart1`` comfort, use the first configured extra comfort (same order as status disambiguation).
    """
    ref = getMapName(climateMap, int(gv3))
    if not ref:
        return None
    configured = [str(r or '').strip() for r in (configured_refs or ()) if str(r or '').strip()]
    if ref in configured:
        return ref
    if int(gv3) == int(climateMap['smart1']):
        extras = hk_temp_mode_extra_refs(configured)
        if extras:
            return extras[0]
    return ref


def gv3_command_needs_setpoints(gv3: int) -> bool:
    """True when ``SET_HOLD_SCHEDULE`` alone is not enough — comfort setpoints must be written too."""
    g = int(gv3)
    if gv3_to_ecobee_set_hold_schedule(g) == ECOBEE_HK_COMFORT_TEMP:
        return True
    ref = getMapName(climateMap, g)
    if ref in _GV3_AWAY_COLLISION_REFS:
        return True
    if ref and ref.startswith('smart') and ref not in _HAP_DIRECT_COMFORT_REFS:
        return True
    if ref == 'unknown':
        return True
    return False


def resolve_gv3_comfort_setpoints(
    gv3: int,
    *,
    configured_refs: Optional[Sequence[str]] = None,
    vendor_comfort_sp: Optional[Mapping[str, Tuple[float, float]]] = None,
    program_comfort_sp: Optional[Mapping[str, Tuple[float, float]]] = None,
    gv3_to_sp: Optional[Mapping[int, Tuple[float, float]]] = None,
    sp_sig_to_gv3: Optional[Mapping[Tuple[float, float], int]] = None,
) -> Optional[Tuple[float, float]]:
    """
    Resolve IoX heat/cool setpoints for a ``GV3`` comfort command.

    Priority: per-``GV3`` cache → inverted signature cache → vendor snapshot targets
    (home/away/sleep) → stored Ecobee program setpoints (cloud discover).
    """
    g = int(gv3)
    if gv3_to_sp and g in gv3_to_sp:
        heat, cool = gv3_to_sp[g]
        return float(heat), float(cool)

    ref = gv3_to_comfort_ref(g, configured_refs)
    alt_gv = int(climateMap[ref]) if ref and ref in climateMap else None
    if alt_gv is not None and alt_gv != g and gv3_to_sp and alt_gv in gv3_to_sp:
        heat, cool = gv3_to_sp[alt_gv]
        return float(heat), float(cool)

    for sig, cached_gv in (sp_sig_to_gv3 or {}).items():
        if int(cached_gv) == g:
            return float(sig[0]), float(sig[1])
    if alt_gv is not None:
        for sig, cached_gv in (sp_sig_to_gv3 or {}).items():
            if int(cached_gv) == alt_gv:
                return float(sig[0]), float(sig[1])

    if ref and vendor_comfort_sp and ref in vendor_comfort_sp:
        heat, cool = vendor_comfort_sp[ref]
        return float(heat), float(cool)

    if ref and program_comfort_sp and ref in program_comfort_sp:
        heat, cool = program_comfort_sp[ref]
        return float(heat), float(cool)

    return None


def gv3_to_ecobee_set_hold_schedule(gv3: int) -> int:
    """Map IoX ``GV3`` → Ecobee ``VENDOR_ECOBEE_SET_HOLD_SCHEDULE`` byte (HAP **0–3** only).

    IoX uses full ``climateList`` indices (e.g. ``vacation`` = 10). Values outside 0–3 were
    previously forwarded as-is and the accessory rejected them (**-70410** invalid write).
    """
    g = int(gv3)
    if g == int(climateMap['home']):
        return _ECOBEE_HK_COMFORT_HOME
    if g == int(climateMap['sleep']):
        return _ECOBEE_HK_COMFORT_SLEEP
    if g == int(climateMap['away']):
        return _ECOBEE_HK_COMFORT_AWAY
    if g == int(climateMap['smart1']):
        return ECOBEE_HK_COMFORT_TEMP
    for name in ('smart2', 'smart3', 'smart4', 'smart5', 'smart6', 'smart7'):
        if g == int(climateMap[name]):
            return ECOBEE_HK_COMFORT_TEMP
    if g == int(climateMap['vacation']):
        return _ECOBEE_HK_COMFORT_AWAY
    if g == int(climateMap['smartAway']):
        return _ECOBEE_HK_COMFORT_AWAY
    if g == int(climateMap['smartHome']):
        return _ECOBEE_HK_COMFORT_HOME
    if g == int(climateMap['demandResponse']):
        return _ECOBEE_HK_COMFORT_AWAY
    if g == int(climateMap['unknown']):
        return ECOBEE_HK_COMFORT_TEMP
    if g == int(climateMap['wakeup']):
        return _ECOBEE_HK_COMFORT_HOME
    _LOG.debug(
        'gv3_to_ecobee_set_hold_schedule: GV3=%s not in climateMap hold mapping; using TEMP (3)',
        g,
    )
    return ECOBEE_HK_COMFORT_TEMP


def hap_target_fan_state_to_clifs(hap_val: int) -> int:
    """HAP ``TargetFanState`` (Ecobee: 0 = On, 1 = Auto) → IoX ``CLIFS`` (cloud ``fanMap``: auto = 0, on = 1)."""
    v = int(hap_val)
    if v == 1:
        return 0
    if v == 0:
        return 1
    return v


def clifs_to_hap_fan_target(clifs: int) -> int:
    """IoX ``CLIFS`` → HAP ``TargetFanState`` (inverse of :func:`hap_target_fan_state_to_clifs`)."""
    v = int(clifs)
    if v == 0:
        return 1
    if v == 1:
        return 0
    return v


# HAP TargetHeatingCoolingMode / HeatingCoolingTarget style values → IoX CLIMD (subset 0–4).
_HAP_MODE_TO_CLIMD = {0: 0, 1: 1, 2: 2, 3: 3}


def hap_current_heating_cooling_to_clihcs(v: int, *, four_value_encoding: bool) -> int:
    """
    Map HAP ``CurrentHeatingCoolingState`` / ``HEATING_COOLING_CURRENT`` → IoX ``CLIHCS``
    (``EN_ECOHCS``: 0 Idle, 1 Heat, 2 Cool, …).

    **Standard HAP** (Apple thermostat, Ecobee): 0 = Off, 1 = Heat, 2 = Cool. Some stacks send
    3 as an alias for Cool — treat as Cool.

    **Extended** (rare): 0 = Off, 1 = Idle, 2 = Heat, 3 = Cool. When *four_value_encoding* is
    True (we observed value 3 at least once on this node), use this mapping.
    """
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 0
    if n < 0:
        return 0
    if four_value_encoding:
        return {0: 0, 1: 0, 2: 1, 3: 2}.get(n, 0)
    if n >= 3:
        return 2
    return {0: 0, 1: 1, 2: 2}.get(n, 0)


def hap_current_fan_state_to_clifrs(v: int) -> int:
    """HAP ``CurrentFanState``: 0 Inactive, 1 Idle, 2 Blowing → IoX ``CLIFRS`` (editor subset 0/1)."""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 0
    return 1 if n >= 2 else 0


def driver_st_from_hap_celsius(use_celsius: bool, celsius: float) -> float:
    if use_celsius:
        return round(float(celsius) * 2) / 2
    return float(toF(float(celsius)))


def _driver_st_to_hap_c(node: 'HomeKitThermostat', driver_val: float) -> float:
    if node.use_celsius:
        return float(driver_val)
    return (float(driver_val) - 32) / 1.8


def _climd_mode(node: 'HomeKitThermostat') -> int:
    """IoX CLIMD: 0 off, 1 heat, 2 cool, 3 auto, 4 aux; default auto if unknown."""
    try:
        raw = node.getDriver('CLIMD')
        if raw is None:
            return 3
        return int(float(raw))
    except (TypeError, ValueError):
        return 3


def _is_homekit_thermostat(node: Any) -> bool:
    name = type(node).__name__
    return name in ('ThermostatNode', 'EcobeeThermostatNode', 'HomeKitThermostat')


def apply_characteristic_to_thermostat(
    node: 'HomeKitThermostat',
    characteristic: str,
    value: Any,
    *,
    log: Optional[logging.Logger] = None,
) -> bool:
    """
    Map one HAP characteristic to IoX drivers. Returns True if *characteristic* was handled
    (including INFORMATIONAL / unknown no-ops that were classified).
    """
    lg = log or _LOG
    # Vendor UUID is not in :func:`classify` mapped table → ``UNKNOWN``; handle before classify.
    if value is not None and is_ecobee_current_mode_characteristic(characteristic):
        try:
            raw = int(value)
        except (TypeError, ValueError):
            try:
                raw = int(float(value))
            except (TypeError, ValueError):
                return True
        resolver = getattr(node, 'hk_comfort_gv3_resolver', None)
        if _is_homekit_thermostat(node) and callable(resolver):
            gv3 = int(resolver(int(raw)))
        else:
            gv3 = ecobee_hk_comfort_to_gv3(int(raw))
        node.set_driver_safe('GV3', gv3)
        return True

    if value is not None:
        vendor_target = parse_ecobee_vendor_comfort_target(characteristic)
        if vendor_target is not None:
            ref, band = vendor_target
            remember = getattr(node, 'remember_hk_vendor_comfort_target', None)
            if _is_homekit_thermostat(node) and callable(remember):
                try:
                    remember(ref, band, float(value))
                except (TypeError, ValueError):
                    pass
            return True

    bucket = classify(characteristic, 0)
    if bucket == CharBucket.UNKNOWN:
        return False
    if bucket == CharBucket.INFORMATIONAL:
        return True
    if value is None:
        return True

    norm = normalize_characteristic_label(characteristic or '')
    nu_chr = normalize_hap_uuid(characteristic or '')

    try:
        if 'CURRENT_TEMPERATURE' in norm or norm.endswith('TEMPERATURE_CURRENT'):
            node.set_st(driver_st_from_hap_celsius(node.use_celsius, float(value)))
            return True
        if (
            nu_chr == _HAP_CURRENT_HEATING_COOLING_UUID_NORM
            or 'CURRENT_HEATING_COOLING' in norm
            or 'HEATING_COOLING_CURRENT' in norm
        ):
            try:
                v = int(value)
            except (TypeError, ValueError):
                v = int(float(value))
            if v == 3:
                try:
                    setattr(node, '_hap_cur_hc_four_value', True)
                except Exception:
                    pass
            four = bool(getattr(node, '_hap_cur_hc_four_value', False))
            node.set_clihcs(hap_current_heating_cooling_to_clihcs(v, four_value_encoding=four))
            return True
        if (
            nu_chr == _HAP_HEATING_COOLING_TARGET_UUID_NORM
            or 'HEATING_COOLING_TARGET' in norm
            or 'TARGET_HEATING_COOLING' in norm
            or ('HEATING_COOLING' in norm and 'CURRENT' not in norm)
        ):
            try:
                v = int(value)
            except (TypeError, ValueError):
                v = int(float(value))
            node.set_climd(_HAP_MODE_TO_CLIMD.get(v, v))
            return True
        if 'HEATING_THRESHOLD' in norm or 'HEAT_TARGET' in norm:
            node.set_clisph(driver_st_from_hap_celsius(node.use_celsius, float(value)), from_hap_c=False)
            return True
        if 'COOLING_THRESHOLD' in norm or 'COOL_TARGET' in norm:
            node.set_clispc(driver_st_from_hap_celsius(node.use_celsius, float(value)), from_hap_c=False)
            return True
        if (
            ('TARGET_TEMPERATURE' in norm or 'TEMPERATURE_TARGET' in norm)
            and 'THRESHOLD' not in norm
        ):
            # TargetTemperature / TEMPERATURE_TARGET: in **Auto** (CLIMD 3), Ecobee still emits this
            # alongside separate heating/cooling thresholds. Mirroring the target to *both* IoX
            # setpoints desyncs one side when the hub sends TargetTemperature and only one threshold
            # afterward (see logs: cool stuck at ~73°F while heat updates to 70°F).
            mode = _climd_mode(node)
            if mode == 3:
                return True
            t = driver_st_from_hap_celsius(node.use_celsius, float(value))
            if mode in (1, 4):
                node.set_clisph(t, from_hap_c=False)
                return True
            if mode == 2:
                node.set_clispc(t, from_hap_c=False)
                return True
            return True
        if 'RELATIVE_HUMIDITY' in norm and 'TARGET' in norm:
            node.set_driver_safe('GV1', int(round(float(value))))
            return True
        if 'RELATIVE_HUMIDITY' in norm:
            node.set_driver_safe('CLIHUM', int(round(float(value))))
            return True
        if (
            nu_chr == _HAP_TARGET_FAN_STATE_UUID_NORM
            or 'TARGET_FAN' in norm
            or ('FAN' in norm and 'TARGET' in norm)
        ):
            try:
                v = int(value)
            except (TypeError, ValueError):
                v = int(float(value))
            node.set_clifs(hap_target_fan_state_to_clifs(v))
            return True
        if (
            nu_chr == _HAP_CURRENT_FAN_STATE_UUID_NORM
            or 'CURRENT_FAN' in norm
            or ('FAN' in norm and 'CURRENT' in norm)
        ):
            try:
                v = int(value)
            except (TypeError, ValueError):
                v = int(float(value))
            node.set_clifrs(hap_current_fan_state_to_clifrs(v))
            return True
    except Exception:
        lg.debug('hap_apply failed for %s=%r', characteristic, value, exc_info=True)
        return True

    lg.debug('MAPPED but unhandled characteristic %s norm=%s', characteristic, norm)
    return True


def _fahrenheit_hap_c_bins(target_f: int) -> tuple[list[float], list[float]]:
    """Return (floor_display_bins, tof_roundtrip_bins) of 0.1 °C for a whole °F target."""
    k0 = int(round((int(target_f) - 32) / 1.8 * 10.0))
    floor_bins: list[float] = []
    tof_bins: list[float] = []
    for dk in range(-40, 41):
        c = (k0 + dk) / 10.0
        if toF(c) == int(target_f):
            tof_bins.append(c)
        if int(c * 1.8 + 32) == int(target_f):
            floor_bins.append(c)
    return floor_bins, tof_bins


def iox_temp_to_hap_celsius(
    node: 'HomeKitThermostat',
    driver_val: float,
    *,
    fahrenheit_wire_bias: Optional[str] = None,
) -> float:
    """IoX thermostat temp driver → HAP **celsius** for ``put_characteristics``.

    Round to **0.1 °C** so accessories (e.g. Ecobee) do not reject long binary floats (-70410).

    **Fahrenheit:** Ecobee's thermostat UI shows ``int(C * 1.8 + 32)`` (truncated exact °F), not
    :func:`hub_node_funcs.toF` round-trip. For ``fahrenheit_wire_bias='low'``, pick the **lowest**
    0.1 °C bin whose truncated display equals the target (e.g. **76 °F → 24.5 °C**, not 24.2 °C
    which displays **75**). Fall back to the lowest ``toF``-compatible bin when needed. ``high``
    keeps the highest ``toF`` bin for troubleshooting.
    """
    if node.use_celsius:
        c = float(driver_val)
        return round(float(c) * 10.0) / 10.0

    if fahrenheit_wire_bias in ('low', 'high'):
        t = int(round(float(driver_val)))
        floor_bins, tof_bins = _fahrenheit_hap_c_bins(t)
        if fahrenheit_wire_bias == 'low':
            if floor_bins:
                return min(floor_bins)
            if tof_bins:
                return min(tof_bins)
        else:
            if tof_bins:
                return max(tof_bins)
            if floor_bins:
                return max(floor_bins)

    c = _driver_st_to_hap_c(node, driver_val)
    return round(float(c) * 10.0) / 10.0


def climd_to_hap_target_mode(climd: int) -> int:
    return int(climd) if int(climd) in (0, 1, 2, 3) else 0


# aiohomekit ``CharacteristicsTypes`` attribute names for hub ``command`` / ``get`` (or a HAP UUID).
# udi-poly-homekit-hub resolves these via ``hasattr(CharacteristicsTypes, spec)`` — not Apple PascalCase.
def hap_name_target_heating_cooling() -> str:
    return 'HEATING_COOLING_TARGET'


def hap_name_target_temperature() -> str:
    return 'TEMPERATURE_TARGET'


def hap_name_heating_threshold() -> str:
    return 'TEMPERATURE_HEATING_THRESHOLD'


def hap_name_cooling_threshold() -> str:
    return 'TEMPERATURE_COOLING_THRESHOLD'


def hap_name_target_fan_state() -> str:
    return 'FAN_STATE_TARGET'


def hap_name_vendor_ecobee_set_hold_schedule() -> str:
    """Ecobee vendor: request a comfort / schedule hold (uint8 index). Writable via HomeKit."""
    return 'VENDOR_ECOBEE_SET_HOLD_SCHEDULE'


def hap_name_vendor_ecobee_current_mode() -> str:
    """Ecobee vendor: current comfort / program index (``GV3``). Readable via hub ``get`` / snapshot."""
    return 'VENDOR_ECOBEE_CURRENT_MODE'


def hap_name_vendor_ecobee_clear_hold() -> str:
    """Ecobee vendor: cancel manual hold and resume the programmed schedule (HAP button)."""
    return 'VENDOR_ECOBEE_CLEAR_HOLD'


def vendor_ecobee_clear_hold_wire_values() -> tuple[bool, ...]:
    """Wire values for :func:`hap_name_vendor_ecobee_clear_hold`.

    Ecobee often ignores a lone ``true`` press; ``false`` then ``true`` matches Home Assistant's
    proven HomeKit controller sequence and reliably clears the hold.
    """
    return (False, True)


def infer_ecobee_clismd(
    hub_comfort_byte: int,
    *,
    heat_sp: Optional[float] = None,
    cool_sp: Optional[float] = None,
    vendor_comfort_sp: Optional[Mapping[str, Tuple[float, float]]] = None,
) -> Optional[int]:
    """Infer IoX ``CLISMD`` from Ecobee HAP comfort byte and active vs program setpoints.

    HAP does not expose hold duration. **0** = Running (schedule), **1** = Hold Next.
    Returns ``None`` when hold state cannot be determined.
    """
    b = int(hub_comfort_byte)
    vendor = dict(vendor_comfort_sp or {})
    direct_ref = {_ECOBEE_HK_COMFORT_HOME: 'home', _ECOBEE_HK_COMFORT_SLEEP: 'sleep', _ECOBEE_HK_COMFORT_AWAY: 'away'}.get(b)

    if heat_sp is None or cool_sp is None:
        if b == ECOBEE_HK_COMFORT_TEMP:
            return 1
        if direct_ref:
            return 0
        return None

    sig = comfort_setpoint_key(float(heat_sp), float(cool_sp))
    if b == ECOBEE_HK_COMFORT_TEMP:
        for ref in ('home', 'sleep', 'away'):
            if ref in vendor and sig == comfort_setpoint_key(*vendor[ref]):
                return 0
        return 1

    if direct_ref:
        if direct_ref not in vendor:
            return 0
        if sig == comfort_setpoint_key(*vendor[direct_ref]):
            return 0
        return 1

    return None


def is_hap_on_characteristic(characteristic: str) -> bool:
    """True for HAP **On** (power state); avoids loose ``'ON' in label`` false positives."""
    if not characteristic:
        return False
    nu = normalize_hap_uuid(characteristic)
    if nu and nu == _HAP_ON_UUID_NORM:
        return True
    norm = normalize_characteristic_label(characteristic)
    return norm in ('ON', 'POWER_STATE')


def is_hap_brightness_characteristic(characteristic: str) -> bool:
    if not characteristic:
        return False
    nu = normalize_hap_uuid(characteristic)
    if nu and nu == _HAP_BRIGHTNESS_UUID_NORM:
        return True
    norm = normalize_characteristic_label(characteristic)
    return 'BRIGHTNESS' in norm


def is_hap_contact_state_characteristic(characteristic: str) -> bool:
    if not characteristic:
        return False
    nu = normalize_hap_uuid(characteristic)
    if nu and nu == _HAP_CONTACT_SENSOR_STATE_UUID_NORM:
        return True
    norm = normalize_characteristic_label(characteristic)
    return 'CONTACT_SENSOR_STATE' in norm or norm == 'CONTACT_STATE'


def hap_on_to_iox(value: Any) -> int:
    """HAP bool **On** → IoX on/off driver (0 = Off, 1 = On)."""
    if isinstance(value, bool):
        return 1 if value else 0
    try:
        return 1 if int(value) else 0
    except (TypeError, ValueError):
        return 1 if value else 0


def iox_on_to_hap(iox_val: Any) -> bool:
    """IoX on/off driver → HAP **On** bool."""
    try:
        return int(iox_val) != 0
    except (TypeError, ValueError):
        return bool(iox_val)


def hap_brightness_to_iox(value: Any) -> int:
    """HAP **Brightness** (0–100) → IoX dimmer driver."""
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return 0


def hap_contact_state_to_iox(value: Any) -> int:
    """HAP **ContactSensorState** (0 = detected, 1 = not) → IoX on/off (1 = detected)."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return 0
    return 1 if v == 0 else 0


def _set_node_driver(node: Any, driver: str, val: Any, *, report: bool = True) -> None:
    setter = getattr(node, 'set_driver_safe', None)
    if callable(setter):
        setter(driver, val, report=report)
        return
    node.setDriver(driver, val, report=report, force=True)


def hap_name_on() -> str:
    return 'ON'


def hap_name_brightness() -> str:
    return 'BRIGHTNESS'


def apply_characteristic_to_light(
    node: Any,
    characteristic: str,
    value: Any,
    *,
    log: Optional[logging.Logger] = None,
) -> bool:
    """Map HAP **On** / **Brightness** to HKHubLight IoX drivers."""
    lg = log or _LOG
    if value is None:
        if is_hap_on_characteristic(characteristic) or is_hap_brightness_characteristic(characteristic):
            return True
        bucket = classify(characteristic, 0)
        return bucket != CharBucket.UNKNOWN
    try:
        if is_hap_on_characteristic(characteristic):
            _set_node_driver(node, 'ST', hap_on_to_iox(value))
            return True
        if is_hap_brightness_characteristic(characteristic):
            _set_node_driver(node, 'GV0', hap_brightness_to_iox(value))
            return True
    except Exception:
        lg.debug('light hap_apply failed for %s=%r', characteristic, value, exc_info=True)
        return True
    bucket = classify(characteristic, 0)
    if bucket == CharBucket.UNKNOWN:
        return False
    return True


def apply_characteristic_to_switch(
    node: Any,
    characteristic: str,
    value: Any,
    *,
    log: Optional[logging.Logger] = None,
) -> bool:
    """Map HAP **On** to HKHubSwitch IoX ``ST``."""
    lg = log or _LOG
    if not is_hap_on_characteristic(characteristic):
        bucket = classify(characteristic, 0)
        return bucket == CharBucket.INFORMATIONAL
    if value is None:
        return True
    try:
        _set_node_driver(node, 'ST', hap_on_to_iox(value))
    except Exception:
        lg.debug('switch hap_apply failed for %s=%r', characteristic, value, exc_info=True)
    return True


def apply_characteristic_to_binary_sensor(
    node: Any,
    characteristic: str,
    value: Any,
    *,
    log: Optional[logging.Logger] = None,
) -> bool:
    """Map HAP sensor characteristics to HKHubBinarySensor IoX drivers."""
    lg = log or _LOG
    bucket = classify(characteristic, 0)
    if bucket == CharBucket.UNKNOWN:
        return False
    if bucket == CharBucket.INFORMATIONAL:
        return True
    if value is None:
        return True
    norm = normalize_characteristic_label(characteristic or '')
    use_c = bool(getattr(node, 'use_celsius', False))
    try:
        if 'CURRENT_TEMPERATURE' in norm or norm.endswith('TEMPERATURE_CURRENT'):
            _set_node_driver(node, 'ST', driver_st_from_hap_celsius(use_c, float(value)))
            return True
        if 'RELATIVE_HUMIDITY' in norm and 'TARGET' not in norm:
            _set_node_driver(node, 'CLIHUM', int(round(float(value))))
            return True
        if is_hap_contact_state_characteristic(characteristic):
            _set_node_driver(node, 'GV2', hap_contact_state_to_iox(value))
            return True
        if 'OCCUPANCY' in norm or 'MOTION_DETECTED' in norm:
            _set_node_driver(node, 'GV1', hap_on_to_iox(value))
            return True
    except Exception:
        lg.debug('binary_sensor hap_apply failed for %s=%r', characteristic, value, exc_info=True)
        return True
    return True


def apply_characteristic_to_sensor(
    node: Any,
    characteristic: str,
    value: Any,
    *,
    log: Optional[logging.Logger] = None,
) -> bool:
    """Backward-compatible alias for :func:`apply_characteristic_to_binary_sensor`."""
    return apply_characteristic_to_binary_sensor(node, characteristic, value, log=log)


def _snapshot_char_priority(characteristic: str) -> int:
    """Order hub snapshot rows so mode/setpoints precede comfort and derived state."""
    norm = normalize_characteristic_label(characteristic or '')
    u = (characteristic or '').upper()
    if 'TEMPERATURE_DISPLAY' in norm:
        return 0
    if 'HEATING_COOLING_TARGET' in norm or 'TARGET_HEATING_COOLING' in norm:
        return 10
    if 'HEATING_THRESHOLD' in norm or 'HEAT_TARGET' in norm:
        return 20
    if 'COOLING_THRESHOLD' in norm or 'COOL_TARGET' in norm:
        return 21
    if ('TARGET_TEMPERATURE' in norm or 'TEMPERATURE_TARGET' in norm) and 'THRESHOLD' not in norm:
        return 25
    if 'CURRENT_HEATING_COOLING' in norm or 'HEATING_COOLING_CURRENT' in norm:
        return 30
    if 'CURRENT_TEMPERATURE' in norm or norm.endswith('TEMPERATURE_CURRENT'):
        return 40
    if 'TARGET_FAN' in norm or ('FAN' in norm and 'TARGET' in norm):
        return 50
    if 'CURRENT_FAN' in norm or ('FAN' in norm and 'CURRENT' in norm):
        return 55
    if 'RELATIVE_HUMIDITY' in norm:
        return 60
    if 'VENDOR_ECOBEE' in u and 'TARGET' in u:
        return 85
    if is_ecobee_current_mode_characteristic(characteristic):
        return 90
    if is_hap_on_characteristic(characteristic):
        return 45
    if is_hap_brightness_characteristic(characteristic):
        return 46
    if is_hap_contact_state_characteristic(characteristic):
        return 47
    if 'MOTION_DETECTED' in norm or 'OCCUPANCY' in norm:
        return 48
    return 50


def apply_snapshot_rows_to_generic_node(
    node: Any,
    rows: Sequence[Mapping[str, Any]],
    *,
    log: Optional[logging.Logger] = None,
) -> int:
    """
    Apply hub ``snapshot`` / ``get`` rows to one generic IoX node.

    Returns the number of characteristic values applied.
    """
    lg = log or _LOG
    if not rows:
        return 0
    applicable: list[tuple[int, int, Any, str]] = []
    for row in rows:
        if not isinstance(row, dict) or 'value' not in row:
            continue
        try:
            aid = int(row.get('aid'))
            iid = int(row.get('iid'))
        except (TypeError, ValueError):
            continue
        if not hap_event_matches_node(aid, iid, node):
            continue
        label = str(row.get('characteristic') or '')
        applicable.append((aid, iid, row.get('value'), label))
    applicable.sort(key=lambda item: (_snapshot_char_priority(item[3]), item[0], item[1]))
    applied = 0
    for aid, iid, value, label in applicable:
        norm = normalize_characteristic_label(label)
        if 'TEMPERATURE_DISPLAY' in norm:
            try:
                node.use_celsius = int(value) == 0
            except (TypeError, ValueError):
                pass
            continue
        try:
            handler = getattr(node, 'on_hap_event', None)
            if callable(handler):
                handler(aid, iid, value, label)
                applied += 1
                continue
            apply_fn = getattr(node, 'apply_hub_characteristic', None)
            if callable(apply_fn) and apply_fn(label, value):
                applied += 1
        except Exception:
            lg.debug(
                'snapshot apply failed for %s %s=%r',
                getattr(node, 'address', '?'),
                label,
                value,
                exc_info=True,
            )
    return applied
