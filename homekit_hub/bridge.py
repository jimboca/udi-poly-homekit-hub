"""
Async HomeKit controller + WebSocket bridge for PG3x.

Runs on a dedicated asyncio loop (see nodes.Controller). No udi_interface imports.

Supports multiple simultaneous pairings; each row has a slot id (explicit or auto).
"""
from __future__ import annotations

import asyncio
import errno
import json
import logging
import os
import sys
from typing import Any, Callable, Optional

import websockets
from zeroconf import ServiceStateChange
from zeroconf._utils.net import InterfaceChoice, IPVersion
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

from aiohomekit import Controller as HKController
from aiohomekit.exceptions import AccessoryNotFoundError, AuthenticationError
from aiohomekit.model.characteristics import CharacteristicPermissions, CharacteristicsTypes
from aiohomekit.model.status_flags import StatusFlags
from aiohomekit.uuid import normalize_uuid

PROTOCOL_VERSION = "1"
TYPED_PAIRING_SLOTS_KEY = "pairing_slots"
DATA_KEY_PAIRINGS = "homekit_pairings"
# Last HAP discover snapshot (for UI; written by discover_collect)
DATA_KEY_LAST_HAP_DISCOVER = "last_hap_discover"
HAP_TYPE_TCP = "_hap._tcp.local."
HAP_TYPE_UDP = "_hap._udp.local."

# ISY **ERR** driver codes (must match profile NLS ``ERRC-*``).
ERR_PAIRING_NO_TARGET = 8
ERR_PAIRING_FAILED = 9


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


def _zeroconf_ctor_kwargs(log: logging.Logger, *, unicast: bool) -> dict[str, Any]:
    """Optional ``AsyncZeroconf`` constructor kwargs from env and host quirks.

    On **BSD / macOS**, unicast mode can log ``sendto`` **EADDRNOTAVAIL** (errno 49) when python-zeroconf
    tries to answer queries via a socket bound to a real LAN address toward **127.0.0.1**.
    Using **Default** interfaces + **IPv4-only** avoids the worst of that for typical HomeKit LANs.

    Override with:
    - ``HOMEKIT_HUB_ZEROCONF_INTERFACES`` — ``default`` | ``all`` (omit or empty = library default for multicast; for unicast on BSD see auto-below)
    - ``HOMEKIT_HUB_ZEROCONF_IP_VERSION`` — ``v4`` | ``v6`` | ``all``
    """
    out: dict[str, Any] = {}
    ic = os.environ.get("HOMEKIT_HUB_ZEROCONF_INTERFACES", "").strip().lower()
    if ic == "default":
        out["interfaces"] = InterfaceChoice.Default
    elif ic == "all":
        out["interfaces"] = InterfaceChoice.All

    ipv = os.environ.get("HOMEKIT_HUB_ZEROCONF_IP_VERSION", "").strip().lower()
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
            log.debug("zeroconf unicast: using InterfaceChoice.Default on %s", sys.platform)
        if "ip_version" not in out:
            out["ip_version"] = IPVersion.V4Only
            log.debug("zeroconf unicast: using IPVersion.V4Only on %s", sys.platform)
    return out


def _async_zeroconf_for_hub(log: logging.Logger) -> tuple[AsyncZeroconf, bool]:
    """Create ``AsyncZeroconf``. If mDNS port **5353** is already bound (Avahi, mDNSResponder, …),
    fall back to **unicast** mode so python-zeroconf does not need a second multicast listener.
    Set env ``HOMEKIT_HUB_ZEROCONF_UNICAST=1`` to skip multicast and use unicast only.
    """
    if os.environ.get("HOMEKIT_HUB_ZEROCONF_UNICAST", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        log.info("HOMEKIT_HUB_ZEROCONF_UNICAST set: using zeroconf unicast mode")
        kw = _zeroconf_ctor_kwargs(log, unicast=True)
        return AsyncZeroconf(unicast=True, **kw), True
    kw_m = _zeroconf_ctor_kwargs(log, unicast=False)
    try:
        return AsyncZeroconf(**kw_m), False
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        log.warning(
            "mDNS port 5353 is already in use on this host; retrying zeroconf in unicast mode "
            "(if problems persist, set Avahi disallow-other-stacks=no or set "
            "HOMEKIT_HUB_ZEROCONF_UNICAST=1)."
        )
        try:
            kw_u = _zeroconf_ctor_kwargs(log, unicast=True)
            return AsyncZeroconf(unicast=True, **kw_u), True
        except TypeError:
            log.error(
                "unicast mode is not supported by this python-zeroconf; free UDP 5353 or upgrade zeroconf"
            )
            raise e


def slot_alias(slot_num: int) -> str:
    return f"slot_{slot_num}"


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


def _resolve_aid_iid(pairing, char_spec: str) -> Optional[tuple[int, int]]:
    uid = _parse_char_name_to_uuid(char_spec)
    if not pairing.accessories:
        return None
    for acc in pairing.accessories:
        for svc in acc.services:
            for ch in svc.characteristics:
                if normalize_uuid(ch.type) == uid:
                    return acc.aid, ch.iid
    return None


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
    ) -> None:
        self.log = logger
        self._get_params = get_params
        self._get_pairing_slot_rows = get_pairing_slot_rows
        self._get_custom_data = get_custom_data
        self._set_custom_data = set_custom_data
        self._pairing_notice = pairing_notice

        self._hk: Optional[HKController] = None
        self._async_zeroconf: Optional[AsyncZeroconf] = None
        self._zc_hap_browsers: list[AsyncServiceBrowser] = []
        self._listeners: dict[str, Callable[[], None]] = {}
        self._clients: set[Any] = set()
        self._ws_server: Any = None
        self._running = False

    async def _abort_start(self) -> None:
        """Undo partial startup (used when async_start or later steps fail)."""
        self._running = False
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
        if self._async_zeroconf is not None:
            if self._zc_hap_browsers:
                for browser in self._zc_hap_browsers:
                    try:
                        await browser.async_cancel()
                    except Exception:
                        self.log.exception("AsyncServiceBrowser.async_cancel after failed start")
                self._zc_hap_browsers = []
            try:
                await self._async_zeroconf.async_close()
            except Exception:
                self.log.exception("AsyncZeroconf.async_close after failed start")
            self._async_zeroconf = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        try:
            # aiohomekit IP transport requires a real AsyncZeroconf; default HKController()
            # passes None and IpController.async_start() crashes (no .zeroconf).
            self._async_zeroconf, using_unicast = _async_zeroconf_for_hub(self.log)
            if using_unicast:
                # aiohomekit looks up an existing AsyncServiceBrowser on the zeroconf
                # instance during startup; in unicast mode we must create one explicitly
                # for each HAP transport type that may be enabled in aiohomekit.
                for hap_type in (HAP_TYPE_TCP, HAP_TYPE_UDP):
                    self._zc_hap_browsers.append(
                        AsyncServiceBrowser(
                            self._async_zeroconf.zeroconf,
                            hap_type,
                            handlers=[lambda **_: None],
                        )
                    )
            self._hk = HKController(async_zeroconf_instance=self._async_zeroconf)
            await self._hk.async_start()
            # Load pairings before accepting WebSocket clients; otherwise an immediate
            # ``list_devices`` runs while ``self._hk.pairings`` is still empty (events
            # appear only after ``_sync_pairing_from_params`` finishes).
            await self._sync_pairing_from_params()
            await self._start_websocket_server()
        except Exception:
            await self._abort_start()
            raise

    async def stop(self) -> None:
        self._running = False
        self._clear_all_listeners()
        await self._shutdown_all_pairings()
        if self._ws_server is not None:
            self._ws_server.close()
            await self._ws_server.wait_closed()
            self._ws_server = None
        if self._hk:
            await self._hk.async_stop()
            self._hk = None
        if self._zc_hap_browsers:
            for browser in self._zc_hap_browsers:
                try:
                    await browser.async_cancel()
                except Exception:
                    self.log.exception("AsyncServiceBrowser.async_cancel")
            self._zc_hap_browsers = []
        if self._async_zeroconf is not None:
            try:
                await self._async_zeroconf.async_close()
            except Exception:
                self.log.exception("AsyncZeroconf.async_close")
            self._async_zeroconf = None
        self._clients.clear()

    async def restart_session(self) -> None:
        """Reload all slots from params + customData (after PG3 param change)."""
        if not self._running or not self._hk:
            return
        self._clear_all_listeners()
        await self._shutdown_all_pairings()
        await self._sync_pairing_from_params()

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
                self._hk.pairings.pop(pid, None)
                try:
                    await pairing.close()
                except Exception:
                    self.log.exception("pairing close for %s", alias)

    def _iter_transport_discoveries(self):
        """Yield discovery objects from all aiohomekit transports (IP, COAP, BLE)."""
        if not self._hk:
            return
        transports = getattr(self._hk, "transports", None)
        if not transports:
            return
        for transport in transports.values():
            discoveries = getattr(transport, "discoveries", None) or {}
            yield from discoveries.values()

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
        loop = asyncio.get_event_loop()
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

    async def discover_collect_raw_zc(
        self, timeout: float = 12.0
    ) -> list[dict[str, Any]]:
        """Raw HAP mDNS sampler used by the ``BONJOUR_COMPARE`` admin runCmd.

        Bypasses ``aiohomekit`` so we can compare what python-zeroconf actually
        sees on the wire (full TXT keys) against what PG3 ``polyglot.bonjour()``
        returns. Reuses the bridge's existing ``AsyncZeroconf`` instance so we
        do not open a second multicast listener.
        """
        if not self._async_zeroconf:
            return []
        zc = self._async_zeroconf.zeroconf
        seen: dict[str, dict[str, Any]] = {}

        def _on_state_change(*, zeroconf, service_type, name, state_change):
            if state_change == ServiceStateChange.Removed:
                seen.pop(name, None)
                return
            seen[name] = {"type": service_type, "name": name}

        browsers: list[AsyncServiceBrowser] = []
        for hap_type in (HAP_TYPE_TCP, HAP_TYPE_UDP):
            browsers.append(
                AsyncServiceBrowser(zc, hap_type, handlers=[_on_state_change])
            )
        try:
            await asyncio.sleep(float(timeout))
        finally:
            for b in browsers:
                try:
                    await b.async_cancel()
                except Exception:
                    self.log.exception("raw-zc browser cancel")

        rows: list[dict[str, Any]] = []
        for name, meta in seen.items():
            try:
                info = AsyncServiceInfo(meta["type"], name)
                ok = await info.async_request(zc, 3000)
            except Exception:
                self.log.exception("raw-zc AsyncServiceInfo for %s", name)
                continue
            if not ok:
                continue
            try:
                addrs = [str(ip) for ip in info.ip_addresses_by_version(IPVersion.All)]
            except Exception:
                addrs = []
            try:
                txt = {
                    (k or "").lower(): (v.decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else v)
                    for k, v in (info.decoded_properties or {}).items()
                    if v is not None
                }
            except Exception:
                txt = {}
            host = (info.server or "").rstrip(".") if info.server else ""
            sf_raw = txt.get("sf")
            try:
                paired_flag = (int(sf_raw) & 0x01) == 0 if sf_raw is not None else None
            except (TypeError, ValueError):
                paired_flag = None
            rows.append(
                {
                    "type": meta["type"],
                    "name": name,
                    "host": host,
                    "port": int(info.port) if info.port else 0,
                    "addresses": addrs,
                    "txt": txt,
                    "id": (txt.get("id") or "").lower(),
                    "paired": paired_flag,
                }
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

        loop = asyncio.get_event_loop()
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

    async def _start_websocket_server(self) -> None:
        host, port = self._ws_bind()
        self.log.info("WebSocket server listening on %s:%s", host, port)
        self._ws_server = await websockets.serve(
            self._ws_connection,
            host,
            port,
            ping_interval=20,
            ping_timeout=20,
        )

    async def _ws_connection(self, ws: Any) -> None:
        self._clients.add(ws)
        self.log.debug("WS client connected from %s", getattr(ws, "remote_address", None))
        try:
            async for raw in ws:
                await self._handle_ws_message(ws, raw)
        except websockets.ConnectionClosed:
            pass
        except Exception:
            self.log.exception("WebSocket handler error")
        finally:
            self._clients.discard(ws)

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
        ver = msg.get("version")
        if ver != PROTOCOL_VERSION:
            await self._send_ws(
                ws,
                {
                    "version": PROTOCOL_VERSION,
                    "action": "error",
                    "message": f"unsupported version {ver!r}, need {PROTOCOL_VERSION}",
                },
            )
            await ws.close()
            return
        action = msg.get("action")
        if action == "hello":
            await self._send_ws(
                ws,
                {
                    "version": PROTOCOL_VERSION,
                    "action": "ack",
                    "protocol": PROTOCOL_VERSION,
                },
            )
            return
        if action == "command":
            await self._handle_command(ws, msg)
            return
        if action == "snapshot":
            await self._handle_snapshot(ws, msg)
            return
        if action == "list_devices":
            await self._handle_list_devices(ws, msg)
            return
        await self._send_ws(
            ws,
            {
                "version": PROTOCOL_VERSION,
                "action": "error",
                "message": f"unknown action {action!r}",
            },
        )

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
        return sorted(ids)

    def _pairing_for_device_id(self, device_id: str):
        if not self._hk:
            return None
        return self._hk.pairings.get(device_id)

    async def _handle_command(self, ws: Any, msg: dict) -> None:
        device_id = (msg.get("device_id") or "").strip().lower()
        char_spec = msg.get("characteristic")
        value = msg.get("value")
        pairing = self._pairing_for_device_id(device_id) if device_id else None
        if not pairing:
            await self._send_ws(
                ws,
                {
                    "version": PROTOCOL_VERSION,
                    "action": "error",
                    "for": "command",
                    "message": "unknown device_id or no active pairing",
                },
            )
            return
        if not isinstance(char_spec, str):
            await self._send_ws(
                ws,
                {
                    "version": PROTOCOL_VERSION,
                    "action": "error",
                    "for": "command",
                    "message": "characteristic must be string",
                },
            )
            return
        resolved = _resolve_aid_iid(pairing, char_spec)
        if not resolved:
            await self._send_ws(
                ws,
                {
                    "version": PROTOCOL_VERSION,
                    "action": "error",
                    "for": "command",
                    "message": f"unknown characteristic {char_spec!r}",
                },
            )
            return
        aid, iid = resolved
        try:
            err = await pairing.put_characteristics([(aid, iid, value)])
            if err:
                await self._send_ws(
                    ws,
                    {
                        "version": PROTOCOL_VERSION,
                        "action": "error",
                        "for": "command",
                        "message": str(err),
                    },
                )
                return
        except Exception as ex:
            self.log.exception("put_characteristics failed")
            await self._send_ws(
                ws,
                {
                    "version": PROTOCOL_VERSION,
                    "action": "error",
                    "for": "command",
                    "message": str(ex),
                },
            )
            return
        await self._send_ws(
            ws,
            {"version": PROTOCOL_VERSION, "action": "ack", "for": "command"},
        )

    async def _handle_snapshot(self, ws: Any, msg: dict) -> None:
        device_id = (msg.get("device_id") or "").strip().lower()
        pairing = self._pairing_for_device_id(device_id) if device_id else None
        if not pairing:
            await self._send_ws(
                ws,
                {
                    "version": PROTOCOL_VERSION,
                    "action": "error",
                    "for": "snapshot",
                    "message": "unknown device_id or no active pairing",
                },
            )
            return

        readable = _readable_characteristics(pairing)
        if not readable:
            await self._send_ws(
                ws,
                {
                    "version": PROTOCOL_VERSION,
                    "action": "snapshot",
                    "device_id": device_id,
                    "values": [],
                },
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
                {
                    "version": PROTOCOL_VERSION,
                    "action": "error",
                    "for": "snapshot",
                    "message": str(ex),
                },
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
            {
                "version": PROTOCOL_VERSION,
                "action": "snapshot",
                "device_id": device_id,
                "values": values,
            },
        )

    async def _handle_list_devices(self, ws: Any, msg: dict) -> None:
        del msg  # currently unused; kept for future request options
        if not self._hk:
            await self._send_ws(
                ws,
                {
                    "version": PROTOCOL_VERSION,
                    "action": "list_devices",
                    "devices": [],
                },
            )
            return
        device_ids = self._active_pairing_device_ids()
        await self._send_ws(
            ws,
            {
                "version": PROTOCOL_VERSION,
                "action": "list_devices",
                "devices": [{"device_id": did} for did in device_ids],
            },
        )

    async def _send_ws(self, ws: Any, obj: dict) -> None:
        try:
            await ws.send(json.dumps(obj, default=str))
        except Exception:
            pass

    async def _broadcast(self, obj: dict) -> None:
        line = json.dumps(obj, default=str)
        dead: list[Any] = []
        for ws in list(self._clients):
            try:
                await ws.send(line)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    def _dispatch_hap_event(self, device_id: str, pairing, ev: dict) -> None:
        if not pairing or not pairing.accessories:
            return
        if not ev:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
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
            loop.create_task(
                self._broadcast(
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
        st = self._listeners.pop(alias, None)
        if st:
            try:
                st()
            except Exception:
                pass
        pairing = self._hk.aliases.pop(alias, None)
        if pairing:
            pid = pairing.id
            self._hk.pairings.pop(pid, None)
            try:
                await pairing.close()
            except Exception:
                self.log.exception("close %s", alias)

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
                title = (
                    "HomeKit pairing code rejected"
                    if isinstance(e, AuthenticationError)
                    else "HomeKit pairing failed"
                )
                self._pairing_notice(
                    ERR_PAIRING_FAILED,
                    title,
                    f"Slot {slot_num}: pairing error",
                    e,
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

    async def _activate_pairing(self, alias: str, pairing) -> None:
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
