"""Live WebSocket tests against a running udi-poly-homekit hub.

These complement mocked tests in ``test_ws_debug_client.py``: they open a real
connection to ``ws://HOST:PORT`` (defaults ``127.0.0.1:8163``), send the same
JSON the hub expects, and assert on responses.

**When they run**

- If ``HOMEKIT_WS_INTEGRATION`` is ``0``, ``no``, ``false``, or ``skip``: tests are skipped.
- If the hub is **reachable** (quick hello/ack probe): tests run.
- If the hub is **not** reachable and ``HOMEKIT_WS_INTEGRATION`` is **unset**: tests are skipped.
- If ``HOMEKIT_WS_INTEGRATION`` is ``1`` / ``true`` / ``yes`` and the hub is **not** reachable: the session **fails** (for CI jobs that must verify the hub).

**Environment**

- ``HOMEKIT_WS_HOST`` (default ``127.0.0.1``)
- ``HOMEKIT_WS_PORT`` (default ``8163``)
- ``HOMEKIT_WS_TOKEN`` optional; sent on ``hello`` when the hub Custom Param ``ws_token`` is set

After deploying plugin code or when ``list_devices`` / snapshots disagree with what accessories are doing,
**restart the PG3 node / plugin** so aiohomekit pairings and the WebSocket hub reload cleanly—otherwise live tests
may skip even though accessories are online.

Run with plugin started locally::

    cd plugins/udi-poly-homekit && python3 -m pytest tests/test_ws_live.py -v

Or run the full suite (live tests skip if hub is down; use ``HOMEKIT_WS_INTEGRATION=1`` to require the hub)::

    HOMEKIT_WS_INTEGRATION=1 python3 -m pytest tests/ -v
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
import websockets

import ws_debug_client as wdc

pytestmark = pytest.mark.integration

PROTO = wdc.PROTOCOL_VERSION


async def _exchange(uri: str, *, token: str = "", after_hello: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Connect, send hello, optional follow-up frames; collect JSON objects until idle timeout or max frames."""
    out: list[dict[str, Any]] = []
    after_hello = after_hello or []
    async with websockets.connect(
        uri,
        open_timeout=5,
        close_timeout=3,
        ping_interval=20,
        ping_timeout=20,
    ) as ws:
        hello: dict[str, Any] = {"version": PROTO, "action": "hello", "client": "pytest-ws-live"}
        if token:
            hello["token"] = token
        await ws.send(json.dumps(hello))
        raw = await asyncio.wait_for(ws.recv(), timeout=8)
        out.append(json.loads(raw))
        for payload in after_hello:
            await ws.send(json.dumps(payload))
        # Drain a few responses (list_devices / error / snapshot may arrive quickly).
        for _ in range(12):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=3)
            except TimeoutError:
                break
            try:
                out.append(json.loads(raw))
            except json.JSONDecodeError:
                pytest.fail(f"non-JSON frame from hub: {raw!r}")
    return out


def test_live_hello_ack(live_hub):
    uri = live_hub["uri"]
    token = live_hub["token"]

    async def run():
        msgs = await _exchange(uri, token=token)
        assert msgs, "expected at least hello ack"
        ack = msgs[0]
        assert ack.get("action") == "ack", ack
        assert ack.get("for") == "hello", ack
        assert ack.get("protocol") == "1", ack
        cap = ack.get("capabilities")
        assert isinstance(cap, dict), ack
        acts = cap.get("actions")
        assert isinstance(acts, list), ack
        assert "snapshot" in acts and "list_devices" in acts, acts

    asyncio.run(run())


def test_live_list_devices_roundtrip(live_hub):
    uri = live_hub["uri"]
    token = live_hub["token"]

    async def run():
        msgs = await _exchange(
            uri,
            token=token,
            after_hello=[{"version": PROTO, "action": "list_devices"}],
        )
        assert len(msgs) >= 2, msgs
        ld = next((m for m in msgs if m.get("action") == "list_devices"), None)
        assert ld is not None, msgs
        assert isinstance(ld.get("devices"), list), ld

    asyncio.run(run())


def test_live_get_unknown_device_error(live_hub):
    uri = live_hub["uri"]
    token = live_hub["token"]

    async def run():
        msgs = await _exchange(
            uri,
            token=token,
            after_hello=[
                {
                    "version": PROTO,
                    "action": "get",
                    "device_id": "00:00:00:00:00:00",
                    "characteristic": "ON",
                }
            ],
        )
        err = next((m for m in msgs if m.get("action") == "error"), None)
        assert err is not None, msgs
        assert isinstance(err.get("message"), str) and err["message"].strip(), err

    asyncio.run(run())


def test_live_subscribe_unknown_device_error(live_hub):
    uri = live_hub["uri"]
    token = live_hub["token"]

    async def run():
        msgs = await _exchange(
            uri,
            token=token,
            after_hello=[
                {
                    "version": PROTO,
                    "action": "subscribe",
                    "device_id": "00:00:00:00:00:00",
                    "aid": 1,
                    "iid": 1,
                }
            ],
        )
        err = next((m for m in msgs if m.get("action") == "error"), None)
        assert err is not None, msgs

    asyncio.run(run())


class _InstrumentConnect:
    """Wrap ``websockets.connect`` so outbound JSON snapshot frames can be recorded."""

    __slots__ = ("_inner", "_snapshots")

    def __init__(self, inner: Any, snapshots: list[dict[str, Any]]) -> None:
        self._inner = inner
        self._snapshots = snapshots

    def __await__(self):  # pragma: no cover - exercised via async with / bridge compat
        return self._inner.__await__()

    async def __aenter__(self):
        ws = await self._inner.__aenter__()
        orig_send = ws.send

        async def send_hook(data: str) -> None:
            try:
                obj = json.loads(data)
                if obj.get("action") == "snapshot":
                    self._snapshots.append(obj)
            except json.JSONDecodeError:
                pass
            await orig_send(data)

        ws.send = send_hook  # type: ignore[method-assign]
        return ws

    async def __aexit__(self, exc_type, exc, tb):
        return await self._inner.__aexit__(exc_type, exc, tb)


def test_live_ws_debug_run_snapshot_all_records_snapshots(live_hub, monkeypatch):
    """Run real :func:`ws_debug_client._run` against the hub (same code path as the CLI).

    Cancel after a short deadline; require at least one outbound ``snapshot`` if traffic allows,
    otherwise skip (quiet accessory / list_devices already enumerated devices).
    """
    from types import SimpleNamespace

    snapshots: list[dict[str, Any]] = []
    orig_connect = websockets.connect

    def wrapping_connect(*args: Any, **kwargs: Any):
        return _InstrumentConnect(orig_connect(*args, **kwargs), snapshots)

    monkeypatch.setattr(wdc.websockets, "connect", wrapping_connect)

    args = SimpleNamespace(
        host=live_hub["host"],
        port=live_hub["port"],
        client_name="pytest-snapshot-all",
        token=live_hub["token"],
        raw=False,
        command=None,
        snapshot_device_id=None,
        snapshot_all=True,
        interactive=False,
        max_messages=None,
        oneshot=False,
    )

    async def instrumented() -> None:
        try:
            await asyncio.wait_for(wdc._run(args), timeout=15.0)
        except asyncio.TimeoutError:
            pass

    asyncio.run(instrumented())

    if not snapshots:
        pytest.skip(
            "No outbound snapshot frames before timeout (no pairings/events yet, hub quiet, or stale hub "
            "process—restart the udi-poly-homekit node on IoX/PG3 and retry)."
        )

    assert any(p.get("device_id") for p in snapshots), snapshots
