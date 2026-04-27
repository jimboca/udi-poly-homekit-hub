#!/usr/bin/env python3
"""PG3x controller: asyncio HomeKit bridge + Polyglot lifecycle."""

import asyncio
import html
import json
import time
from threading import Thread
from typing import Any, Dict, List, Set

from udi_interface import LOGGER, Custom, Node

from homekit_hub import (
    DATA_KEY_LAST_HAP_DISCOVER,
    TYPED_PAIRING_SLOTS_KEY,
    HomeKitHubBridge,
)

from nodes import VERSION


class Controller(Node):
    """HomeKit Hub Node Server controller node."""

    def __init__(self, poly, primary, address, name):
        super().__init__(poly, primary, address, name)
        self.name = "HomeKit Hub"
        self.ready = False
        self.hb = 0
        self.Notices = Custom(poly, "notices")
        self.Data = Custom(poly, "customdata")
        self.Params = Custom(poly, "customparams")
        self.TypedParams = Custom(poly, "customtypedparams")
        self.TypedData = Custom(poly, "customtypeddata")
        self.handler_params_st = None
        self.handler_data_st = None
        self.handler_typedparams_st = None
        self.handler_typed_data_st = None
        self.handler_config_done_st = None
        self._config_snap: dict[str, Any] | None = None
        self.mainloop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: Any = None
        self.bridge: HomeKitHubBridge | None = None

        self.init_typed_params()
        poly.subscribe(poly.START, self.handler_start, address)
        poly.subscribe(poly.STOP, self.handler_stop)
        poly.subscribe(poly.POLL, self.handler_poll)
        poly.subscribe(poly.DISCOVER, self.handler_discover)
        poly.subscribe(poly.CUSTOMPARAMS, self.handler_params)
        poly.subscribe(poly.CUSTOMDATA, self.handler_data)
        poly.subscribe(poly.CUSTOMTYPEDPARAMS, self.handler_typed_params)
        poly.subscribe(poly.CUSTOMTYPEDDATA, self.handler_typed_data)
        poly.subscribe(poly.CONFIGDONE, self.handler_config_done)
        poly.subscribe(poly.LOGLEVEL, self.handler_log_level)
        poly.ready()
        poly.addNode(self, conn_status="ST")

    def init_typed_params(self) -> None:
        """Custom Typed: one isList section for HomeKit pairings (see CONFIG.md). Same idea as udi-poly-notification `init_typed()`."""
        LOGGER.debug("enter")
        self.TypedParams.load(
            [
                {
                    "name": TYPED_PAIRING_SLOTS_KEY,
                    "title": "HomeKit pairing slots",
                    "desc": "DISCOVER appends rows for new unpaired devices. Set Slot to pin a stable number; leave blank to auto-pick the next free slot.",
                    "isList": True,
                    "params": [
                        {
                            "name": "slot",
                            "title": "Slot number (optional; 1, 2, 3, …). Leave empty to use the next available slot automatically.",
                            "isRequired": False,
                        },
                        {
                            "name": "hap_pin",
                            "title": "HomeKit pairing code (e.g. 123-45-678). Clear to unpair this row only.",
                            "isRequired": False,
                        },
                        {
                            "name": "accessory_id",
                            "title": "Accessory id (optional — leave empty to use last DISCOVER; set to pick one device when several are unpaired)",
                            "isRequired": False,
                        },
                        {
                            "name": "accessory_name",
                            "title": "Name substring (optional; same as id — only needed to disambiguate multiple devices)",
                            "isRequired": False,
                        },
                    ],
                }
            ],
            True,
        )
        LOGGER.debug("exit")

    def _bridge_get_params(self) -> dict[str, Any]:
        return {k: self.Params[k] for k in self.Params.keys()}

    def _bridge_get_pairing_slot_rows(self) -> list:
        try:
            rows = self.TypedData.get(TYPED_PAIRING_SLOTS_KEY)
        except Exception:
            return []
        if not isinstance(rows, list):
            return []
        return rows

    def _bridge_get_data(self) -> dict[str, Any]:
        return {k: self.Data[k] for k in self.Data.keys()}

    def _bridge_set_data(self, data: dict[str, Any]) -> None:
        self.Data.load(data)

    def _config_restart_snap(self) -> dict[str, Any]:
        snap = {
            "ws_host": self.Params.get("ws_host"),
            "ws_port": self.Params.get("ws_port"),
        }
        rows = self._bridge_get_pairing_slot_rows()
        snap["_pairing_slots"] = json.dumps(rows, sort_keys=True, default=str)
        return snap

    def _maybe_restart_on_config_change(self) -> None:
        snap = self._config_restart_snap()
        if (
            self.ready
            and self.mainloop
            and self.bridge
            and self._config_snap is not None
            and snap != self._config_snap
        ):
            LOGGER.info("Configuration changed; restarting HomeKit sessions")
            asyncio.run_coroutine_threadsafe(self.bridge.restart_session(), self.mainloop)
        self._config_snap = snap

    def handler_config_done(self):
        self.handler_config_done_st = True

    def handler_log_level(self, level):
        LOGGER.info(f"log level {level}")

    def handler_data(self, data):
        if data is None:
            LOGGER.warning("No custom data on first run")
            self.handler_data_st = False
            return
        self.Data.load(data)
        self.handler_data_st = True

    def handler_typed_params(self, params):
        LOGGER.debug("customtypedparams received: %s", params)
        if params is not None and len(params) > 0:
            self.TypedParams.load(params)
        self.handler_typedparams_st = True

    def handler_typed_data(self, data):
        LOGGER.debug("customtypeddata: %s", data)
        if data is not None:
            self.TypedData.load(data)
        self.handler_typed_data_st = True
        self._maybe_restart_on_config_change()

    def handler_params(self, params):
        LOGGER.debug("customparams: %s", params)
        self.Params.load(params)
        self.handler_params_st = True
        self._maybe_restart_on_config_change()

    def handler_start(self):
        self.Notices.clear()
        LOGGER.info("HomeKit Hub NodeServer %s (profile %s)", self.poly.serverdata.get("version"), VERSION)

        cnt = 60
        while (
            self.handler_params_st is None
            or self.handler_data_st is None
            or self.handler_typedparams_st is None
            or self.handler_typed_data_st is None
        ) and cnt > 0:
            LOGGER.warning(
                "Waiting for params/data/typed: params=%s data=%s typedparams=%s typeddata=%s",
                self.handler_params_st,
                self.handler_data_st,
                self.handler_typedparams_st,
                self.handler_typed_data_st,
            )
            time.sleep(1)
            cnt -= 1
        if cnt == 0:
            LOGGER.error("Timeout waiting for custom params/data/typed config")

        self.mainloop = asyncio.new_event_loop()

        def _run_loop():
            asyncio.set_event_loop(self.mainloop)
            self.mainloop.run_forever()

        self._loop_thread = Thread(target=_run_loop, daemon=True)
        self._loop_thread.start()

        self._config_snap = None
        self.bridge = HomeKitHubBridge(
            LOGGER,
            self._bridge_get_params,
            self._bridge_get_pairing_slot_rows,
            self._bridge_get_data,
            self._bridge_set_data,
        )
        self._config_snap = self._config_restart_snap()
        fut = asyncio.run_coroutine_threadsafe(self.bridge.start(), self.mainloop)
        try:
            fut.result(timeout=120)
        except Exception:
            LOGGER.exception("HomeKit bridge failed to start")

        self.setDriver("ST", 1)
        self.heartbeat()
        self.ready = True
        LOGGER.info("HomeKit Hub ready")

    def handler_stop(self):
        LOGGER.info("Stopping HomeKit Hub")
        self.ready = False
        if self.bridge and self.mainloop:
            fut = asyncio.run_coroutine_threadsafe(self.bridge.stop(), self.mainloop)
            try:
                fut.result(timeout=60)
            except Exception:
                LOGGER.exception("bridge.stop")
        if self.mainloop:
            self.mainloop.call_soon_threadsafe(self.mainloop.stop)
        LOGGER.info("HomeKit Hub stopped")

    def handler_poll(self, polltype):
        if polltype == "longPoll":
            self.heartbeat()

    def handler_discover(self, _data=None):
        """Network scan: results are saved and shown in a Polyglot Notice (no log file needed)."""
        if not (self.bridge and self.mainloop):
            return
        fut = asyncio.run_coroutine_threadsafe(
            self.bridge.discover_collect(12.0), self.mainloop
        )
        try:
            rows = fut.result(timeout=30)
        except Exception:
            LOGGER.exception("HomeKit discover failed")
            return
        self._present_hap_discover_results(rows or [])

    def _typed_data_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        try:
            for k in self.TypedData.keys():
                out[k] = self.TypedData[k]
        except Exception:
            pass
        return out

    def _append_pairing_rows_for_discover(self, discover_rows: list) -> int:
        """
        For each unpaired accessory in the discover result, add a HomeKit pairing slots
        row (accessory_id / accessory_name filled, hap_pin empty) if that id is not
        already present. Persists custom typed data via udi_interface Custom.load(..., save=True).
        """
        if not discover_rows:
            return 0
        unpaired = [r for r in discover_rows if isinstance(r, dict) and not r.get("paired")]
        if not unpaired:
            return 0
        try:
            raw = self.TypedData.get(TYPED_PAIRING_SLOTS_KEY)
        except Exception:
            raw = None
        if not isinstance(raw, list):
            current: List[Dict[str, Any]] = []
        else:
            current = [dict(x) for x in raw if isinstance(x, dict)]
        seen: Set[str] = set()
        for row in current:
            aid = (row.get("accessory_id") or "").strip().lower()
            if aid:
                seen.add(aid)
        added = 0
        for r in unpaired:
            pid = (str(r.get("id") or "")).strip().lower()
            if not pid or pid in seen:
                continue
            pname = (str(r.get("name") or "")).strip()
            current.append(
                {
                    "slot": "",
                    "hap_pin": "",
                    "accessory_id": pid,
                    "accessory_name": pname,
                }
            )
            seen.add(pid)
            added += 1
        if added == 0:
            return 0
        try:
            td = self._typed_data_dict()
            td[TYPED_PAIRING_SLOTS_KEY] = current
            self.TypedData.load(td, save=True)
        except Exception:
            LOGGER.exception("Failed to save Custom Typed data after discover")
            return 0
        if self.ready:
            self._maybe_restart_on_config_change()
        return added

    def _present_hap_discover_results(self, rows: list) -> None:
        try:
            d = {k: self.Data[k] for k in self.Data.keys()}
        except Exception:
            d = {}
        d[DATA_KEY_LAST_HAP_DISCOVER] = list(rows) if rows else []
        self.Data.load(d)

        n_typed = 0
        try:
            n_typed = self._append_pairing_rows_for_discover(rows)
        except Exception:
            LOGGER.exception("append_pairing_rows_for_discover")

        if not rows:
            self.Notices["hap_discover"] = (
                "HomeKit discover: no accessories found. Check LAN, firewall, and that the device is in pairing mode."
            )
            return

        unpaired = [r for r in rows if not r.get("paired")]
        paired = [r for r in rows if r.get("paired")]
        parts = [
            "<b>HomeKit discover</b> — <code>last_hap_discover</code> and "
            f"<b>Custom Typed &gt; HomeKit pairing slots</b> are updated. "
            f"Enter the HomeKit pairing code on the new row(s) and save."
            f" (Snapshot: <code>{html.escape(DATA_KEY_LAST_HAP_DISCOVER)}</code>.)<br/>",
        ]
        if n_typed:
            parts.append(
                f"Added <b>{n_typed}</b> new row(s) for unpaired accessories (existing rows unchanged).<br/>"
            )
        if unpaired:
            parts.append("<b>Unpaired (ready to pair with this hub):</b><ul>")
            for r in unpaired:
                rid = html.escape(str(r.get("id") or ""), quote=True)
                nm = html.escape(str(r.get("name") or "(no name)"), quote=True)
                parts.append(
                    f"<li><b>id</b> <code>{rid}</code> &nbsp; <b>name</b> {nm} &nbsp; "
                    f"({html.escape(str(r.get('host', '')), quote=True)}:{r.get('port', 0)})</li>"
                )
            parts.append("</ul>")
        if paired:
            parts.append(
                "<b>Already paired elsewhere:</b> unpair in Apple Home (or the other controller) first.<ul>"
            )
            for r in paired:
                rid = html.escape(str(r.get("id") or ""), quote=True)
                nm = html.escape(str(r.get("name") or "(no name)"), quote=True)
                parts.append(
                    f"<li><b>id</b> <code>{rid}</code> &nbsp; <b>name</b> {nm}</li>"
                )
            parts.append("</ul>")
        self.Notices["hap_discover"] = "".join(parts)

    def heartbeat(self):
        if self.hb == 0:
            self.reportCmd("DON", 2)
            self.hb = 1
        else:
            self.reportCmd("DOF", 2)
            self.hb = 0

    def query(self):
        self.setDriver("ST", 1)
        self.reportDrivers()
