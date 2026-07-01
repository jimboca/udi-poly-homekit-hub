#!/usr/bin/env python3
"""Generic HomeKit sensor IoX node (room sensors, motion, contact)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Set

from udi_interface import Node

import homekit_hub.hap_apply as hap_apply
from homekit_hub.device_classifier import expected_sensor_nodedef
from hub_node_funcs import get_valid_node_name, hap_event_matches_node

if TYPE_CHECKING:
    from .Controller import Controller

_DEFERRED_DRY_SENSOR_DRIVERS = frozenset({'BATLVL', 'BATLOW'})
_DEFERRED_HUMID_SENSOR_DRIVERS = frozenset({'CLIHUM', 'BATLVL', 'BATLOW'})
_DEFERRED_MOTION_DRIVERS = frozenset({'CLIHUM'})


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
        node_def_id: str | None = None,
    ):
        self.controller = controller
        self.device_id = str(device_id).strip().lower()
        self.aid = int(aid)
        self.role = str(role or 'sensor')
        self.char_bindings = dict(char_bindings or {})
        self.use_celsius = False
        self.address = str(address)
        self.primary = str(primary_addr)
        self._driver_seen: Set[str] = set()
        self.id = str(
            node_def_id or expected_sensor_nodedef(self.role, self.char_bindings)
        )
        nm = get_valid_node_name(name) or 'HK Sensor'
        schema = self._drivers_for_role(self.role, self.char_bindings)
        super().__init__(controller.poly, primary_addr, address, nm)
        self.name = nm
        self.drivers = self._driver_schema_from_db(controller.poly, address, schema)
        self._seed_driver_seen_from_values()
        controller.poly.subscribe(controller.poly.START, self.handler_start, address)

    def _poly(self) -> Any:
        return getattr(self, 'poly', None) or self.controller.poly

    @staticmethod
    def _deferred_drivers_for(role: str, char_bindings: Dict[str, Any] | None) -> frozenset[str]:
        r = str(role or 'sensor').strip().lower()
        if r == 'motion_sensor':
            return _DEFERRED_MOTION_DRIVERS
        if 'RELATIVE_HUMIDITY' in (char_bindings or {}):
            return _DEFERRED_HUMID_SENSOR_DRIVERS
        return _DEFERRED_DRY_SENSOR_DRIVERS

    def _deferred_drivers(self) -> frozenset[str]:
        return self._deferred_drivers_for(self.role, self.char_bindings)

    def _seed_driver_seen_from_values(self) -> None:
        deferred = self._deferred_drivers()
        for spec in self.drivers:
            drv = str(spec.get('driver') or '')
            if drv not in deferred:
                continue
            val = spec.get('value')
            if val not in (None, 0, '0', 0.0):
                self._driver_seen.add(drv)

    def apply_driver_schema(self, *, report: bool = False) -> None:
        """Re-apply nodedef driver uoms/names; keep PG3 values but not stale uoms."""
        schema = self._drivers_for_role(self.role, self.char_bindings)
        self.drivers = self._driver_schema_from_db(self._poly(), self.address, schema)
        self._seed_driver_seen_from_values()
        if not report:
            return
        deferred = self._deferred_drivers()
        for spec in self.drivers:
            drv = str(spec.get('driver') or '')
            if drv in deferred and drv not in self._driver_seen:
                continue
            try:
                self.setDriver(
                    drv,
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
    def _drivers_for_role(role: str, char_bindings: Dict[str, Any] | None = None) -> list:
        """IoX driver list — aligned with udi-poly-ecobee EcobeeSensorF/HF where applicable."""
        r = str(role or 'sensor').strip().lower()
        bindings = char_bindings if isinstance(char_bindings, dict) else {}
        drivers = [
            {'driver': 'ST', 'value': 0, 'uom': 17, 'name': 'Temperature'},
            {'driver': 'GV1', 'value': 0, 'uom': 25, 'name': 'Occupancy'},
            {'driver': 'GV2', 'value': 0, 'uom': 2, 'name': 'Responding'},
        ]
        if r == 'motion_sensor':
            drivers.append({'driver': 'CLIHUM', 'value': 0, 'uom': 22, 'name': 'Humidity'})
        elif 'RELATIVE_HUMIDITY' in bindings:
            drivers.extend(
                [
                    {'driver': 'CLIHUM', 'value': 0, 'uom': 22, 'name': 'Humidity'},
                    {'driver': 'BATLVL', 'value': 0, 'uom': 51, 'name': 'Battery Level'},
                    {'driver': 'BATLOW', 'value': 0, 'uom': 2, 'name': 'Battery Low'},
                ]
            )
        else:
            drivers.extend(
                [
                    {'driver': 'BATLVL', 'value': 0, 'uom': 51, 'name': 'Battery Level'},
                    {'driver': 'BATLOW', 'value': 0, 'uom': 2, 'name': 'Battery Low'},
                ]
            )
        return drivers

    def set_driver_safe(self, driver: str, val: Any, report: bool = True) -> None:
        self._driver_seen.add(str(driver))
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

    def handler_start(self) -> None:
        self.query()

    def query(self, cmd=None):
        del cmd
        schedule = getattr(self.controller, '_schedule_refresh_device_generic_nodes', None)
        if callable(schedule):
            schedule(self.device_id)
            return
        refresh = getattr(self.controller, 'refresh_generic_node', None)
        if callable(refresh):
            refresh(self)
        else:
            self.reportDrivers()

    commands = {'QUERY': query}


class DrySensorNode(SensorNode):
    """Room sensor without HAP relative-humidity hardware."""

    id = 'HKHubSensorDry'


class MotionSensorNode(SensorNode):
    """Built-in thermostat motion child (ambient mirror; no battery)."""

    id = 'HKHubMotionSensor'


class BinarySensorNode(SensorNode):
    """Backward-compatible nodedef id for pre-2.1 sensor nodes."""

    id = 'HKHubBinarySensor'
