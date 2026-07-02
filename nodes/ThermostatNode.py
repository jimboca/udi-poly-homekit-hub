#!/usr/bin/env python3
"""Generic HomeKit thermostat IoX node (Professional)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from udi_interface import LOGGER, Node

import homekit_hub.hap_apply as hap_apply
from hub_node_funcs import get_valid_node_name, hap_event_matches_node, heat_cool_min_span_degrees

if TYPE_CHECKING:
    from .Controller import Controller


class ThermostatNode(Node):
    """Vendor-neutral HAP thermostat mapped to HKHubThermostat drivers."""

    hint = '0x010c0100'
    id = 'HKHubThermostat'

    def __init__(
        self,
        controller: 'Controller',
        address: str,
        name: str,
        *,
        device_id: str,
        aid: int,
        char_bindings: Dict[str, Dict[str, int]],
        use_celsius: bool = False,
    ):
        self.controller = controller
        self.device_id = str(device_id).strip().lower()
        self.aid = int(aid)
        self.char_bindings = dict(char_bindings or {})
        self.use_celsius = bool(use_celsius)
        self._hap_cur_hc_four_value = False
        nm = get_valid_node_name(name) or 'HK Thermostat'
        # Self-parented primary node so room sensors can nest underneath (udi-poly-ecobee HK pattern).
        super().__init__(controller.poly, address, address, nm)
        self.isPrimary = True
        self.name = nm
        self.setDriver('ST', 0, report=False, force=True)

    def set_driver_safe(self, driver: str, val: Any, report: bool = True) -> None:
        try:
            self.setDriver(driver, val, report=report, force=True)
        except Exception:
            LOGGER.debug('setDriver %s=%r failed for %s', driver, val, self.address, exc_info=True)

    def set_st(self, val: float) -> None:
        self.set_driver_safe('ST', val)

    def set_climd(self, val: int) -> None:
        self.set_driver_safe('CLIMD', int(val))

    def set_clihcs(self, val: int) -> None:
        self.set_driver_safe('CLIHCS', int(val))

    def set_clifs(self, val: int) -> None:
        self.set_driver_safe('CLIFS', int(val))

    def set_clifrs(self, val: int) -> None:
        self.set_driver_safe('CLIFRS', int(val))

    def set_clisph(self, val: float, from_hap_c: bool = True) -> None:
        _ = from_hap_c
        self.set_driver_safe('CLISPH', float(val))

    def set_clispc(self, val: float, from_hap_c: bool = True) -> None:
        _ = from_hap_c
        self.set_driver_safe('CLISPC', float(val))

    def apply_hub_characteristic(self, characteristic: str, value: Any) -> bool:
        return hap_apply.apply_characteristic_to_thermostat(
            self, characteristic, value, log=LOGGER
        )

    def on_hap_event(self, aid: int, iid: int, value: Any, label: str) -> None:
        if not hap_event_matches_node(aid, iid, self):
            return
        self.apply_hub_characteristic(label, value)

    def _hub_write(self, hap_name: str, hap_value: Any) -> bool:
        binding_key = self._HAP_WRITE_BINDING_KEY.get(hap_name)
        if binding_key:
            ref = self._char_binding_ref(binding_key)
            if ref is not None:
                try:
                    aid = int(ref['aid'])
                    iid = int(ref['iid'])
                except (KeyError, TypeError, ValueError):
                    aid = iid = 0
                if aid > 0 and iid > 0:
                    ok = self.controller.hub_write_by_iid(
                        self.device_id, aid, iid, hap_value
                    )
                    if not ok:
                        LOGGER.info(
                            'HomeKit %s: %s write failed aid=%s iid=%s value=%r',
                            self.address,
                            binding_key,
                            aid,
                            iid,
                            hap_value,
                        )
                    return ok
        ok = self.controller.hub_write(self.device_id, hap_name, hap_value)
        if not ok:
            LOGGER.info(
                'HomeKit %s: %s write failed value=%r',
                self.address,
                hap_name,
                hap_value,
            )
        return ok

    def _climd_write_mode(self) -> int:
        try:
            return int(float(self.getDriver('CLIMD')))
        except (TypeError, ValueError):
            return 3

    def _heat_cool_min_span(self) -> float:
        return heat_cool_min_span_degrees(self.use_celsius, self.controller._bridge_get_params())

    def _has_char_binding(self, name: str) -> bool:
        bindings = getattr(self, 'char_bindings', None) or {}
        return str(name or '').strip().upper() in bindings

    def _char_binding_ref(self, name: str) -> dict | None:
        bindings = getattr(self, 'char_bindings', None) or {}
        hit = bindings.get(str(name or '').strip().upper())
        return hit if isinstance(hit, dict) else None

    def _hap_min_step_for_hap_name(self, hap_name: str) -> float | None:
        binding_key = self._HAP_WRITE_BINDING_KEY.get(hap_name)
        if not binding_key:
            return None
        ref = self._char_binding_ref(binding_key)
        if not ref:
            return None
        ms = ref.get('minStep')
        if ms is None:
            return None
        try:
            step = float(ms)
        except (TypeError, ValueError):
            return None
        return step if step > 0 else None

    def _iox_temp_to_hap_wire(self, driver_val: float, hap_name: str) -> float:
        return hap_apply.iox_temp_to_hap_celsius(
            self,
            driver_val,
            fahrenheit_wire_bias='low',
            hap_min_step=self._hap_min_step_for_hap_name(hap_name),
        )

    def _after_setpoint_write(self, cmd: dict) -> None:
        """Hook after a successful CLISPH/CLISPC hub write (Ecobee: schedule hold)."""

    def _hap_char_for_heat_driver_write(self) -> str:
        m = self._climd_write_mode()
        if m in (2, 3):
            return hap_apply.hap_name_heating_threshold()
        if self._has_char_binding('TARGET_TEMPERATURE'):
            return hap_apply.hap_name_target_temperature()
        if self._has_char_binding('HEATING_THRESHOLD'):
            return hap_apply.hap_name_heating_threshold()
        return hap_apply.hap_name_target_temperature()

    def _hap_char_for_cool_driver_write(self) -> str:
        m = self._climd_write_mode()
        if m == 3:
            return hap_apply.hap_name_cooling_threshold()
        if m == 2:
            if self._has_char_binding('TARGET_TEMPERATURE'):
                return hap_apply.hap_name_target_temperature()
            if self._has_char_binding('COOLING_THRESHOLD'):
                return hap_apply.hap_name_cooling_threshold()
            return hap_apply.hap_name_target_temperature()
        if m in (1, 4):
            return hap_apply.hap_name_cooling_threshold()
        if self._has_char_binding('COOLING_THRESHOLD'):
            return hap_apply.hap_name_cooling_threshold()
        return hap_apply.hap_name_target_temperature()

    _HAP_WRITE_BINDING_KEY = {
        hap_apply.hap_name_target_temperature(): 'TARGET_TEMPERATURE',
        hap_apply.hap_name_heating_threshold(): 'HEATING_THRESHOLD',
        hap_apply.hap_name_cooling_threshold(): 'COOLING_THRESHOLD',
        hap_apply.hap_name_target_heating_cooling(): 'TARGET_HEATING_COOLING_STATE',
        hap_apply.hap_name_target_fan_state(): 'TARGET_FAN_STATE',
    }

    def query(self, cmd=None):
        del cmd
        refresh_device = getattr(self.controller, 'refresh_device_generic_nodes', None)
        if callable(refresh_device):
            refresh_device(self.device_id)
            return
        refresh = getattr(self.controller, 'refresh_generic_node', None)
        if callable(refresh):
            refresh(self)
        else:
            self.reportDrivers()

    def cmd_set_pf(self, cmd):
        driver = cmd.get('cmd')
        if driver == 'CLISPH':
            heat = float(cmd['value'])
            m = self._climd_write_mode()
            span = self._heat_cool_min_span()
            if m == 3:
                cool = float(self.getDriver('CLISPC'))
                if cool < heat + span:
                    cool = heat + span
                hv = self._iox_temp_to_hap_wire(heat, hap_apply.hap_name_heating_threshold())
                cv = self._iox_temp_to_hap_wire(cool, hap_apply.hap_name_cooling_threshold())
                if self._hub_write(hap_apply.hap_name_heating_threshold(), hv) and self._hub_write(
                    hap_apply.hap_name_cooling_threshold(), cv
                ):
                    self.set_clisph(heat)
                    self.set_clispc(cool)
                    self._after_setpoint_write(cmd)
                return
            c = self._hap_char_for_heat_driver_write()
            v = self._iox_temp_to_hap_wire(heat, c)
            if self._hub_write(c, v):
                self.set_clisph(heat)
                self._after_setpoint_write(cmd)
        elif driver == 'CLISPC':
            cool = float(cmd['value'])
            m = self._climd_write_mode()
            span = self._heat_cool_min_span()
            if m == 3:
                heat = float(self.getDriver('CLISPH'))
                if heat > cool - span:
                    heat = cool - span
                hv = self._iox_temp_to_hap_wire(heat, hap_apply.hap_name_heating_threshold())
                cv = self._iox_temp_to_hap_wire(cool, hap_apply.hap_name_cooling_threshold())
                if self._hub_write(hap_apply.hap_name_heating_threshold(), hv) and self._hub_write(
                    hap_apply.hap_name_cooling_threshold(), cv
                ):
                    self.set_clisph(heat)
                    self.set_clispc(cool)
                    self._after_setpoint_write(cmd)
                return
            c = self._hap_char_for_cool_driver_write()
            v = self._iox_temp_to_hap_wire(cool, c)
            if self._hub_write(c, v):
                self.set_clispc(cool)
                self._after_setpoint_write(cmd)
        elif driver == 'CLIFS':
            v = int(cmd['value'])
            if self._hub_write(hap_apply.hap_name_target_fan_state(), hap_apply.clifs_to_hap_fan_target(v)):
                self.set_clifs(v)

    def cmd_set_mode(self, cmd):
        v = hap_apply.climd_to_hap_target_mode(int(cmd['value']))
        if self._hub_write(hap_apply.hap_name_target_heating_cooling(), v):
            self.set_climd(int(cmd['value']))

    def cmd_set_humidity(self, cmd):
        if self._hub_write('TARGET_RELATIVE_HUMIDITY', int(cmd['value'])):
            self.set_driver_safe('GV1', int(cmd['value']))

    def set_point(self, cmd):
        step = float(cmd.get('value', 1))
        if cmd.get('cmd') == 'DIM':
            step = -step
        mode = self._climd_write_mode()
        if mode == 0:
            return
        span = self._heat_cool_min_span()
        if mode == 3:
            heat = float(self.getDriver('CLISPH')) + step
            cool = float(self.getDriver('CLISPC')) + step
            if cool < heat + span:
                cool = heat + span
            hv = self._iox_temp_to_hap_wire(heat, hap_apply.hap_name_heating_threshold())
            cv = self._iox_temp_to_hap_wire(cool, hap_apply.hap_name_cooling_threshold())
            if self._hub_write(hap_apply.hap_name_heating_threshold(), hv) and self._hub_write(
                hap_apply.hap_name_cooling_threshold(), cv
            ):
                self.set_clisph(heat)
                self.set_clispc(cool)
                self._after_setpoint_write(cmd)
            return
        if mode in (1, 4):
            nxt = float(self.getDriver('CLISPH')) + step
            c = self._hap_char_for_heat_driver_write()
            v = self._iox_temp_to_hap_wire(nxt, c)
            if self._hub_write(c, v):
                self.set_clisph(nxt)
                self._after_setpoint_write(cmd)
            return
        nxt = float(self.getDriver('CLISPC')) + step
        c = self._hap_char_for_cool_driver_write()
        v = self._iox_temp_to_hap_wire(nxt, c)
        if self._hub_write(c, v):
            self.set_clispc(nxt)
            self._after_setpoint_write(cmd)

    commands = {
        'QUERY': query,
        'CLISPH': cmd_set_pf,
        'CLISPC': cmd_set_pf,
        'CLIFS': cmd_set_pf,
        'CLIMD': cmd_set_mode,
        'GV1': cmd_set_humidity,
        'BRT': set_point,
        'DIM': set_point,
    }
    drivers = [
        {'driver': 'ST', 'value': 0, 'uom': 17, 'name': 'Temperature'},
        {'driver': 'CLISPH', 'value': 68, 'uom': 17, 'name': 'Heat setpoint'},
        {'driver': 'CLISPC', 'value': 74, 'uom': 17, 'name': 'Cool setpoint'},
        {'driver': 'CLIMD', 'value': 3, 'uom': 67, 'name': 'Mode'},
        {'driver': 'CLIFS', 'value': 0, 'uom': 68, 'name': 'Fan mode'},
        {'driver': 'CLIHUM', 'value': 0, 'uom': 22, 'name': 'Humidity'},
        {'driver': 'CLIHCS', 'value': 0, 'uom': 25, 'name': 'HVAC state'},
        {'driver': 'CLIFRS', 'value': 0, 'uom': 80, 'name': 'Fan state'},
        {'driver': 'GV1', 'value': 0, 'uom': 22, 'name': 'Humidity setpoint'},
    ]
