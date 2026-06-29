#!/usr/bin/env python3
"""Generic HomeKit light IoX node."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from udi_interface import LOGGER, Node

import homekit_hub.hap_apply as hap_apply
from hub_node_funcs import get_valid_node_name, hap_event_matches_node

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
        super().__init__(controller.poly, controller.address, address, nm)
        self.name = nm

    def set_driver_safe(self, driver: str, val: Any, report: bool = True) -> None:
        try:
            self.setDriver(driver, val, report=report, force=True)
        except Exception:
            LOGGER.debug('setDriver %s=%r failed for %s', driver, val, self.address, exc_info=True)

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
        if self.controller.hub_write(self.device_id, hap_apply.hap_name_on(), True):
            self.set_driver_safe('ST', 1)

    def cmd_off(self, cmd=None):
        del cmd
        if self.controller.hub_write(self.device_id, hap_apply.hap_name_on(), False):
            self.set_driver_safe('ST', 0)

    def cmd_set_brightness(self, cmd):
        try:
            val = int(cmd['value'])
        except (KeyError, TypeError, ValueError):
            return
        if self.controller.hub_write(self.device_id, hap_apply.hap_name_brightness(), val):
            self.set_driver_safe('GV0', val)

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
