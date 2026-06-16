"""
Async HomeKit controller + WebSocket bridge for PG3x.

Runs on a dedicated asyncio loop (see nodes.Controller). No udi_interface imports.

Supports multiple simultaneous pairings; each row has a slot id (explicit or auto).

Zeroconf / mDNS constraints:

- **UDP 5353**: python-zeroconf normally binds the mDNS port. If another stack already
  owns it, multicast ``AsyncZeroconf()`` can raise ``EADDRINUSE``. Use **unicast** or
  **auto** (try multicast, then fall back).
- **aiohomekit** (supported range **3.2.x–3.x**, see ``requirements.txt``): startup
  expects existing ``AsyncServiceBrowser`` instances for ``_hap._tcp.local.`` and
  ``_hap._udp.local.``; we always register both before ``HKController.async_start()``.
  Transport discovery iteration is wrapped to soft-fail across minor library API
  differences (logged once).
- **BSD / macOS errno 49**: unicast with broad interface / dual-stack choices can cause
  ``sendto`` failures; unicast on BSD-like hosts defaults to narrower interface and
  IPv4 unless overridden (Custom Params or env; env wins).
"""
from __future__ import annotations

import asyncio
import errno
import hmac
import json
import logging
import os
import socket
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional

import aiomqtt
import websockets
from zeroconf import InterfaceChoice, IPVersion
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

from aiohomekit import Controller as HKController
from aiohomekit.controller.abstract import TransportType
from aiohomekit.exceptions import AccessoryNotFoundError, AuthenticationError
from aiohomekit.model.characteristics import CharacteristicPermissions, CharacteristicsTypes
from aiohomekit.model.services.service_types import ServicesTypes
from aiohomekit.uuid import normalize_uuid

from .mqtt_topics import (
    DEFAULT_MQTT_BROKER_HOST,
    DEFAULT_MQTT_BROKER_PORT,
    MQTT_QOS_AT_LEAST_ONCE,
    MQTT_TRANSPORT_STATUS_CONNECTED,
    MQTT_TRANSPORT_STATUS_DISABLED,
    MQTT_TRANSPORT_STATUS_NOT_CONNECTED,
    client_out_event_topic,
    client_out_rpc_topic,
    clients_ingress_subscribe_pattern,
    mqtt_transport_enabled,
    normalize_hub_slug_param,
    parse_ingress_client_slug,
    sanitize_client_slug,
)

try:
    from aiohomekit.model.categories import Categories as _HapCategories
except Exception:  # pragma: no cover - optional typing surface
    _HapCategories = None

PROTOCOL_VERSION = "1"

# ``warnings`` on hello ``ack`` / ``list_devices`` (always present; ``[]`` clears client notices).
WS_NOTICE_LEVEL_WARNING = "warning"
WS_NOTICE_LEVEL_ERROR = "error"
WS_NOTICE_CODE_ACCESSORIES_LOAD_FAILED = "accessories_load_failed"
WS_NOTICE_CODE_ACCESSORIES_REFRESH_FAILED = "accessories_refresh_failed"
WS_NOTICE_CODE_GET_CHARACTERISTICS_FAILED = "get_characteristics_failed"
WS_NOTICE_CODE_METADATA_CATEGORY_MISSING = "metadata_category_missing"
WS_NOTICE_CODE_METADATA_INCOMPLETE = "metadata_incomplete"
WS_NOTICE_CODE_METADATA_NO_AI_CHARS = "metadata_no_accessory_info_characteristics"
WS_NOTICE_CODE_METADATA_NO_REPRESENTATIVE = "metadata_no_representative_accessory"
WS_NOTICE_CODE_HUB_CONTROLLER_NOT_READY = "hub_controller_not_ready"
WS_NOTICE_CODE_LIST_DEVICES_INVALID_DEVICE_ID = "list_devices_invalid_device_id"


def _ws_client_notice(
    *,
    level: str,
    code: str,
    message: str,
    device_id: Optional[str] = None,
    primary_aid: Optional[int] = None,
) -> dict[str, Any]:
    """One structured notice for WebSocket clients (``warnings`` array)."""
    row: dict[str, Any] = {"level": level, "code": code, "message": message}
    if device_id:
        row["device_id"] = str(device_id).strip().lower()
    if primary_aid is not None:
        row["primary_aid"] = int(primary_aid)
    return row


# Client → hub actions supported by this build (advertised in hello ``ack.capabilities``).
WS_PROTOCOL_ACTIONS: tuple[str, ...] = (
    "hello",
    "command",
    "snapshot",
    "list_devices",
    "get",
    "subscribe",
    "unsubscribe",
)


@dataclass(frozen=True, slots=True)
class MqttClientSession:
    """Virtual hub client keyed by MQTT ``client_slug`` (topic path segment)."""

    slug: str
TYPED_PAIRING_SLOTS_KEY = "pairing_slots"
DATA_KEY_PAIRINGS = "homekit_pairings"
# Last HAP discover snapshot (for UI; written by discover_collect)
DATA_KEY_LAST_HAP_DISCOVER = "last_hap_discover"
HAP_TYPE_TCP = "_hap._tcp.local."
HAP_TYPE_UDP = "_hap._udp.local."

# WebSocket: per-client outbound queue caps (drop-oldest on overflow).
WS_CLIENT_OUTBOUND_QUEUE_MAX = 256
# HAP → hub broadcast: bounded queue before fan-out to clients (drop-oldest on overflow).
HAP_EVENT_BROADCAST_QUEUE_MAX = 512
PAIRING_HEALTH_PROBE_INTERVAL_SEC = 90.0
PAIRING_HEALTH_PROBE_START_DELAY_SEC = 20.0
# After power-cycle, HAP often needs a moment to listen; zeroconf port can also churn twice.
PAIRING_HEALTH_POST_RESYNC_RETRIES = 6
PAIRING_HEALTH_POST_RESYNC_DELAY_SEC = 2.0
PAIRING_HEALTH_POST_RESYNC_INITIAL_SETTLE_SEC = 0.85
PAIRING_HEALTH_RETRY_LIST_SLEEP_SEC = 0.45
PAIRING_HEALTH_RELOAD_SETTLE_SEC = 1.1
PAIRING_HEALTH_RELOAD_LIST_TRIES = 4
PAIRING_HEALTH_ZEROCONF_REQUEST_MS = 5000

# ISY **ERR** driver codes (must match profile NLS ``ERRC-*``).
ERR_PAIRING_NO_TARGET = 8
ERR_PAIRING_FAILED = 9


def _package_version(dist_name: str) -> str:
    try:
        from importlib.metadata import version

        return str(version(dist_name))
    except Exception:
        return ""


def probe_mdns_port_5353() -> str:
    """Best-effort: whether this process can bind UDP 5353 (multicast zeroconf path)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.bind(("0.0.0.0", 5353))
        except OSError as e:
            return f"bind_udp_5353_failed errno={e.errno} {e!s}"
        finally:
            s.close()
    except OSError as e:
        return f"socket_error {e!s}"
    return "udp_5353_bind_ok"


def _hap_service_browser_noop(*_args: Any, **_kwargs: Any) -> None:
    """No-op HAP browser handler; aiohomekit only requires that the browser exists."""


def normalize_hap_pin(raw: Any) -> str:
    """Return a HomeKit setup code as ``XXX-XX-XXX`` when the value is exactly 8 digits.

    Spaces and existing dashes are ignored for digit extraction; other characters
    mean we return the stripped original string so pairing can fail visibly.
    """
    s = (raw if raw is not None else "").strip()
    if not s:
        return ""
    digits = "".join(c for c in s if c.isdigit())
    if len(digits) == 8:
        return f"{digits[:3]}-{digits[3:5]}-{digits[5:8]}"
    return s


def _zeroconf_ctor_kwargs(
    log: logging.Logger,
    *,
    unicast: bool,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Optional ``AsyncZeroconf`` kwargs from env (wins), Custom Params, and host quirks.

    On **BSD / macOS**, unicast can hit **errno 49** when replying via LAN sockets toward
    ``127.0.0.1``; defaulting to **Default** interfaces + **IPv4-only** mitigates that.

    - ``HOMEKIT_HUB_ZEROCONF_INTERFACES`` or ``zeroconf_interfaces``: ``default`` | ``all``
    - ``HOMEKIT_HUB_ZEROCONF_IP_VERSION`` or ``zeroconf_ip_version``: ``v4`` | ``v6`` | ``all``
    """
    out: dict[str, Any] = {}
    ic_env = os.environ.get("HOMEKIT_HUB_ZEROCONF_INTERFACES", "").strip().lower()
    ic_param = str((params or {}).get("zeroconf_interfaces") or "").strip().lower()
    ic = ic_env or ic_param
    if ic == "default":
        out["interfaces"] = InterfaceChoice.Default
    elif ic == "all":
        out["interfaces"] = InterfaceChoice.All

    ipv_env = os.environ.get("HOMEKIT_HUB_ZEROCONF_IP_VERSION", "").strip().lower()
    ipv_param = str((params or {}).get("zeroconf_ip_version") or "").strip().lower()
    ipv = ipv_env or ipv_param
    if ipv in ("4", "v4", "ipv4"):
        out["ip_version"] = IPVersion.V4Only
    elif ipv in ("6", "v6", "ipv6"):
        out["ip_version"] = IPVersion.V6Only
    elif ipv in ("all", "dual"):
        out["ip_version"] = IPVersion.All

    bsdish = sys.platform.startswith(("freebsd", "darwin"))
    if unicast and bsdish:
        if "interfaces" not in out and ic not in ("all",):
            out["interfaces"] = InterfaceChoice.Default
            log.debug(
                "zeroconf unicast: InterfaceChoice.Default on %s (reduce errno 49)",
                sys.platform,
            )
        if "ip_version" not in out:
            out["ip_version"] = IPVersion.V4Only
            log.debug(
                "zeroconf unicast: IPVersion.V4Only on %s (reduce errno 49)",
                sys.platform,
            )
    return out


def _env_unicast_override() -> bool | None:
    raw = os.environ.get("HOMEKIT_HUB_ZEROCONF_UNICAST", "").strip().lower()
    if not raw:
        return None
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return None


def _param_unicast_policy(params: dict[str, Any] | None) -> str:
    if not params:
        return "auto"
    raw = str(params.get("zeroconf_unicast") or "").strip().lower()
    if raw in ("on", "1", "true", "yes", "unicast"):
        return "on"
    if raw in ("off", "0", "false", "no", "multicast"):
        return "off"
    return "auto"


def _make_azc_unicast(log: logging.Logger, kw: dict[str, Any]) -> AsyncZeroconf:
    try:
        return AsyncZeroconf(unicast=True, **kw)
    except TypeError:
        log.error(
            "unicast mode is not supported by this python-zeroconf; "
            "free UDP 5353 or upgrade zeroconf"
        )
        raise


def _make_azc_multicast(_log: logging.Logger, kw: dict[str, Any]) -> AsyncZeroconf:
    return AsyncZeroconf(**kw) if kw else AsyncZeroconf()


def create_async_zeroconf(
    log: logging.Logger,
    params: dict[str, Any] | None,
) -> tuple[AsyncZeroconf, bool, str]:
    """Build ``AsyncZeroconf``; return ``(instance, using_unicast, mode_label)``."""
    e = _env_unicast_override()
    pol = _param_unicast_policy(params)

    if e is True:
        kw = _zeroconf_ctor_kwargs(log, unicast=True, params=params)
        log.info("zeroconf: HOMEKIT_HUB_ZEROCONF_UNICAST env forces unicast mode")
        return _make_azc_unicast(log, kw), True, "env_on"
    if e is False:
        kw = _zeroconf_ctor_kwargs(log, unicast=False, params=params)
        log.info("zeroconf: HOMEKIT_HUB_ZEROCONF_UNICAST env forces multicast mode")
        return _make_azc_multicast(log, kw), False, "env_multicast"

    if pol == "on":
        kw = _zeroconf_ctor_kwargs(log, unicast=True, params=params)
        log.info("zeroconf: zeroconf_unicast=on (unicast)")
        return _make_azc_unicast(log, kw), True, "param_on"
    if pol == "off":
        kw = _zeroconf_ctor_kwargs(log, unicast=False, params=params)
        log.info("zeroconf: zeroconf_unicast=off (multicast only)")
        try:
            return _make_azc_multicast(log, kw), False, "param_multicast"
        except OSError as ex:
            if ex.errno != errno.EADDRINUSE:
                raise
            log.error(
                "zeroconf: multicast-only but UDP 5353 is in use; "
                "set zeroconf_unicast to auto/on or HOMEKIT_HUB_ZEROCONF_UNICAST=1"
            )
            raise

    kw_m = _zeroconf_ctor_kwargs(log, unicast=False, params=params)
    try:
        log.info("zeroconf: auto — trying multicast AsyncZeroconf first")
        return _make_azc_multicast(log, kw_m), False, "auto_multicast"
    except OSError as ex:
        if ex.errno != errno.EADDRINUSE:
            raise
        log.warning(
            "zeroconf: auto — mDNS port 5353 in use; falling back to unicast "
            "(set zeroconf_unicast=on to skip multicast attempt)"
        )
        kw_u = _zeroconf_ctor_kwargs(log, unicast=True, params=params)
        return _make_azc_unicast(log, kw_u), True, "auto_unicast_fallback"


class ZeroconfManager:
    """Owns ``AsyncZeroconf`` and mandatory HAP ``AsyncServiceBrowser`` instances."""

    def __init__(self, log: logging.Logger) -> None:
        self.log = log
        self._azc: Optional[AsyncZeroconf] = None
        self._hap_browsers: list[AsyncServiceBrowser] = []
        self.using_unicast = False
        self.mode_label = ""

    @property
    def async_zeroconf(self) -> Optional[AsyncZeroconf]:
        return self._azc

    @property
    def hap_browsers(self) -> list[AsyncServiceBrowser]:
        return self._hap_browsers

    async def start(self, params: dict[str, Any] | None) -> AsyncZeroconf:
        self._azc, self.using_unicast, self.mode_label = create_async_zeroconf(
            self.log, params
        )
        assert self._azc is not None
        zc = self._azc.zeroconf
        for hap_type in (HAP_TYPE_TCP, HAP_TYPE_UDP):
            self._hap_browsers.append(
                AsyncServiceBrowser(zc, hap_type, handlers=[_hap_service_browser_noop])
            )
        return self._azc

    async def stop(self) -> None:
        for browser in self._hap_browsers:
            try:
                await browser.async_cancel()
            except Exception:
                self.log.exception("AsyncServiceBrowser.async_cancel")
        self._hap_browsers.clear()
        if self._azc is not None:
            try:
                await self._azc.async_close()
            except Exception:
                self.log.exception("AsyncZeroconf.async_close")
            self._azc = None


def slot_alias(slot_num: int) -> str:
    return f"slot_{slot_num}"


def slot_num_from_alias(alias: str) -> int | None:
    """Parse ``slot_<n>`` pairing alias to a positive slot number."""
    if not isinstance(alias, str) or not alias.startswith("slot_"):
        return None
    tail = alias[5:].strip()
    if not tail.isdigit():
        return None
    n = int(tail)
    if n < 1:
        return None
    return n


def _ip_lan_endpoint_str(pairing: Any) -> str | None:
    """Return ``host:port`` for IP pairings from aiohomekit zeroconf ``description``, or None."""
    pdata = getattr(pairing, "pairing_data", None)
    if not isinstance(pdata, dict) or pdata.get("Connection") != "IP":
        return None
    desc = getattr(pairing, "description", None)
    if desc is None:
        return None
    host = getattr(desc, "address", None)
    port = getattr(desc, "port", None)
    if not host or port is None:
        return None
    h = str(host).strip()
    if not h:
        return None
    try:
        p = int(port)
    except (TypeError, ValueError):
        return None
    return f"{h}:{p}"


def _parse_slot_value(raw: Any) -> Optional[int]:
    """Return positive int slot, or None if empty / invalid / must be auto-assigned."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        n = int(s)
    except (TypeError, ValueError):
        return None
    if n < 1:
        return None
    return n


def assign_pairing_slot_rows(rows: list, log: logging.Logger) -> list[tuple[int, dict[str, Any]]]:
    """
    Map each typed row to a slot number:
    - If the row has a **slot** field, use it (duplicates: later row is re-assigned to the next free slot).
    - If **slot** is empty, assign the **smallest positive integer** not already used (fills gaps, then extends).
    Returns (slot, row) sorted by slot.
    """
    if not isinstance(rows, list):
        return []
    explicit: list[tuple[int, dict[str, Any]]] = []
    auto: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        n = _parse_slot_value(row.get("slot"))
        if n is not None:
            explicit.append((n, row))
        else:
            auto.append(row)

    used: set[int] = set()
    out: list[tuple[int, dict[str, Any]]] = []

    for n, row in explicit:
        if n in used:
            log.warning(
                "Duplicate slot %s in typed config; assigning next free slot to this row",
                n,
            )
            auto.append(row)
            continue
        used.add(n)
        out.append((n, row))

    for row in auto:
        n = 1
        while n in used:
            n += 1
        used.add(n)
        out.append((n, row))

    out.sort(key=lambda x: x[0])
    if len(out) > 128:
        log.warning("Large number of pairing rows (%d); expect high resource use", len(out))
    return out


def _row_pin_and_filters(row: dict[str, Any]) -> tuple[str, str, str]:
    pin = normalize_hap_pin(row.get("hap_pin"))
    acc_id = (row.get("accessory_id") or "").strip().lower()
    acc_name = (row.get("accessory_name") or "").strip()
    return pin, acc_id, acc_name


def _resolve_filters_from_last_discover(
    data: dict[str, Any],
    acc_id: str,
    acc_name: str,
    log: logging.Logger,
    slot_num: int,
) -> tuple[str, str]:
    """
    If the typed row leaves id/name empty, use unpaired device(s) from
    `last_hap_discover` (set when the user runs DISCOVER) so they do not have to
    re-copy ids into the form. Multiple unpaired: use the first, with a warning.
    If still unresolved, return empty and pairing falls back to first unpaired
    on the network (or times out if ambiguous).
    """
    if acc_id or acc_name:
        return acc_id, acc_name
    raw = data.get(DATA_KEY_LAST_HAP_DISCOVER)
    if not isinstance(raw, list) or not raw:
        log.info(
            "Slot %s: accessory_id/accessory_name empty; no %s — pairing will use "
            "the first unpaired accessory found on the network. Run DISCOVER first "
            "so the hub can target the device from a saved scan.",
            slot_num,
            DATA_KEY_LAST_HAP_DISCOVER,
        )
        return "", ""
    unpaired = [r for r in raw if isinstance(r, dict) and not r.get("paired")]
    if not unpaired:
        log.info(
            "Slot %s: %s has no unpaired devices; waiting for on-network unpaired target",
            slot_num,
            DATA_KEY_LAST_HAP_DISCOVER,
        )
        return "", ""
    if len(unpaired) > 1:
        log.warning(
            "Slot %s: %d unpaired in %s; using the first. Set accessory_id (or name) on this row to pick a different one.",
            slot_num,
            len(unpaired),
            DATA_KEY_LAST_HAP_DISCOVER,
        )
    pick = unpaired[0]
    pid = (str(pick.get("id") or "")).strip().lower()
    pname = (str(pick.get("name") or "")).strip()
    log.info(
        "Slot %s: using id/name from %s: %r / %r",
        slot_num,
        DATA_KEY_LAST_HAP_DISCOVER,
        pid,
        pname,
    )
    return pid, pname


def _build_uuid_to_char_name() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for attr, val in vars(CharacteristicsTypes).items():
        if attr.startswith("_") or not isinstance(val, str):
            continue
        mapping[normalize_uuid(val)] = attr
    return mapping


_UUID_TO_NAME = _build_uuid_to_char_name()


def characteristic_label(type_uuid: str) -> str:
    nu = normalize_uuid(type_uuid)
    return _UUID_TO_NAME.get(nu, nu)


def _parse_char_name_to_uuid(spec: str) -> str:
    spec = spec.strip()
    if hasattr(CharacteristicsTypes, spec):
        return normalize_uuid(getattr(CharacteristicsTypes, spec))
    return normalize_uuid(spec)


def _resolve_aid_iid_detailed(
    pairing, char_spec: str
) -> tuple[Optional[tuple[int, int]], Optional[str]]:
    """Resolve HAP (aid, iid) for *char_spec*.

    Returns ``((aid, iid), None)`` on success, or ``(None, error_message)`` on failure.
    Never raises: invalid UUID / type tokens become a readable error string for RPC clients.
    """
    try:
        uid = _parse_char_name_to_uuid(char_spec)
    except ValueError as e:
        return None, str(e)
    if not pairing.accessories:
        return None, "accessory list not loaded"
    for acc in pairing.accessories:
        for svc in acc.services:
            for ch in svc.characteristics:
                if normalize_uuid(ch.type) == uid:
                    return (acc.aid, ch.iid), None
    return None, f"unknown characteristic {char_spec!r}"


def _resolve_aid_iid(pairing, char_spec: str) -> Optional[tuple[int, int]]:
    resolved, err = _resolve_aid_iid_detailed(pairing, char_spec)
    return resolved if err is None else None


def _subscribable_characteristics(pairing) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    if not pairing.accessories:
        return out
    for acc in pairing.accessories:
        aid = acc.aid
        for svc in acc.services:
            for ch in svc.characteristics:
                if CharacteristicPermissions.events in ch.perms:
                    out.append((aid, ch.iid))
    return out


def _readable_characteristics(pairing) -> list[tuple[int, int, str]]:
    """Return (aid, iid, characteristic_label) for readable characteristics."""
    out: list[tuple[int, int, str]] = []
    if not pairing.accessories:
        return out
    read_tokens = {
        str(getattr(CharacteristicPermissions, "paired_read", "paired_read")),
        str(getattr(CharacteristicPermissions, "read", "read")),
        "pr",
    }
    for acc in pairing.accessories:
        aid = acc.aid
        for svc in acc.services:
            for ch in svc.characteristics:
                perms = {str(p) for p in (getattr(ch, "perms", None) or [])}
                if perms & read_tokens:
                    out.append((aid, ch.iid, characteristic_label(ch.type)))
    return out


# HAP Accessory Information characteristic labels → WebSocket ``list_devices`` JSON keys.
_WS_DEVICE_META_BY_LABEL: dict[str, str] = {
    "Name": "name",
    "Manufacturer": "manufacturer",
    "Model": "model",
    "SerialNumber": "serial_number",
    "FirmwareRevision": "firmware_revision",
    "HardwareRevision": "hardware_revision",
}


def _build_accessory_info_uuid_to_label() -> dict[str, str]:
    """Map normalized HAP characteristic UUID → logical label (Name, Manufacturer, …).

    Some accessories advertise Accessory Information characteristics outside the standard
    service UUID, or aiohomekit exposes ``characteristic_label`` as a raw UUID when the
    type is unknown. UUID matching keeps ``list_devices`` metadata and HAP reads working.
    """
    m: dict[str, str] = {}
    for ct_name, label in (
        ("NAME", "Name"),
        ("MANUFACTURER", "Manufacturer"),
        ("MODEL", "Model"),
        ("SERIAL_NUMBER", "SerialNumber"),
        ("FIRMWARE_REVISION", "FirmwareRevision"),
        ("HARDWARE_REVISION", "HardwareRevision"),
        ("CATEGORY", "Category"),
        ("CONFIGURED_NAME", "ConfiguredName"),
    ):
        if not hasattr(CharacteristicsTypes, ct_name):
            continue
        try:
            m[normalize_uuid(getattr(CharacteristicsTypes, ct_name))] = label
        except Exception:
            continue
    return m


_ACCESSORY_INFO_UUID_TO_LABEL: dict[str, str] = _build_accessory_info_uuid_to_label()


def _accessory_info_char_label(ch) -> Optional[str]:
    """Return the logical Accessory Information label for ``ch``, or ``None``."""
    try:
        nu = normalize_uuid(ch.type)
    except Exception:
        return None
    lab = _ACCESSORY_INFO_UUID_TO_LABEL.get(nu)
    if lab:
        return lab
    try:
        lab2 = characteristic_label(ch.type)
    except Exception:
        return None
    if lab2 in _WS_DEVICE_META_BY_LABEL or lab2 in ("Category", "ConfiguredName"):
        return lab2
    return None


def _hap_category_bridge_id() -> int:
    if _HapCategories is None:
        return 2
    try:
        return int(_HapCategories.BRIDGE)
    except Exception:
        return 2


def _category_id_to_label(cat_id: int) -> str:
    if _HapCategories is None:
        return ""
    for name in dir(_HapCategories):
        if name.startswith("_"):
            continue
        val = getattr(_HapCategories, name, None)
        if val == cat_id:
            return name
    return ""


def _accessory_info_category_value(acc) -> Optional[int]:
    """Read integer HAP **Category** from the Accessory Information service, if present."""
    if not acc:
        return None
    for svc in acc.services:
        for ch in svc.characteristics:
            if _accessory_info_char_label(ch) != "Category":
                continue
            v = getattr(ch, "value", None)
            if isinstance(v, bool):
                return None
            if isinstance(v, int):
                return v
            if isinstance(v, float) and v == int(v):
                return int(v)
            if isinstance(v, str) and v.strip().isdigit():
                return int(v.strip())
    return None


def _build_thermostat_like_service_uuids() -> frozenset[str]:
    out: list[str] = []
    for name in ("THERMOSTAT", "HEATER_COOLER"):
        if not hasattr(ServicesTypes, name):
            continue
        try:
            out.append(normalize_uuid(getattr(ServicesTypes, name)))
        except Exception:
            continue
    return frozenset(out)


_THERMOSTAT_LIKE_SERVICE_UUIDS: frozenset[str] = _build_thermostat_like_service_uuids()


def _accessory_information_service_uuid_normalized() -> Optional[str]:
    """Normalized UUID for HAP **Accessory Information** (``ServicesTypes.ACCESSORY_INFORMATION``)."""
    if not hasattr(ServicesTypes, "ACCESSORY_INFORMATION"):
        return None
    try:
        return normalize_uuid(getattr(ServicesTypes, "ACCESSORY_INFORMATION"))
    except Exception:
        return None


_ACCESSORY_INFORMATION_SERVICE_UUID_NORM: Optional[str] = (
    _accessory_information_service_uuid_normalized()
)


def _services_for_accessory_information_metadata(acc: Any) -> list[Any]:
    """
    Services whose metadata feeds ``list_devices`` Accessory Information fields.

    Ecobee (and similar) accessories expose **Name** on both Accessory Information (primary
    label, snapshot ``NAME`` at low ``iid``) and on Motion / Occupancy services (e.g.
    "Kitchen Motion"). Scanning *all* services made ``name`` depend on iteration order; we
    only ingest AI characteristics from the standard **Accessory Information** service when
    it is present on the model.
    """
    all_svcs = list(getattr(acc, "services", None) or [])
    nu_ai = _ACCESSORY_INFORMATION_SERVICE_UUID_NORM
    if not nu_ai:
        return all_svcs
    picked = [svc for svc in all_svcs if _normalized_service_type_uuid(svc) == nu_ai]
    return picked if picked else all_svcs


def _normalized_service_type_uuid(svc: Any) -> str:
    try:
        t = getattr(svc, "type", None)
        if t is None:
            return ""
        return normalize_uuid(t)
    except Exception:
        return ""


def _accessory_has_thermostat_like_service(acc) -> bool:
    """True when ``acc`` exposes Thermostat or Heater Cooler HAP services."""
    if not acc:
        return False
    svcs = getattr(acc, "services", None)
    if not svcs:
        return False
    for svc in svcs:
        nu = _normalized_service_type_uuid(svc)
        if nu and nu in _THERMOSTAT_LIKE_SERVICE_UUIDS:
            return True
    return False


def _prefer_accessory_with_thermostat_service(ordered: list[Any]) -> Optional[Any]:
    """Lowest ``aid`` among accessories that expose thermostat-like services (Ecobee bridge layout)."""
    picks = [a for a in ordered if _accessory_has_thermostat_like_service(a)]
    if not picks:
        return None
    return min(picks, key=lambda a: int(getattr(a, "aid", 0) or 0))


def _representative_accessory(pairing) -> Any:
    """Pick one accessory whose Accessory Information populates ``list_devices`` metadata.

    For a standalone device this is typically ``aid`` 1. For a HomeKit **bridge** pairing,
    skip accessories that advertise category **Bridge** when other accessories exist so
    clients see a meaningful child. When **Category** is missing from Accessory Information
    (common on Ecobee), prefer the accessory that exposes **Thermostat** / **Heater Cooler**
    services so metadata and ``primary_aid`` match the climate endpoint—not a separate
    Occupancy child that may share the lowest ``aid``.
    """
    accs = getattr(pairing, "accessories", None)
    if not accs:
        return None
    ordered = sorted(accs, key=lambda a: int(getattr(a, "aid", 0) or 0))
    if len(ordered) == 1:
        return ordered[0]
    bridge_id = _hap_category_bridge_id()
    non_bridge: list[Any] = []
    for a in ordered:
        cat = _accessory_info_category_value(a)
        if cat is not None and cat != bridge_id:
            non_bridge.append(a)
    if non_bridge:
        therm_nb = [a for a in non_bridge if _accessory_has_thermostat_like_service(a)]
        if therm_nb:
            return min(therm_nb, key=lambda a: int(getattr(a, "aid", 0) or 0))
        return min(non_bridge, key=lambda a: int(getattr(a, "aid", 0) or 0))
    therm_first = _prefer_accessory_with_thermostat_service(ordered)
    if therm_first is not None:
        return therm_first
    return ordered[0]


def _infer_thermostat_category_from_services(pairing, entry: dict[str, Any]) -> None:
    """If **Category** is absent from AI reads, infer thermostat (9) from HAP services."""
    if entry.get("category") is not None:
        return
    accs = getattr(pairing, "accessories", None)
    if not accs:
        return
    for acc in sorted(accs, key=lambda a: int(getattr(a, "aid", 0) or 0)):
        if not _accessory_has_thermostat_like_service(acc):
            continue
        entry["category"] = 9
        cl = _category_id_to_label(9)
        if cl:
            entry["category_label"] = cl
        try:
            entry["primary_aid"] = int(getattr(acc, "aid", 0) or 0)
        except (TypeError, ValueError):
            entry["primary_aid"] = 0
        return


def _accessory_summaries_for_pairing(pairing) -> list[dict[str, Any]]:
    """One summary dict per HAP accessory (``aid``) for ``list_devices`` / hello ``devices[]``.

    Built from the cached ``/accessories`` model only (no extra HAP GET per accessory).
    When Accessory Information omits **Category** but Thermostat / Heater Cooler services are
    present, **category** / **category_label** are filled with thermostat (9) and
    **category_inferred** is ``True``. Plugins can filter rows where ``category`` == 9 or
    ``thermostat_like`` is true without relying on the pairing-level representative row alone.
    """
    accs = getattr(pairing, "accessories", None)
    if not accs:
        return []
    out: list[dict[str, Any]] = []
    for acc in sorted(accs, key=lambda a: int(getattr(a, "aid", 0) or 0)):
        try:
            aid = int(getattr(acc, "aid", 0) or 0)
        except (TypeError, ValueError):
            continue
        meta = _accessory_information_metadata(acc)
        cat_raw = _accessory_info_category_value(acc)
        thermostat_like = _accessory_has_thermostat_like_service(acc)
        cat = cat_raw
        inferred = False
        if cat is None and thermostat_like:
            cat = 9
            inferred = True
        row: dict[str, Any] = {"aid": aid}
        for key in ("name", "manufacturer", "model", "serial_number", "firmware_revision"):
            v = meta.get(key)
            if isinstance(v, str) and v.strip():
                row[key] = v.strip()
        if cat is not None:
            row["category"] = cat
            cl = _category_id_to_label(cat)
            if cl:
                row["category_label"] = cl
        if inferred:
            row["category_inferred"] = True
        if thermostat_like:
            row["thermostat_like"] = True
        out.append(row)
    return out


def _accessories_imply_thermostat_class(accessories: Any) -> bool:
    """True when per-accessory summaries already expose a thermostat (hint for warnings)."""
    if not isinstance(accessories, list):
        return False
    for item in accessories:
        if not isinstance(item, dict):
            continue
        if item.get("category") == 9:
            return True
        if item.get("thermostat_like"):
            return True
    return False


def _characteristic_display_string(ch) -> str:
    v = getattr(ch, "value", None)
    if v is None:
        return ""
    if isinstance(v, bytes):
        try:
            return v.decode("utf-8", errors="replace").strip()
        except Exception:
            return str(v).strip()
    if isinstance(v, (dict, list)):
        return ""
    return str(v).strip()


def _char_is_readable(ch) -> bool:
    read_tokens = {
        str(getattr(CharacteristicPermissions, "paired_read", "paired_read")),
        str(getattr(CharacteristicPermissions, "read", "read")),
        "pr",
    }
    perms = {str(p) for p in (getattr(ch, "perms", None) or [])}
    return bool(perms & read_tokens)


def _value_to_meta_string(val: Any) -> str:
    """Normalize a HAP characteristic value for WebSocket metadata strings."""
    if val is None:
        return ""
    if isinstance(val, Enum):
        val = val.value
    if isinstance(val, bytes):
        try:
            return val.decode("utf-8", errors="replace").strip()
        except Exception:
            return str(val).strip()
    if isinstance(val, (dict, list)):
        return ""
    if isinstance(val, bool):
        return ""
    return str(val).strip()


def _accessory_information_metadata_needs_hap_reads(static: dict[str, Any]) -> bool:
    """Return True when cached Accessory Information is incomplete and HAP GETs may help.

    Downstream clients (e.g. HomeKit-mode Ecobee) rely on **category** and **category_label**
    in ``list_devices`` / hello ``devices[]``. Previously we only issued reads when
    **Manufacturer** was empty, which skipped **Category** reads when manufacturer was
    already present in the cached ``/accessories`` model.
    """
    if static.get("category") is None:
        return True
    if not (static.get("manufacturer") or "").strip():
        return True
    if not (static.get("model") or "").strip():
        return True
    return False


def _accessory_information_metadata(acc) -> dict[str, Any]:
    """Fields from the HAP Accessory Information service (cached model / last /accessories)."""
    out: dict[str, Any] = {}
    if not acc:
        return out
    for svc in _services_for_accessory_information_metadata(acc):
        for ch in svc.characteristics:
            label = _accessory_info_char_label(ch)
            if not label:
                continue
            if label == "Category":
                v = getattr(ch, "value", None)
                if isinstance(v, bool):
                    continue
                if isinstance(v, float) and v == int(v):
                    v = int(v)
                if isinstance(v, int):
                    out["category"] = v
                    cl = _category_id_to_label(v)
                    if cl:
                        out["category_label"] = cl
                elif isinstance(v, str) and v.strip().isdigit():
                    iv = int(v.strip())
                    out["category"] = iv
                    cl = _category_id_to_label(iv)
                    if cl:
                        out["category_label"] = cl
                continue
            json_key = _WS_DEVICE_META_BY_LABEL.get(label)
            if not json_key:
                continue
            s = _characteristic_display_string(ch)
            if s:
                out[json_key] = s
    return out


async def _accessory_information_metadata_with_reads(
    pairing,
    acc,
    log: Optional[logging.Logger] = None,
    *,
    client_notices: Optional[list[dict[str, Any]]] = None,
    notice_device_id: str = "",
) -> dict[str, Any]:
    """Accessory Information from the in-memory model, then HAP reads when metadata is incomplete.

    Many accessories omit values from the initial ``/accessories`` JSON; a
    ``get_characteristics`` pass is often required before **Category**, **Manufacturer**,
    **Model**, etc. appear. Clients filter on **category** (e.g. thermostat = 9) as well
    as vendor strings.

    When ``client_notices`` is provided, structured **warning** / **error** objects are
    appended for downstream Node Servers to surface in their UI or logs.
    """
    static = _accessory_information_metadata(acc)
    if not acc or not pairing:
        return static
    if not _accessory_information_metadata_needs_hap_reads(static):
        return static

    aid = acc.aid
    pairs: list[tuple[int, int]] = []
    labels: dict[tuple[int, int], str] = {}
    for svc in acc.services:
        for ch in svc.characteristics:
            label = _accessory_info_char_label(ch)
            if not label:
                continue
            key = (aid, ch.iid)
            if key in labels:
                continue
            # Accessory Information strings are often readable even when perms omit
            # ``paired_read`` / ``read`` in the cached model; still attempt HAP GET.
            pairs.append(key)
            labels[key] = label
    if not pairs:
        if log and not static.get("manufacturer"):
            log.debug(
                "list_devices metadata: no Accessory Information characteristics matched "
                "for aid=%s (cached model may be incomplete)",
                aid,
            )
        if (
            client_notices is not None
            and notice_device_id
            and _accessory_information_metadata_needs_hap_reads(static)
        ):
            client_notices.append(
                _ws_client_notice(
                    level=WS_NOTICE_LEVEL_WARNING,
                    code=WS_NOTICE_CODE_METADATA_NO_AI_CHARS,
                    message=(
                        "No Accessory Information characteristics matched in the cached model; "
                        "metadata may be incomplete until /accessories refreshes"
                    ),
                    device_id=notice_device_id,
                    primary_aid=int(getattr(acc, "aid", 0) or 0),
                )
            )
        return static
    try:
        result = await pairing.get_characteristics(pairs)
    except Exception:
        if log:
            log.debug(
                "list_devices metadata: get_characteristics failed aid=%s count=%d",
                aid,
                len(pairs),
                exc_info=True,
            )
        if client_notices is not None and notice_device_id:
            client_notices.append(
                _ws_client_notice(
                    level=WS_NOTICE_LEVEL_ERROR,
                    code=WS_NOTICE_CODE_GET_CHARACTERISTICS_FAILED,
                    message="get_characteristics failed while loading Accessory Information metadata",
                    device_id=notice_device_id,
                    primary_aid=int(getattr(acc, "aid", 0) or 0),
                )
            )
        return static

    merged: dict[str, Any] = dict(static)
    configured_name: Optional[str] = None
    for key, payload in result.items():
        if not isinstance(key, tuple) or len(key) != 2:
            continue
        lab = labels.get(key)
        if not lab or not isinstance(payload, dict) or "value" not in payload:
            continue
        val = payload.get("value")
        if lab == "Category":
            v = val
            if isinstance(v, Enum):
                v = v.value
            if isinstance(v, bool):
                continue
            if isinstance(v, float) and v == int(v):
                v = int(v)
            if isinstance(v, int):
                merged["category"] = v
                cl = _category_id_to_label(v)
                if cl:
                    merged["category_label"] = cl
            elif isinstance(v, str) and v.strip().isdigit():
                iv = int(v.strip())
                merged["category"] = iv
                cl = _category_id_to_label(iv)
                if cl:
                    merged["category_label"] = cl
            continue
        if lab == "ConfiguredName":
            s = _value_to_meta_string(val)
            if s:
                configured_name = s
            continue
        json_key = _WS_DEVICE_META_BY_LABEL.get(lab)
        if not json_key:
            continue
        s = _value_to_meta_string(val)
        if s:
            merged[json_key] = s
    if configured_name and not merged.get("name"):
        merged["name"] = configured_name
    if log and merged.get("category") is None:
        for pair_key, lab in labels.items():
            if lab != "Category":
                continue
            pl = result.get(pair_key)
            if pl is not None:
                log.debug(
                    "list_devices metadata: Category HAP read aid=%s iid=%s payload=%r",
                    pair_key[0],
                    pair_key[1],
                    pl,
                )
    if log and _accessory_information_metadata_needs_hap_reads(merged):
        log.debug(
            "list_devices metadata: incomplete after get_characteristics aid=%s "
            "requested_pairs=%d merged_keys=%s",
            aid,
            len(pairs),
            sorted(merged.keys()),
        )
    if (
        client_notices is not None
        and notice_device_id
        and merged.get("category") is not None
        and _accessory_information_metadata_needs_hap_reads(merged)
    ):
        client_notices.append(
            _ws_client_notice(
                level=WS_NOTICE_LEVEL_WARNING,
                code=WS_NOTICE_CODE_METADATA_INCOMPLETE,
                message=(
                    "Accessory Information is still missing manufacturer and/or model after HAP read"
                ),
                device_id=notice_device_id,
                primary_aid=int(getattr(acc, "aid", 0) or 0),
            )
        )
    return merged


async def _device_list_entry_resolved(
    device_id: str, pairing, log: Optional[logging.Logger] = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """One ``list_devices`` / hello ``devices[]`` row plus optional client ``warnings`` entries."""
    canon = str(device_id or "").strip().lower()
    entry: dict[str, Any] = {"device_id": canon}
    client_notices: list[dict[str, Any]] = []
    if not pairing:
        return entry, client_notices

    meta: dict[str, Any] = {}
    acc: Any = None

    for attempt in range(2):
        if attempt == 1:
            try:
                await pairing.list_accessories_and_characteristics()
            except Exception:
                if log:
                    log.debug(
                        "list_devices metadata: list_accessories_and_characteristics retry failed "
                        "device_id=%s",
                        device_id,
                        exc_info=True,
                    )
                client_notices.append(
                    _ws_client_notice(
                        level=WS_NOTICE_LEVEL_ERROR,
                        code=WS_NOTICE_CODE_ACCESSORIES_REFRESH_FAILED,
                        message=(
                            "list_accessories_and_characteristics failed while refreshing metadata "
                            "for Accessory Information"
                        ),
                        device_id=device_id,
                        primary_aid=(int(getattr(acc, "aid", 0) or 0) if acc else None),
                    )
                )
                break

        acc = _representative_accessory(pairing)
        if not acc:
            meta = {}
            if getattr(pairing, "accessories", None):
                client_notices.append(
                    _ws_client_notice(
                        level=WS_NOTICE_LEVEL_WARNING,
                        code=WS_NOTICE_CODE_METADATA_NO_REPRESENTATIVE,
                        message=(
                            "Could not pick a representative accessory for list_devices metadata "
                            "(accessories present but layout unexpected)"
                        ),
                        device_id=device_id,
                    )
                )
            break

        meta = await _accessory_information_metadata_with_reads(
            pairing,
            acc,
            log,
            client_notices=client_notices,
            notice_device_id=device_id,
        )

        # Many bridges (e.g. Ecobee) omit or deny **Category** on HAP GET even when
        # **Manufacturer** / **Model** succeed. Infer thermostat (9) from services here so
        # we do not always pay for ``list_accessories_and_characteristics`` solely to
        # re-attempt Category.
        if pairing and meta.get("category") is None:
            probe = dict(meta)
            _infer_thermostat_category_from_services(pairing, probe)
            if probe.get("category") is not None:
                meta["category"] = probe["category"]
                cl = probe.get("category_label")
                if cl:
                    meta["category_label"] = cl

        if meta.get("category") is not None:
            break

        if attempt == 0 and getattr(pairing, "accessories", None):
            if log:
                log.debug(
                    "list_devices metadata: category missing from Accessory Information; "
                    "refreshing /accessories device_id=%s primary_aid=%s",
                    device_id,
                    getattr(acc, "aid", None),
                )
            continue
        break

    if meta:
        entry.update(meta)
    if acc:
        try:
            entry["primary_aid"] = int(getattr(acc, "aid", 0) or 0)
        except (TypeError, ValueError):
            entry["primary_aid"] = 0

    if pairing:
        _infer_thermostat_category_from_services(pairing, entry)

    accessory_summaries: list[dict[str, Any]] = []
    if pairing:
        accessory_summaries = _accessory_summaries_for_pairing(pairing)
        if accessory_summaries:
            entry["accessories"] = accessory_summaries

    if (
        pairing
        and getattr(pairing, "accessories", None)
        and entry.get("category") is None
        and "primary_aid" in entry
        and not _accessories_imply_thermostat_class(accessory_summaries)
    ):
        if log:
            log.warning(
                "list_devices metadata: category still missing after Accessory Information reads "
                "and optional /accessories refresh device_id=%s primary_aid=%s entry_keys=%s",
                device_id,
                entry.get("primary_aid"),
                sorted(k for k in entry.keys() if k != "device_id"),
            )
        client_notices.append(
            _ws_client_notice(
                level=WS_NOTICE_LEVEL_WARNING,
                code=WS_NOTICE_CODE_METADATA_CATEGORY_MISSING,
                message=(
                    "HAP category is missing from Accessory Information after reads and optional "
                    "/accessories refresh; device-type filtering in downstream clients may not work"
                ),
                device_id=device_id,
                primary_aid=entry.get("primary_aid"),
            )
        )
    # PROTOCOL: ``device_id`` is the stable pairing id (events, snapshot, get). Metadata must
    # never clobber or clear it (e.g. accidental keys in merged dicts).
    entry["device_id"] = canon
    return entry, client_notices


def _list_devices_ws_message(
    devices: list[dict[str, Any]], warnings: list[dict[str, Any]]
) -> dict[str, Any]:
    """Hub → client ``list_devices`` JSON (always includes ``warnings``, possibly empty)."""
    msg: dict[str, Any] = {
        "version": PROTOCOL_VERSION,
        "action": "list_devices",
        "devices": devices,
        "warnings": warnings,
    }
    return msg


class HomeKitHubBridge:
    """Multi-pairing HomeKit hub: WebSocket server + fan-out events."""

    def __init__(
        self,
        logger: logging.Logger,
        get_params: Callable[[], dict[str, Any]],
        get_pairing_slot_rows: Callable[[], list],
        get_custom_data: Callable[[], dict[str, Any]],
        set_custom_data: Callable[[dict[str, Any]], None],
        pairing_notice: Optional[
            Callable[[int, str, str, Optional[Exception]], None]
        ] = None,
        pairing_health_notice: Optional[
            Callable[[bool, str, list[str], dict[str, str], bool], None]
        ] = None,
        mqtt_transport_notice: Optional[Callable[[int], None]] = None,
        hub_rpc_error_notice: Optional[
            Callable[[str, str, dict[str, Any]], None]
        ] = None,
    ) -> None:
        self.log = logger
        self._get_params = get_params
        self._get_pairing_slot_rows = get_pairing_slot_rows
        self._get_custom_data = get_custom_data
        self._set_custom_data = set_custom_data
        self._pairing_notice = pairing_notice
        self._pairing_health_notice = pairing_health_notice
        self._mqtt_transport_notice = mqtt_transport_notice
        self._hub_rpc_error_notice = hub_rpc_error_notice
        self._mqtt_drv_last: Optional[int] = None

        self._hk: Optional[HKController] = None
        self._async_zeroconf: Optional[AsyncZeroconf] = None
        self._zcm: Optional[ZeroconfManager] = None
        self._listeners: dict[str, Callable[[], None]] = {}
        # WebSocket client → (outbound text queue, sender task). Slow clients do not block others.
        self._client_out: dict[Any, tuple[asyncio.Queue[Optional[str]], asyncio.Task[None]]] = {}
        # When Custom Param ``ws_token`` is set: clients must complete ``hello`` with matching token first.
        self._ws_hello_authed: set[Any] = set()
        # Optional per-connection HAP event filter: (device_id_lower, aid, iid). Missing key → no filter (all events).
        self._ws_event_filters: dict[Any, set[tuple[str, int, int]]] = {}
        self._hap_evt_queue: Optional[asyncio.Queue[Optional[dict[str, Any]]]] = None
        self._hap_evt_worker: Optional[asyncio.Task[None]] = None
        self._pairing_probe_task: Optional[asyncio.Task[None]] = None
        self._pairing_unhealthy_aliases: set[str] = set()
        self._pairing_health_fault_active = False
        self._transport_discovery_warned = False
        self._ws_server: Any = None
        self._running = False
        self._mqtt_task: Optional[asyncio.Task[None]] = None
        self._mqtt_pub_client: Any = None
        self._mqtt_handles: dict[str, MqttClientSession] = {}

    def _emit_mqtt_transport_status(self, code: int) -> None:
        """Notify controller of MQTT transport state (deduped; safe from asyncio thread)."""
        if self._mqtt_drv_last == code:
            return
        self._mqtt_drv_last = code
        fn = self._mqtt_transport_notice
        if fn is None:
            return
        try:
            fn(code)
        except Exception:
            self.log.exception("MQTT transport status callback failed code=%s", code)

    def _emit_hub_rpc_error_notice(
        self,
        for_what: str,
        message: str,
        *,
        device_id: str = "",
        mqtt_client_slug: str = "",
        characteristic: Any = None,
    ) -> None:
        """PG3-visible notice for hub → client RPC failures (command / get / …)."""
        fn = self._hub_rpc_error_notice
        if fn is None:
            return
        ctx: dict[str, Any] = {
            "device_id": device_id,
            "mqtt_client_slug": mqtt_client_slug,
            "characteristic": characteristic,
        }
        try:
            fn(for_what, message, ctx)
        except Exception:
            self.log.exception("hub_rpc_error_notice callback failed for=%s", for_what)

    async def _abort_start(self) -> None:
        """Undo partial startup (used when async_start or later steps fail)."""
        self._running = False
        await self._stop_pairing_probe_worker()
        await self._stop_hap_event_worker()
        await self._detach_all_ws_clients()
        await self._stop_mqtt_transport()
        if self._ws_server is not None:
            try:
                self._ws_server.close()
                await self._ws_server.wait_closed()
            except Exception:
                self.log.exception("closing WebSocket server after failed start")
            self._ws_server = None
        if self._hk is not None:
            try:
                await self._hk.async_stop()
            except Exception:
                self.log.exception("HomeKit controller async_stop after failed start")
            self._hk = None
        if self._zcm is not None:
            try:
                await self._zcm.stop()
            except Exception:
                self.log.exception("ZeroconfManager.stop after failed start")
            self._zcm = None
            self._async_zeroconf = None

    async def _stop_hap_event_worker(self) -> None:
        """Signal the HAP broadcast worker to exit and wait for it."""
        t = self._hap_evt_worker
        self._hap_evt_worker = None
        q = self._hap_evt_queue
        if q is not None:
            try:
                q.put_nowait(None)
            except Exception:
                pass
        self._hap_evt_queue = None
        if t is not None:
            try:
                await t
            except asyncio.CancelledError:
                pass
            except Exception:
                self.log.exception("HAP event broadcast worker stop")

    async def _stop_pairing_probe_worker(self) -> None:
        t = self._pairing_probe_task
        self._pairing_probe_task = None
        if t is not None:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
            except Exception:
                self.log.exception("pairing health probe worker stop")
        self._emit_pairing_health_state(False)
        self._pairing_unhealthy_aliases.clear()

    def _dispatch_pairing_health_notice(
        self,
        *,
        fault_active: bool,
        recovered_lan: dict[str, str],
        unhealthy_aliases_for_nodes: list[str],
        fault_transition: bool,
    ) -> None:
        """Notify controller: optional fault transition + optional recovered LAN endpoints per alias."""
        if fault_transition:
            self._pairing_health_fault_active = fault_active
        if not self._pairing_health_notice:
            return
        if not fault_transition and not recovered_lan:
            return
        if fault_active:
            aliases_txt = ", ".join(unhealthy_aliases_for_nodes)
            detail = (
                f"Unhealthy pairing aliases: {aliases_txt}"
                if aliases_txt
                else "Pairing health probe failure"
            )
        else:
            detail = "All pairing probes recovered"
        try:
            self._pairing_health_notice(
                fault_active,
                detail,
                unhealthy_aliases_for_nodes,
                recovered_lan,
                fault_transition,
            )
        except Exception:
            self.log.exception("pairing health notice callback")

    async def _hap_event_broadcast_worker(self) -> None:
        q = self._hap_evt_queue
        if q is None:
            return
        try:
            while True:
                msg = await q.get()
                if msg is None:
                    break
                await self._broadcast_hap_event(msg)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.log.exception("HAP event broadcast worker")

    async def _broadcast_hap_event(self, msg: dict[str, Any]) -> None:
        """Fan-out HAP ``event`` frames; respect optional per-WebSocket characteristic filters."""
        if msg.get("action") != "event":
            await self._broadcast(msg)
            return
        did = (msg.get("device_id") or "").strip().lower()
        try:
            aid_i = int(msg.get("aid"))
            iid_i = int(msg.get("iid"))
        except (TypeError, ValueError):
            return
        line = json.dumps(msg, default=str)
        for ws in list(self._client_out.keys()):
            filt = self._ws_event_filters.get(ws)
            if not filt:
                self._enqueue_ws_line(ws, line)
                continue
            if (did, aid_i, iid_i) in filt:
                self._enqueue_ws_line(ws, line)
        for sess in list(self._mqtt_handles.values()):
            filt_m = self._ws_event_filters.get(sess)
            if not filt_m:
                await self._mqtt_publish_line(sess.slug, "event", line)
                continue
            if (did, aid_i, iid_i) in filt_m:
                await self._mqtt_publish_line(sess.slug, "event", line)

    def _enqueue_hap_broadcast(self, msg: dict[str, Any]) -> None:
        q = self._hap_evt_queue
        if q is None:
            return
        dropped = False
        while True:
            try:
                q.put_nowait(msg)
                if dropped:
                    self.log.warning(
                        "HAP event hub queue full (%d); dropped oldest pending broadcast",
                        HAP_EVENT_BROADCAST_QUEUE_MAX,
                    )
                return
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    dropped = True
                except asyncio.QueueEmpty:
                    pass

    async def _ws_outbound_worker(self, ws: Any, queue: asyncio.Queue[Optional[str]]) -> None:
        try:
            while True:
                line = await queue.get()
                if line is None:
                    break
                await ws.send(line)
        except websockets.ConnectionClosed:
            pass
        except Exception:
            self.log.exception("WebSocket outbound sender error")

    def _enqueue_ws_line(self, ws: Any, line: str) -> None:
        tup = self._client_out.get(ws)
        if not tup:
            return
        q, _ = tup
        dropped = False
        while True:
            try:
                q.put_nowait(line)
                if dropped:
                    self.log.warning(
                        "WebSocket client %s outbound queue full (%d); dropped oldest message",
                        getattr(ws, "remote_address", None),
                        WS_CLIENT_OUTBOUND_QUEUE_MAX,
                    )
                return
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    dropped = True
                except asyncio.QueueEmpty:
                    pass

    async def _detach_ws_client(self, ws: Any) -> None:
        self._ws_hello_authed.discard(ws)
        self._ws_event_filters.pop(ws, None)
        tup = self._client_out.pop(ws, None)
        if not tup:
            return
        q, task = tup
        try:
            q.put_nowait(None)
        except Exception:
            pass
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            self.log.exception("WebSocket outbound worker join")

    async def _detach_all_ws_clients(self) -> None:
        for ws in list(self._client_out.keys()):
            await self._detach_ws_client(ws)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        try:
            # aiohomekit IP transport requires a real AsyncZeroconf; default HKController()
            # passes None and IpController.async_start() crashes (no .zeroconf).
            self._zcm = ZeroconfManager(self.log)
            self._async_zeroconf = await self._zcm.start(self._get_params())
            self._hk = HKController(async_zeroconf_instance=self._async_zeroconf)
            await self._hk.async_start()
            # Load pairings before accepting WebSocket clients; otherwise an immediate
            # ``list_devices`` runs while ``self._hk.pairings`` is still empty (events
            # appear only after ``_sync_pairing_from_params`` finishes).
            await self._sync_pairing_from_params()
            self._pairing_probe_task = asyncio.create_task(self._pairing_health_probe_loop())
            self._hap_evt_queue = asyncio.Queue(maxsize=HAP_EVENT_BROADCAST_QUEUE_MAX)
            self._hap_evt_worker = asyncio.create_task(self._hap_event_broadcast_worker())
            await self._start_websocket_server()
            self._maybe_start_mqtt_transport()
        except Exception:
            await self._abort_start()
            raise

    async def stop(self) -> None:
        self._running = False
        await self._stop_pairing_probe_worker()
        self._clear_all_listeners()
        await self._stop_hap_event_worker()
        await self._stop_mqtt_transport()
        await self._shutdown_all_pairings()
        if self._ws_server is not None:
            self._ws_server.close()
            await self._ws_server.wait_closed()
            self._ws_server = None
        await self._detach_all_ws_clients()
        if self._hk:
            await self._hk.async_stop()
            self._hk = None
        if self._zcm is not None:
            await self._zcm.stop()
            self._zcm = None
            self._async_zeroconf = None

    async def restart_session(self) -> None:
        """Reload all slots from params + customData (after PG3 param change)."""
        if not self._running or not self._hk:
            return
        self._clear_all_listeners()
        self._emit_pairing_health_state(False)
        self._pairing_unhealthy_aliases.clear()
        await self._shutdown_all_pairings()
        await self._sync_pairing_from_params()
        await self._stop_mqtt_transport()
        self._maybe_start_mqtt_transport()

    def _emit_pairing_health_state(self, unhealthy: bool) -> None:
        """Force-clear or sync fault notification (probe worker stop / session restart)."""
        fault_active = bool(unhealthy)
        if fault_active:
            self._dispatch_pairing_health_notice(
                fault_active=True,
                recovered_lan={},
                unhealthy_aliases_for_nodes=sorted(self._pairing_unhealthy_aliases),
                fault_transition=self._pairing_health_fault_active != fault_active,
            )
            return
        self._dispatch_pairing_health_notice(
            fault_active=False,
            recovered_lan={},
            unhealthy_aliases_for_nodes=[],
            fault_transition=self._pairing_health_fault_active,
        )

    async def _pairing_health_probe_loop(self) -> None:
        try:
            await asyncio.sleep(PAIRING_HEALTH_PROBE_START_DELAY_SEC)
            while self._running:
                await self._probe_pairings_health_once()
                await asyncio.sleep(PAIRING_HEALTH_PROBE_INTERVAL_SEC)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.log.exception("pairing health probe loop")

    def _ip_hap_service_name(self, hk: HKController, device_id: str) -> str | None:
        """Return full HAP DNS-SD instance name (``Name._hap._tcp.local.``) or None."""
        ip_ctrl = hk.transports.get(TransportType.IP)
        if not ip_ctrl:
            return None
        discovery = ip_ctrl.discoveries.get(device_id)
        if not discovery or not getattr(discovery, "description", None):
            return None
        return f"{discovery.description.name}.{ip_ctrl.hap_type}"

    async def _bump_ip_pairing_zeroconf(
        self, hk: HKController, alias: str, pairing, *, log_failures: bool
    ) -> bool:
        """Re-query HAP DNS-SD and push ServiceInfo into aiohomekit (no TCP close).

        Returns True if a record was applied. Safe to call repeatedly while the accessory
        is booting or flapping ports.
        """
        pdata = getattr(pairing, "pairing_data", None)
        if not isinstance(pdata, dict) or pdata.get("Connection") != "IP":
            return False
        ip_ctrl = hk.transports.get(TransportType.IP)
        if not ip_ctrl:
            return False
        zc_inst = getattr(ip_ctrl, "_async_zeroconf_instance", None)
        zc = getattr(zc_inst, "zeroconf", None) if zc_inst else None
        if not zc:
            return False
        pid = getattr(pairing, "id", None)
        if not pid:
            return False
        device_id = str(pid).lower()
        service_full = self._ip_hap_service_name(hk, device_id)
        if not service_full:
            try:
                await hk.async_find(device_id, timeout=8.0)
            except Exception:
                if log_failures:
                    self.log.debug(
                        "zeroconf bump: async_find failed for %s (%s)",
                        alias,
                        device_id,
                        exc_info=True,
                    )
                return False
            service_full = self._ip_hap_service_name(hk, device_id)
        if not service_full:
            return False
        try:
            info = AsyncServiceInfo(ip_ctrl.hap_type, service_full)
            await info.async_request(zc, PAIRING_HEALTH_ZEROCONF_REQUEST_MS)
            ip_ctrl._async_handle_loaded_service_info(info)
            return True
        except Exception:
            if log_failures:
                self.log.debug(
                    "zeroconf bump failed for %s (%s)", alias, service_full, exc_info=True
                )
            return False

    async def _resync_ip_pairing_zeroconf(self, alias: str, pairing) -> None:
        """Close stale TCP session and re-resolve HAP DNS-SD so IP/port match the accessory.

        After power loss or reboot, accessories often advertise a new port while aiohomekit
        may still target the previous endpoint until fresh ServiceInfo is processed.
        """
        hk = self._hk
        if not hk or pairing is None:
            return
        pdata = getattr(pairing, "pairing_data", None)
        if not isinstance(pdata, dict) or pdata.get("Connection") != "IP":
            return

        try:
            await pairing.close()
        except Exception:
            self.log.debug(
                "pairing close before zeroconf resync failed for %s", alias, exc_info=True
            )

        await asyncio.sleep(0.15)
        if await self._bump_ip_pairing_zeroconf(hk, alias, pairing, log_failures=True):
            await asyncio.sleep(0.2)
            await self._bump_ip_pairing_zeroconf(hk, alias, pairing, log_failures=False)

    async def _reload_saved_pairing_for_alias(self, alias: str) -> bool:
        """Replace in-memory pairing with a fresh ``IpPairing`` from saved blob.

        Zeroconf bumps alone cannot reset a wedged ``SecureHomeKitConnection`` / connector
        task graph; reloading clears stale asyncio state while preserving keys.
        """
        hk = self._hk
        if not hk:
            return False
        slot_num = slot_num_from_alias(alias)
        if slot_num is None:
            return False
        blob = self._get_pairings_blob()
        saved = blob.get(str(slot_num))
        if not isinstance(saved, dict) or not saved.get("AccessoryPairingID"):
            return False
        self.log.info(
            "pairing health: reloading %s from saved pairing blob (fresh session)",
            alias,
        )
        await self._close_alias_if_present(alias)
        await asyncio.sleep(0.45)
        try:
            hk.load_pairing(alias, dict(saved))
        except Exception:
            self.log.exception("pairing health: load_pairing failed for %s", alias)
            return False
        pairing = hk.aliases.get(alias)
        if not pairing:
            return False
        await self._bump_ip_pairing_zeroconf(hk, alias, pairing, log_failures=True)
        await asyncio.sleep(0.35)
        await self._bump_ip_pairing_zeroconf(hk, alias, pairing, log_failures=False)
        return True

    async def _probe_pairings_health_once(self) -> None:
        hk = self._hk
        if not self._running or not hk:
            return
        aliases = getattr(hk, "aliases", None)
        if not isinstance(aliases, dict) or not aliases:
            return
        recovered_lan: dict[str, str] = {}
        for alias, pairing in list(aliases.items()):
            if pairing is None:
                continue
            try:
                await pairing.list_accessories_and_characteristics()
            except Exception:
                await self._resync_ip_pairing_zeroconf(alias, pairing)
                await asyncio.sleep(PAIRING_HEALTH_POST_RESYNC_INITIAL_SETTLE_SEC)
                probe_ok = False
                last_exc: BaseException | None = None
                for attempt in range(PAIRING_HEALTH_POST_RESYNC_RETRIES):
                    if attempt:
                        await asyncio.sleep(PAIRING_HEALTH_POST_RESYNC_DELAY_SEC)
                        pdata = getattr(pairing, "pairing_data", None)
                        if (
                            isinstance(pdata, dict)
                            and pdata.get("Connection") == "IP"
                        ):
                            await self._bump_ip_pairing_zeroconf(
                                hk, alias, pairing, log_failures=False
                            )
                            await asyncio.sleep(0.2)
                    pairing = hk.aliases.get(alias)
                    if pairing is None:
                        last_exc = RuntimeError(f"pairing missing for {alias!r} after resync")
                        break
                    try:
                        await pairing.list_accessories_and_characteristics()
                        probe_ok = True
                        break
                    except Exception as ex:
                        last_exc = ex
                        await asyncio.sleep(PAIRING_HEALTH_RETRY_LIST_SLEEP_SEC)
                if not probe_ok and await self._reload_saved_pairing_for_alias(alias):
                    pairing = hk.aliases.get(alias)
                    await asyncio.sleep(PAIRING_HEALTH_RELOAD_SETTLE_SEC)
                    for reopen_try in range(PAIRING_HEALTH_RELOAD_LIST_TRIES):
                        if reopen_try:
                            await asyncio.sleep(PAIRING_HEALTH_POST_RESYNC_DELAY_SEC)
                            if pairing and isinstance(
                                getattr(pairing, "pairing_data", None), dict
                            ):
                                if pairing.pairing_data.get("Connection") == "IP":
                                    await self._bump_ip_pairing_zeroconf(
                                        hk, alias, pairing, log_failures=False
                                    )
                                    await asyncio.sleep(0.2)
                        pairing = hk.aliases.get(alias)
                        if pairing is None:
                            break
                        try:
                            await pairing.list_accessories_and_characteristics()
                            probe_ok = True
                            last_exc = None
                            break
                        except Exception as ex:
                            last_exc = ex
                if not probe_ok:
                    self._pairing_unhealthy_aliases.add(alias)
                    if last_exc is not None:
                        self.log.warning(
                            "pairing health probe failed for %s; will retry and recover when reachable",
                            alias,
                            exc_info=(type(last_exc), last_exc, last_exc.__traceback__),
                        )
                    else:
                        self.log.warning(
                            "pairing health probe failed for %s; will retry and recover when reachable",
                            alias,
                        )
                    continue

            pairing = hk.aliases.get(alias)
            if pairing is None:
                self.log.warning(
                    "pairing health: no in-memory pairing for %s after probe; skipping listener refresh",
                    alias,
                )
                continue

            if alias not in self._pairing_unhealthy_aliases:
                continue

            self._attach_listener(alias, pairing)
            to_sub = _subscribable_characteristics(pairing)
            if to_sub:
                try:
                    await pairing.subscribe(to_sub)
                except Exception:
                    self.log.exception(
                        "pairing health recovery subscribe failed for %s", alias
                    )
                    continue
            self._pairing_unhealthy_aliases.discard(alias)
            ep = _ip_lan_endpoint_str(pairing)
            if ep:
                recovered_lan[alias] = ep
            self.log.info(
                "pairing health probe recovered %s; listener/subscriptions refreshed",
                alias,
            )
        new_fault = bool(self._pairing_unhealthy_aliases)
        fault_transition = self._pairing_health_fault_active != new_fault
        unhealthy_out = [] if not new_fault else sorted(self._pairing_unhealthy_aliases)
        self._dispatch_pairing_health_notice(
            fault_active=new_fault,
            recovered_lan=recovered_lan,
            unhealthy_aliases_for_nodes=unhealthy_out,
            fault_transition=fault_transition,
        )

    async def full_restart(self) -> None:
        """Full hub recycle (zeroconf + WebSocket + pairings)."""
        await self.stop()
        await self.start()

    def zeroconf_diag(self) -> dict[str, Any]:
        """Snapshot for support: mode, browsers, transports, 5353 probe, versions."""
        zcm = self._zcm
        transports: dict[str, int] = {}
        hk = self._hk
        if hk and getattr(hk, "transports", None):
            try:
                for name, transport in hk.transports.items():
                    d = getattr(transport, "discoveries", None) or {}
                    transports[str(name)] = len(d)
            except (AttributeError, TypeError):
                pass
        return {
            "hub_running": self._running,
            "zeroconf_mode": getattr(zcm, "mode_label", "") if zcm else "",
            "using_unicast": bool(zcm.using_unicast) if zcm else False,
            "hap_browser_count": len(zcm.hap_browsers) if zcm else 0,
            "hap_browser_types": [HAP_TYPE_TCP, HAP_TYPE_UDP],
            "mdns_5353_probe": probe_mdns_port_5353(),
            "transports_discovery_counts": transports,
            "platform": sys.platform,
            "python_version": sys.version.split()[0],
            "zeroconf_version": _package_version("zeroconf"),
            "aiohomekit_version": _package_version("aiohomekit"),
        }

    def _clear_all_listeners(self) -> None:
        for stop in self._listeners.values():
            try:
                stop()
            except Exception:
                pass
        self._listeners.clear()

    async def _shutdown_all_pairings(self) -> None:
        if not self._hk:
            return
        for alias in list(self._hk.aliases.keys()):
            pairing = self._hk.aliases.pop(alias, None)
            if pairing:
                pid = pairing.id
                pid_key = str(pid).strip().lower() if pid else ""
                self._hk.pairings.pop(pid_key, None)
                try:
                    await pairing.close()
                except Exception:
                    self.log.exception("pairing close for %s", alias)

    def _iter_transport_discoveries(self):
        """Yield discovery objects from all aiohomekit transports (IP, COAP, BLE).

        Soft-fails on unexpected ``aiohomekit`` internal shapes (library version skew);
        logs a one-time warning. Prefer this over direct ``Controller.transports`` access.
        """
        if not self._hk:
            return
        try:
            transports = getattr(self._hk, "transports", None)
            if not transports:
                return
            for transport in transports.values():
                discoveries = getattr(transport, "discoveries", None) or {}
                yield from discoveries.values()
        except (AttributeError, TypeError) as ex:
            if not self._transport_discovery_warned:
                self._transport_discovery_warned = True
                self.log.warning(
                    "aiohomekit transport discovery iteration failed (%s); use aiohomekit "
                    "in the supported range (see requirements.txt / module docstring).",
                    ex,
                )

    def _row_from_discovery(self, discovery: Any) -> dict[str, Any] | None:
        d = discovery.description
        if not d or not getattr(d, "id", None):
            return None
        addrs = getattr(d, "addresses", None)
        if isinstance(addrs, (list, tuple)) and addrs:
            host = str(addrs[0])
        else:
            host = str(getattr(d, "address", "") or "")
        try:
            port = int(d.port)
        except (TypeError, ValueError):
            port = 0
        return {
            "id": d.id,
            "name": d.name or "",
            "paired": bool(discovery.paired),
            "host": host,
            "port": port,
        }

    async def discover_collect(self, timeout: float = 12.0) -> list[dict[str, Any]]:
        """
        Collect HAP accessories seen via mDNS over a real-time window.

        aiohomekit's ``async_discover()`` only iterates the *current* ``.discoveries``
        cache once (it is not a long listen). Devices appear as zeroconf callbacks
        fill ``transport.discoveries`` — we must poll for ``timeout`` seconds.
        """
        if not self._hk:
            return []
        if not getattr(self._hk, "transports", None):
            self.log.warning(
                "aiohomekit Controller has no transports; discovery may be incomplete"
            )
        self.log.info(
            "HomeKit discovery (%.1fs window, mDNS _hap._tcp; devices appear as announced)...",
            timeout,
        )
        seen_ids: set[str] = set()
        rows: list[dict[str, Any]] = []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + float(timeout)
        interval = 0.5

        while loop.time() < deadline:
            for discovery in self._iter_transport_discoveries():
                row = self._row_from_discovery(discovery)
                if not row:
                    continue
                did = row["id"]
                if did in seen_ids:
                    continue
                seen_ids.add(did)
                rows.append(row)
                self.log.info(
                    "HAP accessory: name=%r id=%s paired=%s %s:%s",
                    row["name"],
                    did,
                    row["paired"],
                    row["host"],
                    row["port"],
                )
            await asyncio.sleep(interval)

        self.log.info(
            "Discovery window ended: %d unique HAP accessory(ies) in this window",
            len(rows),
        )
        if not rows:
            self.log.warning(
                "No HAP accessories seen — confirm pairing mode, same LAN/VLAN, mDNS not blocked, "
                "and (BSD/macOS) see CONFIG.md zeroconf env if using unicast."
            )
        return rows

    async def _wait_for_pairing_discovery(
        self,
        accessory_id: str,
        accessory_name: str,
        timeout: float = 30.0,
    ) -> Any:
        """
        Resolve a discovery suitable for SRP pairing: unpaired, optional id/name match.
        Uses async_find() when id is known; otherwise polls transport.discoveries.
        """
        if not self._hk:
            return None
        aid = (accessory_id or "").strip().lower()
        aname = (accessory_name or "").strip()

        if aid:
            try:
                discovery = await self._hk.async_find(aid, timeout=timeout)
            except AccessoryNotFoundError:
                self.log.error(
                    "Accessory id %s not found on the network within %.0fs (mDNS / HAP)",
                    aid,
                    timeout,
                )
                return None
            except Exception:
                self.log.exception("async_find failed for id %s", aid)
                return None
            d = discovery.description
            if aname and aname.lower() not in (d.name or "").lower():
                self.log.error(
                    "Accessory name did not match filter (id=%s name=%r)",
                    aid,
                    d.name,
                )
                return None
            if discovery.paired:
                self.log.warning(
                    "Skipping %s (%s): already paired; unpair in HomeKit first",
                    d.name,
                    d.id,
                )
                return None
            return discovery

        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while loop.time() < end:
            for discovery in self._iter_transport_discoveries():
                d = discovery.description
                if not d:
                    continue
                if aname and aname.lower() not in (d.name or "").lower():
                    continue
                if discovery.paired:
                    self.log.warning(
                        "Skipping %s (%s): already paired; unpair in HomeKit first",
                        d.name,
                        d.id,
                    )
                    continue
                return discovery
            await asyncio.sleep(0.5)
        return None

    def _ws_bind(self) -> tuple[str, int]:
        p = self._get_params()
        host = (p.get("ws_host") or "127.0.0.1").strip()
        try:
            port = int(p.get("ws_port") or 8163)
        except (TypeError, ValueError):
            port = 8163
        return host, port

    def _ws_expected_token(self) -> str:
        """Shared secret for WebSocket ``hello`` when Custom Param ``ws_token`` is non-empty."""
        p = self._get_params()
        raw = p.get("ws_token")
        if raw is None:
            return ""
        return str(raw).strip()

    @staticmethod
    def _ws_merge_request_id(request: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        """Echo client ``id`` on RPC replies (``command`` / ``snapshot`` / ``get``) for multiplexed in-flight calls."""
        rid = request.get("id")
        if rid is None or rid == "":
            return payload
        merged = dict(payload)
        merged["id"] = rid
        return merged

    def _ws_capabilities(self) -> dict[str, Any]:
        need_token = bool(self._ws_expected_token())
        cap: dict[str, Any] = {
            "actions": list(WS_PROTOCOL_ACTIONS),
            "auth": "token" if need_token else "none",
            "rpc": {
                "multiplex": True,
                "id_echo": (
                    "Optional client `id` (string or number, echoed as sent) on `command`, "
                    "`snapshot`, and `get` is included on every matching success or `error` reply."
                ),
            },
            "events": {
                "mode": "filtered_after_subscribe",
                "description": (
                    "By default all HAP events are forwarded. After at least one successful "
                    "`subscribe`, only matching (device_id, aid, iid) events are sent until "
                    "the subscription set becomes empty (then defaults are restored)."
                ),
            },
        }
        p = self._get_params()
        if mqtt_transport_enabled(p):
            hub = normalize_hub_slug_param(p.get("mqtt_hub_slug"))
            cap["mqtt"] = {
                "enabled": True,
                "hub_slug": hub,
                "ingress_publish_template": f"udi/homekit/hubs/{hub}/clients/{{client_slug}}/in",
                "egress_rpc_template": f"udi/homekit/hubs/{hub}/clients/{{client_slug}}/out/rpc",
                "egress_event_template": f"udi/homekit/hubs/{hub}/clients/{{client_slug}}/out/event",
            }
        return cap

    async def _stop_mqtt_transport(self) -> None:
        self._mqtt_pub_client = None
        t = self._mqtt_task
        self._mqtt_task = None
        if t is not None:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
            except Exception:
                self.log.exception("MQTT transport task stop")
        for sess in list(self._mqtt_handles.values()):
            self._ws_hello_authed.discard(sess)
            self._ws_event_filters.pop(sess, None)
        self._mqtt_handles.clear()
        code = (
            MQTT_TRANSPORT_STATUS_DISABLED
            if not mqtt_transport_enabled(self._get_params())
            else MQTT_TRANSPORT_STATUS_NOT_CONNECTED
        )
        self._emit_mqtt_transport_status(code)

    def _maybe_start_mqtt_transport(self) -> None:
        if not mqtt_transport_enabled(self._get_params()):
            self._emit_mqtt_transport_status(MQTT_TRANSPORT_STATUS_DISABLED)
            return
        if self._mqtt_task is not None and not self._mqtt_task.done():
            return
        self._mqtt_task = asyncio.create_task(self._mqtt_run_forever(), name="homekit-hub-mqtt")

    def _mqtt_broker_params(self) -> tuple[str, int, str, str]:
        p = self._get_params()
        host = str(p.get("mqtt_host") or DEFAULT_MQTT_BROKER_HOST).strip() or DEFAULT_MQTT_BROKER_HOST
        try:
            port = int(p.get("mqtt_port") or DEFAULT_MQTT_BROKER_PORT)
        except (TypeError, ValueError):
            port = DEFAULT_MQTT_BROKER_PORT
        if port < 1 or port > 65535:
            port = DEFAULT_MQTT_BROKER_PORT
        user = str(p.get("mqtt_username") or "").strip()
        password = str(p.get("mqtt_password") or "")
        return host, port, user, password

    async def _mqtt_publish_json(self, slug: str, channel: str, obj: dict[str, Any]) -> None:
        line = json.dumps(obj, default=str)
        await self._mqtt_publish_line(slug, channel, line)

    async def _mqtt_publish_line(self, slug: str, channel: str, line: str) -> None:
        c = self._mqtt_pub_client
        if c is None:
            return
        hub = normalize_hub_slug_param(self._get_params().get("mqtt_hub_slug"))
        if channel == "event":
            topic = client_out_event_topic(hub, slug)
        else:
            topic = client_out_rpc_topic(hub, slug)
        try:
            await c.publish(
                topic,
                payload=line.encode("utf-8"),
                qos=MQTT_QOS_AT_LEAST_ONCE,
                retain=False,
            )
        except Exception:
            self.log.exception("MQTT publish failed topic=%s", topic)

    def _detach_mqtt_session(self, sess: MqttClientSession) -> None:
        self._ws_hello_authed.discard(sess)
        self._ws_event_filters.pop(sess, None)
        self._mqtt_handles.pop(sess.slug, None)

    async def _close_client_session(self, client: Any) -> None:
        if isinstance(client, MqttClientSession):
            self._detach_mqtt_session(client)
            return
        try:
            await client.close()
        except Exception:
            pass

    async def _mqtt_run_forever(self) -> None:
        attempt = 0
        while self._running:
            params = self._get_params()
            if not mqtt_transport_enabled(params):
                self._emit_mqtt_transport_status(MQTT_TRANSPORT_STATUS_DISABLED)
                return
            host, port, user, pw = self._mqtt_broker_params()
            hub = normalize_hub_slug_param(params.get("mqtt_hub_slug"))
            pattern = clients_ingress_subscribe_pattern(hub)
            client_kw: dict[str, Any] = {"hostname": host, "port": port}
            if user:
                client_kw["username"] = user
                client_kw["password"] = pw
            if attempt == 0:
                self.log.info(
                    "MQTT connecting host=%s port=%s hub_slug=%s subscribe_pattern=%s",
                    host,
                    port,
                    hub,
                    pattern,
                )
            self._emit_mqtt_transport_status(MQTT_TRANSPORT_STATUS_NOT_CONNECTED)
            try:
                async with aiomqtt.Client(**client_kw) as client:
                    self._mqtt_pub_client = client
                    await client.subscribe(pattern, qos=MQTT_QOS_AT_LEAST_ONCE)
                    self.log.info(
                        "MQTT connected host=%s port=%s subscribe_pattern=%s",
                        host,
                        port,
                        pattern,
                    )
                    self._emit_mqtt_transport_status(MQTT_TRANSPORT_STATUS_CONNECTED)
                    attempt = 0
                    async for message in client.messages:
                        if not self._running:
                            break
                        if not mqtt_transport_enabled(self._get_params()):
                            break
                        topic_s = str(message.topic)
                        await self._handle_mqtt_inbound(topic_s, message.payload)
            except asyncio.CancelledError:
                self._mqtt_pub_client = None
                raise
            except Exception as e:
                self._mqtt_pub_client = None
                if not self._running:
                    break
                self.log.warning(
                    "MQTT client error host=%s port=%s: %s",
                    host,
                    port,
                    e,
                    exc_info=attempt == 0,
                )
                delays = (1.0, 2.0, 5.0, 10.0, 30.0)
                delay = delays[min(attempt, len(delays) - 1)]
                attempt += 1
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    break
            finally:
                self._mqtt_pub_client = None

    async def _handle_mqtt_inbound(self, topic: str, payload: bytes) -> None:
        params = self._get_params()
        hub = normalize_hub_slug_param(params.get("mqtt_hub_slug"))
        slug = parse_ingress_client_slug(topic, hub)
        if not slug:
            self.log.debug("MQTT ignored topic=%r", topic)
            return
        sess = self._mqtt_handles.setdefault(slug, MqttClientSession(slug=slug))
        try:
            raw_pl: bytes | str
            if isinstance(payload, (bytes, bytearray)):
                raw_pl = bytes(payload)
            else:
                raw_pl = str(payload)
            text = raw_pl.decode("utf-8") if isinstance(raw_pl, (bytes, bytearray)) else raw_pl
        except UnicodeDecodeError:
            await self._mqtt_publish_json(
                slug,
                "rpc",
                {
                    "version": PROTOCOL_VERSION,
                    "action": "error",
                    "message": "invalid payload encoding",
                },
            )
            return
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            await self._mqtt_publish_json(
                sess.slug,
                "rpc",
                {
                    "version": PROTOCOL_VERSION,
                    "action": "error",
                    "message": "invalid json",
                },
            )
            return
        if not isinstance(msg, dict):
            await self._mqtt_publish_json(
                sess.slug,
                "rpc",
                {
                    "version": PROTOCOL_VERSION,
                    "action": "error",
                    "message": "json root must be object",
                },
            )
            return
        if msg.get("action") == "hello":
            raw_client = msg.get("client")
            if raw_client is not None and str(raw_client).strip() != "":
                got = sanitize_client_slug(str(raw_client))
                if got != slug:
                    await self._mqtt_publish_json(
                        sess.slug,
                        "rpc",
                        {
                            "version": PROTOCOL_VERSION,
                            "action": "error",
                            "for": "hello",
                            "message": "hello.client (sanitized) must match MQTT topic client_slug",
                        },
                    )
                    return
        try:
            await self._dispatch_client_message(sess, msg)
        except Exception as ex:
            self.log.exception("MQTT inbound dispatch failed slug=%s", slug)
            act = msg.get("action")
            err: dict[str, Any] = {
                "version": PROTOCOL_VERSION,
                "action": "error",
                "message": str(ex),
            }
            if isinstance(act, str) and act:
                err["for"] = act
            await self._mqtt_publish_json(
                sess.slug,
                "rpc",
                self._ws_merge_request_id(msg, err),
            )
            self._emit_hub_rpc_error_notice(
                str(act or "dispatch"),
                str(ex),
                mqtt_client_slug=slug,
                device_id=str(msg.get("device_id") or ""),
                characteristic=msg.get("characteristic"),
            )

    async def _start_websocket_server(self) -> None:
        host, port = self._ws_bind()
        self.log.info("WebSocket server listening on %s:%s", host, port)
        self._ws_server = await websockets.serve(
            self._ws_connection,
            host,
            port,
            ping_interval=45,
            ping_timeout=90,
        )

    async def _ws_connection(self, ws: Any) -> None:
        q: asyncio.Queue[Optional[str]] = asyncio.Queue(maxsize=WS_CLIENT_OUTBOUND_QUEUE_MAX)
        task = asyncio.create_task(self._ws_outbound_worker(ws, q))
        self._client_out[ws] = (q, task)
        self.log.debug("WS client connected from %s", getattr(ws, "remote_address", None))
        try:
            async for raw in ws:
                await self._handle_ws_message(ws, raw)
        except websockets.ConnectionClosed:
            pass
        except Exception:
            self.log.exception("WebSocket handler error")
        finally:
            await self._detach_ws_client(ws)

    async def _handle_ws_hello(self, ws: Any, msg: dict[str, Any]) -> bool:
        """Validate optional ``ws_token`` and send hello ``ack`` (``devices[]`` on the ack). False → not authed."""
        expected = self._ws_expected_token()
        if expected and not isinstance(ws, MqttClientSession):
            got_raw = msg.get("token")
            if got_raw is None:
                got_raw = msg.get("ws_token")
            got = "" if got_raw is None else str(got_raw)
            if not hmac.compare_digest(
                got.encode("utf-8"),
                expected.encode("utf-8"),
            ):
                await self._send_ws(
                    ws,
                    {
                        "version": PROTOCOL_VERSION,
                        "action": "error",
                        "for": "hello",
                        "message": "invalid or missing token (use token or ws_token matching Custom Param ws_token)",
                    },
                )
                try:
                    await ws.close()
                except Exception:
                    pass
                return False
        devices_payload, list_warnings = await self._build_list_devices_payload()
        device_ids = [
            x
            for x in (
                str(d.get("device_id") or "").strip().lower() for d in devices_payload
            )
            if x
        ]
        ack_body: dict[str, Any] = {
            "version": PROTOCOL_VERSION,
            "action": "ack",
            "for": "hello",
            "protocol": PROTOCOL_VERSION,
            "device_ids": device_ids,
            "devices": devices_payload,
            "capabilities": self._ws_capabilities(),
            "warnings": list_warnings,
        }
        await self._send_ws(ws, ack_body)
        return True

    async def _handle_ws_message(self, ws: Any, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await self._send_ws(
                ws,
                {
                    "version": PROTOCOL_VERSION,
                    "action": "error",
                    "message": "invalid json",
                },
            )
            return
        if not isinstance(msg, dict):
            await self._send_ws(
                ws,
                {
                    "version": PROTOCOL_VERSION,
                    "action": "error",
                    "message": "json root must be object",
                },
            )
            return
        try:
            await self._dispatch_client_message(ws, msg)
        except Exception as ex:
            self.log.exception("WebSocket dispatch failed")
            act = msg.get("action")
            err: dict[str, Any] = {
                "version": PROTOCOL_VERSION,
                "action": "error",
                "message": str(ex),
            }
            if isinstance(act, str) and act:
                err["for"] = act
            await self._send_ws(ws, self._ws_merge_request_id(msg, err))
            self._emit_hub_rpc_error_notice(
                str(act or "dispatch"),
                str(ex),
                device_id=str(msg.get("device_id") or ""),
                characteristic=msg.get("characteristic"),
            )

    async def _dispatch_client_message(self, client: Any, msg: dict[str, Any]) -> None:
        """Shared JSON dispatch for WebSocket connections and MQTT virtual sessions."""
        ver = msg.get("version")
        if ver != PROTOCOL_VERSION:
            await self._send_ws(
                client,
                {
                    "version": PROTOCOL_VERSION,
                    "action": "error",
                    "message": f"unsupported version {ver!r}, need {PROTOCOL_VERSION}",
                },
            )
            await self._close_client_session(client)
            return
        action = msg.get("action")
        is_mqtt = isinstance(client, MqttClientSession)
        if self._ws_expected_token() and not is_mqtt and client not in self._ws_hello_authed:
            if action != "hello":
                await self._send_ws(
                    client,
                    {
                        "version": PROTOCOL_VERSION,
                        "action": "error",
                        "message": (
                            "ws_token is configured on the hub: send action hello with a matching "
                            "token field first"
                        ),
                    },
                )
                await self._close_client_session(client)
                return
        if action == "hello":
            if await self._handle_ws_hello(client, msg):
                self._ws_hello_authed.add(client)
            return
        if action == "command":
            await self._handle_command(client, msg)
            return
        if action == "snapshot":
            await self._handle_snapshot(client, msg)
            return
        if action == "list_devices":
            await self._handle_list_devices(client, msg)
            return
        if action == "get":
            await self._handle_get(client, msg)
            return
        if action == "subscribe":
            await self._handle_subscribe(client, msg)
            return
        if action == "unsubscribe":
            await self._handle_unsubscribe(client, msg)
            return
        await self._send_ws(
            client,
            {
                "version": PROTOCOL_VERSION,
                "action": "error",
                "message": f"unknown action {action!r}",
            },
        )

    async def _build_list_devices_payload(
        self,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Paired hub rows for ``list_devices`` / hello, with optional Accessory Information metadata.

        Returns ``(devices, warnings)`` where ``warnings`` is a list of structured client notices
        (same array attached to hello ``ack`` and each ``list_devices``; use ``[]`` when healthy).
        """
        device_ids = await self._active_pairing_device_ids_stable()
        out: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        for did in device_ids:
            nd = str(did or "").strip().lower()
            if not nd:
                self.log.error(
                    "list_devices: skipping empty pairing id in active list (raw=%r)",
                    did,
                )
                warnings.append(
                    _ws_client_notice(
                        level=WS_NOTICE_LEVEL_ERROR,
                        code=WS_NOTICE_CODE_LIST_DEVICES_INVALID_DEVICE_ID,
                        message=(
                            "Hub internal error: empty pairing identifier in active pairings list; "
                            "this entry was skipped"
                        ),
                    )
                )
                continue
            pairing = self._pairing_for_device_id(nd)
            if pairing and not pairing.accessories:
                try:
                    await pairing.list_accessories_and_characteristics()
                except Exception:
                    self.log.debug(
                        "list_devices: list_accessories_and_characteristics failed for metadata device_id=%s",
                        nd,
                        exc_info=True,
                    )
                    warnings.append(
                        _ws_client_notice(
                            level=WS_NOTICE_LEVEL_ERROR,
                            code=WS_NOTICE_CODE_ACCESSORIES_LOAD_FAILED,
                            message=(
                                "list_accessories_and_characteristics failed while loading the "
                                "accessory database for this pairing"
                            ),
                            device_id=nd,
                        )
                    )
            row, row_warnings = await _device_list_entry_resolved(nd, pairing, self.log)
            row["device_id"] = nd
            fin = str(row.get("device_id") or "").strip().lower()
            if not fin:
                self.log.error(
                    "list_devices: row missing device_id after resolve (unexpected) raw_id=%r row=%r",
                    did,
                    row,
                )
                warnings.append(
                    _ws_client_notice(
                        level=WS_NOTICE_LEVEL_ERROR,
                        code=WS_NOTICE_CODE_LIST_DEVICES_INVALID_DEVICE_ID,
                        message=(
                            "Hub internal error: list_devices row was built without a device_id; "
                            "this entry was omitted from the payload"
                        ),
                    )
                )
                continue
            out.append(row)
            warnings.extend(row_warnings)
        return out, warnings

    def _active_pairing_device_ids(self) -> list[str]:
        """Sorted AccessoryPairingID values for aiohomekit pairings in memory.

        Union of ``Controller.pairings`` keys and ``pairing.id`` from ``aliases`` so
        ``list_devices`` stays consistent with event ``device_id`` even if one map is
        briefly empty during startup or transport quirks.
        """
        hk = self._hk
        if not hk:
            return []
        ids: set[str] = set()
        pr = getattr(hk, "pairings", None)
        if isinstance(pr, dict):
            for k in pr.keys():
                s = str(k).strip().lower()
                if s:
                    ids.add(s)
            # Values carry Pairing objects; ``id`` may match event ``device_id`` even if
            # keys are alias-shaped or lag behind during IP session setup.
            for p in pr.values():
                if p is None:
                    continue
                pid = getattr(p, "id", None)
                if pid is None:
                    continue
                s = str(pid).strip().lower()
                if s:
                    ids.add(s)
        al = getattr(hk, "aliases", None)
        if isinstance(al, dict):
            for pairing in al.values():
                if pairing is None:
                    continue
                pid = getattr(pairing, "id", None)
                if pid is None:
                    continue
                s = str(pid).strip().lower()
                if s:
                    ids.add(s)
        # Active HAP listeners imply a live pairing; include ids in case maps are momentarily inconsistent.
        if isinstance(al, dict):
            for alias in list(self._listeners.keys()):
                pairing = al.get(alias)
                if pairing is None:
                    continue
                pid = getattr(pairing, "id", None)
                if pid is None:
                    continue
                s = str(pid).strip().lower()
                if s:
                    ids.add(s)
        return sorted(ids)

    def _pairing_for_device_id(self, device_id: str):
        if not self._hk:
            return None
        did = str(device_id or "").strip().lower()
        if not did:
            return None
        p = self._hk.pairings.get(did)
        if p is not None:
            return p
        al = getattr(self._hk, "aliases", None)
        if isinstance(al, dict):
            for pairing in al.values():
                if pairing is None:
                    continue
                pid = getattr(pairing, "id", None)
                if pid is None:
                    continue
                if str(pid).strip().lower() == did:
                    return pairing
        return None

    async def _active_pairing_device_ids_stable(self) -> list[str]:
        """Retry briefly before returning an empty paired-device list."""
        ids = self._active_pairing_device_ids()
        if ids:
            return ids
        if not self._hk:
            return []
        for delay_s in (0.25, 0.35, 0.45, 1.0, 1.5, 2.0):
            await asyncio.sleep(delay_s)
            ids = self._active_pairing_device_ids()
            if ids:
                self.log.debug(
                    "stable list_devices retry recovered ids after %.2fs: %s",
                    delay_s,
                    ids,
                )
                return ids
        return []

    def _resolve_ws_aid_iid(
        self, pairing, msg: dict[str, Any]
    ) -> tuple[Optional[tuple[int, int]], Optional[str]]:
        """Subscribe/unsubscribe body: ``aid``+``iid`` or ``characteristic`` name/UUID.

        Returns ``(pair, None)`` on success, ``(None, None)`` when the message omits selectors,
        or ``(None, err)`` when a characteristic token cannot be resolved.
        """
        aid = msg.get("aid")
        iid = msg.get("iid")
        if aid is not None and iid is not None:
            try:
                return (int(aid), int(iid)), None
            except (TypeError, ValueError):
                return None, None
        char_spec = msg.get("characteristic")
        if isinstance(char_spec, str) and char_spec.strip():
            return _resolve_aid_iid_detailed(pairing, char_spec.strip())
        return None, None

    async def _handle_get(self, ws: Any, msg: dict[str, Any]) -> None:
        """Read a subset of characteristics (same ``values`` shape as ``snapshot``)."""
        device_id = (msg.get("device_id") or "").strip().lower()
        pairing = self._pairing_for_device_id(device_id) if device_id else None
        if not pairing:
            m = "unknown device_id or no active pairing"
            await self._send_ws(
                ws,
                self._ws_merge_request_id(
                    msg,
                    {
                        "version": PROTOCOL_VERSION,
                        "action": "error",
                        "for": "get",
                        "message": m,
                    },
                ),
            )
            self._emit_hub_rpc_error_notice("get", m, device_id=device_id)
            return
        specs: list[str] = []
        ch_list = msg.get("characteristics")
        if isinstance(ch_list, list):
            for x in ch_list:
                if isinstance(x, str) and x.strip():
                    specs.append(x.strip())
        one = msg.get("characteristic")
        if isinstance(one, str) and one.strip():
            specs.append(one.strip())
        seen: set[str] = set()
        uniq: list[str] = []
        for s in specs:
            key = s.casefold()
            if key in seen:
                continue
            seen.add(key)
            uniq.append(s)
        specs = uniq
        if not specs:
            m = "provide characteristic (string) or characteristics (string array)"
            await self._send_ws(
                ws,
                self._ws_merge_request_id(
                    msg,
                    {
                        "version": PROTOCOL_VERSION,
                        "action": "error",
                        "for": "get",
                        "message": m,
                    },
                ),
            )
            self._emit_hub_rpc_error_notice("get", m, device_id=device_id)
            return

        pairs: list[tuple[int, int]] = []
        labels: dict[tuple[int, int], str] = {}

        def try_build() -> Optional[tuple[str, str]]:
            pairs.clear()
            labels.clear()
            for spec in specs:
                resolved, err = _resolve_aid_iid_detailed(pairing, spec)
                if err is not None:
                    return spec, err
                assert resolved is not None
                aid, iid = resolved
                if (aid, iid) in labels:
                    continue
                pairs.append((aid, iid))
                try:
                    ch = pairing.accessories.aid(aid).characteristics.iid(iid)
                    labels[(aid, iid)] = characteristic_label(ch.type)
                except Exception:
                    labels[(aid, iid)] = spec
            return None

        fail = try_build()
        if fail is not None:
            _bad_spec, bad_err = fail
            if bad_err.startswith("unknown characteristic"):
                try:
                    await pairing.list_accessories_and_characteristics()
                except Exception as ex:
                    self.log.exception("get: list_accessories_and_characteristics failed")
                    await self._send_ws(
                        ws,
                        self._ws_merge_request_id(
                            msg,
                            {
                                "version": PROTOCOL_VERSION,
                                "action": "error",
                                "for": "get",
                                "message": str(ex),
                            },
                        ),
                    )
                    self._emit_hub_rpc_error_notice(
                        "get",
                        str(ex),
                        device_id=device_id,
                    )
                    return
                fail = try_build()
        if fail is not None:
            bad_spec, bad_err = fail
            await self._send_ws(
                ws,
                self._ws_merge_request_id(
                    msg,
                    {
                        "version": PROTOCOL_VERSION,
                        "action": "error",
                        "for": "get",
                        "message": bad_err,
                    },
                ),
            )
            self._emit_hub_rpc_error_notice(
                "get",
                bad_err,
                device_id=device_id,
                characteristic=bad_spec,
            )
            return

        try:
            result = await pairing.get_characteristics(pairs)
        except Exception as ex:
            self.log.exception("get: get_characteristics failed")
            m = str(ex)
            await self._send_ws(
                ws,
                self._ws_merge_request_id(
                    msg,
                    {
                        "version": PROTOCOL_VERSION,
                        "action": "error",
                        "for": "get",
                        "message": m,
                    },
                ),
            )
            self._emit_hub_rpc_error_notice("get", m, device_id=device_id)
            return

        values: list[dict[str, Any]] = []
        for aid, iid in pairs:
            payload = result.get((aid, iid), {})
            if not isinstance(payload, dict):
                payload = {}
            item: dict[str, Any] = {
                "aid": aid,
                "iid": iid,
                "characteristic": labels.get((aid, iid), f"{aid}.{iid}"),
            }
            if "value" in payload:
                item["value"] = payload.get("value")
            if "status" in payload:
                item["status"] = payload.get("status")
            values.append(item)

        await self._send_ws(
            ws,
            self._ws_merge_request_id(
                msg,
                {
                    "version": PROTOCOL_VERSION,
                    "action": "get",
                    "device_id": device_id,
                    "values": values,
                },
            ),
        )

    async def _handle_subscribe(self, ws: Any, msg: dict[str, Any]) -> None:
        """Register WebSocket-side filtering so only selected HAP events are forwarded."""
        device_id = (msg.get("device_id") or "").strip().lower()
        mqtt_slug = ws.slug if isinstance(ws, MqttClientSession) else ""
        pairing = self._pairing_for_device_id(device_id) if device_id else None
        if not pairing:
            m = "unknown device_id or no active pairing"
            await self._send_ws(
                ws,
                self._ws_merge_request_id(
                    msg,
                    {
                        "version": PROTOCOL_VERSION,
                        "action": "error",
                        "for": "subscribe",
                        "message": m,
                    },
                ),
            )
            self._emit_hub_rpc_error_notice(
                "subscribe",
                m,
                device_id=device_id,
                mqtt_client_slug=mqtt_slug,
            )
            return
        if not pairing.accessories:
            try:
                await pairing.list_accessories_and_characteristics()
            except Exception as ex:
                self.log.exception("subscribe: list_accessories_and_characteristics failed")
                m = str(ex)
                await self._send_ws(
                    ws,
                    self._ws_merge_request_id(
                        msg,
                        {
                            "version": PROTOCOL_VERSION,
                            "action": "error",
                            "for": "subscribe",
                            "message": m,
                        },
                    ),
                )
                self._emit_hub_rpc_error_notice(
                    "subscribe",
                    m,
                    device_id=device_id,
                    mqtt_client_slug=mqtt_slug,
                )
                return
        resolved, resolve_err = self._resolve_ws_aid_iid(pairing, msg)
        if resolve_err is not None:
            await self._send_ws(
                ws,
                self._ws_merge_request_id(
                    msg,
                    {
                        "version": PROTOCOL_VERSION,
                        "action": "error",
                        "for": "subscribe",
                        "message": resolve_err,
                    },
                ),
            )
            self._emit_hub_rpc_error_notice(
                "subscribe",
                resolve_err,
                device_id=device_id,
                mqtt_client_slug=mqtt_slug,
                characteristic=msg.get("characteristic"),
            )
            return
        if not resolved:
            m = "need aid+iid (integers) or characteristic (string)"
            await self._send_ws(
                ws,
                self._ws_merge_request_id(
                    msg,
                    {
                        "version": PROTOCOL_VERSION,
                        "action": "error",
                        "for": "subscribe",
                        "message": m,
                    },
                ),
            )
            self._emit_hub_rpc_error_notice(
                "subscribe",
                m,
                device_id=device_id,
                mqtt_client_slug=mqtt_slug,
            )
            return
        aid_i, iid_i = resolved
        s = self._ws_event_filters.get(ws)
        if s is None:
            s = set()
            self._ws_event_filters[ws] = s
        s.add((device_id, aid_i, iid_i))
        await self._send_ws(
            ws,
            self._ws_merge_request_id(
                msg,
                {
                    "version": PROTOCOL_VERSION,
                    "action": "ack",
                    "for": "subscribe",
                    "device_id": device_id,
                    "aid": aid_i,
                    "iid": iid_i,
                },
            ),
        )

    async def _handle_unsubscribe(self, ws: Any, msg: dict[str, Any]) -> None:
        device_id = (msg.get("device_id") or "").strip().lower()
        mqtt_slug = ws.slug if isinstance(ws, MqttClientSession) else ""
        pairing = self._pairing_for_device_id(device_id) if device_id else None
        if not pairing:
            m = "unknown device_id or no active pairing"
            await self._send_ws(
                ws,
                self._ws_merge_request_id(
                    msg,
                    {
                        "version": PROTOCOL_VERSION,
                        "action": "error",
                        "for": "unsubscribe",
                        "message": m,
                    },
                ),
            )
            self._emit_hub_rpc_error_notice(
                "unsubscribe",
                m,
                device_id=device_id,
                mqtt_client_slug=mqtt_slug,
            )
            return
        if not pairing.accessories:
            try:
                await pairing.list_accessories_and_characteristics()
            except Exception as ex:
                self.log.exception("unsubscribe: list_accessories_and_characteristics failed")
                m = str(ex)
                await self._send_ws(
                    ws,
                    self._ws_merge_request_id(
                        msg,
                        {
                            "version": PROTOCOL_VERSION,
                            "action": "error",
                            "for": "unsubscribe",
                            "message": m,
                        },
                    ),
                )
                self._emit_hub_rpc_error_notice(
                    "unsubscribe",
                    m,
                    device_id=device_id,
                    mqtt_client_slug=mqtt_slug,
                )
                return
        resolved, resolve_err = self._resolve_ws_aid_iid(pairing, msg)
        if resolve_err is not None:
            await self._send_ws(
                ws,
                self._ws_merge_request_id(
                    msg,
                    {
                        "version": PROTOCOL_VERSION,
                        "action": "error",
                        "for": "unsubscribe",
                        "message": resolve_err,
                    },
                ),
            )
            self._emit_hub_rpc_error_notice(
                "unsubscribe",
                resolve_err,
                device_id=device_id,
                mqtt_client_slug=mqtt_slug,
                characteristic=msg.get("characteristic"),
            )
            return
        if not resolved:
            m = "need aid+iid (integers) or characteristic (string)"
            await self._send_ws(
                ws,
                self._ws_merge_request_id(
                    msg,
                    {
                        "version": PROTOCOL_VERSION,
                        "action": "error",
                        "for": "unsubscribe",
                        "message": m,
                    },
                ),
            )
            self._emit_hub_rpc_error_notice(
                "unsubscribe",
                m,
                device_id=device_id,
                mqtt_client_slug=mqtt_slug,
            )
            return
        aid_i, iid_i = resolved
        s = self._ws_event_filters.get(ws)
        if s:
            s.discard((device_id, aid_i, iid_i))
            if not s:
                self._ws_event_filters.pop(ws, None)
        await self._send_ws(
            ws,
            self._ws_merge_request_id(
                msg,
                {
                    "version": PROTOCOL_VERSION,
                    "action": "ack",
                    "for": "unsubscribe",
                    "device_id": device_id,
                    "aid": aid_i,
                    "iid": iid_i,
                },
            ),
        )

    async def _handle_command(self, ws: Any, msg: dict) -> None:
        device_id = (msg.get("device_id") or "").strip().lower()
        char_spec = msg.get("characteristic")
        value = msg.get("value")
        mqtt_slug = ws.slug if isinstance(ws, MqttClientSession) else ""
        pairing = self._pairing_for_device_id(device_id) if device_id else None
        if not pairing:
            m = "unknown device_id or no active pairing"
            await self._send_ws(
                ws,
                self._ws_merge_request_id(
                    msg,
                    {
                        "version": PROTOCOL_VERSION,
                        "action": "error",
                        "for": "command",
                        "message": m,
                    },
                ),
            )
            self._emit_hub_rpc_error_notice(
                "command",
                m,
                device_id=device_id,
                mqtt_client_slug=mqtt_slug,
                characteristic=char_spec,
            )
            return
        if not isinstance(char_spec, str):
            m = "characteristic must be string"
            await self._send_ws(
                ws,
                self._ws_merge_request_id(
                    msg,
                    {
                        "version": PROTOCOL_VERSION,
                        "action": "error",
                        "for": "command",
                        "message": m,
                    },
                ),
            )
            self._emit_hub_rpc_error_notice(
                "command",
                m,
                device_id=device_id,
                mqtt_client_slug=mqtt_slug,
                characteristic=char_spec,
            )
            return
        resolved, resolve_err = _resolve_aid_iid_detailed(pairing, char_spec)
        if resolve_err is not None:
            await self._send_ws(
                ws,
                self._ws_merge_request_id(
                    msg,
                    {
                        "version": PROTOCOL_VERSION,
                        "action": "error",
                        "for": "command",
                        "message": resolve_err,
                    },
                ),
            )
            self._emit_hub_rpc_error_notice(
                "command",
                resolve_err,
                device_id=device_id,
                mqtt_client_slug=mqtt_slug,
                characteristic=char_spec,
            )
            return
        assert resolved is not None
        aid, iid = resolved
        try:
            err = await pairing.put_characteristics([(aid, iid, value)])
            if err:
                m = str(err)
                await self._send_ws(
                    ws,
                    self._ws_merge_request_id(
                        msg,
                        {
                            "version": PROTOCOL_VERSION,
                            "action": "error",
                            "for": "command",
                            "message": m,
                        },
                    ),
                )
                self._emit_hub_rpc_error_notice(
                    "command",
                    m,
                    device_id=device_id,
                    mqtt_client_slug=mqtt_slug,
                    characteristic=char_spec,
                )
                return
        except Exception as ex:
            self.log.exception("put_characteristics failed")
            m = str(ex)
            await self._send_ws(
                ws,
                self._ws_merge_request_id(
                    msg,
                    {
                        "version": PROTOCOL_VERSION,
                        "action": "error",
                        "for": "command",
                        "message": m,
                    },
                ),
            )
            self._emit_hub_rpc_error_notice(
                "command",
                m,
                device_id=device_id,
                mqtt_client_slug=mqtt_slug,
                characteristic=char_spec,
            )
            return
        await self._send_ws(
            ws,
            self._ws_merge_request_id(
                msg,
                {"version": PROTOCOL_VERSION, "action": "ack", "for": "command"},
            ),
        )

    async def _handle_snapshot(self, ws: Any, msg: dict) -> None:
        device_id = (msg.get("device_id") or "").strip().lower()
        pairing = self._pairing_for_device_id(device_id) if device_id else None
        if not pairing:
            await self._send_ws(
                ws,
                self._ws_merge_request_id(
                    msg,
                    {
                        "version": PROTOCOL_VERSION,
                        "action": "error",
                        "for": "snapshot",
                        "message": "unknown device_id or no active pairing",
                    },
                ),
            )
            return

        readable = _readable_characteristics(pairing)
        if not readable:
            try:
                await pairing.list_accessories_and_characteristics()
            except Exception as ex:
                self.log.exception("snapshot: list_accessories_and_characteristics failed")
                await self._send_ws(
                    ws,
                    self._ws_merge_request_id(
                        msg,
                        {
                            "version": PROTOCOL_VERSION,
                            "action": "error",
                            "for": "snapshot",
                            "message": str(ex),
                        },
                    ),
                )
                return
            readable = _readable_characteristics(pairing)

        if not readable:
            await self._send_ws(
                ws,
                self._ws_merge_request_id(
                    msg,
                    {
                        "version": PROTOCOL_VERSION,
                        "action": "error",
                        "for": "snapshot",
                        "message": "no readable characteristics after HAP /accessories refresh",
                    },
                ),
            )
            return

        pairs = [(aid, iid) for aid, iid, _ in readable]
        labels = {(aid, iid): label for aid, iid, label in readable}
        try:
            result = await pairing.get_characteristics(pairs)
        except Exception as ex:
            self.log.exception("get_characteristics failed")
            await self._send_ws(
                ws,
                self._ws_merge_request_id(
                    msg,
                    {
                        "version": PROTOCOL_VERSION,
                        "action": "error",
                        "for": "snapshot",
                        "message": str(ex),
                    },
                ),
            )
            return

        values: list[dict[str, Any]] = []
        for aid, iid in pairs:
            payload = result.get((aid, iid), {})
            if not isinstance(payload, dict):
                payload = {}
            item: dict[str, Any] = {
                "aid": aid,
                "iid": iid,
                "characteristic": labels.get((aid, iid), f"{aid}.{iid}"),
            }
            if "value" in payload:
                item["value"] = payload.get("value")
            if "status" in payload:
                item["status"] = payload.get("status")
            values.append(item)

        await self._send_ws(
            ws,
            self._ws_merge_request_id(
                msg,
                {
                    "version": PROTOCOL_VERSION,
                    "action": "snapshot",
                    "device_id": device_id,
                    "values": values,
                },
            ),
        )

    async def _handle_list_devices(self, ws: Any, msg: dict) -> None:
        del msg  # currently unused; kept for future request options
        if not self._hk:
            self.log.debug("list_devices: hk controller is not ready; returning empty list")
            await self._send_ws(
                ws,
                _list_devices_ws_message(
                    [],
                    [
                        _ws_client_notice(
                            level=WS_NOTICE_LEVEL_ERROR,
                            code=WS_NOTICE_CODE_HUB_CONTROLLER_NOT_READY,
                            message="HomeKit controller is not ready; paired device list is empty",
                        )
                    ],
                ),
            )
            return
        devices_payload, list_warnings = await self._build_list_devices_payload()
        device_ids = [str(d.get("device_id") or "") for d in devices_payload]
        pairings = getattr(self._hk, "pairings", None)
        aliases = getattr(self._hk, "aliases", None)
        pairings_keys = sorted(str(k).strip().lower() for k in pairings.keys()) if isinstance(pairings, dict) else []
        alias_ids = sorted(
            str(getattr(p, "id", "")).strip().lower()
            for p in aliases.values()
            if p is not None and str(getattr(p, "id", "")).strip()
        ) if isinstance(aliases, dict) else []
        self.log.debug(
            "list_devices: pairings_count=%d aliases_count=%d pairings_keys=%s alias_ids=%s response_ids=%s",
            len(pairings) if isinstance(pairings, dict) else -1,
            len(aliases) if isinstance(aliases, dict) else -1,
            pairings_keys,
            alias_ids,
            device_ids,
        )
        await self._send_ws(ws, _list_devices_ws_message(devices_payload, list_warnings))

    async def _send_ws(self, ws: Any, obj: dict) -> None:
        if isinstance(ws, MqttClientSession):
            await self._mqtt_publish_json(ws.slug, "rpc", obj)
            return
        self._enqueue_ws_line(ws, json.dumps(obj, default=str))

    async def _broadcast(self, obj: dict) -> None:
        line = json.dumps(obj, default=str)
        for ws in list(self._client_out.keys()):
            self._enqueue_ws_line(ws, line)
        for sess in list(self._mqtt_handles.values()):
            await self._mqtt_publish_line(sess.slug, "rpc", line)

    async def _broadcast_device_list_update(self, *, reason: str) -> None:
        """Push latest paired device list to all connected clients."""
        devices_payload, list_warnings = await self._build_list_devices_payload()
        device_ids = [str(d.get("device_id") or "") for d in devices_payload]
        await self._broadcast(_list_devices_ws_message(devices_payload, list_warnings))
        self.log.debug(
            "broadcast list_devices update reason=%s count=%d ids=%s",
            reason,
            len(device_ids),
            device_ids,
        )

    def _dispatch_hap_event(self, device_id: str, pairing, ev: dict) -> None:
        if not pairing or not pairing.accessories:
            return
        if not ev:
            return

        for key, payload in ev.items():
            if not isinstance(key, tuple) or len(key) != 2:
                continue
            aid, iid = key
            if "value" not in payload:
                continue
            try:
                ch = pairing.accessories.aid(aid).characteristics.iid(iid)
                label = characteristic_label(ch.type)
            except Exception:
                label = f"{aid}.{iid}"
            self._enqueue_hap_broadcast(
                {
                    "version": PROTOCOL_VERSION,
                    "action": "event",
                    "device_id": device_id,
                    "characteristic": label,
                    "aid": aid,
                    "iid": iid,
                    "value": payload.get("value"),
                }
            )

    def _attach_listener(self, alias: str, pairing) -> None:
        old_stop = self._listeners.pop(alias, None)
        if old_stop:
            try:
                old_stop()
            except Exception:
                pass
        device_id = pairing.id.lower()

        def _cb(ev: dict) -> None:
            self._dispatch_hap_event(device_id, pairing, ev)

        self._listeners[alias] = pairing.dispatcher_connect(_cb)

    def _get_pairings_blob(self) -> dict[str, Any]:
        data = self._get_custom_data()
        raw = data.get(DATA_KEY_PAIRINGS)
        if isinstance(raw, dict):
            return dict(raw)
        return {}

    def _set_pairings_blob(self, blob: dict[str, Any]) -> None:
        data = dict(self._get_custom_data())
        data[DATA_KEY_PAIRINGS] = blob
        self._set_custom_data(data)

    async def _sync_pairing_from_params(self) -> None:
        rows = self._get_pairing_slot_rows()
        if not isinstance(rows, list):
            rows = []
        assigned = assign_pairing_slot_rows(rows, self.log)
        configured_slots = {n for n, _ in assigned}
        blob = self._get_pairings_blob()
        consumed_saved_slot_keys: set[str] = set()

        # Orphan slots: saved in homekit_pairings but no typed row for that slot anymore
        for sk in list(blob.keys()):
            try:
                n = int(str(sk).strip())
            except (TypeError, ValueError):
                continue
            if n in configured_slots:
                continue
            self.log.info("Removing orphan pairing slot %s (no longer in typed config)", n)
            alias = slot_alias(n)
            if self._hk:
                try:
                    saved = blob.get(sk)
                    if isinstance(saved, dict) and saved.get("AccessoryPairingID"):
                        if alias not in self._hk.aliases:
                            self._hk.load_pairing(alias, saved)
                        await self._hk.remove_pairing(alias)
                except Exception:
                    self.log.warning(
                        "Orphan slot %s: could not remove pairing from accessory",
                        n,
                        exc_info=True,
                    )
            if sk in blob:
                del blob[sk]
                self._set_pairings_blob(blob)
            await self._close_alias_if_present(alias)

        for slot_num, row in assigned:
            alias = slot_alias(slot_num)
            slot_key = str(slot_num)
            pin, acc_id, acc_name = _row_pin_and_filters(row)
            acc_id, acc_name = _resolve_filters_from_last_discover(
                self._get_custom_data(), acc_id, acc_name, self.log, slot_num
            )
            saved = blob.get(slot_key)
            if isinstance(saved, dict) and not saved.get("AccessoryPairingID"):
                saved = None
            if saved:
                consumed_saved_slot_keys.add(slot_key)

            if not pin:
                if saved and self._hk:
                    try:
                        if alias not in self._hk.aliases:
                            self._hk.load_pairing(alias, saved)
                        await self._hk.remove_pairing(alias)
                    except Exception:
                        self.log.warning(
                            "Slot %s: could not remove pairing from accessory",
                            slot_num,
                            exc_info=True,
                        )
                if slot_key in blob:
                    del blob[slot_key]
                    self._set_pairings_blob(blob)
                await self._close_alias_if_present(alias)
                continue

            # Startup guard: PIN-only row with no saved customdata and no discover
            # snapshot cannot be restored or targeted, and attempting fresh pairing
            # just spams "already paired" warnings while scanning.
            if (
                not saved
                and not acc_id
                and not acc_name
            ):
                raw = self._get_custom_data()
                last = raw.get(DATA_KEY_LAST_HAP_DISCOVER) if isinstance(raw, dict) else None
                has_last_discover = isinstance(last, list) and len(last) > 0
                has_any_saved_pairings = any(
                    isinstance(v, dict) and v.get("AccessoryPairingID")
                    for v in blob.values()
                )
                if not has_last_discover and not has_any_saved_pairings:
                    self.log.warning(
                        "Slot %s: skipping auto-pair for PIN-only row; customdata has no saved pairings "
                        "and no %s snapshot. Run DISCOVER (or set accessory_id/name) before pairing.",
                        slot_num,
                        DATA_KEY_LAST_HAP_DISCOVER,
                    )
                    if self._pairing_notice:
                        self._pairing_notice(
                            ERR_PAIRING_NO_TARGET,
                            "HomeKit pairing skipped: missing saved data",
                            f"Slot {slot_num}: PIN-only row cannot be restored because customdata has no "
                            f"saved pairings and no {DATA_KEY_LAST_HAP_DISCOVER} snapshot. Run DISCOVER, "
                            "then enter/save the pairing code on the correct row.",
                            None,
                        )
                    continue

            # Resilience path for older configs: if this row has no id/name filter and
            # no blob at its current slot, try a unique unclaimed saved pairing blob.
            # This helps recover when slot numbering changed but pairing data still exists.
            if (
                not saved
                and not acc_id
                and not acc_name
            ):
                candidates: list[tuple[str, dict[str, Any]]] = []
                for bkey, bval in blob.items():
                    if bkey in consumed_saved_slot_keys:
                        continue
                    if not isinstance(bval, dict):
                        continue
                    if not bval.get("AccessoryPairingID"):
                        continue
                    candidates.append((bkey, bval))
                if len(candidates) == 1:
                    from_key, from_saved = candidates[0]
                    saved = from_saved
                    consumed_saved_slot_keys.add(from_key)
                    self.log.info(
                        "Slot %s: recovering saved pairing from prior slot %s for PIN-only row",
                        slot_num,
                        from_key,
                    )
                    if from_key != slot_key:
                        blob[slot_key] = from_saved
                        del blob[from_key]
                        self._set_pairings_blob(blob)
                        self.log.info(
                            "Slot %s: moved recovered pairing blob from slot %s to %s",
                            slot_num,
                            from_key,
                            slot_key,
                        )
                elif len(candidates) > 1:
                    self.log.warning(
                        "Slot %s: multiple saved pairings exist (%d); cannot auto-select for PIN-only row. "
                        "Set slot explicitly or fill accessory_id/accessory_name.",
                        slot_num,
                        len(candidates),
                    )

            if saved:
                try:
                    self._hk.load_pairing(alias, saved)
                    pairing = self._hk.aliases.get(alias)
                    if pairing:
                        await self._activate_pairing(alias, pairing)
                    continue
                except Exception:
                    self.log.exception("Slot %s: load_pairing failed; will try new pair", slot_num)
                    if slot_key in blob:
                        del blob[slot_key]
                        self._set_pairings_blob(blob)
                    await self._close_alias_if_present(alias)

            await self._pair_with_pin(slot_num, alias, pin, acc_id, acc_name, blob)

    async def _close_alias_if_present(self, alias: str) -> None:
        if not self._hk:
            return
        removed_device_id: str | None = None
        st = self._listeners.pop(alias, None)
        if st:
            try:
                st()
            except Exception:
                pass
        pairing = self._hk.aliases.pop(alias, None)
        if pairing:
            pid = pairing.id
            removed_device_id = str(pid).strip().lower() if pid else None
            # Keys are normalized to lower case in ``HKController.load_pairing`` / ``_ensure_top_level_pairing_registered``.
            pid_key = str(pid).strip().lower() if pid else ""
            self._hk.pairings.pop(pid_key, None)
            try:
                await pairing.close()
            except Exception:
                self.log.exception("close %s", alias)
        if removed_device_id:
            await self._broadcast_device_list_update(reason=f"alias_closed:{alias}")

    async def _pair_with_pin(
        self,
        slot_num: int,
        alias: str,
        pin: str,
        accessory_id: str,
        accessory_name: str,
        blob: dict[str, Any],
    ) -> None:
        matched = await self._wait_for_pairing_discovery(
            accessory_id, accessory_name, timeout=30.0
        )
        if not matched:
            paired_seen = False
            if not accessory_id and not accessory_name:
                try:
                    for discovery in self._iter_transport_discoveries():
                        if getattr(discovery, "paired", False):
                            paired_seen = True
                            break
                except Exception:
                    paired_seen = False
            self.log.error(
                "Slot %s: no unpaired accessory matched id=%r name=%r (try DISCOVER, pairing mode, same LAN)",
                slot_num,
                accessory_id,
                accessory_name,
            )
            if self._pairing_notice:
                if paired_seen:
                    detail = (
                        f"Slot {slot_num}: no unpaired accessory matched id={accessory_id!r} name={accessory_name!r}. "
                        "A paired HomeKit accessory was seen on the network; if this was previously paired to this "
                        "plugin, its saved pairing keys are missing from custom data. Unpair/reset the accessory "
                        "from the other controller (or factory reset if needed), then run DISCOVER and pair again."
                    )
                else:
                    detail = (
                        f"Slot {slot_num}: no unpaired accessory matched id={accessory_id!r} name={accessory_name!r}. "
                        "Run DISCOVER with the device in HomeKit pairing mode on the same LAN."
                    )
                self._pairing_notice(
                    ERR_PAIRING_NO_TARGET,
                    "HomeKit pairing: no matching accessory",
                    detail,
                    None,
                )
            return

        try:
            finish = await matched.async_start_pairing(alias)
            pairing = await finish(pin)
        except Exception as e:
            self.log.exception("Slot %s: pairing failed", slot_num)
            if self._pairing_notice:
                if isinstance(e, AuthenticationError):
                    title = "HomeKit pairing code rejected"
                    detail = (
                        f"Slot {slot_num}: accessory rejected the pairing code. "
                        "Re-enter the HomeKit setup code exactly as shown on the device and try again."
                    )
                    # AuthenticationError string payloads can contain low-level bytearray details
                    # that are confusing in PG3 notices; keep traceback in logs, show clean guidance in UI.
                    notice_exc: Optional[Exception] = None
                else:
                    title = "HomeKit pairing failed"
                    detail = f"Slot {slot_num}: pairing error"
                    notice_exc = e
                self._pairing_notice(
                    ERR_PAIRING_FAILED,
                    title,
                    detail,
                    notice_exc,
                )
            return

        pdata = dict(pairing.pairing_data)
        slot_key = str(slot_num)
        blob[slot_key] = pdata
        self._set_pairings_blob(blob)
        self.log.info(
            "Slot %s: saved pairing for accessory %s",
            slot_num,
            pdata.get("AccessoryPairingID"),
        )
        await self._activate_pairing(alias, pairing)

    def _ensure_top_level_pairing_registered(self, alias: str, pairing) -> None:
        """Keep aggregate ``HKController`` maps in sync with transport-local pairings.

        aiohomekit's IP ``finish_pairing`` stores the new ``IpPairing`` only on the IP
        transport controller (``IpController.pairings[alias] = …``). The top-level
        :class:`aiohomekit.Controller` is updated when ``load_pairing`` runs (restart /
        customdata restore), which copies into ``Controller.pairings`` (device id → pairing)
        and ``Controller.aliases`` (slot alias → pairing).

        Fresh PIN pairing never goes through ``load_pairing``, so without mirroring here
        ``self._hk.pairings`` and ``self._hk.aliases`` stay empty: ``list_devices`` reports
        no devices, hello ``ack`` carries an empty ``devices[]``, and shutdown paths that
        walk top-level aliases miss the session even though it is active.
        """
        if not self._hk or not pairing:
            return
        pdata = getattr(pairing, "pairing_data", None)
        if not isinstance(pdata, dict):
            return
        apid = pdata.get("AccessoryPairingID")
        if not apid:
            return
        pid = str(apid).strip().lower()
        if not pid:
            return
        self._hk.pairings[pid] = pairing
        self._hk.aliases[alias] = pairing

    async def _activate_pairing(self, alias: str, pairing) -> None:
        self._ensure_top_level_pairing_registered(alias, pairing)
        self._attach_listener(alias, pairing)
        try:
            await pairing.list_accessories_and_characteristics()
        except Exception:
            self.log.exception("list_accessories_and_characteristics for %s", alias)
            return
        to_sub = _subscribable_characteristics(pairing)
        if to_sub:
            try:
                await pairing.subscribe(to_sub)
                self.log.info(
                    "%s: subscribed to %d event characteristics",
                    alias,
                    len(to_sub),
                )
            except Exception:
                self.log.exception("subscribe failed for %s", alias)
        self.log.info("HomeKit session active for %s (%s)", alias, pairing.id)
        await self._broadcast_device_list_update(reason=f"pairing_active:{alias}")
