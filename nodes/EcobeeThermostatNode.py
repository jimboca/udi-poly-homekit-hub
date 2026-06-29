#!/usr/bin/env python3
"""Ecobee HomeKit thermostat IoX node with comfort (GV3) support."""

from __future__ import annotations

from udi_interface import LOGGER

import homekit_hub.hap_apply as hap_apply
from hub_node_funcs import climateMap

from .ThermostatNode import ThermostatNode


class EcobeeThermostatNode(ThermostatNode):
    """Ecobee fingerprint thermostat — full comfort via vendor HAP characteristics."""

    id = 'HKHubEcobeeThermostat'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._hk_last_comfort_byte: int | None = None
        self._hk_sp_sig_to_gv3: dict[tuple[float, float], int] = {}
        self._hk_gv3_to_sp: dict[int, tuple[float, float]] = {}
        self._hk_vendor_comfort_sp: dict[str, tuple[float, float]] = {}
        self._hk_vendor_partial: dict[tuple[str, str], float] = {}

    def _configured_climate_refs(self) -> list[str]:
        return ['home', 'away', 'sleep', 'smart1', 'vacation', 'smartAway']

    def hk_comfort_gv3_resolver(self, hub_byte: int) -> int:
        self._hk_last_comfort_byte = int(hub_byte)
        gv3 = self._resolve_hk_comfort_gv3()
        if int(hub_byte) == hap_apply.ECOBEE_HK_COMFORT_TEMP and gv3 != int(climateMap['unknown']):
            self._remember_hk_comfort_signature(gv3)
        self.sync_clismd_from_hap_state()
        return gv3

    def refresh_gv3_after_hk_setpoint(self) -> None:
        if getattr(self, '_hk_last_comfort_byte', None) != hap_apply.ECOBEE_HK_COMFORT_TEMP:
            return
        gv3 = self._resolve_hk_comfort_gv3()
        self.set_driver_safe('GV3', gv3)

    def set_clisph(self, val: float, from_hap_c: bool = True) -> None:
        super().set_clisph(val, from_hap_c=from_hap_c)
        self.refresh_gv3_after_hk_setpoint()
        self.sync_clismd_from_hap_state()

    def set_clispc(self, val: float, from_hap_c: bool = True) -> None:
        super().set_clispc(val, from_hap_c=from_hap_c)
        self.refresh_gv3_after_hk_setpoint()
        self.sync_clismd_from_hap_state()

    def _resolve_hk_comfort_gv3(self) -> int:
        heat = cool = None
        try:
            heat = float(self.getDriver('CLISPH'))
            cool = float(self.getDriver('CLISPC'))
        except (TypeError, ValueError):
            pass
        hub_byte = self._hk_last_comfort_byte if self._hk_last_comfort_byte is not None else 0
        gv3, cache = hap_apply.resolve_hk_comfort_gv3(
            hub_byte,
            heat_sp=heat,
            cool_sp=cool,
            configured_refs=self._configured_climate_refs(),
            sp_sig_to_gv3=self._hk_sp_sig_to_gv3,
            vendor_comfort_sp=self._hk_vendor_comfort_sp,
        )
        self._hk_sp_sig_to_gv3 = cache
        return gv3

    def _remember_hk_comfort_signature(self, gv3: int) -> None:
        try:
            heat = float(self.getDriver('CLISPH'))
            cool = float(self.getDriver('CLISPC'))
        except (TypeError, ValueError):
            return
        self._hk_sp_sig_to_gv3[hap_apply.comfort_setpoint_key(heat, cool)] = int(gv3)
        self._hk_gv3_to_sp[int(gv3)] = (float(heat), float(cool))

    def remember_hk_vendor_comfort_target(self, ref: str, band: str, hap_celsius: float) -> None:
        r = str(ref or '').strip()
        b = str(band or '').strip().lower()
        if not r or b not in ('heat', 'cool'):
            return
        sp = hap_apply.driver_st_from_hap_celsius(self.use_celsius, float(hap_celsius))
        self._hk_vendor_partial[(r, b)] = float(sp)
        heat = self._hk_vendor_partial.get((r, 'heat'))
        cool = self._hk_vendor_partial.get((r, 'cool'))
        if heat is None or cool is None:
            return
        self._hk_vendor_comfort_sp[r] = (float(heat), float(cool))
        self.sync_clismd_from_hap_state()

    def _comfort_setpoints_for_gv3_command(self, gv3: int) -> tuple[float, float] | None:
        return hap_apply.resolve_gv3_comfort_setpoints(
            int(gv3),
            configured_refs=self._configured_climate_refs(),
            vendor_comfort_sp=self._hk_vendor_comfort_sp,
            program_comfort_sp={},
            gv3_to_sp=self._hk_gv3_to_sp,
            sp_sig_to_gv3=self._hk_sp_sig_to_gv3,
        )

    def _hub_write_hold_setpoints(self, heat: float, cool: float) -> bool:
        span = self._heat_cool_min_span()
        if cool < heat + span:
            cool = heat + span
        hv = hap_apply.iox_temp_to_hap_celsius(self, heat, fahrenheit_wire_bias='low')
        cv = hap_apply.iox_temp_to_hap_celsius(self, cool, fahrenheit_wire_bias='low')
        if self._hub_write(hap_apply.hap_name_heating_threshold(), hv) and self._hub_write(
            hap_apply.hap_name_cooling_threshold(), cv
        ):
            return True
        m = self._climd_write_mode()
        if m in (1, 4):
            return self._hub_write(hap_apply.hap_name_target_temperature(), hv)
        if m == 2:
            return self._hub_write(hap_apply.hap_name_target_temperature(), cv)
        return False

    def _hub_clear_hold(self) -> bool:
        c = hap_apply.hap_name_vendor_ecobee_clear_hold()
        ok = True
        for val in hap_apply.vendor_ecobee_clear_hold_wire_values():
            if not self._hub_write(c, val):
                ok = False
        return ok

    def set_clismd(self, val: int) -> None:
        self.set_driver_safe('CLISMD', int(val))

    def _hold_type_from_cmd(self, cmd: dict, default: int = 1) -> int:
        """IoX optional ``HoldType`` on multi-select commands; default hold-next when omitted."""
        query = cmd.get('query') or {}
        raw = query.get('HoldType.uom25')
        if raw is None or raw == '':
            return default
        try:
            v = int(float(raw))
        except (TypeError, ValueError):
            return default
        return v if v in (1, 2) else default

    def _mark_hold_active(self, cmd: dict | None = None, hold_type: int | None = None) -> None:
        """Setpoint / comfort holds imply a manual hold; HAP does not expose hold duration."""
        if hold_type is None:
            hold_type = self._hold_type_from_cmd(cmd or {}, default=1)
        self.set_clismd(hold_type)

    def _after_setpoint_write(self, cmd: dict) -> None:
        self._mark_hold_active(cmd)

    def sync_clismd_from_hap_state(self) -> None:
        """Update ``CLISMD`` from HAP comfort byte + active vs program setpoints."""
        hub_byte = getattr(self, '_hk_last_comfort_byte', None)
        if hub_byte is None:
            return
        try:
            cur = int(float(self.getDriver('CLISMD')))
        except (TypeError, ValueError):
            cur = 0
        try:
            heat = float(self.getDriver('CLISPH'))
            cool = float(self.getDriver('CLISPC'))
        except (TypeError, ValueError):
            heat = cool = None
        inferred = hap_apply.infer_ecobee_clismd(
            int(hub_byte),
            heat_sp=heat,
            cool_sp=cool,
            vendor_comfort_sp=self._hk_vendor_comfort_sp,
        )
        if inferred is None or cur == inferred:
            return
        if cur == 2 and inferred == 1:
            return
        self.set_clismd(inferred)

    def cmd_set_gv3(self, cmd):
        try:
            v = int(float(cmd['value']))
        except (KeyError, TypeError, ValueError):
            LOGGER.debug('cmd_set_gv3: bad value %r', cmd)
            return
        c = hap_apply.hap_name_vendor_ecobee_set_hold_schedule()
        hub_byte = hap_apply.gv3_to_ecobee_set_hold_schedule(v)
        comfort_sp = None
        if hap_apply.gv3_command_needs_setpoints(v):
            comfort_sp = self._comfort_setpoints_for_gv3_command(v)
            if comfort_sp is None:
                LOGGER.info(
                    'HomeKit %s: GV3=%s needs comfort setpoints but none cached yet',
                    self.address,
                    v,
                )
                return
        if comfort_sp is not None and not self._hub_write_hold_setpoints(comfort_sp[0], comfort_sp[1]):
            return
        if self._hub_write(c, hub_byte):
            self._hk_last_comfort_byte = int(hub_byte)
            self.set_driver_safe('GV3', v)
            if comfort_sp is not None:
                self.set_driver_safe('CLISPH', comfort_sp[0])
                self.set_driver_safe('CLISPC', comfort_sp[1])
            self._remember_hk_comfort_signature(v)
            self._mark_hold_active(cmd)

    def cmd_set_schedule_mode(self, cmd):
        try:
            v = int(float(cmd['value']))
        except (KeyError, TypeError, ValueError):
            return
        if v == 0:
            if self._hub_clear_hold():
                self.set_clismd(0)
            return
        self.set_clismd(v)

    commands = {
        **ThermostatNode.commands,
        'GV3': cmd_set_gv3,
        'CLISMD': cmd_set_schedule_mode,
    }
    drivers = ThermostatNode.drivers + [
        {'driver': 'GV3', 'value': int(climateMap['home']), 'uom': 25, 'name': 'Comfort'},
        {'driver': 'CLISMD', 'value': 0, 'uom': 25, 'name': 'Schedule mode'},
    ]
