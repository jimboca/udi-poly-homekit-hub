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
    DEFAULT_MQTT_BROKER_HOST,
    DEFAULT_MQTT_BROKER_PORT,
    MQTT_TRANSPORT_STATUS_CONNECTED,
    MQTT_TRANSPORT_STATUS_DISABLED,
    MQTT_TRANSPORT_STATUS_NOT_CONNECTED,
    TYPED_PAIRING_SLOTS_KEY,
    HomeKitHubBridge,
    assign_pairing_slot_rows,
    mqtt_transport_enabled,
    normalize_hap_pin,
)
from homekit_hub.bridge import _parse_slot_value

from nodes import VERSION
from .PairedDeviceNode import PairedDeviceNode

# %% professional-only begin
from dev_settings import (
    dev_edition_override_active,
    edition_at_least,
    licensed_edition,
    resolve_edition,
)
from homekit_hub.paths import ensure_persistent_dir
import homekit_hub.hap_apply as hap_apply
from node_funcs import generic_node_address, generic_node_title, legacy_generic_node_address
from .BinarySensorNode import BinarySensorNode
from .EcobeeThermostatNode import EcobeeThermostatNode
from .LightNode import LightNode
from .SwitchNode import SwitchNode
from .ThermostatNode import ThermostatNode

_DEV_EDITION_NOTICE_KEYS = ('dev_edition_override', 'dev_edition_mismatch')
# %% professional-only end

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
ERR_PAIRING_HEALTH = 11

# Merged into Custom Params before the bridge reads them (PG3 may omit unset keys).
_DEFAULT_BRIDGE_PARAMS: dict[str, str] = {
    "ws_host": "127.0.0.1",
    "ws_port": "8163",
    # Optional shared secret: when non-empty, WebSocket clients must send it on ``hello`` (see PROTOCOL.md).
    "ws_token": "",
    # Optional LAN MQTT (same JSON as WebSocket); see PROTOCOL.md / CONFIG.md.
    "mqtt_enable": "true",
    "mqtt_host": DEFAULT_MQTT_BROKER_HOST,
    "mqtt_port": str(DEFAULT_MQTT_BROKER_PORT),
    "mqtt_username": "",
    "mqtt_password": "",
    "mqtt_hub_slug": "default",
    # Matches prior entrypoint default: unicast-friendly when mDNS 5353 is shared.
    "zeroconf_unicast": "on",
    "zeroconf_interfaces": "",
    "zeroconf_ip_version": "",
    # IoX child node titles: see CONFIG.md (string true/false; merged like other Custom Params).
    "change_node_names": "true",
    # Professional: master switch for generic IoX child nodes (default off).
    "generic_nodes_enable": "false",
    # Ecobee HK: minimum heat/cool delta when writing thresholds (degrees F unless stat uses °C).
    "hk_heat_cool_min_delta": "3",
}

_DEFAULT_PAIRING_GENERIC_NODES = "false"

# Custom Params that affect the hub MQTT transport. They must be included in
# ``_config_restart_snap`` so saving only MQTT settings still runs
# ``bridge.restart_session()`` (which stops/starts the MQTT task).
_BRIDGE_MQTT_RESTART_KEYS: tuple[str, ...] = (
    "mqtt_enable",
    "mqtt_host",
    "mqtt_port",
    "mqtt_username",
    "mqtt_password",
    "mqtt_hub_slug",
)

# After CONFIGDONE, PG3 may still be delivering CUSTOM* events on other threads; retry briefly.
_HUB_BOOTSTRAP_AFTER_CONFIG_MAX_ATTEMPTS = 120
_HUB_BOOTSTRAP_AFTER_CONFIG_RETRY_SEC = 0.1
# If CONFIGDONE never arrives (misbehaving client), still try once custom handlers have run.
_HUB_BOOTSTRAP_FALLBACK_SEC = 75.0
_DATA_KEY_NODE_KEY_NEXT_INDEX = "node_key_next_index"


def _coerce_change_node_names(val: Any) -> bool:
    """Custom Param ``change_node_names`` string (or bool); default True when unset or ambiguous."""
    if val is None:
        return True
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    if s in ("false", "0", "no", "off"):
        return False
    if s in ("true", "1", "yes", "on"):
        return True
    return True


def _coerce_bool_param(val: Any, *, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    if s in ("false", "0", "no", "off"):
        return False
    if s in ("true", "1", "yes", "on"):
        return True
    return default


def _alpha_key_from_index(n: int) -> str:
    """Index 0->a, 25->z, 26->aa, ..."""
    x = max(0, int(n))
    chars: list[str] = []
    while True:
        x, rem = divmod(x, 26)
        chars.append(chr(ord("a") + rem))
        if x == 0:
            break
        x -= 1
    return "".join(reversed(chars))


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
        self._paired_nodes: dict[str, PairedDeviceNode] = {}
        self.change_node_names = True
        self._discover_notice_token = 0
        self._node_key_next_index_cache: Optional[int] = None
        self._mqtt_transport_driver = MQTT_TRANSPORT_STATUS_DISABLED
        # %% professional-only begin
        self.edition = 'Standard'
        self._generic_nodes: dict[str, Any] = {}
        # %% professional-only end

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
                        {
                            "name": "node_key",
                            "title": "Stable node key (plugin-managed identity; auto-generated if empty. Edit only to match a previously paired device you want to keep on the same IoX node address)",
                            "isRequired": False,
                        },
                        {
                            "name": "generic_nodes",
                            "title": "Create generic IoX control nodes (Professional)",
                            "desc": (
                                "Default false. Set true (and enable hub generic_nodes_enable) "
                                "to manage this device with generic IoX nodes in this plugin "
                                "instead of a separate vendor plugin."
                            ),
                            "type": "BOOLEAN",
                            "isRequired": False,
                            "defaultValue": [_DEFAULT_PAIRING_GENERIC_NODES],
                        },
                    ],
                },
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

    def _hub_rpc_error_notice_callback(
        self, for_what: str, message: str, ctx: Dict[str, Any]
    ) -> None:
        """Hub → client RPC failure (command / get / …): PG3 Notice from the asyncio thread."""
        did = str(ctx.get("device_id") or "").strip()
        slug = str(ctx.get("mqtt_client_slug") or "").strip()
        ch = ctx.get("characteristic")
        parts: list[str] = [
            f"<p><code>{html.escape(for_what)}</code>: {html.escape(message)}</p>",
        ]
        if did:
            parts.append(f"<p>device_id: <code>{html.escape(did)}</code></p>")
        if slug:
            parts.append(f"<p>MQTT client_slug: <code>{html.escape(slug)}</code></p>")
        if ch is not None and str(ch).strip():
            parts.append(f"<p>characteristic: <code>{html.escape(str(ch))}</code></p>")
        self._pg3_warn_and_notice(
            "homekit_hub_rpc_error",
            title="HomeKit hub client RPC error",
            log_message=f"HomeKit hub RPC error ({for_what}): {message}",
            notice_html="".join(parts),
        )

    def _pg3_warn_and_notice(
        self,
        notice_key: str,
        *,
        title: str,
        log_message: str,
        notice_html: str,
        emit_notice: bool = True,
    ) -> None:
        """Log **WARNING** and optionally set a PG3 **Notice** (HTML body is concatenated after the title).

        Duplicated from **udi-poly-ecobee** ``nodes.backends.homekit.HomeKitBackend._pg3_warn_and_notice`` so each
        Node Server stays self-contained (no shared package).
        """
        LOGGER.warning("%s", log_message)
        if not emit_notice:
            return
        try:
            self.Notices[notice_key] = (
                f"<p><b>{html.escape(title)}</b></p>"
                f"{notice_html}"
                "<p>See the Node Server log for details.</p>"
            )
        except Exception:
            LOGGER.exception("PG3 Notice %r failed", notice_key)

    def _apply_mqtt_transport_driver(self, code: int) -> None:
        """Update cached **GV1** (UOM 25) from hub MQTT transport state (asyncio thread + PG3 thread)."""
        self._mqtt_transport_driver = int(code)
        try:
            self.setDriver("GV1", self._mqtt_transport_driver, report=True, force=True, uom=25)
        except Exception:
            LOGGER.exception("setDriver GV1 (MQTT transport status)")

    def _mqtt_transport_notice_callback(self, code: int) -> None:
        """Bridge runs on the asyncio thread; **GV1** must be IoX-visible."""
        prev = self._mqtt_transport_driver
        self._apply_mqtt_transport_driver(code)
        if mqtt_transport_enabled(self._bridge_get_params()) and prev == MQTT_TRANSPORT_STATUS_CONNECTED:
            if code == MQTT_TRANSPORT_STATUS_NOT_CONNECTED:
                self._pg3_warn_and_notice(
                    "homekit_mqtt_transport_lost",
                    title="HomeKit hub MQTT session lost",
                    log_message="HomeKit hub MQTT transport dropped from connected to not connected (broker/hub/network).",
                    notice_html=(
                        "<p>The hub lost its MQTT subscription or broker session while <code>mqtt_enable</code> "
                        "is on. Clients publishing to ingress topics may fail until the hub reconnects. Check the "
                        "broker, ACLs, and <b>udi-poly-homekit-hub</b> logs.</p>"
                    ),
                )
            elif code == MQTT_TRANSPORT_STATUS_DISABLED:
                self._pg3_warn_and_notice(
                    "homekit_mqtt_transport_disabled",
                    title="HomeKit hub MQTT disabled",
                    log_message="HomeKit hub MQTT was disabled while it had been connected (Custom Params or hub shutdown).",
                    notice_html=(
                        "<p>MQTT ingress is no longer active on the hub. WebSocket clients are unaffected if the "
                        "hub WebSocket is still running.</p>"
                    ),
                )
        if code == MQTT_TRANSPORT_STATUS_CONNECTED:
            for nk in ("homekit_mqtt_transport_lost", "homekit_mqtt_transport_disabled"):
                try:
                    self.Notices.delete(nk)
                except Exception:
                    try:
                        del self.Notices[nk]
                    except Exception:
                        pass

    def _sync_mqtt_status_driver_from_params(self) -> None:
        """Align **GV1** with Custom Params after (re)start; live updates still come from the bridge."""
        if not mqtt_transport_enabled(self._bridge_get_params()):
            self._apply_mqtt_transport_driver(MQTT_TRANSPORT_STATUS_DISABLED)
            return
        if self._mqtt_transport_driver == MQTT_TRANSPORT_STATUS_DISABLED:
            self._apply_mqtt_transport_driver(MQTT_TRANSPORT_STATUS_NOT_CONNECTED)

    @staticmethod
    def _slot_from_alias(alias: str) -> Optional[int]:
        if not isinstance(alias, str):
            return None
        if not alias.startswith("slot_"):
            return None
        try:
            n = int(alias[5:])
        except (TypeError, ValueError):
            return None
        return n if n > 0 else None

    def _set_paired_nodes_health(self, unhealthy_aliases: list[str]) -> None:
        unhealthy_slots = {
            s for s in (self._slot_from_alias(a) for a in unhealthy_aliases) if s is not None
        }
        for node in self._paired_nodes.values():
            node.update_health(node.slot in unhealthy_slots)

    def _persist_typed_discover_from_recovered_lan(
        self, by_alias: dict[str, str]
    ) -> None:
        """Update ``discover_endpoint`` on pairing rows when hub reports recovered LAN host:port."""
        slots_eps: dict[int, str] = {}
        for alias, endpoint in by_alias.items():
            slot = self._slot_from_alias(alias)
            if slot is None:
                continue
            ep = (endpoint or "").strip()
            if not ep:
                continue
            slots_eps[slot] = ep
        if not slots_eps:
            return
        try:
            raw_rows = self.TypedData.get(TYPED_PAIRING_SLOTS_KEY)
        except Exception:
            return
        if not isinstance(raw_rows, list):
            return
        rows = [dict(x) if isinstance(x, dict) else x for x in raw_rows]
        changed = False
        for row in rows:
            if not isinstance(row, dict):
                continue
            sn = _parse_slot_value(row.get("slot"))
            if sn is None or sn not in slots_eps:
                continue
            want = slots_eps[sn]
            if (row.get("discover_endpoint") or "").strip() == want:
                continue
            row["discover_endpoint"] = want
            changed = True
        if not changed:
            return
        try:
            td = self._typed_data_dict()
            td[TYPED_PAIRING_SLOTS_KEY] = rows
            self.TypedData.load(td, save=True)
        except Exception:
            LOGGER.exception(
                "Failed to persist recovered LAN endpoints to Custom Typed pairing rows"
            )
            return
        LOGGER.info(
            "Updated Custom Typed discover_endpoint after pairing recovery for slot(s): %s",
            ", ".join(str(s) for s in sorted(slots_eps.keys())),
        )

    def _pairing_health_notice_callback(
        self,
        unhealthy: bool,
        detail: str,
        unhealthy_aliases: list[str],
        recovered_lan_endpoints: dict[str, str],
        fault_transition: bool,
    ) -> None:
        if recovered_lan_endpoints:
            self._persist_typed_discover_from_recovered_lan(recovered_lan_endpoints)
        self._set_paired_nodes_health(unhealthy_aliases)
        if not fault_transition:
            return
        if unhealthy:
            self.Notices["homekit_pairing_health"] = (
                "<b>HomeKit pairing health degraded</b><br/>"
                f"{html.escape(detail)}<br/>"
                "The hub will keep probing and auto-recover listeners/subscriptions when reachable."
            )
            try:
                self.setDriver("ERR", ERR_PAIRING_HEALTH, report=True, force=True, uom=25)
            except Exception:
                LOGGER.exception("setDriver ERR for pairing health degrade")
            return
        try:
            self.Notices.delete("homekit_pairing_health")
        except Exception:
            try:
                del self.Notices["homekit_pairing_health"]
            except Exception:
                pass
        try:
            cur = self.getDriver("ERR")
            if cur is not None and int(cur) == ERR_PAIRING_HEALTH:
                self.setDriver("ERR", ERR_OK, report=True, force=True, uom=25)
        except Exception:
            LOGGER.exception("clear pairing health ERR state")

    def _config_restart_snap(self) -> dict[str, Any]:
        p = self._bridge_get_params()
        snap = {
            "ws_host": p.get("ws_host"),
            "ws_port": p.get("ws_port"),
            "zeroconf_unicast": p.get("zeroconf_unicast"),
            "zeroconf_interfaces": p.get("zeroconf_interfaces"),
            "zeroconf_ip_version": p.get("zeroconf_ip_version"),
        }
        for k in _BRIDGE_MQTT_RESTART_KEYS:
            snap[k] = p.get(k)
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
        mqtt_changed = any(snap.get(k) != prev.get(k) for k in _BRIDGE_MQTT_RESTART_KEYS)
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
        elif mqtt_changed:
            LOGGER.info("MQTT hub settings changed; reloading MQTT transport")
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
        self._sync_mqtt_status_driver_from_params()

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
        self._ensure_pairing_row_node_keys()
        self._ensure_pairing_row_generic_nodes_default()

        # %% professional-only begin
        self._update_edition()
        try:
            ensure_persistent_dir()
        except Exception:
            LOGGER.exception('ensure_persistent_dir on bootstrap')
        # %% professional-only end

        self.mainloop = asyncio.new_event_loop()

        def _run_loop():
            asyncio.set_event_loop(self.mainloop)
            self.mainloop.run_forever()

        self._loop_thread = Thread(target=_run_loop, daemon=True)
        self._loop_thread.start()
        try:
            self.setDriver("ST", 1, report=True, force=True, uom=25)
            self.setDriver("GV0", 0, report=True, force=True, uom=25)
            self.setDriver("GV1", MQTT_TRANSPORT_STATUS_DISABLED, report=True, force=True, uom=25)
        except Exception:
            LOGGER.exception("setDriver ST/GV0/GV1 before bridge start")

        self._config_snap = None
        self.bridge = HomeKitHubBridge(
            LOGGER,
            self._bridge_get_params,
            self._bridge_get_pairing_slot_rows,
            self._bridge_get_data,
            self._bridge_set_data,
            pairing_notice=self._pairing_notice_callback,
            pairing_health_notice=self._pairing_health_notice_callback,
            mqtt_transport_notice=self._mqtt_transport_notice_callback,
            hub_rpc_error_notice=self._hub_rpc_error_notice_callback,
            # %% professional-only begin
            pairing_classified=self._pairing_classified_callback,
            generic_hap_event=self._generic_hap_event_callback,
            is_professional=self.is_professional,
            inventory_notice=self._inventory_export_notice_callback,
            # %% professional-only end
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
        self._sync_mqtt_status_driver_from_params()
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
        self._sync_paired_nodes_from_data()
        self._maybe_post_pair_success_notice()
        self._maybe_clear_hap_discover_notice_for_paired()
        self._maybe_clear_pairing_error_notice_for_success()
        self.handler_data_st = True
        # %% professional-only begin
        self._resync_all_generic_nodes()
        # %% professional-only end

    def _paired_slots_from_data(self) -> dict[int, str]:
        try:
            pairings = self.Data.get("homekit_pairings")
        except Exception:
            return {}
        if not isinstance(pairings, dict):
            return {}
        out: dict[int, str] = {}
        for raw_slot, item in pairings.items():
            if not isinstance(item, dict):
                continue
            pid = str(item.get("AccessoryPairingID") or "").strip().lower()
            if not pid:
                continue
            slot = _parse_slot_value(raw_slot)
            if slot is None or slot < 1:
                continue
            out[slot] = pid
        return out

    @staticmethod
    def _truncate_isy_node_name(title: str) -> str:
        s = str(title or "").strip()
        if len(s) <= 80:
            return s
        return s[:77] + "..."

    def _discover_display_name_by_id(self) -> dict[str, str]:
        """Map lowercase HAP accessory id -> display name from last DISCOVER snapshot."""
        out: dict[str, str] = {}
        try:
            rows = self.Data.get(DATA_KEY_LAST_HAP_DISCOVER)
        except Exception:
            return out
        if not isinstance(rows, list):
            return out
        for r in rows:
            if not isinstance(r, dict):
                continue
            pid = str(r.get("id") or "").strip().lower()
            if not pid:
                continue
            nm = str(r.get("name") or "").strip()
            if nm:
                out[pid] = nm
        return out

    def _pairing_slot_display_names(self) -> dict[int, str]:
        """Per slot: human title — prefer live DISCOVER name by id so renames refresh IoX titles.

        Order: ``last_hap_discover`` name for accessory id and/or pairing id, then typed
        ``accessory_name``, then ``accessory_id`` string, then truncated pairing id.
        """
        discover = self._discover_display_name_by_id()
        paired_by_slot = self._paired_slots_from_data()
        try:
            rows = self.TypedData.get(TYPED_PAIRING_SLOTS_KEY)
        except Exception:
            return {}
        if not isinstance(rows, list):
            return {}
        out: dict[int, str] = {}
        for slot, row in assign_pairing_slot_rows(rows, LOGGER):
            if not isinstance(row, dict):
                continue
            aname = str(row.get("accessory_name") or "").strip()
            aid_raw = str(row.get("accessory_id") or "").strip()
            aid_l = aid_raw.lower()
            paired_pid = paired_by_slot.get(slot)

            ids_to_try: list[str] = []
            if aid_l:
                ids_to_try.append(aid_l)
            if paired_pid and paired_pid not in ids_to_try:
                ids_to_try.append(paired_pid)

            title = ""
            for lid in ids_to_try:
                nm = discover.get(lid, "")
                if nm:
                    title = nm
                    break
            if not title and aname:
                title = aname
            if not title and aid_raw:
                title = aid_raw
            if not title and paired_pid:
                title = self._truncate_isy_node_name(paired_pid)
            if title:
                out[slot] = self._truncate_isy_node_name(title)
        return out

    def _typed_row_node_key_map(self) -> dict[str, int]:
        try:
            rows = self.TypedData.get(TYPED_PAIRING_SLOTS_KEY)
        except Exception:
            return {}
        if not isinstance(rows, list):
            return {}
        out: dict[str, int] = {}
        for slot, row in assign_pairing_slot_rows(rows, LOGGER):
            if not isinstance(row, dict):
                continue
            node_key = str(row.get("node_key") or "").strip().lower()
            if node_key:
                out[node_key] = slot
        return out

    def _get_node_key_next_index(self) -> int:
        # Tests may construct Controller via __new__ without __init__; tolerate missing cache.
        cached = getattr(self, "_node_key_next_index_cache", None)
        if cached is not None:
            return max(0, int(cached))
        idx = 0
        try:
            raw = self.Data.get(_DATA_KEY_NODE_KEY_NEXT_INDEX)
            idx = int(raw) if raw is not None else 0
        except Exception:
            idx = 0
        idx = max(0, idx)
        self._node_key_next_index_cache = idx
        return idx

    def _set_node_key_next_index(self, idx: int) -> None:
        next_idx = max(0, int(idx))
        self._node_key_next_index_cache = next_idx
        try:
            d = self._bridge_get_data()
            d[_DATA_KEY_NODE_KEY_NEXT_INDEX] = next_idx
            self._bridge_set_data(d)
        except Exception:
            LOGGER.exception("Failed to persist node_key allocator cursor")

    def _allocate_node_key(self, used: Set[str]) -> str:
        idx = self._get_node_key_next_index()
        while True:
            key = _alpha_key_from_index(idx)
            idx += 1
            if key in used:
                continue
            self._set_node_key_next_index(idx)
            return key

    def _ensure_pairing_row_node_keys(self) -> bool:
        """Assign stable alphabetic node_key values to pairing rows that need them."""
        try:
            raw_rows = self.TypedData.get(TYPED_PAIRING_SLOTS_KEY)
        except Exception:
            return False
        if not isinstance(raw_rows, list):
            return False

        rows = [dict(x) if isinstance(x, dict) else x for x in raw_rows]
        used: Set[str] = set()
        changed = False
        for row in rows:
            if not isinstance(row, dict):
                continue
            node_key = str(row.get("node_key") or "").strip().lower()
            if node_key.isalpha() and node_key not in used:
                used.add(node_key)
                continue
            new_key = self._allocate_node_key(used)
            used.add(new_key)
            row["node_key"] = new_key
            changed = True

        if not changed:
            return False

        try:
            td = self._typed_data_dict()
            td[TYPED_PAIRING_SLOTS_KEY] = rows
            self.TypedData.load(td, save=True)
        except Exception:
            LOGGER.exception("Failed to persist node_key values in typed pairing rows")
            return False
        LOGGER.info("Assigned stable node_key values for HomeKit pairing rows")
        return True

    @staticmethod
    def _pairing_row_generic_nodes_is_blank(row: dict[str, Any]) -> bool:
        raw = row.get("generic_nodes")
        if raw is None:
            return True
        if isinstance(raw, str) and not raw.strip():
            return True
        return False

    def _ensure_pairing_row_generic_nodes_default(self) -> bool:
        """Seed blank pairing-row generic_nodes values as false (Professional opt-in)."""
        try:
            raw_rows = self.TypedData.get(TYPED_PAIRING_SLOTS_KEY)
        except Exception:
            return False
        if not isinstance(raw_rows, list):
            return False

        rows = [dict(x) if isinstance(x, dict) else x for x in raw_rows]
        changed = False
        for row in rows:
            if not isinstance(row, dict):
                continue
            if self._pairing_row_generic_nodes_is_blank(row):
                row["generic_nodes"] = _DEFAULT_PAIRING_GENERIC_NODES
                changed = True

        if not changed:
            return False

        try:
            td = self._typed_data_dict()
            td[TYPED_PAIRING_SLOTS_KEY] = rows
            self.TypedData.load(td, save=True)
        except Exception:
            LOGGER.exception("Failed to persist generic_nodes defaults in typed pairing rows")
            return False
        LOGGER.info("Seeded generic_nodes=false for HomeKit pairing rows missing a value")
        return True

    def _sync_paired_nodes_from_data(self) -> None:
        paired = self._paired_slots_from_data()
        display_by_slot = self._pairing_slot_display_names()
        discover_names = self._discover_display_name_by_id()
        key_map = self._typed_row_node_key_map()
        desired: dict[str, tuple[int, str, bool]] = {}
        for node_key, slot in key_map.items():
            desired[node_key] = (slot, display_by_slot.get(slot, ""), False)
        for slot, pid in paired.items():
            for node_key, key_slot in key_map.items():
                if key_slot == slot:
                    label = display_by_slot.get(slot, "")
                    if not label:
                        label = discover_names.get(pid, "") or self._truncate_isy_node_name(pid)
                    desired[node_key] = (slot, label, True)
                    break
        desired_keys = set(desired.keys())
        existing_keys = set(self._paired_nodes.keys())

        for node_key in sorted(existing_keys - desired_keys):
            node = self._paired_nodes.pop(node_key, None)
            if node is None:
                continue
            try:
                if hasattr(self.poly, "delNode"):
                    self.poly.delNode(node.address)
                elif hasattr(self.poly, "removeNode"):
                    self.poly.removeNode(node.address)
            except Exception:
                LOGGER.exception(
                    "Failed to delete paired device node for key %s", node_key
                )

        for node_key in sorted(desired_keys):
            slot, display_name, is_paired = desired[node_key]
            node = self._paired_nodes.get(node_key)
            if node is None:
                try:
                    node = PairedDeviceNode(
                        self, node_key, slot, display_name, is_paired
                    )
                    self.poly.addNode(node)
                    self._paired_nodes[node_key] = node
                    node.reconcile_isy_name()
                except Exception:
                    LOGGER.exception(
                        "Failed to create paired device node for key %s", node_key
                    )
                    continue
            else:
                node.update_identity(slot, display_name, is_paired)

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
        notice = (
            "<b>HomeKit pairing successful</b><br/>"
            f"Paired device id(s): <code>{html.escape(ids_txt)}</code><br/>"
            "This notice clears automatically after two long polls."
        )
        # %% professional-only begin
        if self._generic_nodes_master_enabled():
            missing_row = []
            for did in sorted(new_ids):
                if not self._device_generic_nodes_enabled(did):
                    missing_row.append(did)
            if missing_row:
                notice += (
                    "<br/><br/>To add generic control nodes (thermostat, light, …) under this hub, "
                    "set <b>Create generic IoX control nodes</b> to <b>true</b> on the pairing row "
                    "in <b>Custom Typed → HomeKit pairing slots</b> and <b>Save</b>. "
                    "The <b>Paired HomeKit device</b> child node remains for slot status; "
                    "generic nodes appear as additional siblings."
                )
        # %% professional-only end
        self.Notices["homekit_pair_success"] = notice
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
        self._ensure_pairing_row_node_keys()
        self._ensure_pairing_row_generic_nodes_default()
        self._auto_discover_if_needed_from_typed_update()
        self._sync_paired_nodes_from_data()
        self._maybe_restart_on_config_change()
        # %% professional-only begin
        self._resync_all_generic_nodes()
        # %% professional-only end

    def _refresh_change_node_names_flag(self) -> None:
        try:
            dflt = str(_DEFAULT_BRIDGE_PARAMS.get("change_node_names") or "true")
            raw = self.Params.get("change_node_names")
            if raw is None or (isinstance(raw, str) and not str(raw).strip()):
                raw = dflt
        except Exception:
            raw = "true"
        self.change_node_names = _coerce_change_node_names(raw)

    def _ensure_default_custom_params(self) -> None:
        """Polyglot only shows Custom Params that exist in saved config; seed new keys (Kasa pattern).

        Without this, keys added in a plugin upgrade never appear in the PG3 editor until typed manually.
        """
        for key, default in _DEFAULT_BRIDGE_PARAMS.items():
            if key in self.Params:
                continue
            try:
                self.Params[key] = default
                LOGGER.info(
                    "Seeded default Custom Param %s=%r (first time / missing in PG3 store)",
                    key,
                    default,
                )
            except Exception:
                LOGGER.exception("Failed to seed default Custom Param %s", key)

    def handler_params(self, params):
        LOGGER.debug("customparams: %s", params)
        self.Params.load(params)
        self._ensure_default_custom_params()
        self._refresh_change_node_names_flag()
        # %% professional-only begin
        self._update_edition()
        # %% professional-only end
        self.handler_params_st = True
        self._maybe_restart_on_config_change()
        if self.handler_typed_data_st:
            self._sync_paired_nodes_from_data()
        # %% professional-only begin
        self._resync_all_generic_nodes()
        # %% professional-only end

    def handler_start(self):
        self._async_loop_death_reported = False
        self._hub_bootstrap_generation += 1
        bootstrap_gen = self._hub_bootstrap_generation
        self.Notices.clear()
        # %% professional-only begin
        self._update_edition()
        # %% professional-only end
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
        self._paired_nodes.clear()
        # %% professional-only begin
        self._remove_all_generic_nodes()
        # %% professional-only end
        try:
            self.setDriver("GV0", 0, report=True, force=True, uom=25)
            self.setDriver("ST", 0, report=True, force=True, uom=25)
            self.setDriver("GV1", MQTT_TRANSPORT_STATUS_DISABLED, report=True, force=True, uom=25)
        except Exception:
            LOGGER.exception("setDriver ST/GV0/GV1 on stop")
        self._mqtt_transport_driver = MQTT_TRANSPORT_STATUS_DISABLED
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

    def _set_discover_progress_notice(self, seconds_left: int) -> None:
        sec = max(0, int(seconds_left))
        self.Notices["discover_progress"] = (
            "<b>HomeKit DISCOVER running</b><br/>"
            f"Scan window ends in <b>{sec}</b> second(s)."
        )

    def _clear_discover_progress_notice(self) -> None:
        try:
            self.Notices.delete("discover_progress")
        except Exception:
            try:
                del self.Notices["discover_progress"]
            except Exception:
                pass

    def _start_discover_progress_notice(self, seconds: int) -> int:
        token = self._discover_notice_token + 1
        self._discover_notice_token = token
        self._set_discover_progress_notice(seconds)

        def _tick(remaining: int, this_token: int) -> None:
            if this_token != self._discover_notice_token:
                return
            self._set_discover_progress_notice(remaining)
            if remaining <= 0:
                return
            Timer(1.0, lambda: _tick(remaining - 1, this_token)).start()

        if seconds > 0:
            Timer(1.0, lambda: _tick(seconds - 1, token)).start()
        return token

    def _stop_discover_progress_notice(self, token: int) -> None:
        if token == self._discover_notice_token:
            self._discover_notice_token += 1
        self._clear_discover_progress_notice()

    def handler_discover(self, _data=None):
        """Network scan: results are saved and shown in a Polyglot Notice (no log file needed).

        PG3 may invoke this via ``poly.subscribe(DISCOVER)`` (MQTT ``discover``) and/or via
        ``runCmd``; the latter requires ``commands['DISCOVER']`` (see udi-poly-ecobee / udi-poly-kasa).
        """
        discover_notice_token: Optional[int] = None
        try:
            LOGGER.info("HomeKit DISCOVER: starting (zeroconf HAP scan)")
            if not (self.bridge and self.mainloop):
                LOGGER.warning(
                    "HomeKit DISCOVER skipped: bridge not ready. Wait until the log shows "
                    "'HomeKit Hub ready' after the Node Server starts, then try again."
                )
                return
            discover_notice_token = self._start_discover_progress_notice(12)
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
        finally:
            if discover_notice_token is not None:
                self._stop_discover_progress_notice(discover_notice_token)

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
        used_node_keys: Set[str] = set()
        for row in current:
            nk = str(row.get("node_key") or "").strip().lower()
            if nk.isalpha():
                used_node_keys.add(nk)

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
            if self._pairing_row_generic_nodes_is_blank(row):
                row["generic_nodes"] = _DEFAULT_PAIRING_GENERIC_NODES
            if _parse_slot_value(row.get("slot")) is None:
                row["slot"] = str(self._take_next_free_slot(used_slots))
            nk = str(row.get("node_key") or "").strip().lower()
            if not nk.isalpha() or nk in used_node_keys:
                nk = self._allocate_node_key(used_node_keys)
                row["node_key"] = nk
            used_node_keys.add(nk)
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
                "node_key": self._allocate_node_key(used_node_keys),
                "generic_nodes": _DEFAULT_PAIRING_GENERIC_NODES,
            }
            used_node_keys.add(str(new_row["node_key"]))
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
        if not unpaired and paired:
            parts.append(
                "<b>No unpaired HomeKit devices are currently available for pairing.</b><br/>"
                "If you just unpaired this accessory, wait 30-60 seconds and run <b>DISCOVER</b> again. "
                "If it still shows paired, power-cycle or reboot/reset the accessory, then rediscover.<br/>"
            )
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
            self.setDriver("GV1", self._mqtt_transport_driver, report=True, force=True, uom=25)
        except Exception:
            LOGGER.exception("setDriver ST/GV1 on query")
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

    def _clear_slot_pin_and_reload(self, slot: int, *, source: str) -> bool:
        if slot < 1:
            LOGGER.warning("UNPAIR[%s]: slot must be >= 1 (got %s)", source, slot)
            return False
        try:
            raw_rows = self.TypedData.get(TYPED_PAIRING_SLOTS_KEY)
        except Exception:
            raw_rows = None
        if not isinstance(raw_rows, list):
            LOGGER.warning("UNPAIR[%s]: no Custom Typed pairing rows loaded", source)
            return False
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
                "UNPAIR[%s]: no pairing row resolved to slot %s (check Custom Typed slots)",
                source,
                slot,
            )
            return False
        if not cleared:
            LOGGER.warning(
                "UNPAIR[%s]: slot %s already has an empty pairing code in Custom Typed",
                source,
                slot,
            )
            return False
        try:
            td = self._typed_data_dict()
            td[TYPED_PAIRING_SLOTS_KEY] = raw_rows
            self.TypedData.load(td, save=True)
        except Exception as e:
            LOGGER.exception("UNPAIR[%s]: failed to save Custom Typed data", source)
            self.report_error(
                ERR_TYPED_SAVE,
                "homekit_err_config",
                "Failed to save Custom Typed data after UNPAIR",
                exc=e,
                log_message=f"UNPAIR typed save ({source})",
            )
            return False
        LOGGER.info(
            "UNPAIR[%s]: cleared hap_pin for slot %s; reloading hub sessions",
            source,
            slot,
        )
        self._maybe_restart_on_config_change()
        return True

    def _clear_node_key_pin_and_reload(self, node_key: str, *, source: str) -> bool:
        key = str(node_key or "").strip().lower()
        slot = self._typed_row_node_key_map().get(key)
        if slot is None:
            LOGGER.warning("UNPAIR[%s]: no row found for node_key %s", source, key)
            return False
        return self._clear_slot_pin_and_reload(slot, source=source)

    def _delete_node_key_config_and_node(self, node_key: str, *, source: str) -> bool:
        key = str(node_key or "").strip().lower()
        if not key:
            LOGGER.warning("DELETE[%s]: missing node_key", source)
            return False

        removed_typed_row = False
        removed_slot: Optional[int] = None
        try:
            raw_rows = self.TypedData.get(TYPED_PAIRING_SLOTS_KEY)
        except Exception:
            raw_rows = None
        if isinstance(raw_rows, list):
            assigned = assign_pairing_slot_rows(raw_rows, LOGGER)
            keep_rows: list[Any] = []
            for sn, row in assigned:
                row_key = (
                    str(row.get("node_key") or "").strip().lower()
                    if isinstance(row, dict)
                    else ""
                )
                if row_key == key and not removed_typed_row:
                    removed_typed_row = True
                    removed_slot = sn
                    continue
                keep_rows.append(row)
            if removed_typed_row:
                try:
                    td = self._typed_data_dict()
                    td[TYPED_PAIRING_SLOTS_KEY] = keep_rows
                    self.TypedData.load(td, save=True)
                except Exception as e:
                    LOGGER.exception("DELETE[%s]: failed to save Custom Typed data", source)
                    self.report_error(
                        ERR_TYPED_SAVE,
                        "homekit_err_config",
                        "Failed to save Custom Typed data after DELETE",
                        exc=e,
                        log_message=f"DELETE typed save ({source})",
                    )
                    return False

        removed_pairing = False
        try:
            data = self._bridge_get_data()
            pairings = data.get("homekit_pairings")
            if isinstance(pairings, dict):
                for pair_key in list(pairings.keys()):
                    key_slot = _parse_slot_value(pair_key)
                    if removed_slot is not None and key_slot == removed_slot:
                        del pairings[pair_key]
                        removed_pairing = True
            if removed_pairing:
                self._bridge_set_data(data)
        except Exception as e:
            LOGGER.exception("DELETE[%s]: failed to save custom data", source)
            self.report_error(
                ERR_TYPED_SAVE,
                "homekit_err_config",
                "Failed to save custom data after DELETE",
                exc=e,
                log_message=f"DELETE customdata save ({source})",
            )
            return False

        node = self._paired_nodes.pop(key, None)
        if node is not None:
            try:
                if hasattr(self.poly, "delNode"):
                    self.poly.delNode(node.address)
                elif hasattr(self.poly, "removeNode"):
                    self.poly.removeNode(node.address)
            except Exception:
                LOGGER.exception("DELETE[%s]: failed to remove node for key %s", source, key)

        if not removed_typed_row and not removed_pairing:
            LOGGER.warning(
                "DELETE[%s]: no row/pairing matched node_key %s; removing node only if present",
                source,
                key,
            )
            return node is not None

        LOGGER.info(
            "DELETE[%s]: removed node_key %s config (typed_row=%s pairings=%s); reloading hub sessions",
            source,
            key,
            removed_typed_row,
            removed_pairing,
        )
        self._maybe_restart_on_config_change()
        return True

    # %% professional-only begin
    def is_professional(self) -> bool:
        return edition_at_least(getattr(self, 'edition', 'Standard'), 'Professional')

    def _update_edition(self) -> None:
        self.edition = resolve_edition(self.poly, LOGGER)
        self._sync_dev_edition_notice()

    def _sync_dev_edition_notice(self) -> None:
        override_key = 'dev_edition_override'
        licensed = licensed_edition(self.poly)
        if dev_edition_override_active(self.poly, self.edition):
            notice = (
                f'Edition override active (local dev only): licensed {licensed}, '
                f'running as {self.edition} via dev_edition.txt.'
            )
            self.Notices[override_key] = notice
            LOGGER.warning(notice)
        else:
            try:
                self.Notices.delete(override_key)
            except Exception:
                pass

    def _generic_nodes_master_enabled(self) -> bool:
        try:
            raw = self.Params.get('generic_nodes_enable')
        except Exception:
            raw = None
        if raw is None:
            raw = _DEFAULT_BRIDGE_PARAMS.get('generic_nodes_enable', 'false')
        return _coerce_bool_param(raw, default=False)

    def _node_key_for_device_id(self, device_id: str) -> Optional[str]:
        did = str(device_id or '').strip().lower()
        if not did:
            return None
        paired_by_slot = self._paired_slots_from_data()
        for node_key, node in self._paired_nodes.items():
            pid = paired_by_slot.get(int(node.slot))
            if pid == did:
                return node_key
        return None

    def _device_generic_nodes_enabled(self, device_id: str) -> bool:
        if not self._generic_nodes_master_enabled():
            return False
        nk = self._node_key_for_device_id(device_id)
        if nk is None:
            return False
        slot = self._paired_nodes[nk].slot
        try:
            rows = self.TypedData.get(TYPED_PAIRING_SLOTS_KEY)
        except Exception:
            return False
        if not isinstance(rows, list):
            return False
        for sn, row in assign_pairing_slot_rows(rows, LOGGER):
            if sn != slot or not isinstance(row, dict):
                continue
            return _coerce_bool_param(row.get('generic_nodes'), default=False)
        return False

    def _generic_nodes_skip_reason(self, device_id: str) -> Optional[str]:
        if not self.is_professional():
            return 'not Professional edition'
        if not self._generic_nodes_master_enabled():
            return 'hub generic_nodes_enable is false'
        nk = self._node_key_for_device_id(device_id)
        if nk is None:
            return 'no paired device node for device_id yet'
        if not self._device_generic_nodes_enabled(device_id):
            return 'pairing row generic_nodes is false (Custom Typed → HomeKit pairing slots)'
        return None

    def _pairing_display_name(self, device_id: str) -> str:
        nk = self._node_key_for_device_id(device_id)
        if nk and nk in self._paired_nodes:
            nm = self._paired_nodes[nk].display_name
            if nm:
                return nm
        return device_id

    def paired_node_title(self, node: PairedDeviceNode) -> str:
        from node_funcs import append_isy_node_suffix

        base = PairedDeviceNode._node_title(node.node_key, node.display_name)
        if not node.paired:
            return base
        paired_by_slot = self._paired_slots_from_data()
        device_id = paired_by_slot.get(int(node.slot))
        if device_id and self._device_generic_nodes_enabled(str(device_id).lower()):
            return append_isy_node_suffix(base, ' (Pairing)')
        return base

    def _paired_device_id(self, node: PairedDeviceNode) -> Optional[str]:
        paired_by_slot = self._paired_slots_from_data()
        device_id = paired_by_slot.get(int(node.slot))
        if not device_id:
            return None
        return str(device_id).strip().lower()

    def _push_paired_node_isy_title(self, node: PairedDeviceNode) -> None:
        """Push paired-slot IoX title; force rename when generic control nodes are active."""
        requested = node._requested_title()
        node.name = requested
        if not self.change_node_names:
            return
        device_id = self._paired_device_id(node)
        force_rename = bool(device_id and self._device_generic_nodes_enabled(device_id))
        poly = self.poly
        cname = None
        if hasattr(poly, 'getNodeNameFromDb'):
            try:
                cname = poly.getNodeNameFromDb(node.address)
            except Exception:
                cname = None
        if not force_rename:
            if cname is None or cname == requested:
                return
        if not hasattr(poly, 'renameNode'):
            return
        try:
            poly.renameNode(node.address, requested)
            LOGGER.info(
                'Renamed paired slot node %s to %r (generic_nodes=%s)',
                node.address,
                requested,
                force_rename,
            )
        except Exception:
            LOGGER.exception('renameNode failed for %s', node.address)

    def _reconcile_paired_node_title_for_device(self, device_id: str) -> None:
        nk = self._node_key_for_device_id(device_id)
        if nk and nk in self._paired_nodes:
            self._push_paired_node_isy_title(self._paired_nodes[nk])

    def _generic_node_address(self, device_id: str, row: dict[str, Any]) -> str:
        return generic_node_address(
            str(device_id or ''),
            int(row.get('aid') or 0),
            str(row.get('role') or 'node'),
        )

    def hub_write(self, device_id: str, char_spec: str, value: Any) -> bool:
        if not (self.bridge and self.mainloop):
            return False
        fut = asyncio.run_coroutine_threadsafe(
            self.bridge.put_characteristic(device_id, char_spec, value),
            self.mainloop,
        )
        try:
            err = fut.result(timeout=30)
            return err is None
        except Exception:
            LOGGER.exception('hub_write failed for %s %s', device_id, char_spec)
            return False

    def hub_snapshot_values(self, device_id: str) -> list:
        """Return hub snapshot rows for *device_id* (empty list on failure)."""
        if not (self.bridge and self.mainloop):
            return []
        fut = asyncio.run_coroutine_threadsafe(
            self.bridge.fetch_snapshot_values(device_id),
            self.mainloop,
        )
        try:
            values, err = fut.result(timeout=60)
        except Exception:
            LOGGER.exception('hub_snapshot_values failed for %s', device_id)
            return []
        if err:
            LOGGER.debug('hub_snapshot_values %s: %s', device_id, err)
            return []
        return list(values or [])

    def refresh_generic_node(self, node: Any, *, report: bool = True) -> None:
        """Pull a HAP snapshot and map values onto one generic IoX node."""
        did = str(getattr(node, 'device_id', '') or '').strip().lower()
        if not did:
            return
        rows = self.hub_snapshot_values(did)
        if not rows:
            return
        applied = hap_apply.apply_snapshot_rows_to_generic_node(node, rows, log=LOGGER)
        if applied and report:
            try:
                node.reportDrivers()
            except Exception:
                LOGGER.debug('reportDrivers after snapshot failed for %s', getattr(node, 'address', '?'), exc_info=True)

    def _schedule_refresh_generic_node(self, node: Any) -> None:
        Timer(0.0, lambda: self.refresh_generic_node(node)).start()

    def _inventory_export_notice_callback(self, device_id: str, path: str) -> None:
        self.Notices['homekit_inventory_export'] = (
            '<b>Device inventory exported</b><br/>'
            f'device_id: <code>{html.escape(device_id)}</code><br/>'
            f'path: <code>{html.escape(path)}</code>'
        )

    def export_device_inventory_manual(self, node_key: str) -> None:
        if not self.is_professional():
            self.Notices['homekit_inventory_export'] = (
                '<b>Device inventory requires Professional edition</b>'
            )
            return
        nk = str(node_key or '').strip().lower()
        node = self._paired_nodes.get(nk)
        if node is None:
            return
        paired = self._paired_slots_from_data()
        device_id = paired.get(int(node.slot))
        if not device_id or not (self.bridge and self.mainloop):
            return
        fut = asyncio.run_coroutine_threadsafe(
            self.bridge._export_device_inventory(
                f'slot_{node.slot}',
                self.bridge._pairing_for_device_id(device_id),
                reason='manual_export',
            ),
            self.mainloop,
        )
        try:
            fut.result(timeout=60)
        except Exception:
            LOGGER.exception('manual EXPORT_INVENTORY failed for %s', nk)

    def _pairing_classified_callback(
        self,
        alias: str,
        device_id: str,
        reason: str,
        classification: list,
        pairing: Any,
    ) -> None:
        del alias, pairing
        Timer(
            0.0,
            lambda: self._sync_generic_nodes(str(device_id).lower(), list(classification or [])),
        ).start()

    def _generic_hap_event_callback(
        self,
        device_id: str,
        aid: int,
        iid: int,
        value: Any,
        label: str,
    ) -> None:
        did = str(device_id or '').strip().lower()
        for node in list(self._generic_nodes.values()):
            handler = getattr(node, 'on_hap_event', None)
            if not callable(handler):
                continue
            if getattr(node, 'device_id', '').lower() != did:
                continue
            try:
                handler(int(aid), int(iid), value, str(label or ''))
            except Exception:
                LOGGER.debug('generic node HAP event failed for %s', getattr(node, 'address', '?'), exc_info=True)

    def _resync_all_generic_nodes(self) -> None:
        if not self.is_professional():
            self._remove_all_generic_nodes()
            return
        if not (self.bridge and self.mainloop):
            return
        for did in self._current_paired_ids_from_data():
            fut = asyncio.run_coroutine_threadsafe(
                self._classify_device_async(did),
                self.mainloop,
            )
            try:
                _alias, classification = fut.result(timeout=60)
            except Exception:
                LOGGER.exception('generic node resync classify failed for %s', did)
                continue
            self._sync_generic_nodes(did, classification)

    async def _classify_device_async(self, device_id: str) -> tuple[str, list]:
        from homekit_hub.device_classifier import classify_accessories

        pairing = self.bridge._pairing_for_device_id(device_id) if self.bridge else None
        if pairing is None:
            return ('', [])
        classification = classify_accessories(getattr(pairing, 'accessories', None))
        return ('', classification)

    def _delete_generic_node_by_address(self, addr: str) -> None:
        addr = str(addr or '').strip()
        if not addr:
            return
        node = self._generic_nodes.pop(addr, None)
        try:
            if hasattr(self.poly, 'delNode'):
                self.poly.delNode(addr)
            elif node is not None and hasattr(self.poly, 'removeNode'):
                self.poly.removeNode(node.address)
        except Exception:
            LOGGER.exception('Failed to delete generic node %s', addr)

    def _remove_legacy_generic_nodes_for_device(
        self, device_id: str, classification: list
    ) -> None:
        did = str(device_id or '').strip().lower()
        if not did:
            return
        legacy_addrs: set[str] = set()
        for row in classification:
            if not isinstance(row, dict):
                continue
            legacy = legacy_generic_node_address(
                did,
                int(row.get('aid') or 0),
                str(row.get('role') or 'node'),
            )
            current = self._generic_node_address(did, row)
            if legacy != current:
                legacy_addrs.add(legacy)
        for addr in list(self._generic_nodes.keys()):
            node = self._generic_nodes.get(addr)
            if node is None or getattr(node, 'device_id', '').lower() != did:
                continue
            if addr.startswith('hkg_'):
                legacy_addrs.add(addr)
        for addr in sorted(legacy_addrs):
            LOGGER.info('Removing legacy generic IoX node %s for %s', addr, did)
            self._delete_generic_node_by_address(addr)

    def _remove_generic_nodes_for_device(self, device_id: str) -> None:
        did = str(device_id or '').strip().lower()
        for addr in list(self._generic_nodes.keys()):
            node = self._generic_nodes.get(addr)
            if node is None or getattr(node, 'device_id', '').lower() != did:
                continue
            self._delete_generic_node_by_address(addr)
        self._reconcile_paired_node_title_for_device(did)

    def _remove_all_generic_nodes(self) -> None:
        for addr in list(self._generic_nodes.keys()):
            self._delete_generic_node_by_address(addr)

    def _sync_generic_nodes(self, device_id: str, classification: list) -> None:
        if not self.is_professional():
            return
        did = str(device_id or '').strip().lower()
        if not did:
            return
        if not self._device_generic_nodes_enabled(did):
            reason = self._generic_nodes_skip_reason(did)
            if reason:
                LOGGER.info('Generic IoX nodes skipped for %s: %s', did, reason)
            self._remove_generic_nodes_for_device(did)
            return
        self._remove_legacy_generic_nodes_for_device(did, classification)
        display = self._pairing_display_name(did)
        role_rows = [row for row in classification if isinstance(row, dict)]
        sibling_count = len(role_rows)
        desired_addrs = {self._generic_node_address(did, row) for row in role_rows}
        for addr in list(self._generic_nodes.keys()):
            node = self._generic_nodes.get(addr)
            if node is None or getattr(node, 'device_id', '').lower() != did:
                continue
            if addr not in desired_addrs:
                self._delete_generic_node_by_address(addr)

        for row in classification:
            if not isinstance(row, dict):
                continue
            addr = self._generic_node_address(did, row)
            node_def = str(row.get('node_def_id') or '')
            aid = int(row.get('aid') or 0)
            bindings = row.get('char_bindings') if isinstance(row.get('char_bindings'), dict) else {}
            title = generic_node_title(
                display,
                str(row.get('role') or 'device'),
                sibling_count=sibling_count,
            )
            existing = self._generic_nodes.get(addr)
            if existing is not None:
                existing.char_bindings = dict(bindings)
                self._schedule_refresh_generic_node(existing)
                continue
            try:
                if node_def == 'HKHubEcobeeThermostat':
                    node = EcobeeThermostatNode(
                        self, addr, title, device_id=did, aid=aid, char_bindings=bindings
                    )
                elif node_def == 'HKHubThermostat':
                    node = ThermostatNode(
                        self, addr, title, device_id=did, aid=aid, char_bindings=bindings
                    )
                elif node_def == 'HKHubLight':
                    node = LightNode(
                        self, addr, title, device_id=did, aid=aid, char_bindings=bindings
                    )
                elif node_def == 'HKHubSwitch':
                    node = SwitchNode(
                        self, addr, title, device_id=did, aid=aid, char_bindings=bindings
                    )
                elif node_def == 'HKHubBinarySensor':
                    node = BinarySensorNode(
                        self, addr, title, device_id=did, aid=aid, char_bindings=bindings
                    )
                else:
                    continue
                self.poly.addNode(node)
                self._generic_nodes[addr] = node
                LOGGER.info('Created generic IoX node %s (%s) for %s', addr, node_def, did)
                self._schedule_refresh_generic_node(node)
            except Exception:
                LOGGER.exception('Failed to create generic node %s for %s', node_def, did)
        self._reconcile_paired_node_title_for_device(did)
    # %% professional-only end

    def cmd_unpair(self, command=None):
        """Backward-compatible controller UNPAIR path."""
        cmd = command if isinstance(command, dict) else {}
        try:
            slot = int(cmd.get("value"))
        except (TypeError, ValueError):
            LOGGER.warning("UNPAIR[controller]: missing or invalid slot selection")
            return
        self._clear_slot_pin_and_reload(slot, source="controller")

    # Must match profile/nodedefs.xml; runCmd only sees commands listed here.
    id = "HKHubController"
    commands = {
        "DISCOVER": cmd_discover,
        "QUERY": query,
        "ZEROCONF_DIAG": cmd_zeroconf_diag,
    }
    drivers = [
        {"driver": "ST", "value": 1, "uom": 25, "name": "NodeServer Online"},
        {"driver": "GV0", "value": 0, "uom": 25, "name": "Bridge Status"},
        {"driver": "GV1", "value": 0, "uom": 25, "name": "MQTT transport"},
        {"driver": "ERR", "value": 0, "uom": 25, "name": "Hub error code"},
    ]
