#!/usr/bin/env python3
"""PG3x child node representing a HomeKit pairing slot."""

from __future__ import annotations

from typing import TYPE_CHECKING

from udi_interface import LOGGER, Node

if TYPE_CHECKING:
    from .Controller import Controller

# IoX/PG3 device-tree icon comes from Polyglot ``addnode`` ``hint``, not from NLS ``ND-*-ICON`` alone.
# Default ``[0, 0, 0, 0]`` maps to the “unknown” / bulb glyph. This value is: home (0x01), Relay (0x04),
# On/Off Power Switch (0x02), node-specific n/a (0x00). See UniversalDevicesInc/hints hint.yaml.
_PAIRED_DEVICE_HINT = "0x01040200"


class PairedDeviceNode(Node):
    """Child node representing one HomeKit slot row."""

    @staticmethod
    def _node_title(node_key: str, display_name: str) -> str:
        base = str(display_name or "").strip()
        if not base:
            nk = str(node_key or "").strip().lower()
            return f"HK Device {nk.upper()}" if nk else "HK Device"
        if len(base) > 80:
            return base[:77] + "..."
        return base

    def __init__(
        self,
        controller: "Controller",
        node_key: str,
        slot: int,
        display_name: str,
        paired: bool,
    ):
        self.controller = controller
        self.node_key = str(node_key or "").strip().lower()
        self.slot = int(slot)
        self.display_name = str(display_name or "").strip()
        self.paired = bool(paired)
        address = f"hkp_{self.node_key}"
        title = self._requested_title()
        super().__init__(controller.poly, controller.address, address, title)
        self.setDriver("ST", 1 if self.paired else 0, report=False, force=True, uom=25)
        self.setDriver("GV0", self.slot, report=False, force=True, uom=56)
        # GV1: 0=Healthy, 1=Degraded (paired transport), 2=Not paired (no active pairing)
        self.setDriver(
            "GV1",
            0 if self.paired else 2,
            report=False,
            force=True,
            uom=25,
        )

    def _requested_title(self) -> str:
        fn = getattr(self.controller, 'paired_node_title', None)
        if callable(fn):
            return fn(self)
        return self._node_title(self.node_key, self.display_name)

    def reconcile_isy_name(self) -> None:
        """Align IoX node title with discover/typed name; respects ``change_node_names``."""
        push = getattr(self.controller, "_push_paired_node_isy_title", None)
        if callable(push):
            push(self)
            return
        requested = self._requested_title()
        poly = self.poly
        if not hasattr(poly, "getNodeNameFromDb"):
            self.name = requested
            return
        try:
            cname = poly.getNodeNameFromDb(self.address)
        except Exception:
            cname = None
        if cname is None:
            self.name = requested
            return
        if cname == requested:
            self.name = requested
            return
        if self.controller.change_node_names:
            LOGGER.warning(
                "Existing node name '%s' for %s does not match '%s'; renaming to match",
                cname,
                self.address,
                requested,
            )
            if hasattr(poly, "renameNode"):
                try:
                    poly.renameNode(self.address, requested)
                except Exception:
                    LOGGER.error(
                        "renameNode failed for %s (known issue on some PG3x builds)",
                        self.address,
                        exc_info=True,
                    )
            self.name = requested
        else:
            LOGGER.warning(
                "Existing node name '%s' for %s does not match '%s'; "
                "set change_node_names=false in Custom Params to keep the IoX name unchanged",
                cname,
                self.address,
                requested,
            )
            self.name = cname

    def update_identity(self, slot: int, display_name: str, paired: bool) -> None:
        self.slot = int(slot)
        self.display_name = str(display_name or "").strip()
        self.paired = bool(paired)
        self.reconcile_isy_name()
        try:
            self.setDriver("ST", 1 if self.paired else 0, report=True, force=True, uom=25)
            self.setDriver("GV0", self.slot, report=True, force=True, uom=56)
            self.setDriver(
                "GV1",
                0 if self.paired else 2,
                report=True,
                force=True,
                uom=25,
            )
        except Exception:
            LOGGER.exception(
                "paired node update: key=%s slot=%s display=%s",
                self.node_key,
                self.slot,
                self.display_name,
            )

    def query(self):
        self.reportDrivers()

    def update_health(self, unhealthy: bool) -> None:
        try:
            val = 2 if not self.paired else (1 if unhealthy else 0)
            self.setDriver("GV1", val, report=True, force=True, uom=25)
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

    def cmd_export_inventory(self, command=None):
        del command
        self.controller.export_device_inventory_manual(self.node_key)

    hint = _PAIRED_DEVICE_HINT
    id = "HKHubPairedDevice"
    commands = {
        "QUERY": query,
        "UNPAIR": cmd_unpair,
        "DELETE": cmd_delete,
        "EXPORT_INVENTORY": cmd_export_inventory,
    }
    drivers = [
        {"driver": "ST", "value": 0, "uom": 25, "name": "Paired status"},
        {"driver": "GV0", "value": 0, "uom": 56, "name": "Pairing slot"},
        {"driver": "GV1", "value": 0, "uom": 25, "name": "Health status"},
    ]
