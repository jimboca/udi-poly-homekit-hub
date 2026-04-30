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


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _to_json(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, default=str)


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
    elif action == "error":
        parts.append(f"  message        : {msg.get('message')}")
        if "for" in msg:
            parts.append(f"  for            : {msg.get('for')}")
    elif action == "snapshot":
        parts.append(f"  device_id      : {msg.get('device_id')}")
        vals = msg.get("values")
        if isinstance(vals, list):
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
                        status=(
                            f" [status={item.get('status')}]"
                            if "status" in item
                            else ""
                        ),
                    )
                )
    elif action == "list_devices":
        devices = msg.get("devices")
        if isinstance(devices, list):
            parts.append(f"  devices        : {len(devices)}")
            for item in devices:
                if isinstance(item, dict):
                    parts.append(f"    - {item.get('device_id')}")
            if len(devices) == 0:
                parts.append(
                    "  note           : no paired accessories in hub memory (DISCOVER-only targets are not listed). "
                    "If **event** frames show a device_id but this list is empty, use hub **0.1.7+** or send "
                    "**list_devices** again after startup — earlier builds could answer before pairings finished loading."
                )

    if show_raw:
        parts.append("  raw:")
        for line in _to_json(msg).splitlines():
            parts.append(f"    {line}")

    return "\n".join(parts)


async def _run(args: argparse.Namespace) -> None:
    uri = f"ws://{args.host}:{args.port}"
    print(f"[{_now()}] connecting to {uri}")

    async with websockets.connect(
        uri, ping_interval=20, ping_timeout=20, close_timeout=5
    ) as ws:
        hello = {
            "version": PROTOCOL_VERSION,
            "action": "hello",
            "client": args.client_name,
        }
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

        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                print(f"[{_now()}] non-JSON frame:\n{raw}")
                continue
            if args.snapshot_all and msg.get("action") == "list_devices":
                devices = msg.get("devices")
                if isinstance(devices, list):
                    for item in devices:
                        if not isinstance(item, dict):
                            continue
                        did = (item.get("device_id") or "").strip().lower()
                        if not did:
                            continue
                        payload = {
                            "version": PROTOCOL_VERSION,
                            "action": "snapshot",
                            "device_id": did,
                        }
                        await ws.send(json.dumps(payload))
                        print(f"[{_now()}] requested snapshot for {did!r}")
            print(_format_message(msg, show_raw=args.raw))
            print("-" * 72)


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

See PROTOCOL.md for actions: hello, command, snapshot, list_devices, and hub events.
Hub ws_host / ws_port default to 127.0.0.1 and 8163 (Custom Params on the Polyglot node).
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
        "--raw",
        action="store_true",
        help="Also print full pretty JSON for each received message.",
    )
    p.add_argument(
        "--command",
        help="Optional JSON payload to send once after hello (example: '{\"version\":\"1\",\"action\":\"command\",...}')",
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
    return p


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
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
