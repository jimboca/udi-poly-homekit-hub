#!/usr/bin/env python3
"""Generic HomeKit fan IoX node (Fan / Fan v2)."""

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


class FanNode(Node):
    id = 'HKHubFan'
    hint = '0x01020300'

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
        nm = get_valid_node_name(name) or 'HK Fan'
        super().__init__(controller.poly, controller.address, address, nm)
        self.name = nm

    def set_driver_safe(self, driver: str, val: Any, report: bool = True) -> None:
        try:
            self.setDriver(driver, val, report=report, force=True)
        except Exception:
            LOGGER.debug('setDriver %s=%r failed for %s', driver, val, self.address, exc_info=True)

    def _write_char(self, binding_key: str, value: Any, *, hap_name: str | None = None) -> bool:
        return hub_write_bound_characteristic(
            self.controller,
            self.device_id,
            self.char_bindings,
            binding_key,
            value,
            hap_name_fallback=hap_name,
        )

    def _set_power(self, on: bool) -> bool:
        """Prefer Fan v2 **Active**; also write **On** when both are bound."""
        ok = False
        if 'ACTIVE' in self.char_bindings:
            ok = self._write_char('ACTIVE', 1 if on else 0, hap_name=hap_apply.hap_name_active()) or ok
        if 'ON' in self.char_bindings:
            ok = self._write_char('ON', bool(on), hap_name=hap_apply.hap_name_on()) or ok
        if not ok:
            ok = self._write_char('ACTIVE', 1 if on else 0, hap_name=hap_apply.hap_name_active())
            if not ok:
                ok = self._write_char('ON', bool(on), hap_name=hap_apply.hap_name_on())
        return ok

    def on_hap_event(self, aid: int, iid: int, value: Any, label: str) -> None:
        if not hap_event_matches_node(aid, iid, self):
            return
        hap_apply.apply_characteristic_to_fan(self, label, value, log=LOGGER)

    def query(self, cmd=None):
        del cmd
        refresh = getattr(self.controller, 'refresh_generic_node', None)
        if callable(refresh):
            refresh(self)
        else:
            self.reportDrivers()

    def cmd_on(self, cmd=None):
        del cmd
        if self._set_power(True):
            self.set_driver_safe('ST', 1)

    def cmd_off(self, cmd=None):
        del cmd
        if self._set_power(False):
            self.set_driver_safe('ST', 0)

    def cmd_set_speed(self, cmd):
        try:
            val = int(cmd['value'])
        except (KeyError, TypeError, ValueError):
            return
        val = max(0, min(100, val))
        if self._write_char('ROTATION_SPEED', val, hap_name=hap_apply.hap_name_rotation_speed()):
            self.set_driver_safe('GV0', val)

    def cmd_set_direction(self, cmd):
        try:
            val = int(cmd['value'])
        except (KeyError, TypeError, ValueError):
            return
        val = 1 if val else 0
        if self._write_char(
            'ROTATION_DIRECTION', val, hap_name=hap_apply.hap_name_rotation_direction()
        ):
            self.set_driver_safe('GV1', val)

    commands = {
        'QUERY': query,
        'DON': cmd_on,
        'DOF': cmd_off,
        'GV0': cmd_set_speed,
        'GV1': cmd_set_direction,
    }
    drivers = [
        {'driver': 'ST', 'value': 0, 'uom': 25, 'name': 'Fan'},
        {'driver': 'GV0', 'value': 0, 'uom': 56, 'name': 'Speed'},
        {'driver': 'GV1', 'value': 0, 'uom': 25, 'name': 'Direction'},
    ]
