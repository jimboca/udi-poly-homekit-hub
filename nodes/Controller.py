#!/usr/bin/env python3
"""PG3x controller: asyncio HomeKit bridge + Polyglot lifecycle."""

import asyncio
import html
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from threading import Thread
from typing import Any, Dict, List, Optional, Set

from udi_interface import LOGGER, Custom, Node

from homekit_hub import (
    DATA_KEY_LAST_HAP_DISCOVER,
    TYPED_PAIRING_SLOTS_KEY,
    HomeKitHubBridge,
    normalize_hap_pin,
)
from homekit_hub.bridge import _parse_slot_value

from nodes import VERSION

# ISY-visible error codes (driver ERR, UOM 25 index + NLS ERRC-*). See README.
ERR_OK = 0
ERR_BRIDGE_START = 1
ERR_DISCOVER_SCAN = 2
ERR_DISCOVER_UNEXPECTED = 3
ERR_TYPED_SAVE = 4
ERR_APPEND_PAIRING = 5
ERR_BRIDGE_STOP = 6
ERR_STATUS_UPDATE = 7

BONJOUR_COMPARE_WINDOW_SECONDS = 12.0


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
        self.CompareData = Custom(poly, "compare")
        self.handler_params_st = None
        self.handler_data_st = None
        self.handler_typedparams_st = None
        self.handler_typed_data_st = None
        self.handler_config_done_st = None
        self._config_snap: dict[str, Any] | None = None
        self.mainloop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: Any = None
        self.bridge: HomeKitHubBridge | None = None
        self._bonjour_lock = threading.Lock()
        self._bonjour_active = False
        self._bonjour_buf: list[Any] = []

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
        poly.subscribe(poly.BONJOUR, self._on_bonjour_event)
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
                        {
                            "name": "discover_endpoint",
                            "title": "LAN host:port from last DISCOVER (informational; optional)",
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
        data = {k: self.Data[k] for k in self.Data.keys()}
        try:
            raw_pairings = data.get("homekit_pairings")
            if isinstance(raw_pairings, dict):
                pairing_slots = sorted(str(k) for k in raw_pairings.keys())
            else:
                pairing_slots = []
            raw_discover = data.get(DATA_KEY_LAST_HAP_DISCOVER)
            discover_count = len(raw_discover) if isinstance(raw_discover, list) else 0
            LOGGER.debug(
                "bridge_get_data snapshot: keys=%s homekit_pairings_slots=%s last_hap_discover_count=%d",
                sorted(list(data.keys())),
                pairing_slots,
                discover_count,
            )
        except Exception:
            LOGGER.debug("bridge_get_data snapshot logging failed")
        return data

    def _bridge_set_data(self, data: dict[str, Any]) -> None:
        # Persist bridge-written custom data (homekit_pairings / last_hap_discover)
        # so slots and discovery snapshots survive restart.
        self.Data.load(data, save=True)
        try:
            LOGGER.debug(
                "customdata saved by bridge: keys=%s has_pairings=%s has_last_hap_discover=%s",
                sorted(list(data.keys())),
                "homekit_pairings" in data,
                DATA_KEY_LAST_HAP_DISCOVER in data,
            )
        except Exception:
            LOGGER.debug("customdata saved by bridge (key introspection failed)")

    def _pairing_notice_callback(
        self,
        code: int,
        title: str,
        log_message: str,
        exc: Optional[Exception],
    ) -> None:
        """Bridge runs on the asyncio thread; Notices + ERR must be PG3-visible."""
        extra_html = ""
        if log_message:
            extra_html = f"{html.escape(log_message)}<br/>"
        self.report_error(
            code,
            "homekit_err_config",
            title,
            exc=exc,
            log_message=log_message,
            extra_html=extra_html,
        )

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

    def report_error(
        self,
        code: int,
        notice_key: str,
        title: str,
        *,
        exc: Optional[Exception] = None,
        log_message: str = "",
        extra_html: str = "",
        set_st_error: bool = False,
    ) -> None:
        """Log (with traceback if ``exc``), post a Polyglot Notice, set **ERR** error code.

        Use **set_st_error** only for hub-fatal faults (sets **ST** = 2 = Error per profile).
        """
        lm = log_message or title
        if exc is not None:
            LOGGER.exception("%s", lm)
        else:
            LOGGER.error("%s", lm)

        parts = [f"<b>{html.escape(title)}</b><br/>"]
        if exc is not None:
            parts.append(
                f"<code>{html.escape(type(exc).__name__)}</code>: "
                f"{html.escape(str(exc))}<br/>"
            )
        if extra_html:
            parts.append(extra_html)
        parts.append("See the Node Server log for the full traceback.")
        self.Notices[notice_key] = "".join(parts)

        try:
            self.setDriver("ERR", code, report=True, force=True, uom=25)
            if set_st_error:
                self.setDriver("ST", 2, report=True, force=True)
        except Exception as e2:
            LOGGER.exception("report_error: setDriver failed")
            try:
                self.Notices["homekit_meta"] = (
                    "<b>Could not update error status drivers</b><br/>"
                    f"{html.escape(str(e2))}"
                )
            except Exception:
                pass

    def clear_hub_error_indicators(self, *, keep_config_notice: bool = False) -> None:
        """Reset error code and clear hub error notices after a healthy start.

        ``keep_config_notice=True`` preserves ``homekit_err_config`` notices emitted
        during startup (for example, PIN-only rows with missing customdata) so the
        user can see guidance in the PG3 UI.
        """
        keys = ["homekit_bridge", "homekit_err_discover", "homekit_meta"]
        if not keep_config_notice:
            keys.append("homekit_err_config")
        for key in keys:
            try:
                self.Notices.delete(key)
            except Exception:
                try:
                    del self.Notices[key]
                except Exception:
                    pass
        try:
            self.setDriver("ERR", ERR_OK, report=True, force=True, uom=25)
        except Exception:
            LOGGER.exception("clear_hub_error_indicators: set ERR")

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
        # Use TypedData after load — PG3 may send a partial ``data`` dict without ``pairing_slots``.
        self._normalize_and_persist_typed_pairing_pins()
        self._auto_discover_if_needed_from_typed_update()
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

        # One-shot migration: dashed PIN in UI for codes stored without dashes.
        self._normalize_and_persist_typed_pairing_pins()

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
            pairing_notice=self._pairing_notice_callback,
        )
        self._config_snap = self._config_restart_snap()
        fut = asyncio.run_coroutine_threadsafe(self.bridge.start(), self.mainloop)
        try:
            fut.result(timeout=120)
        except Exception as e:
            self.ready = False
            self.report_error(
                ERR_BRIDGE_START,
                "homekit_bridge",
                "HomeKit Hub failed to start",
                exc=e,
                log_message="HomeKit bridge failed to start",
                extra_html=(
                    "If the error mentions <code>zeroconf</code> / port <b>5353</b>, another mDNS stack "
                    "may own that port. The hub defaults to a compatible unicast mode (set in "
                    "<code>homekit-poly.py</code>); you can override with env "
                    "<code>HOMEKIT_HUB_ZEROCONF_UNICAST</code> for the Node Server process, or on Linux "
                    "with Avahi set <code>disallow-other-stacks=no</code> in <code>avahi-daemon.conf</code>.<br/>"
                ),
                set_st_error=True,
            )
            return

        # Keep startup config guidance notices from bridge.start() visible in UI.
        self.clear_hub_error_indicators(keep_config_notice=True)
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
            except Exception as e:
                self.report_error(
                    ERR_BRIDGE_STOP,
                    "homekit_bridge",
                    "HomeKit bridge stop failed",
                    exc=e,
                    log_message="bridge.stop",
                )
        if self.mainloop:
            self.mainloop.call_soon_threadsafe(self.mainloop.stop)
        LOGGER.info("HomeKit Hub stopped")

    def handler_poll(self, polltype):
        if polltype == "longPoll":
            self.heartbeat()

    def handler_discover(self, _data=None):
        """Network scan: results are saved and shown in a Polyglot Notice (no log file needed).

        PG3 may invoke this via ``poly.subscribe(DISCOVER)`` (MQTT ``discover``) and/or via
        ``runCmd``; the latter requires ``commands['DISCOVER']`` (see udi-poly-ecobee / udi-poly-kasa).
        """
        try:
            LOGGER.info("HomeKit DISCOVER: starting (zeroconf HAP scan)")
            if not (self.bridge and self.mainloop):
                LOGGER.warning(
                    "HomeKit DISCOVER skipped: bridge not ready. Wait until the log shows "
                    "'HomeKit Hub ready' after the Node Server starts, then try again."
                )
                return
            fut = asyncio.run_coroutine_threadsafe(
                self.bridge.discover_collect(12.0), self.mainloop
            )
            try:
                rows = fut.result(timeout=30)
            except Exception as e:
                self.report_error(
                    ERR_DISCOVER_SCAN,
                    "homekit_err_discover",
                    "HomeKit discover scan failed",
                    exc=e,
                    log_message="HomeKit discover: scan failed",
                )
                return
            n = len(rows) if rows else 0
            LOGGER.info("HomeKit DISCOVER: scan finished, %d accessory(ies) in result", n)
            self._present_hap_discover_results(rows or [], source="manual")
        except Exception as e:
            self.report_error(
                ERR_DISCOVER_UNEXPECTED,
                "homekit_err_discover",
                "HomeKit discover failed",
                exc=e,
                log_message="HomeKit DISCOVER: unexpected error",
            )

    def _typed_data_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        try:
            for k in self.TypedData.keys():
                out[k] = self.TypedData[k]
        except Exception:
            pass
        return out

    def _normalize_and_persist_typed_pairing_pins(self) -> bool:
        """Persist ``hap_pin`` in Custom Typed pairing rows as ``XXX-XX-XXX`` when it is 8 digits.

        Applies to newly entered undashed codes and upgrades existing stored values.
        """
        try:
            raw = self.TypedData.get(TYPED_PAIRING_SLOTS_KEY)
        except Exception:
            return False
        if not isinstance(raw, list):
            return False
        new_rows: List[Any] = []
        changed = False
        for item in raw:
            if not isinstance(item, dict):
                new_rows.append(item)
                continue
            row = dict(item)
            pin_raw = row.get("hap_pin")
            pin_in = "" if pin_raw is None else str(pin_raw).strip()
            norm = normalize_hap_pin(pin_in)
            if norm != pin_in:
                row["hap_pin"] = norm
                changed = True
            new_rows.append(row)
        if not changed:
            return False
        try:
            td = self._typed_data_dict()
            td[TYPED_PAIRING_SLOTS_KEY] = new_rows
            self.TypedData.load(td, save=True)
        except Exception:
            LOGGER.exception(
                "Failed to persist normalized HomeKit pairing PINs to Custom Typed data"
            )
            return False
        LOGGER.info("Updated Custom Typed pairing slot PIN(s) to dashed XXX-XX-XXX form")
        return True

    def _typed_update_needs_discover(self) -> bool:
        """Auto-discover only for pairing-oriented edits that need selection help.

        Trigger when at least one row has a PIN entered but no id/name filter,
        and we do not have a useful last_hap_discover snapshot yet.
        """
        try:
            rows = self.TypedData.get(TYPED_PAIRING_SLOTS_KEY)
        except Exception:
            return False
        if not isinstance(rows, list):
            return False
        has_pin_without_filter = False
        for row in rows:
            if not isinstance(row, dict):
                continue
            pin = (row.get("hap_pin") or "").strip()
            if not pin:
                continue
            acc_id = (row.get("accessory_id") or "").strip()
            acc_name = (row.get("accessory_name") or "").strip()
            if not acc_id and not acc_name:
                has_pin_without_filter = True
                break
        if not has_pin_without_filter:
            return False
        try:
            last = self.Data.get(DATA_KEY_LAST_HAP_DISCOVER)
        except Exception:
            last = None
        if isinstance(last, list) and len(last) > 0:
            return False
        return True

    def _auto_discover_if_needed_from_typed_update(self) -> None:
        """Run DISCOVER automatically for select typed-data updates."""
        if not self._typed_update_needs_discover():
            return
        if not (self.ready and self.bridge and self.mainloop):
            LOGGER.info(
                "Typed update needs discover, but bridge is not ready yet; skipping auto-discover"
            )
            return
        try:
            LOGGER.info(
                "Typed update has hap_pin with empty id/name and no cached discover; running auto-discover"
            )
            fut = asyncio.run_coroutine_threadsafe(
                self.bridge.discover_collect(12.0), self.mainloop
            )
            rows = fut.result(timeout=30)
            n = len(rows) if rows else 0
            LOGGER.info("HomeKit auto-discover: scan finished, %d accessory(ies)", n)
            self._present_hap_discover_results(rows or [], source="auto")
        except Exception as e:
            self.report_error(
                ERR_DISCOVER_SCAN,
                "homekit_err_discover",
                "HomeKit auto-discover scan failed",
                exc=e,
                log_message="HomeKit auto-discover: scan failed",
            )

    @staticmethod
    def _discover_endpoint_str(r: dict[str, Any]) -> str:
        h = (str(r.get("host") or "")).strip()
        p = r.get("port")
        if h and p is not None and str(p).strip() != "":
            return f"{h}:{p}"
        return ""

    @staticmethod
    def _pairing_row_needs_discover_fill(row: dict[str, Any]) -> bool:
        """True if row has no PIN and no id/name — safe to fill from DISCOVER."""
        if (row.get("hap_pin") or "").strip():
            return False
        if (row.get("accessory_id") or "").strip():
            return False
        if (row.get("accessory_name") or "").strip():
            return False
        return True

    @staticmethod
    def _take_next_free_slot(used: Set[int]) -> int:
        """Smallest positive integer not in ``used``; adds it to ``used``."""
        n = 1
        while n in used:
            n += 1
        used.add(n)
        return n

    def _used_pairing_slots_from_rows(self, rows: List[dict[str, Any]]) -> Set[int]:
        out: Set[int] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            sn = _parse_slot_value(row.get("slot"))
            if sn is not None:
                out.add(sn)
        return out

    def _append_pairing_rows_for_discover(self, discover_rows: list) -> tuple[int, int, int]:
        """
        Sync unpaired DISCOVER results into **Custom Typed** ``pairing_slots``:

        - **Merge**: existing row with same ``accessory_id`` gets missing ``accessory_name``
          and ``discover_endpoint`` refreshed from the scan.
        - **Fill**: empty placeholder rows (no PIN, no id, no name — slot-only is ok)
          consume unpaired devices not yet listed.
        - **Append**: remaining unpaired devices get new rows.

        Each row is pre-filled with ``accessory_id``, ``accessory_name``, optional
        ``discover_endpoint``, and an unused **slot** number; user adds ``hap_pin`` and saves.

        Returns ``(n_appended, n_filled_blanks, n_merged)``.
        """
        if not discover_rows:
            return (0, 0, 0)
        unpaired = [r for r in discover_rows if isinstance(r, dict) and not r.get("paired")]
        if not unpaired:
            return (0, 0, 0)
        try:
            raw = self.TypedData.get(TYPED_PAIRING_SLOTS_KEY)
        except Exception:
            raw = None
        if not isinstance(raw, list):
            current = []
        else:
            current = [dict(x) for x in raw if isinstance(x, dict)]

        used_slots = self._used_pairing_slots_from_rows(current)

        by_id: dict[str, dict[str, Any]] = {}
        for r in unpaired:
            pid = (str(r.get("id") or "")).strip().lower()
            if pid:
                by_id[pid] = r

        seen: Set[str] = set()
        for row in current:
            aid = (row.get("accessory_id") or "").strip().lower()
            if aid:
                seen.add(aid)

        n_merged = 0
        for row in current:
            if not isinstance(row, dict):
                continue
            pid = (row.get("accessory_id") or "").strip().lower()
            if not pid or pid not in by_id:
                continue
            r = by_id[pid]
            changed = False
            pname = (str(r.get("name") or "")).strip()
            if pname and not (row.get("accessory_name") or "").strip():
                row["accessory_name"] = pname
                changed = True
            ep = self._discover_endpoint_str(r)
            if ep and (row.get("discover_endpoint") or "").strip() != ep:
                row["discover_endpoint"] = ep
                changed = True
            if _parse_slot_value(row.get("slot")) is None:
                row["slot"] = str(self._take_next_free_slot(used_slots))
                changed = True
            if changed:
                n_merged += 1

        blank_idxs = [i for i, row in enumerate(current) if isinstance(row, dict) and self._pairing_row_needs_discover_fill(row)]
        bi = 0
        n_filled = 0
        for r in unpaired:
            pid = (str(r.get("id") or "")).strip().lower()
            if not pid or pid in seen:
                continue
            if bi >= len(blank_idxs):
                break
            idx = blank_idxs[bi]
            bi += 1
            row = current[idx]
            pname = (str(r.get("name") or "")).strip()
            row["accessory_id"] = pid
            if pname:
                row["accessory_name"] = pname
            ep = self._discover_endpoint_str(r)
            if ep:
                row["discover_endpoint"] = ep
            row.setdefault("hap_pin", "")
            if _parse_slot_value(row.get("slot")) is None:
                row["slot"] = str(self._take_next_free_slot(used_slots))
            seen.add(pid)
            n_filled += 1

        n_appended = 0
        for r in unpaired:
            pid = (str(r.get("id") or "")).strip().lower()
            if not pid or pid in seen:
                continue
            pname = (str(r.get("name") or "")).strip()
            ep = self._discover_endpoint_str(r)
            new_row: Dict[str, Any] = {
                "slot": str(self._take_next_free_slot(used_slots)),
                "hap_pin": "",
                "accessory_id": pid,
                "accessory_name": pname,
            }
            if ep:
                new_row["discover_endpoint"] = ep
            current.append(new_row)
            seen.add(pid)
            n_appended += 1

        if n_appended == 0 and n_filled == 0 and n_merged == 0:
            return (0, 0, 0)
        try:
            td = self._typed_data_dict()
            td[TYPED_PAIRING_SLOTS_KEY] = current
            self.TypedData.load(td, save=True)
        except Exception as e:
            self.report_error(
                ERR_TYPED_SAVE,
                "homekit_err_config",
                "Failed to save Custom Typed data after discover",
                exc=e,
                log_message="Failed to save Custom Typed data after discover",
            )
            return (0, 0, 0)
        if self.ready:
            self._maybe_restart_on_config_change()
        return (n_appended, n_filled, n_merged)

    def _present_hap_discover_results(self, rows: list, *, source: str = "manual") -> None:
        """Persist discover snapshot and UI notice.

        ``source="auto"`` + empty ``rows``: do not clear ``last_hap_discover`` or replace the
        DISCOVER notice (PG3 often sends partial typed payloads; an empty auto-scan should not
        wipe a prior snapshot).
        """
        if source == "auto" and not rows:
            LOGGER.info(
                "HomeKit auto-discover: no accessories in scan window; leaving "
                "%s and hap_discover notice unchanged. Run DISCOVER on the controller with the "
                "accessory in HomeKit pairing mode; check LAN/mDNS (CONFIG.md).",
                DATA_KEY_LAST_HAP_DISCOVER,
            )
            return
        try:
            d = {k: self.Data[k] for k in self.Data.keys()}
        except Exception:
            d = {}
        d[DATA_KEY_LAST_HAP_DISCOVER] = list(rows) if rows else []
        self.Data.load(d, save=True)
        try:
            LOGGER.debug(
                "customdata saved after discover: rows=%d keys=%s",
                len(rows) if rows else 0,
                sorted(list(d.keys())),
            )
        except Exception:
            LOGGER.debug("customdata saved after discover (key introspection failed)")

        n_appended = n_filled = n_merged = 0
        try:
            n_appended, n_filled, n_merged = self._append_pairing_rows_for_discover(rows)
        except Exception as e:
            self.report_error(
                ERR_APPEND_PAIRING,
                "homekit_err_config",
                "Failed to update pairing rows after discover",
                exc=e,
                log_message="append_pairing_rows_for_discover",
            )

        if not rows:
            self.Notices["hap_discover"] = (
                "HomeKit discover: no accessories found. Check LAN, firewall, and that the device is in pairing mode."
            )
            return

        unpaired = [r for r in rows if not r.get("paired")]
        paired = [r for r in rows if r.get("paired")]
        parts = [
            "<b>HomeKit discover</b> — <code>last_hap_discover</code> is saved and "
            "<b>Custom Typed &gt; HomeKit pairing slots</b> is updated with "
            "<b>slot</b>, <b>accessory id</b>, <b>name</b>, and <b>LAN host:port</b> (informational) "
            "where needed. <b>Enter only the HomeKit pairing code</b> on each target row, then save.<br/>"
            f"(Snapshot: <code>{html.escape(DATA_KEY_LAST_HAP_DISCOVER)}</code>.)<br/>",
        ]
        n_typed_total = n_appended + n_filled + n_merged
        if n_appended:
            parts.append(f"Added <b>{n_appended}</b> new row(s) for unpaired accessories.<br/>")
        if n_filled:
            parts.append(
                f"Filled <b>{n_filled}</b> empty row(s) (no PIN / id / name yet) with discover details.<br/>"
            )
        if n_merged:
            parts.append(
                f"Refreshed <b>{n_merged}</b> existing row(s) with the latest discover name or address.<br/>"
            )
        if not n_typed_total:
            parts.append(
                "No changes to <b>HomeKit pairing slots</b> (every unpaired device already had a row, "
                "or none were unpaired).<br/>"
            )
        if n_typed_total:
            parts.append(
                "<b>Refresh</b> the node server <b>configuration</b> page (reload the editor) "
                "to see updated rows under <b>Custom Typed</b> &gt; <b>HomeKit pairing slots</b>.<br/>"
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

    def _on_bonjour_event(self, payload: Any) -> None:
        """Capture BONJOUR responses while a comparison is active.

        Matches the IoX / ``ioxplugin`` pattern: Polyglot delivers the same dict
        ``__bonjourHandler`` sees — typically ``command``, ``mdns``, ``success``.
        We keep the raw payload verbatim in the compare JSON.
        """
        if isinstance(payload, dict):
            cmd = payload.get("command")
            if cmd is not None and cmd != "bonjour":
                LOGGER.debug("BONJOUR event ignored during compare (unexpected command=%r)", cmd)
                return
        with self._bonjour_lock:
            if not self._bonjour_active:
                return
            try:
                self._bonjour_buf.append(payload)
            except Exception:
                LOGGER.exception("BONJOUR buffer append failed")
        try:
            LOGGER.debug("BONJOUR payload received during compare: %s", json.dumps(payload, default=str)[:2000])
        except Exception:
            LOGGER.debug("BONJOUR payload received during compare (non-serializable): %r", payload)

    @staticmethod
    def _normalize_bj_records(payload_list: list[Any]) -> list[dict[str, Any]]:
        """Best-effort flattening of PG3 BONJOUR payloads into row dicts.

        The PG3 server payload schema is not publicly documented. This handles
        the common shapes: dict with a list under ``services``/``records``/``mdns``,
        a top-level list of records, and individual records.
        """
        rows: list[dict[str, Any]] = []

        def _push(rec: Any) -> None:
            if not isinstance(rec, dict):
                return
            rows.append(rec)

        for payload in payload_list or []:
            if isinstance(payload, list):
                for item in payload:
                    _push(item)
                continue
            if not isinstance(payload, dict):
                continue
            # IoX template uses message["mdns"] (see iox_controller_template.__bonjourHandler).
            for key in ("mdns", "services", "records", "results", "bonjour"):
                if isinstance(payload.get(key), list):
                    for item in payload[key]:
                        _push(item)
                    break
            else:
                _push(payload)
        return rows

    @staticmethod
    def _bj_extract_id(rec: dict[str, Any]) -> str:
        for key_path in (("txt", "id"), ("properties", "id"), ("TXT", "id"), ("id",)):
            cur: Any = rec
            ok = True
            for k in key_path:
                if isinstance(cur, dict) and k in cur:
                    cur = cur[k]
                else:
                    ok = False
                    break
            if ok and isinstance(cur, str) and cur:
                return cur.lower()
        return ""

    def cmd_bonjour_compare(self, _cmd=None) -> None:
        """Capture PG3 BONJOUR + raw zeroconf + aiohomekit-normalized samples.

        Writes both the raw payloads and a normalized diff to
        ``logs/bonjour_compare_<timestamp>.json`` and to
        ``Custom('compare')['bonjour_compare_last']``. Posts a Notice with
        per-source counts and overlap. See ``BONJOUR_FEASIBILITY.md``.

        The heavy work runs on a background thread so the Polyglot **Command**
        thread can dequeue incoming ``bonjour`` MQTT replies and deliver
        ``BONJOUR`` events. Blocking this thread on ``Future.result()`` would
        otherwise stall those responses (empty ``_bonjour_buf``).
        """
        if not (self.bridge and self.mainloop):
            self.report_error(
                ERR_DISCOVER_UNEXPECTED,
                "homekit_err_discover",
                "BONJOUR compare skipped: bridge not ready",
                log_message="cmd_bonjour_compare: bridge not ready",
            )
            return
        LOGGER.info("BONJOUR_COMPARE: starting background worker (Command thread returns immediately)")
        Thread(target=self._bonjour_compare_worker, name="bonjour_compare", daemon=True).start()

    def _bonjour_compare_worker(self) -> None:
        """Run BONJOUR compare off the Command thread (see ``cmd_bonjour_compare``)."""
        bj_payloads: list[Any] = []
        with self._bonjour_lock:
            self._bonjour_buf = []
            self._bonjour_active = True

        try:
            try:
                # Same shape as ioxplugin ``searchForDevicesUsingMDNS`` / udi_interface ``bonjour``:
                # type, subtypes (None = no subtype filter), protocol per query.
                self.poly.bonjour("_hap", None, "tcp")
                self.poly.bonjour("_hap", None, "udp")
            except Exception as e:
                LOGGER.exception("polyglot.bonjour() failed")
                self.report_error(
                    ERR_DISCOVER_UNEXPECTED,
                    "homekit_err_discover",
                    "polyglot.bonjour() call failed",
                    exc=e,
                )

            zc_fut = asyncio.run_coroutine_threadsafe(
                self.bridge.discover_collect(BONJOUR_COMPARE_WINDOW_SECONDS),
                self.mainloop,
            )
            raw_fut = asyncio.run_coroutine_threadsafe(
                self.bridge.discover_collect_raw_zc(BONJOUR_COMPARE_WINDOW_SECONDS),
                self.mainloop,
            )
            wait_timeout = BONJOUR_COMPARE_WINDOW_SECONDS + 10.0
            try:
                ahk_rows = zc_fut.result(timeout=wait_timeout)
            except Exception as e:
                LOGGER.exception("aiohomekit discover_collect failed during compare")
                ahk_rows = []
                self.report_error(
                    ERR_DISCOVER_SCAN,
                    "homekit_err_discover",
                    "aiohomekit scan failed during BONJOUR compare",
                    exc=e,
                )
            try:
                raw_rows = raw_fut.result(timeout=wait_timeout)
            except Exception as e:
                LOGGER.exception("raw zeroconf scan failed during compare")
                raw_rows = []
                self.report_error(
                    ERR_DISCOVER_SCAN,
                    "homekit_err_discover",
                    "Raw zeroconf scan failed during BONJOUR compare",
                    exc=e,
                )

            # Let the Command thread finish dequeuing BONJOUR for any replies
            # that land immediately after the asyncio window.
            time.sleep(0.25)

            with self._bonjour_lock:
                bj_payloads = list(self._bonjour_buf)
                self._bonjour_active = False
                self._bonjour_buf = []
        finally:
            with self._bonjour_lock:
                self._bonjour_active = False

        bj_records = self._normalize_bj_records(bj_payloads)
        ids_bj = {self._bj_extract_id(r) for r in bj_records if self._bj_extract_id(r)}
        ids_ahk = {(r.get("id") or "").lower() for r in ahk_rows if r.get("id")}
        ids_zc = {(r.get("id") or "").lower() for r in raw_rows if r.get("id")}

        result = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "window_seconds": BONJOUR_COMPARE_WINDOW_SECONDS,
            "counts": {
                "bonjour_payloads": len(bj_payloads),
                "bonjour_records_normalized": len(bj_records),
                "aiohomekit_rows": len(ahk_rows),
                "raw_zeroconf_rows": len(raw_rows),
            },
            "ids": {
                "bonjour": sorted(ids_bj),
                "aiohomekit": sorted(ids_ahk),
                "raw_zeroconf": sorted(ids_zc),
                "in_bonjour_only": sorted(ids_bj - ids_ahk - ids_zc),
                "in_zeroconf_only": sorted((ids_ahk | ids_zc) - ids_bj),
                "overlap_all_three": sorted(ids_bj & ids_ahk & ids_zc),
            },
            "raw_bonjour_payloads": bj_payloads,
            "bonjour_records_normalized": bj_records,
            "aiohomekit_rows": ahk_rows,
            "raw_zeroconf_rows": raw_rows,
        }

        try:
            self.CompareData["bonjour_compare_last"] = result
        except Exception:
            LOGGER.exception("save bonjour_compare_last to Custom('compare')")

        log_dir = Path(__file__).resolve().parent.parent / "logs"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            LOGGER.exception("create logs/ directory for compare output")
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        out_path = log_dir / f"bonjour_compare_{ts}.json"
        try:
            with out_path.open("w", encoding="utf-8") as fh:
                json.dump(result, fh, indent=2, default=str)
            LOGGER.info("BONJOUR_COMPARE: worker wrote %s", out_path)
        except Exception:
            LOGGER.exception("write %s", out_path)

        notice_parts = [
            "<b>BONJOUR vs Zeroconf compare</b><br/>",
            f"PG3 BONJOUR: {len(bj_records)} record(s) "
            f"(from {len(bj_payloads)} payload(s)).<br/>",
            f"aiohomekit (filtered): {len(ahk_rows)} accessory(ies).<br/>",
            f"Raw zeroconf TXT: {len(raw_rows)} record(s).<br/>",
            f"Overlap (all three): <b>{len(ids_bj & ids_ahk & ids_zc)}</b>. "
            f"BONJOUR-only: {len(ids_bj - ids_ahk - ids_zc)}. "
            f"Zeroconf-only: {len((ids_ahk | ids_zc) - ids_bj)}.<br/>",
            f"Saved to <code>{html.escape(str(out_path))}</code> and "
            "<code>Custom('compare')['bonjour_compare_last']</code>. See "
            "<code>BONJOUR_FEASIBILITY.md</code> for context.",
        ]
        self.Notices["bonjour_compare"] = "".join(notice_parts)

    def heartbeat(self):
        if self.hb == 0:
            self.reportCmd("DON", 2)
            self.hb = 1
        else:
            self.reportCmd("DOF", 2)
            self.hb = 0

    def query(self):
        self.reportDrivers()

    def cmd_discover(self, command=None):
        """DISCOVER from ISY/PG3 UI (runCmd); same work as ``handler_discover``."""
        self.handler_discover()

    # Must match profile/nodedefs.xml; runCmd only sees commands listed here.
    id = "HKHubController"
    commands = {
        "DISCOVER": cmd_discover,
        "QUERY": query,
        "BONJOUR_COMPARE": cmd_bonjour_compare,
    }
    drivers = [
        {"driver": "ST", "value": 1, "uom": 25, "name": "Hub status"},
        {"driver": "ERR", "value": 0, "uom": 25, "name": "Hub error code"},
    ]
