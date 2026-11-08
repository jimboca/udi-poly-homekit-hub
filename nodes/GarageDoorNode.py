#!/usr/bin/env python3
"""Generic HomeKit garage door IoX node."""

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


class GarageDoorNode(Node):
    id = 'HKHubGarageDoor'
    # Residential / Barrier / Garage Door Opener
    hint = '0x01120100'

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
        nm = get_valid_node_name(name) or 'HK Garage Door'
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
        hap_apply.apply_characteristic_to_garage_door(self, label, value, log=LOGGER)

    def query(self, cmd=None):
        del cmd
        refresh = getattr(self.controller, 'refresh_generic_node', None)
        if callable(refresh):
            refresh(self)
        else:
            self.reportDrivers()

    def cmd_open(self, cmd=None):
        del cmd
        if self._write_char(
            'DOOR_STATE_TARGET',
            hap_apply.HAP_TARGET_DOOR_OPEN,
            hap_name=hap_apply.hap_name_door_state_target(),
        ):
            self.set_driver_safe('ST', hap_apply.IOX_DOOR_OPENING)

    def cmd_close(self, cmd=None):
        del cmd
        if self._write_char(
            'DOOR_STATE_TARGET',
            hap_apply.HAP_TARGET_DOOR_CLOSED,
            hap_name=hap_apply.hap_name_door_state_target(),
        ):
            self.set_driver_safe('ST', hap_apply.IOX_DOOR_CLOSING)

    def cmd_lock(self, cmd=None):
        del cmd
        if 'LOCK_MECHANISM_TARGET_STATE' not in self.char_bindings:
            return
        if self._write_char(
            'LOCK_MECHANISM_TARGET_STATE',
            hap_apply.HAP_LOCK_TARGET_SECURED,
            hap_name=hap_apply.hap_name_lock_target(),
        ):
            self.set_driver_safe('GV2', 1)

    def cmd_unlock(self, cmd=None):
        del cmd
        if 'LOCK_MECHANISM_TARGET_STATE' not in self.char_bindings:
            return
        if self._write_char(
            'LOCK_MECHANISM_TARGET_STATE',
            hap_apply.HAP_LOCK_TARGET_UNSECURED,
            hap_name=hap_apply.hap_name_lock_target(),
        ):
            self.set_driver_safe('GV2', 0)

    commands = {
        'QUERY': query,
        'DON': cmd_open,
        'DOF': cmd_close,
        'LOCK': cmd_lock,
        'UNLOCK': cmd_unlock,
    }
    drivers = [
        {'driver': 'ST', 'value': 0, 'uom': 25, 'name': 'Door State'},
        {'driver': 'GV1', 'value': 0, 'uom': 25, 'name': 'Obstruction'},
        {'driver': 'GV2', 'value': 3, 'uom': 25, 'name': 'Lock'},
    ]
