#!/usr/bin/env python3
"""PG3x controller: asyncio HomeKit bridge + Polyglot lifecycle."""

import asyncio
import html
import json
from pathlib import Path
from threading import Thread, Timer
from typing import Any, Dict, List, Optional, Set

import markdown2
from udi_interface import LOGGER, Custom, Node

from homekit_hub import (
    DATA_KEY_LAST_HAP_DISCOVER,
    TYPED_PAIRING_SLOTS_KEY,
    HomeKitHubBridge,
    assign_pairing_slot_rows,
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
# 8–9: pairing (see profile NLS)
ERR_ASYNC_LOOP_DEAD = 10

# Merged into Custom Params before the bridge reads them (PG3 may omit unset keys).
_DEFAULT_BRIDGE_PARAMS: dict[str, str] = {
    "ws_host": "127.0.0.1",
    "ws_port": "8163",
    # Matches prior entrypoint default: unicast-friendly when mDNS 5353 is shared.
    "zeroconf_unicast": "on",
    "zeroconf_interfaces": "",
    "zeroconf_ip_version": "",
}

# After CONFIGDONE, PG3 may still be delivering CUSTOM* events on other threads; retry briefly.
_HUB_BOOTSTRAP_AFTER_CONFIG_MAX_ATTEMPTS = 120
_HUB_BOOTSTRAP_AFTER_CONFIG_RETRY_SEC = 0.1
# If CONFIGDONE never arrives (misbehaving client), still try once custom handlers have run.
_HUB_BOOTSTRAP_FALLBACK_SEC = 75.0


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
        self._known_paired_ids: Set[str] = set()
        self._pair_success_notice_polls_remaining = 0
        self.mainloop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: Any = None
        self._async_loop_death_reported = False
        self.bridge: HomeKitHubBridge | None = None
        self._pending_hub_bootstrap = False
        self._hub_bootstrap_generation = 0

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
        raw = {k: self.Params[k] for k in self.Params.keys()}
        out: dict[str, Any] = {}
        for k, default in _DEFAULT_BRIDGE_PARAMS.items():
            v = raw.get(k, default)
            if v is None or (isinstance(v, str) and not str(v).strip()):
                out[k] = default
            else:
                out[k] = v
        for k, v in raw.items():
            if k not in out:
                out[k] = v
        return out

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
        p = self._bridge_get_params()
        snap = {
            "ws_host": p.get("ws_host"),
            "ws_port": p.get("ws_port"),
            "zeroconf_unicast": p.get("zeroconf_unicast"),
            "zeroconf_interfaces": p.get("zeroconf_interfaces"),
            "zeroconf_ip_version": p.get("zeroconf_ip_version"),
        }
        rows = self._bridge_get_pairing_slot_rows()
        snap["_pairing_slots"] = json.dumps(rows, sort_keys=True, default=str)
        return snap

    def _maybe_restart_on_config_change(self) -> None:
        snap = self._config_restart_snap()
        prev = self._config_snap
        self._config_snap = snap
        if not (self.ready and self.mainloop and self.bridge and prev is not None):
            return
        if snap == prev:
            return
        network_changed = (
            snap.get("ws_host") != prev.get("ws_host")
            or snap.get("ws_port") != prev.get("ws_port")
            or snap.get("zeroconf_unicast") != prev.get("zeroconf_unicast")
            or snap.get("zeroconf_interfaces") != prev.get("zeroconf_interfaces")
            or snap.get("zeroconf_ip_version") != prev.get("zeroconf_ip_version")
        )
        pairing_changed = snap.get("_pairing_slots") != prev.get("_pairing_slots")
        if network_changed:
            LOGGER.info("Hub bind/zeroconf config changed; restarting bridge")
            try:
                self.setDriver("GV0", 0, report=True, force=True, uom=25)
            except Exception:
                LOGGER.exception("setDriver GV0 before full_restart")
            fut = asyncio.run_coroutine_threadsafe(
                self.bridge.full_restart(), self.mainloop
            )
            fut.add_done_callback(self._on_full_restart_done)
        elif pairing_changed:
            LOGGER.info("Typed pairing config changed; reloading HomeKit sessions")
            asyncio.run_coroutine_threadsafe(self.bridge.restart_session(), self.mainloop)

    def _on_full_restart_done(self, fut) -> None:
        """Apply Bridge Status after ``full_restart`` completes (config-driven recycle)."""
        try:
            fut.result()
        except Exception:
            LOGGER.exception("Bridge full restart after config change failed")
            try:
                self.setDriver("GV0", 2, report=True, force=True, uom=25)
                self.setDriver("ERR", ERR_BRIDGE_START, report=True, force=True, uom=25)
            except Exception:
                LOGGER.exception("setDriver after full_restart failure")
            self.ready = False
            return
        try:
            self.setDriver("GV0", 1, report=True, force=True, uom=25)
            self.setDriver("ERR", ERR_OK, report=True, force=True, uom=25)
        except Exception:
            LOGGER.exception("setDriver GV0 after full_restart")
        self.ready = True

    def handler_config_done(self):
        self.handler_config_done_st = True
        self._try_finish_hub_bootstrap(attempt=0, reason="CONFIGDONE")

    def _custom_handlers_have_run(self) -> bool:
        """True once each CUSTOM* handler has been invoked at least once (value may be False for data)."""
        return (
            self.handler_params_st is not None
            and self.handler_data_st is not None
            and self.handler_typedparams_st is not None
            and self.handler_typed_data_st is not None
        )

    def _try_finish_hub_bootstrap(self, attempt: int = 0, *, reason: str = "") -> None:
        """Start asyncio hub after Polyglot config is loaded (CONFIGDONE + custom handlers).

        ``udi_interface`` publishes CONFIGDONE after ``getAll``; CUSTOM* handlers may still be
        finishing on other threads, so we retry with short delays instead of blocking with
        ``time.sleep`` in ``handler_start``.
        """
        if not self._pending_hub_bootstrap:
            return
        if self.mainloop is not None:
            return
        if not self._custom_handlers_have_run():
            if attempt >= _HUB_BOOTSTRAP_AFTER_CONFIG_MAX_ATTEMPTS:
                LOGGER.error(
                    "Timeout waiting for custom params/data/typed after %s (last attempt %d)",
                    reason or "bootstrap",
                    attempt,
                )
                self._pending_hub_bootstrap = False
                return
            if attempt == 0:
                LOGGER.warning(
                    "Hub bootstrap (%s): custom config not ready yet; retrying without blocking START",
                    reason or "pending",
                )
            Timer(
                _HUB_BOOTSTRAP_AFTER_CONFIG_RETRY_SEC,
                lambda: self._try_finish_hub_bootstrap(attempt + 1, reason=reason),
            ).start()
            return

        self._pending_hub_bootstrap = False
        self._run_hub_bootstrap()

    def _run_hub_bootstrap(self) -> None:
        """Create asyncio loop thread, bridge, and start listening (PG3 thread)."""
        # One-shot migration: dashed PIN in UI for codes stored without dashes.
        self._normalize_and_persist_typed_pairing_pins()

        self.mainloop = asyncio.new_event_loop()

        def _run_loop():
            asyncio.set_event_loop(self.mainloop)
            self.mainloop.run_forever()

        self._loop_thread = Thread(target=_run_loop, daemon=True)
        self._loop_thread.start()
        try:
            self.setDriver("ST", 1, report=True, force=True, uom=25)
            self.setDriver("GV0", 0, report=True, force=True, uom=25)
        except Exception:
            LOGGER.exception("setDriver ST/GV0 before bridge start")

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
                    "may own that port. See <b>CONFIG.md</b> — Custom Params "
                    "<code>zeroconf_*</code> (default keeps unicast-friendly behavior on shared mDNS hosts); "
                    "environment variables override those when set. On Linux with Avahi, "
                    "<code>disallow-other-stacks=no</code> in <code>avahi-daemon.conf</code> can help.<br/>"
                ),
                set_st_error=True,
            )
            return

        self.clear_hub_error_indicators(keep_config_notice=True)
        try:
            self.setDriver("GV0", 1, report=True, force=True, uom=25)
        except Exception:
            LOGGER.exception("setDriver GV0 after bridge start")
        self.heartbeat()
        self.ready = True
        LOGGER.info("HomeKit Hub ready")

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

        Use **set_st_error** only for hub-fatal faults (sets **GV0** Bridge Status = 2 = Error).
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
                self.setDriver("GV0", 2, report=True, force=True, uom=25)
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

    def _check_asyncio_loop_thread_health(self) -> None:
        """If the asyncio loop thread dies while the hub is ready, surface bridge failure on ISY.

        ``longPoll`` runs on the PG3 thread. **ST** stays under Polyglot control (Node Server
        connection only); this path sets **GV0** = Error and **ERR** via ``report_error`` (and
        clears ``ready``) so operators still see a dead hub without conflating **ST**.
        """
        t = self._loop_thread
        if t is None or t.is_alive():
            return
        if not self.ready:
            return
        if self._async_loop_death_reported:
            return
        self._async_loop_death_reported = True
        self.ready = False
        self.report_error(
            ERR_ASYNC_LOOP_DEAD,
            "homekit_bridge",
            "HomeKit Hub asyncio loop stopped unexpectedly",
            log_message="asyncio loop thread is not alive while hub was ready",
            extra_html=(
                "The background event loop thread exited while the hub was running. "
                "Restart the Node Server from Polyglot.<br/>"
            ),
            set_st_error=True,
        )

    def handler_data(self, data):
        if data is None:
            LOGGER.warning("No custom data on first run")
            self.handler_data_st = False
            return
        self.Data.load(data)
        self._maybe_post_pair_success_notice()
        self._maybe_clear_hap_discover_notice_for_paired()
        self._maybe_clear_pairing_error_notice_for_success()
        self.handler_data_st = True

    def _current_paired_ids_from_data(self) -> Set[str]:
        try:
            pairings = self.Data.get("homekit_pairings")
        except Exception:
            return set()
        if not isinstance(pairings, dict):
            return set()
        return {
            str(v.get("AccessoryPairingID") or "").strip().lower()
            for v in pairings.values()
            if isinstance(v, dict) and str(v.get("AccessoryPairingID") or "").strip()
        }

    def _maybe_post_pair_success_notice(self) -> None:
        """Post a transient notice when new pairing id(s) appear in customdata."""
        current_ids = self._current_paired_ids_from_data()
        new_ids = current_ids - self._known_paired_ids
        self._known_paired_ids = set(current_ids)
        if not new_ids:
            return
        ids_txt = ", ".join(sorted(new_ids))
        self.Notices["homekit_pair_success"] = (
            "<b>HomeKit pairing successful</b><br/>"
            f"Paired device id(s): <code>{html.escape(ids_txt)}</code><br/>"
            "This notice clears automatically after two long polls."
        )
        self._pair_success_notice_polls_remaining = 2
        LOGGER.info(
            "Posted transient pairing success notice for id(s): %s",
            ids_txt,
        )

    def _maybe_clear_hap_discover_notice_for_paired(self) -> None:
        """Clear stale DISCOVER notice when listed device is now paired here."""
        try:
            notice_text = self.Notices.get("hap_discover")
        except Exception:
            notice_text = None
        if not notice_text:
            return
        try:
            last = self.Data.get(DATA_KEY_LAST_HAP_DISCOVER)
        except Exception:
            last = None
        if not isinstance(last, list) or not last:
            return
        discover_ids = {
            str(r.get("id") or "").strip().lower()
            for r in last
            if isinstance(r, dict) and str(r.get("id") or "").strip()
        }
        if not discover_ids:
            return
        try:
            pairings = self.Data.get("homekit_pairings")
        except Exception:
            pairings = None
        if not isinstance(pairings, dict) or not pairings:
            return
        paired_ids = {
            str(v.get("AccessoryPairingID") or "").strip().lower()
            for v in pairings.values()
            if isinstance(v, dict) and str(v.get("AccessoryPairingID") or "").strip()
        }
        if not paired_ids:
            return
        if discover_ids.isdisjoint(paired_ids):
            return
        try:
            self.Notices.delete("hap_discover")
        except Exception:
            try:
                del self.Notices["hap_discover"]
            except Exception:
                return
        LOGGER.info(
            "Cleared hap_discover notice: discovered device now paired by this plugin (%s)",
            ", ".join(sorted(discover_ids & paired_ids)),
        )

    def _maybe_clear_pairing_error_notice_for_success(self) -> None:
        """Clear stale pairing error notice after a successful saved pairing appears."""
        try:
            notice_text = self.Notices.get("homekit_err_config")
        except Exception:
            notice_text = None
        if not notice_text:
            return
        text = str(notice_text)
        if (
            "HomeKit pairing code rejected" not in text
            and "HomeKit pairing failed" not in text
            and "pairing error" not in text
        ):
            return
        try:
            pairings = self.Data.get("homekit_pairings")
        except Exception:
            pairings = None
        if not isinstance(pairings, dict):
            return
        paired_ids = {
            str(v.get("AccessoryPairingID") or "").strip().lower()
            for v in pairings.values()
            if isinstance(v, dict) and str(v.get("AccessoryPairingID") or "").strip()
        }
        if not paired_ids:
            return
        try:
            self.Notices.delete("homekit_err_config")
        except Exception:
            try:
                del self.Notices["homekit_err_config"]
            except Exception:
                return
        try:
            self.setDriver("ERR", ERR_OK, report=True, force=True, uom=25)
        except Exception:
            LOGGER.exception("Could not reset ERR after clearing pairing error notice")
        LOGGER.info(
            "Cleared homekit_err_config pairing error notice after successful pairing (%s)",
            ", ".join(sorted(paired_ids)),
        )

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
        self._async_loop_death_reported = False
        self._hub_bootstrap_generation += 1
        bootstrap_gen = self._hub_bootstrap_generation
        self.Notices.clear()
        LOGGER.info("HomeKit Hub NodeServer %s (profile %s)", self.poly.serverdata.get("version"), VERSION)
        cfg_md = Path(__file__).resolve().parent.parent / "CONFIG.md"
        if cfg_md.is_file():
            try:
                self.poly.setCustomParamsDoc(
                    markdown2.markdown_path(
                        str(cfg_md),
                        extras=["tables", "fenced-code-blocks"],
                    )
                )
            except Exception:
                LOGGER.exception("Failed to convert/set CONFIG.md as custom params doc")

        self._pending_hub_bootstrap = True
        LOGGER.info(
            "Hub bootstrap deferred until CONFIGDONE and custom params/data/typed are loaded "
            "(no blocking wait in START)"
        )

        def _fallback_bootstrap() -> None:
            if self._hub_bootstrap_generation != bootstrap_gen:
                return
            if not self._pending_hub_bootstrap or self.mainloop is not None:
                return
            LOGGER.warning(
                "CONFIGDONE not received within %.0fs; attempting hub bootstrap if custom config is ready",
                _HUB_BOOTSTRAP_FALLBACK_SEC,
            )
            self._try_finish_hub_bootstrap(attempt=0, reason="CONFIGDONE fallback timer")

        Timer(_HUB_BOOTSTRAP_FALLBACK_SEC, _fallback_bootstrap).start()

        if self.handler_config_done_st:
            LOGGER.info(
                "CONFIGDONE already received before START; will bootstrap when custom config is ready"
            )
            self._try_finish_hub_bootstrap(attempt=0, reason="START after CONFIGDONE")

    def handler_stop(self):
        LOGGER.info("Stopping HomeKit Hub")
        self.ready = False
        try:
            self.setDriver("GV0", 0, report=True, force=True, uom=25)
            self.setDriver("ST", 0, report=True, force=True, uom=25)
        except Exception:
            LOGGER.exception("setDriver ST/GV0 on stop")
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
        self._pending_hub_bootstrap = False
        self._loop_thread = None
        self.mainloop = None
        self.bridge = None
        LOGGER.info("HomeKit Hub stopped")

    def handler_poll(self, polltype):
        if polltype == "longPoll":
            self._check_asyncio_loop_thread_health()
            self.heartbeat()
            if self._pair_success_notice_polls_remaining > 0:
                self._pair_success_notice_polls_remaining -= 1
                if self._pair_success_notice_polls_remaining == 0:
                    try:
                        self.Notices.delete("homekit_pair_success")
                    except Exception:
                        try:
                            del self.Notices["homekit_pair_success"]
                        except Exception:
                            pass
                    LOGGER.info("Cleared transient pairing success notice after 2 longPolls")

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
        Sync DISCOVER results into **Custom Typed** ``pairing_slots``:

        - **Merge**: existing row with same ``accessory_id`` gets missing ``accessory_name``
          and ``discover_endpoint`` refreshed from the scan.
        - **Fill**: empty placeholder rows (no PIN, no id, no name — slot-only is ok)
          consume discover devices not yet listed.
        - **Append**: remaining discover devices get new rows.

        Preferred source is unpaired devices. If none are unpaired, we still seed rows
        from paired discoveries so users who deleted a row can re-create it via DISCOVER
        without manually typing accessory id/name.

        Each row is pre-filled with ``accessory_id``, ``accessory_name``, optional
        ``discover_endpoint``, and an unused **slot** number; user adds ``hap_pin`` and saves.

        Returns ``(n_appended, n_filled_blanks, n_merged)``.
        """
        if not discover_rows:
            return (0, 0, 0)
        unpaired = [r for r in discover_rows if isinstance(r, dict) and not r.get("paired")]
        paired = [r for r in discover_rows if isinstance(r, dict) and r.get("paired")]
        candidates = unpaired if unpaired else paired
        if not candidates:
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
        for r in candidates:
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
        n_typed_total = n_appended + n_filled + n_merged
        if n_typed_total:
            typed_blurb = (
                "<b>Custom Typed &gt; HomeKit pairing slots</b> is updated with "
                "<b>slot</b>, <b>accessory id</b>, <b>name</b>, and <b>LAN host:port</b> (informational) "
                "where needed."
            )
        else:
            typed_blurb = (
                "<b>Custom Typed &gt; HomeKit pairing slots</b> was checked, with no row changes in this scan."
            )
        parts = [
            "<b>HomeKit discover</b> — <code>last_hap_discover</code> is saved and "
            f"{typed_blurb} <b>Enter only the HomeKit pairing code</b> on each target row, then save.<br/>"
            f"(Snapshot: <code>{html.escape(DATA_KEY_LAST_HAP_DISCOVER)}</code>.)<br/>",
        ]
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

    def heartbeat(self):
        if self.hb == 0:
            self.reportCmd("DON", 2)
            self.hb = 1
        else:
            self.reportCmd("DOF", 2)
            self.hb = 0

    def query(self):
        try:
            self.setDriver("ST", 1, report=True, force=True, uom=25)
        except Exception:
            LOGGER.exception("setDriver ST on query")
        self.reportDrivers()

    def cmd_discover(self, command=None):
        """DISCOVER from ISY/PG3 UI (runCmd); same work as ``handler_discover``."""
        self.handler_discover()

    def cmd_zeroconf_diag(self, command=None):
        """Post a one-shot Notice with zeroconf mode, transports, and version info."""
        if not (self.ready and self.bridge and self.mainloop):
            LOGGER.warning(
                "ZEROCONF_DIAG skipped: hub not ready (wait for log line 'HomeKit Hub ready')."
            )
            return
        diag = self.bridge.zeroconf_diag()
        line = json.dumps(diag, indent=2, sort_keys=True, default=str)
        LOGGER.info("ZEROCONF_DIAG:\n%s", line)
        self.Notices["zeroconf_diag"] = (
            "<b>Zeroconf / hub diagnostic</b><br/><pre>"
            f"{html.escape(line)}</pre>"
        )

    def cmd_unpair(self, command=None):
        """UNPAIR slot N: clear that slot's pairing code in Custom Typed and reload sessions.

        The resolved slot follows the same assignment rules as the hub (explicit ``slot``
        field or auto-filled slot numbers). Choose the target slot from the command picker.
        """
        cmd = command if isinstance(command, dict) else {}
        try:
            slot = int(cmd.get("value"))
        except (TypeError, ValueError):
            LOGGER.warning("UNPAIR: missing or invalid slot selection")
            return
        if slot < 1:
            LOGGER.warning("UNPAIR: slot must be >= 1 (got %s)", slot)
            return
        try:
            raw_rows = self.TypedData.get(TYPED_PAIRING_SLOTS_KEY)
        except Exception:
            raw_rows = None
        if not isinstance(raw_rows, list):
            LOGGER.warning("UNPAIR: no Custom Typed pairing rows loaded")
            return
        assigned = assign_pairing_slot_rows(raw_rows, LOGGER)
        cleared = False
        matched = False
        for sn, row in assigned:
            if sn != slot:
                continue
            matched = True
            if not isinstance(row, dict):
                break
            if (row.get("hap_pin") or "").strip():
                row["hap_pin"] = ""
                cleared = True
            break
        if not matched:
            LOGGER.warning(
                "UNPAIR: no pairing row resolved to slot %s (check Custom Typed slots)",
                slot,
            )
            return
        if not cleared:
            LOGGER.warning(
                "UNPAIR: slot %s already has an empty pairing code in Custom Typed",
                slot,
            )
            return
        try:
            td = self._typed_data_dict()
            td[TYPED_PAIRING_SLOTS_KEY] = raw_rows
            self.TypedData.load(td, save=True)
        except Exception as e:
            LOGGER.exception("UNPAIR: failed to save Custom Typed data")
            self.report_error(
                ERR_TYPED_SAVE,
                "homekit_err_config",
                "Failed to save Custom Typed data after UNPAIR",
                exc=e,
                log_message="UNPAIR typed save",
            )
            return
        LOGGER.info("UNPAIR: cleared hap_pin for slot %s; reloading hub sessions", slot)
        self._maybe_restart_on_config_change()

    # Must match profile/nodedefs.xml; runCmd only sees commands listed here.
    id = "HKHubController"
    commands = {
        "DISCOVER": cmd_discover,
        "QUERY": query,
        "UNPAIR": cmd_unpair,
        "ZEROCONF_DIAG": cmd_zeroconf_diag,
    }
    drivers = [
        {"driver": "ST", "value": 0, "uom": 25, "name": "NodeServer Online"},
        {"driver": "GV0", "value": 0, "uom": 25, "name": "Bridge Status"},
        {"driver": "ERR", "value": 0, "uom": 25, "name": "Hub error code"},
    ]
