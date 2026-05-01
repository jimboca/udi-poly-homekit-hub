#!/usr/bin/env python3
"""PG3x child node representing a HomeKit pairing slot."""

from __future__ import annotations

from typing import TYPE_CHECKING

from udi_interface import LOGGER, Node

if TYPE_CHECKING:
    from .Controller import Controller


class PairedDeviceNode(Node):
    """Child node representing one HomeKit slot row."""

    def __init__(
        self,
        controller: "Controller",
        node_key: str,
        slot: int,
        device_label: str,
        paired: bool,
    ):
        self.controller = controller
        self.node_key = str(node_key or "").strip().lower()
        self.slot = int(slot)
        self.device_label = str(device_label or "").strip().lower()
        self.paired = bool(paired)
        address = f"hkp_{self.node_key}"
        name = f"HK Device {self.node_key.upper()}"
        super().__init__(controller.poly, controller.address, address, name)
        self.setDriver("ST", 1 if self.paired else 0, report=False, force=True, uom=25)
        self.setDriver("GV0", self.slot, report=False, force=True, uom=56)
        self.setDriver("GV1", 0, report=False, force=True, uom=25)

    def update_identity(self, slot: int, device_label: str, paired: bool) -> None:
        self.slot = int(slot)
        self.device_label = str(device_label or "").strip().lower()
        self.paired = bool(paired)
        try:
            self.setDriver("ST", 1 if self.paired else 0, report=True, force=True, uom=25)
            self.setDriver("GV0", self.slot, report=True, force=True, uom=56)
        except Exception:
            LOGGER.exception(
                "paired node update: key=%s slot=%s label=%s",
                self.node_key,
                self.slot,
                self.device_label,
            )

    def query(self):
        self.reportDrivers()

    def update_health(self, unhealthy: bool) -> None:
        try:
            self.setDriver("GV1", 1 if unhealthy else 0, report=True, force=True, uom=25)
        except Exception:
            LOGGER.exception(
                "paired node health update: key=%s unhealthy=%s",
                self.node_key,
                unhealthy,
            )

    def cmd_unpair(self, command=None):
        del command
        self.controller._clear_node_key_pin_and_reload(self.node_key, source=self.address)

    def cmd_delete(self, command=None):
        del command
        self.controller._delete_node_key_config_and_node(self.node_key, source=self.address)

    id = "HKHubPairedDevice"
    commands = {"QUERY": query, "UNPAIR": cmd_unpair, "DELETE": cmd_delete}
    drivers = [
        {"driver": "ST", "value": 0, "uom": 25, "name": "Paired status"},
        {"driver": "GV0", "value": 0, "uom": 56, "name": "Pairing slot"},
        {"driver": "GV1", "value": 0, "uom": 25, "name": "Health status"},
    ]
