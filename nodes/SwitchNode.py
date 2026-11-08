#!/usr/bin/env python3
"""Generic HomeKit switch / outlet IoX node."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from udi_interface import Node

import homekit_hub.hap_apply as hap_apply
from hub_node_funcs import (
    get_valid_node_name,
    hap_event_matches_node,
    hub_write_bound_characteristic,
)

if TYPE_CHECKING:
    from .Controller import Controller


class SwitchNode(Node):
    id = 'HKHubSwitch'
    hint = '0x01040200'

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
        nm = get_valid_node_name(name) or 'HK Switch'
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
            pass

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
        hap_apply.apply_characteristic_to_switch(self, label, value)

    def query(self, cmd=None):
        del cmd
        refresh = getattr(self.controller, 'refresh_generic_node', None)
        if callable(refresh):
            refresh(self)
        else:
            self.reportDrivers()

    def _set_power(self, on: bool) -> bool:
        """Write On, or Active when the accessory is a valve."""
        ok = False
        if 'ON' in self.char_bindings:
            ok = self._write_char('ON', bool(on), hap_name=hap_apply.hap_name_on()) or ok
        if 'ACTIVE' in self.char_bindings:
            ok = self._write_char('ACTIVE', 1 if on else 0, hap_name=hap_apply.hap_name_active()) or ok
        if not ok:
            ok = self._write_char('ON', bool(on), hap_name=hap_apply.hap_name_on())
        return ok

    def cmd_on(self, cmd=None):
        del cmd
        if self._set_power(True):
            self.set_driver_safe('ST', 1)

    def cmd_off(self, cmd=None):
        del cmd
        if self._set_power(False):
            self.set_driver_safe('ST', 0)

    commands = {'QUERY': query, 'DON': cmd_on, 'DOF': cmd_off}
    drivers = [{'driver': 'ST', 'value': 0, 'uom': 25, 'name': 'Switch'}]


class ValveNode(SwitchNode):
    """HomeKit Valve. On and off write Active. Status is Closed (0) or Open (1)."""

    id = 'HKHubValve'
    drivers = [{'driver': 'ST', 'value': 0, 'uom': 25, 'name': 'Valve'}]
