#!/usr/bin/env python3
"""Generic HomeKit binary sensor IoX node."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from udi_interface import Node

import homekit_hub.hap_apply as hap_apply
from hub_node_funcs import get_valid_node_name, hap_event_matches_node

if TYPE_CHECKING:
    from .Controller import Controller


class BinarySensorNode(Node):
    id = 'HKHubBinarySensor'
    hint = '0x01030200'

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
        self.use_celsius = False
        nm = get_valid_node_name(name) or 'HK Sensor'
        super().__init__(controller.poly, controller.address, address, nm)
        self.name = nm

    def set_driver_safe(self, driver: str, val: Any, report: bool = True) -> None:
        try:
            self.setDriver(driver, val, report=report, force=True)
        except Exception:
            pass

    def on_hap_event(self, aid: int, iid: int, value: Any, label: str) -> None:
        if not hap_event_matches_node(aid, iid, self):
            return
        hap_apply.apply_characteristic_to_binary_sensor(self, label, value)

    def query(self, cmd=None):
        del cmd
        refresh = getattr(self.controller, 'refresh_generic_node', None)
        if callable(refresh):
            refresh(self)
        else:
            self.reportDrivers()

    commands = {'QUERY': query}
    drivers = [
        {'driver': 'ST', 'value': 0, 'uom': 17, 'name': 'Temperature'},
        {'driver': 'GV1', 'value': 0, 'uom': 25, 'name': 'Motion/Occupancy'},
        {'driver': 'GV2', 'value': 0, 'uom': 25, 'name': 'Valid temp'},
        {'driver': 'CLIHUM', 'value': 0, 'uom': 22, 'name': 'Humidity'},
    ]
