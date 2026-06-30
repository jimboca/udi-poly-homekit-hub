#!/usr/bin/env python3
"""Generic HomeKit sensor IoX node (room sensors, motion, contact)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from udi_interface import Node

import homekit_hub.hap_apply as hap_apply
from hub_node_funcs import get_valid_node_name, hap_event_matches_node

if TYPE_CHECKING:
    from .Controller import Controller


class SensorNode(Node):
    id = 'HKHubSensor'
    hint = '0x01030200'

    def __init__(
        self,
        controller: 'Controller',
        primary_addr: str,
        address: str,
        name: str,
        *,
        device_id: str,
        aid: int,
        char_bindings: Dict[str, Dict[str, int]],
        role: str = 'sensor',
    ):
        self.controller = controller
        self.device_id = str(device_id).strip().lower()
        self.aid = int(aid)
        self.role = str(role or 'sensor')
        self.char_bindings = dict(char_bindings or {})
        self.use_celsius = False
        self.address = str(address)
        self.primary = str(primary_addr)
        nm = get_valid_node_name(name) or 'HK Sensor'
        schema = self._drivers_for_role(self.role)
        super().__init__(controller.poly, primary_addr, address, nm)
        self.name = nm
        self.drivers = self._driver_schema_from_db(controller.poly, address, schema)

    def _poly(self) -> Any:
        return getattr(self, 'poly', None) or self.controller.poly

    def apply_driver_schema(self, *, report: bool = False) -> None:
        """Re-apply nodedef driver uoms/names; keep PG3 values but not stale uoms."""
        schema = self._drivers_for_role(self.role)
        self.drivers = self._driver_schema_from_db(self._poly(), self.address, schema)
        if report:
            for spec in self.drivers:
                try:
                    self.setDriver(
                        spec['driver'],
                        spec['value'],
                        report=True,
                        force=True,
                        uom=spec['uom'],
                    )
                except Exception:
                    pass

    @staticmethod
    def _driver_schema_from_db(poly: Any, address: str, schema: list) -> list:
        values: dict[str, Any] = {}
        if hasattr(poly, 'db_getNodeDrivers'):
            try:
                for drv in poly.db_getNodeDrivers(address) or []:
                    if isinstance(drv, dict) and drv.get('driver'):
                        values[str(drv['driver'])] = drv.get('value')
            except Exception:
                pass
        merged: list[dict] = []
        for spec in schema:
            row = dict(spec)
            if row['driver'] in values:
                row['value'] = values[row['driver']]
            merged.append(row)
        return merged

    @staticmethod
    def _drivers_for_role(role: str) -> list:
        """IoX driver list — aligned with udi-poly-ecobee EcobeeSensorF/HF where applicable."""
        r = str(role or 'sensor').strip().lower()
        drivers = [
            {'driver': 'ST', 'value': 0, 'uom': 17, 'name': 'Temperature'},
            {'driver': 'GV1', 'value': 0, 'uom': 25, 'name': 'Occupancy'},
            {'driver': 'GV2', 'value': 0, 'uom': 2, 'name': 'Responding'},
            {'driver': 'BATLVL', 'value': 0, 'uom': 51, 'name': 'Battery Level'},
            {'driver': 'BATLOW', 'value': 0, 'uom': 2, 'name': 'Battery Low'},
        ]
        if r != 'motion_sensor':
            drivers.insert(
                3,
                {'driver': 'CLIHUM', 'value': 0, 'uom': 22, 'name': 'Humidity'},
            )
        return drivers

    def set_driver_safe(self, driver: str, val: Any, report: bool = True) -> None:
        try:
            self.setDriver(driver, val, report=report, force=True)
        except Exception:
            pass

    def apply_hub_characteristic(self, label: str, value: Any) -> bool:
        return hap_apply.apply_characteristic_to_sensor(self, label, value)

    def on_hap_event(self, aid: int, iid: int, value: Any, label: str) -> None:
        if not hap_event_matches_node(aid, iid, self):
            return
        hap_apply.apply_characteristic_to_sensor(self, label, value)

    def query(self, cmd=None):
        del cmd
        refresh = getattr(self.controller, 'refresh_generic_node', None)
        if callable(refresh):
            refresh(self)
        else:
            self.reportDrivers()

    commands = {'QUERY': query}


class BinarySensorNode(SensorNode):
    """Backward-compatible nodedef id for pre-2.1 sensor nodes."""

    id = 'HKHubBinarySensor'
