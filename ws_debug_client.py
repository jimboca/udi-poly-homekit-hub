#!/usr/bin/env python3
"""
Simple WebSocket debug client for the udi-poly-homekit hub.

Connects to ``ws://<host>:<port>``, sends the protocol ``hello``, and prints
inbound messages in a human-readable format. Protocol details: ``PROTOCOL.md``.

Usage (from the Node Server repo root, with ``websockets`` installed):

    # Default: 127.0.0.1:8163 — connect, hello, then print all hub → client frames
    python3 ws_debug_client.py

    # Match Custom Params ws_host / ws_port
    python3 ws_debug_client.py --host 127.0.0.1 --port 8163

    # When the hub Custom Param ws_token is set
    python3 ws_debug_client.py --token 'your-shared-secret'

    # After hello, request one device snapshot (lowercase AccessoryPairingID)
    python3 ws_debug_client.py --snapshot-device-id aa:bb:cc:dd:ee:ff

    # list_devices, then snapshot every paired device (paired accessories only)
    python3 ws_debug_client.py --snapshot-all

    # Send arbitrary JSON after hello (must include "version": "1")
    python3 ws_debug_client.py --command '{"version":"1","action":"list_devices"}'

    # Full JSON for each received message
    python3 ws_debug_client.py --raw

``list_devices`` / ``--snapshot-all`` only enumerate **paired** accessories
(aiohomekit active pairings). Unpaired devices seen via DISCOVER / mDNS do not
appear until pairing completes and the hub loads that pairing.

If ``list_devices`` is briefly empty while the IP session is still coming up,
``--snapshot-all`` will **also** request a ``snapshot`` for each distinct
``device_id`` seen on ``event`` frames (same ids events use), so you still get
full snapshots without passing ``--snapshot-device-id`` by hand.

Use ``--max-messages N`` or ``--oneshot`` to stop after N inbound frames instead
of monitoring until Ctrl+C (scripts and ``make ws-*`` targets rely on this).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed


PROTOCOL_VERSION = "1"


class _SnapshotAllState:
    """Mutable state for ``--snapshot-all`` (list_devices + event fallback)."""

    __slots__ = ("snap_done", "snap_event_fallback", "snap_fallback_notice")

    def __init__(self) -> None:
        self.snap_done: set[str] = set()
        self.snap_event_fallback = False
        self.snap_fallback_notice = False


def snapshot_all_handle_inbound(
    msg: dict[str, Any], state: _SnapshotAllState
) -> tuple[list[dict[str, Any]], list[str]]:
    """Compute outbound snapshot payloads and log lines for ``--snapshot-all``.

    Pure helper used by :func:`_run` and unit tests (no WebSocket I/O).

    Returns ``(outbound_snapshots, extra_print_lines)``.
    """
    outbound: list[dict[str, Any]] = []
    lines: list[str] = []

    if msg.get("action") == "list_devices":
        devices = msg.get("devices")
        if isinstance(devices, list):
            if len(devices) == 0:
                state.snap_event_fallback = True
                if not state.snap_fallback_notice:
                    state.snap_fallback_notice = True
                    lines.append(
                        f"[{_now()}] snapshot-all: list_devices returned 0 devices; "
                        "will request snapshot for each device_id seen on event frames"
                    )
            else:
                state.snap_event_fallback = False
            for item in devices:
                if not isinstance(item, dict):
                    continue
                did = (item.get("device_id") or "").strip().lower()
                if not did or did in state.snap_done:
                    continue
                outbound.append(
                    {
                        "version": PROTOCOL_VERSION,
                        "action": "snapshot",
                        "device_id": did,
                    }
                )
                state.snap_done.add(did)
                lines.append(f"[{_now()}] requested snapshot for {did!r}")

    if state.snap_event_fallback and msg.get("action") == "event":
        did = (msg.get("device_id") or "").strip().lower()
        if did and did not in state.snap_done:
            outbound.append(
                {
                    "version": PROTOCOL_VERSION,
                    "action": "snapshot",
                    "device_id": did,
                }
            )
            state.snap_done.add(did)
            lines.append(
                f"[{_now()}] snapshot-all: requested snapshot for {did!r} "
                "(fallback from event; list_devices was empty)"
            )

    return outbound, lines


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _to_json(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, default=str)


def _append_list_device_item_lines(parts: list[str], item: dict[str, Any]) -> None:
    """Pretty-print one ``list_devices`` / hello ``devices[]`` row."""
    parts.append(f"    - {item.get('device_id')}")
    indent = "        "
    for key in (
        "name",
        "manufacturer",
        "model",
        "serial_number",
        "firmware_revision",
        "hardware_revision",
        "category",
        "category_label",
        "primary_aid",
    ):
        if key not in item:
            continue
        parts.append(f"{indent}{key}: {item[key]}")


def _append_snapshot_like_values(parts: list[str], msg: dict[str, Any], *, source: str) -> None:
    """Pretty-print ``values`` for ``snapshot`` or ``get`` (same payload shape)."""
    parts.append(f"  device_id      : {msg.get('device_id')}")
    vals = msg.get("values")
    if not isinstance(vals, list):
        return
    parts.append(f"  values         : {len(vals)} characteristic(s)")
    for item in vals:
        if not isinstance(item, dict):
            continue
        parts.append(
            "    - {char} ({aid}/{iid}) = {value!r}{status}".format(
                char=item.get("characteristic"),
                aid=item.get("aid"),
                iid=item.get("iid"),
                value=item.get("value"),
                status=(f" [status={item.get('status')}]" if "status" in item else ""),
            )
        )
    if len(vals) == 0:
        if source == "get":
            parts.append(
                "  note           : 0 values — check hub logs / a nearby action=error for get, "
                "or confirm the accessory is online and characteristic names resolve."
            )
        else:
            parts.append(
                "  note           : 0 values usually means the hub never loaded the HAP layout for this pairing "
                "(snapshot triggers /accessories first) or get_characteristics returned nothing; "
                "look for a nearby action=error for snapshot, or confirm the accessory is online."
            )


def _format_message(msg: dict[str, Any], show_raw: bool) -> str:
    ts = _now()
    action = msg.get("action", "<missing>")
    version = msg.get("version", "<missing>")
    parts = [f"[{ts}] action={action} version={version}"]

    if action == "event":
        parts.append(f"  device_id      : {msg.get('device_id')}")
        parts.append(f"  characteristic : {msg.get('characteristic')}")
        parts.append(f"  aid/iid        : {msg.get('aid')}/{msg.get('iid')}")
        parts.append(f"  value          : {msg.get('value')!r}")
    elif action == "ack":
        ack_for = msg.get("for")
        protocol = msg.get("protocol")
        if ack_for:
            parts.append(f"  ack_for        : {ack_for}")
        if protocol:
            parts.append(f"  protocol       : {protocol}")
        if ack_for == "hello":
            devs = msg.get("devices")
            if isinstance(devs, list) and devs:
                parts.append(f"  devices        : {len(devs)} paired (metadata)")
                for item in devs:
                    if isinstance(item, dict):
                        _append_list_device_item_lines(parts, item)
            else:
                dids = msg.get("device_ids")
                if isinstance(dids, list):
                    parts.append(f"  device_ids     : {len(dids)} paired")
                    for x in dids:
                        parts.append(f"    - {x}")
            cap = msg.get("capabilities")
            if isinstance(cap, dict):
                acts = cap.get("actions")
                if isinstance(acts, list):
                    parts.append(f"  capabilities.actions: {', '.join(str(a) for a in acts)}")
                if "auth" in cap:
                    parts.append(f"  capabilities.auth: {cap.get('auth')}")
                ev = cap.get("events")
                if isinstance(ev, dict):
                    mode = ev.get("mode")
                    if mode is not None:
                        parts.append(f"  capabilities.events.mode: {mode}")
                    desc = ev.get("description")
                    if isinstance(desc, str) and desc.strip():
                        parts.append(f"  capabilities.events.description: {desc.strip()}")
        elif ack_for in ("subscribe", "unsubscribe"):
            parts.append(f"  device_id      : {msg.get('device_id')}")
            parts.append(f"  aid/iid        : {msg.get('aid')}/{msg.get('iid')}")
    elif action == "error":
        parts.append(f"  message        : {msg.get('message')}")
        if "for" in msg:
            parts.append(f"  for            : {msg.get('for')}")
    elif action == "snapshot":
        _append_snapshot_like_values(parts, msg, source="snapshot")
    elif action == "get":
        _append_snapshot_like_values(parts, msg, source="get")
    elif action == "list_devices":
        devices = msg.get("devices")
        if isinstance(devices, list):
            parts.append(f"  devices        : {len(devices)}")
            for item in devices:
                if isinstance(item, dict):
                    _append_list_device_item_lines(parts, item)
            if len(devices) == 0:
                parts.append(
                    "  note           : list_devices is empty at this moment. DISCOVER-only targets are not listed "
                    "until paired. Some runtimes can briefly report an empty list during pairing/session reload even "
                    "while active event subscriptions still emit device_id updates; retry list_devices after a few seconds."
                )

    if show_raw:
        parts.append("  raw:")
        for line in _to_json(msg).splitlines():
            parts.append(f"    {line}")

    return "\n".join(parts)


def _interactive_help_text() -> str:
    return (
        "Interactive mode commands:\n"
        '  - Enter a JSON object to send it as-is (example: {"version":"1","action":"list_devices"})\n'
        '  - /list        send {"version":"1","action":"list_devices"}\n'
        "  - /snapshot <device_id>\n"
        "  - /get <device_id> <characteristic>\n"
        "  - /quit        close client\n"
        "  - /help        show this help"
    )


def _interactive_command_to_payload(line: str) -> tuple[bool, dict[str, Any] | None]:
    """Parse interactive input into protocol payload.

    Returns ``(should_quit, payload)``.
    """
    raw = line.strip()
    if not raw:
        return False, None
    if raw in {"/q", "/quit", "/exit"}:
        return True, None
    if raw in {"/h", "/help"}:
        print(_interactive_help_text())
        return False, None
    if raw == "/list":
        return False, {"version": PROTOCOL_VERSION, "action": "list_devices"}
    if raw.startswith("/snapshot "):
        did = raw.split(" ", 1)[1].strip().lower()
        if not did:
            print(f"[{_now()}] interactive error: /snapshot requires device_id")
            return False, None
        return False, {"version": PROTOCOL_VERSION, "action": "snapshot", "device_id": did}
    if raw.startswith("/get "):
        rest = raw.split(" ", 1)[1].strip()
        parts = rest.split(" ", 1)
        if len(parts) != 2:
            print(f"[{_now()}] interactive error: /get requires device_id and characteristic")
            return False, None
        did, characteristic = parts[0].strip().lower(), parts[1].strip()
        if not did or not characteristic:
            print(f"[{_now()}] interactive error: /get requires device_id and characteristic")
            return False, None
        return False, {
            "version": PROTOCOL_VERSION,
            "action": "get",
            "device_id": did,
            "characteristic": characteristic,
        }
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as ex:
        print(f"[{_now()}] interactive JSON error: {ex}")
        return False, None
    if not isinstance(payload, dict):
        print(f"[{_now()}] interactive error: payload must be a JSON object")
        return False, None
    return False, payload


async def _interactive_sender(ws: Any) -> None:
    print(_interactive_help_text())
    while True:
        try:
            line = await asyncio.to_thread(input, "ws> ")
        except EOFError:
            line = "/quit"
        should_quit, payload = _interactive_command_to_payload(line)
        if should_quit:
            try:
                await ws.close()
            except Exception:
                pass
            return
        if payload is None:
            continue
        await ws.send(json.dumps(payload))
        print(f"[{_now()}] sent interactive payload: action={payload.get('action')}")


async def _run(args: argparse.Namespace) -> None:
    uri = f"ws://{args.host}:{args.port}"
    print(f"[{_now()}] connecting to {uri}")

    async with websockets.connect(uri, ping_interval=20, ping_timeout=20, close_timeout=5) as ws:
        hello = {
            "version": PROTOCOL_VERSION,
            "action": "hello",
            "client": args.client_name,
        }
        if args.token:
            hello["token"] = args.token
        await ws.send(json.dumps(hello))
        print(f"[{_now()}] sent hello as {args.client_name!r}")

        if args.command:
            payload = json.loads(args.command)
            await ws.send(json.dumps(payload))
            print(f"[{_now()}] sent command payload")
        if args.snapshot_device_id:
            payload = {
                "version": PROTOCOL_VERSION,
                "action": "snapshot",
                "device_id": args.snapshot_device_id.strip().lower(),
            }
            await ws.send(json.dumps(payload))
            print(f"[{_now()}] requested snapshot for {payload['device_id']!r}")
        if args.snapshot_all:
            payload = {
                "version": PROTOCOL_VERSION,
                "action": "list_devices",
            }
            await ws.send(json.dumps(payload))
            print(f"[{_now()}] requested active device list")

        snap_state = _SnapshotAllState()

        max_rx: int | None = getattr(args, "max_messages", None)
        if getattr(args, "oneshot", False) and max_rx is None:
            max_rx = 1

        sender_task = asyncio.create_task(_interactive_sender(ws)) if args.interactive else None
        received = 0
        try:
            async for raw in ws:
                received += 1
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    print(f"[{_now()}] non-JSON frame:\n{raw}")
                    if max_rx is not None and received >= max_rx:
                        print(f"[{_now()}] --max-messages {max_rx} reached; exiting")
                        break
                    continue
                if args.snapshot_all:
                    outbound, extra_lines = snapshot_all_handle_inbound(msg, snap_state)
                    for line in extra_lines:
                        print(line)
                    for payload in outbound:
                        await ws.send(json.dumps(payload))
                print(_format_message(msg, show_raw=args.raw))
                print("-" * 72)
                if max_rx is not None and received >= max_rx:
                    print(f"[{_now()}] --max-messages {max_rx} reached; exiting")
                    break
        finally:
            if sender_task is not None:
                sender_task.cancel()
                try:
                    await sender_task
                except asyncio.CancelledError:
                    pass


def _build_parser() -> argparse.ArgumentParser:
    epilog = """
examples:
  %(prog)s
      Connect to ws://127.0.0.1:8163, send hello, print events/acks/errors until Ctrl+C.

  %(prog)s --host 192.168.1.10 --port 8163 --raw
      Custom bind; include full JSON for every received message.

  %(prog)s --snapshot-device-id 12:34:56:78:90:ab
      After hello, request snapshot for that paired accessory (id lowercase).

  %(prog)s --snapshot-all
      Send list_devices, then snapshot each returned device_id (paired only).

  %(prog)s --command '{"version":"1","action":"list_devices"}'
      Send custom JSON once after hello (quote carefully in your shell).

  %(prog)s --interactive
      Keep a command prompt open while connected; send JSON or shortcuts like /list.

  %(prog)s --oneshot
      Receive one inbound frame (typically hello ack), then exit (same as --max-messages 1).

  %(prog)s --snapshot-all --max-messages 30
      Bounded run for scripts/CI: stop after 30 inbound frames instead of monitoring forever.

See PROTOCOL.md for actions (hello, command, snapshot, list_devices, get, subscribe, unsubscribe) and hub events.
Hub ws_host / ws_port / optional ws_token are Custom Params on the Polyglot node (defaults 127.0.0.1:8163).
"""
    p = argparse.ArgumentParser(
        description="Debug client for udi-poly-homekit WebSocket stream.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog,
    )
    p.add_argument("--host", default="127.0.0.1", help="WebSocket host (default: 127.0.0.1)")
    p.add_argument("--port", default=8163, type=int, help="WebSocket port (default: 8163)")
    p.add_argument(
        "--client-name",
        default="ws-debug-client",
        help="Value for hello.client (default: ws-debug-client)",
    )
    p.add_argument(
        "--token",
        default="",
        help="Optional hello.token when hub Custom Param ws_token is set",
    )
    p.add_argument(
        "--raw",
        action="store_true",
        help="Also print full pretty JSON for each received message.",
    )
    p.add_argument(
        "--command",
        help='Optional JSON payload to send once after hello (example: \'{"version":"1","action":"command",...}\')',
    )
    p.add_argument(
        "--snapshot-device-id",
        help="Optional AccessoryPairingID to request an initial snapshot.",
    )
    p.add_argument(
        "--snapshot-all",
        action="store_true",
        help=(
            "Request list_devices on connect and then snapshot each device_id. "
            "list_devices is paired accessories only (empty until pairing succeeds)."
        ),
    )
    p.add_argument(
        "--interactive",
        action="store_true",
        help="Interactive command prompt while connected (JSON input and shortcuts).",
    )
    p.add_argument(
        "--max-messages",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Exit after receiving N inbound WebSocket frames (JSON or not). "
            "Default: unlimited (monitor until disconnect/Ctrl+C). "
            "Use 1 with no other actions for a quick hello/ack check."
        ),
    )
    p.add_argument(
        "--oneshot",
        action="store_true",
        help="Short for --max-messages 1 (single inbound frame, then exit).",
    )
    return p


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    if args.max_messages is not None and args.max_messages < 1:
        print(f"[{_now()}] --max-messages must be >= 1 (got {args.max_messages})")
        return 2
    try:
        asyncio.run(_run(args))
        return 0
    except json.JSONDecodeError as ex:
        print(f"[{_now()}] invalid JSON in --command: {ex}")
        return 2
    except (ConnectionClosed, OSError) as ex:
        print(f"[{_now()}] websocket connection closed/error: {ex}")
        return 1
    except KeyboardInterrupt:
        print(f"\n[{_now()}] interrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
