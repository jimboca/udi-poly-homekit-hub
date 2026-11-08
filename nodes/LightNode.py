#!/usr/bin/env python3
"""Generic HomeKit light IoX node."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from udi_interface import LOGGER, Node

import homekit_hub.hap_apply as hap_apply
from hub_node_funcs import (
    get_valid_node_name,
    hap_event_matches_node,
    hub_write_bound_characteristic,
)

if TYPE_CHECKING:
    from .Controller import Controller


class LightNode(Node):
    id = 'HKHubLight'
    hint = '0x01010200'

    def __init__(
        self,
        controller: 'Controller',
        address: str,
        name: str,
        *,
        device_id: str,
        aid: int,
        char_bindings: Dict[str, Dict[str, int]],
    ):
        self.controller = controller
        self.device_id = str(device_id).strip().lower()
        self.aid = int(aid)
        self.char_bindings = dict(char_bindings or {})
        nm = get_valid_node_name(name) or 'HK Light'
        primary = controller.address
        pairing_primary = getattr(controller, '_pairing_primary_address', None)
        if callable(pairing_primary):
            primary = pairing_primary(self.device_id)
        super().__init__(controller.poly, primary, address, nm)
        self.name = nm

    def set_driver_safe(self, driver: str, val: Any, report: bool = True) -> None:
        try:
            self.setDriver(driver, val, report=report, force=True)
        except Exception:
            LOGGER.debug('setDriver %s=%r failed for %s', driver, val, self.address, exc_info=True)

    def _write_char(self, binding_key: str, value: Any, *, hap_name: str) -> bool:
        return hub_write_bound_characteristic(
            self.controller,
            self.device_id,
            self.char_bindings,
            binding_key,
            value,
            hap_name_fallback=hap_name,
        )

    def on_hap_event(self, aid: int, iid: int, value: Any, label: str) -> None:
        if not hap_event_matches_node(aid, iid, self):
            return
        hap_apply.apply_characteristic_to_light(self, label, value, log=LOGGER)

    def query(self, cmd=None):
        del cmd
        refresh = getattr(self.controller, 'refresh_generic_node', None)
        if callable(refresh):
            refresh(self)
        else:
            self.reportDrivers()

    def cmd_on(self, cmd=None):
        del cmd
        if self._write_char('ON', True, hap_name=hap_apply.hap_name_on()):
            self.set_driver_safe('ST', 1)

    def cmd_off(self, cmd=None):
        del cmd
        if self._write_char('ON', False, hap_name=hap_apply.hap_name_on()):
            self.set_driver_safe('ST', 0)

    def cmd_set_brightness(self, cmd):
        try:
            val = int(cmd['value'])
        except (KeyError, TypeError, ValueError):
            return
        if self._write_char('BRIGHTNESS', val, hap_name=hap_apply.hap_name_brightness()):
            self.set_driver_safe('GV0', val)

    def cmd_set_hue(self, cmd):
        try:
            val = int(cmd['value'])
        except (KeyError, TypeError, ValueError):
            return
        if self._write_char('HUE', val, hap_name=hap_apply.hap_name_hue()):
            self.set_driver_safe('GV1', val)

    def cmd_set_sat(self, cmd):
        try:
            val = int(cmd['value'])
        except (KeyError, TypeError, ValueError):
            return
        if self._write_char('SATURATION', val, hap_name=hap_apply.hap_name_saturation()):
            self.set_driver_safe('GV2', val)

    def cmd_set_color_temp(self, cmd):
        try:
            kelvin = int(cmd['value'])
        except (KeyError, TypeError, ValueError):
            return
        mired = hap_apply.iox_kelvin_to_hap_mired(kelvin)
        if mired <= 0:
            return
        if self._write_char(
            'COLOR_TEMPERATURE',
            mired,
            hap_name=hap_apply.hap_name_color_temperature(),
        ):
            self.set_driver_safe('GV3', kelvin)

    commands = {
        'QUERY': query,
        'DON': cmd_on,
        'DOF': cmd_off,
        'GV0': cmd_set_brightness,
    }
    drivers = [
        {'driver': 'ST', 'value': 0, 'uom': 25, 'name': 'Light'},
        {'driver': 'GV0', 'value': 0, 'uom': 56, 'name': 'Brightness'},
    ]


class ColorLightNode(LightNode):
    """HomeKit light that advertises hue, saturation, or color temperature."""

    id = 'HKHubColorLight'

    commands = {
        'QUERY': LightNode.query,
        'DON': LightNode.cmd_on,
        'DOF': LightNode.cmd_off,
        'GV0': LightNode.cmd_set_brightness,
        'GV1': LightNode.cmd_set_hue,
        'GV2': LightNode.cmd_set_sat,
        'GV3': LightNode.cmd_set_color_temp,
    }
    drivers = [
        {'driver': 'ST', 'value': 0, 'uom': 25, 'name': 'Light'},
        {'driver': 'GV0', 'value': 0, 'uom': 56, 'name': 'Brightness'},
        {'driver': 'GV1', 'value': 0, 'uom': 56, 'name': 'Hue'},
        {'driver': 'GV2', 'value': 0, 'uom': 51, 'name': 'Saturation'},
        {'driver': 'GV3', 'value': 0, 'uom': 26, 'name': 'Color Temperature'},
    ]
