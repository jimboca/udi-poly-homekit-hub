"""Unit tests for ``ws_debug_client`` formatting, parsing, and ``--snapshot-all`` logic."""

from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import ws_debug_client as wdc


@pytest.fixture
def fixed_now(monkeypatch):
    monkeypatch.setattr(wdc, "_now", lambda: "TS")


def test_snapshot_all_empty_list_devices_sets_fallback_once(fixed_now):
    st = wdc._SnapshotAllState()
    out, lines = wdc.snapshot_all_handle_inbound(
        {"version": "1", "action": "list_devices", "devices": []}, st
    )
    assert out == []
    assert len(lines) == 1
    assert "0 devices" in lines[0]
    assert st.snap_event_fallback is True
    assert st.snap_fallback_notice is True


def test_snapshot_all_empty_list_notice_only_once(fixed_now):
    st = wdc._SnapshotAllState()
    _, lines1 = wdc.snapshot_all_handle_inbound(
        {"version": "1", "action": "list_devices", "devices": []}, st
    )
    _, lines2 = wdc.snapshot_all_handle_inbound(
        {"version": "1", "action": "list_devices", "devices": []}, st
    )
    assert len(lines1) == 1
    assert lines2 == []


def test_snapshot_all_event_fallback_requests_snapshot(fixed_now):
    st = wdc._SnapshotAllState()
    wdc.snapshot_all_handle_inbound({"version": "1", "action": "list_devices", "devices": []}, st)
    out, lines = wdc.snapshot_all_handle_inbound(
        {
            "version": "1",
            "action": "event",
            "device_id": "AA:BB:CC:DD:EE:FF",
            "characteristic": "ON",
            "aid": 1,
            "iid": 2,
            "value": 1,
        },
        st,
    )
    assert out == [
        {"version": wdc.PROTOCOL_VERSION, "action": "snapshot", "device_id": "aa:bb:cc:dd:ee:ff"}
    ]
    assert len(lines) == 1
    assert "fallback from event" in lines[0]
    assert "aa:bb:cc:dd:ee:ff" in lines[0]


def test_snapshot_all_populated_list_devices_requests_snapshots_and_clears_fallback(fixed_now):
    st = wdc._SnapshotAllState()
    wdc.snapshot_all_handle_inbound({"version": "1", "action": "list_devices", "devices": []}, st)
    assert st.snap_event_fallback is True

    out, lines = wdc.snapshot_all_handle_inbound(
        {
            "version": "1",
            "action": "list_devices",
            "devices": [
                {"device_id": "11:22:33:44:55:66"},
                {"device_id": "aa:bb:cc:dd:ee:ff"},
            ],
        },
        st,
    )
    assert st.snap_event_fallback is False
    assert [p["device_id"] for p in out] == ["11:22:33:44:55:66", "aa:bb:cc:dd:ee:ff"]
    assert len(lines) == 2
    assert all("requested snapshot for" in ln for ln in lines)


def test_snapshot_all_event_dedupes_after_list_snapshot(fixed_now):
    st = wdc._SnapshotAllState()
    wdc.snapshot_all_handle_inbound(
        {
            "version": "1",
            "action": "list_devices",
            "devices": [{"device_id": "aa:bb:cc:dd:ee:ff"}],
        },
        st,
    )
    out, lines = wdc.snapshot_all_handle_inbound(
        {
            "version": "1",
            "action": "event",
            "device_id": "aa:bb:cc:dd:ee:ff",
            "characteristic": "ON",
            "aid": 1,
            "iid": 2,
            "value": 0,
        },
        st,
    )
    assert out == []
    assert lines == []


def test_snapshot_all_skips_non_dict_device_rows(fixed_now):
    st = wdc._SnapshotAllState()
    out, lines = wdc.snapshot_all_handle_inbound(
        {
            "version": "1",
            "action": "list_devices",
            "devices": ["bad", {"device_id": "aa:bb:cc:dd:ee:ff"}, None],
        },
        st,
    )
    assert out == [{"version": "1", "action": "snapshot", "device_id": "aa:bb:cc:dd:ee:ff"}]
    assert len(lines) == 1


def test_snapshot_all_ignores_list_devices_when_devices_not_a_list(fixed_now):
    st = wdc._SnapshotAllState()
    out, lines = wdc.snapshot_all_handle_inbound(
        {"version": "1", "action": "list_devices", "devices": None}, st
    )
    assert out == []
    assert lines == []


@pytest.mark.parametrize(
    ("action", "extra"),
    [
        (
            "event",
            {
                "device_id": "aa:bb:cc:dd:ee:ff",
                "characteristic": "ON",
                "aid": 1,
                "iid": 9,
                "value": True,
            },
        ),
        ("error", {"message": "oops", "for": "get"}),
        (
            "snapshot",
            {"device_id": "aa:bb:cc:dd:ee:ff", "values": [{"characteristic": "ON", "aid": 1, "iid": 2, "value": 0}]},
        ),
    ],
)
def test_format_message_smoke(action, extra):
    msg = {"version": "1", "action": action, **extra}
    text = wdc._format_message(msg, show_raw=False)
    assert action in text
    assert len(text) > 20


@pytest.mark.parametrize(
    ("line", "should_quit", "payload"),
    [
        ("", False, None),
        ("/quit", True, None),
        ("/list", False, {"version": wdc.PROTOCOL_VERSION, "action": "list_devices"}),
        (
            "/snapshot AA:BB:CC:DD:EE:FF",
            False,
            {"version": wdc.PROTOCOL_VERSION, "action": "snapshot", "device_id": "aa:bb:cc:dd:ee:ff"},
        ),
        (
            "/get aa:bb:cc:dd:ee:ff ON",
            False,
            {
                "version": wdc.PROTOCOL_VERSION,
                "action": "get",
                "device_id": "aa:bb:cc:dd:ee:ff",
                "characteristic": "ON",
            },
        ),
        (
            '{"version":"1","action":"ping"}',
            False,
            {"version": "1", "action": "ping"},
        ),
    ],
)
def test_interactive_command_to_payload_ok(line, should_quit, payload):
    q, p = wdc._interactive_command_to_payload(line)
    assert q is should_quit
    assert p == payload


def test_run_respects_max_messages(fixed_now):
    inbox = [
        json.dumps({"version": "1", "action": "ack", "for": "hello", "protocol": "1"}),
        json.dumps({"version": "1", "action": "event", "device_id": "aa:bb:cc:dd:ee:ff"}),
        json.dumps({"version": "1", "action": "event", "device_id": "aa:bb:cc:dd:ee:ff"}),
    ]

    class FakeWS:
        def __init__(self) -> None:
            self._pending = list(inbox)

        async def send(self, data: str) -> None:
            pass

        def __aiter__(self):
            return self

        async def __anext__(self) -> str:
            if not self._pending:
                raise StopAsyncIteration
            return self._pending.pop(0)

    ws = FakeWS()

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def cm(*a, **k):
        yield ws

    args = SimpleNamespace(
        host="127.0.0.1",
        port=8163,
        client_name="t",
        token="",
        raw=False,
        command=None,
        snapshot_device_id=None,
        snapshot_all=False,
        interactive=False,
        max_messages=2,
        oneshot=False,
    )

    async def run_client() -> None:
        await wdc._run(args)

    with patch.object(wdc.websockets, "connect", cm):
        asyncio.run(run_client())

    assert len(ws._pending) == 1


def test_main_max_messages_invalid_returns_2():
    with patch.object(sys, "argv", ["ws_debug_client.py", "--max-messages", "0"]):
        assert wdc.main() == 2


def test_main_invalid_command_json_returns_2():
    with patch.object(sys, "argv", ["ws_debug_client.py"]):
        with patch.object(wdc.asyncio, "run", side_effect=json.JSONDecodeError("msg", "doc", 0)):
            rc = wdc.main()
    assert rc == 2


def test_run_snapshot_all_sends_ws_payloads(fixed_now):
    inbox = [
        json.dumps({"version": "1", "action": "list_devices", "devices": []}),
        json.dumps(
            {
                "version": "1",
                "action": "event",
                "device_id": "aa:bb:cc:dd:ee:ff",
                "characteristic": "ON",
                "aid": 1,
                "iid": 2,
                "value": 1,
            }
        ),
    ]

    class FakeWS:
        def __init__(self) -> None:
            self.sent: list[str] = []
            self._pending = list(inbox)

        async def send(self, data: str) -> None:
            self.sent.append(data)

        def __aiter__(self):
            return self

        async def __anext__(self) -> str:
            if not self._pending:
                raise StopAsyncIteration
            return self._pending.pop(0)

    ws = FakeWS()

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def cm(*a, **k):
        yield ws

    args = SimpleNamespace(
        host="127.0.0.1",
        port=8163,
        client_name="t",
        token="",
        raw=False,
        command=None,
        snapshot_device_id=None,
        snapshot_all=True,
        interactive=False,
        max_messages=None,
        oneshot=False,
    )

    async def run_client() -> None:
        await wdc._run(args)

    with patch.object(wdc.websockets, "connect", cm):
        asyncio.run(run_client())

    snapshot_frames = [json.loads(x) for x in ws.sent if '"action": "snapshot"' in x]
    assert any(
        p == {"version": "1", "action": "snapshot", "device_id": "aa:bb:cc:dd:ee:ff"}
        for p in snapshot_frames
    )


def test_run_with_command_json_sent_after_hello():
    class EmptyRecvWS:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send(self, data: str) -> None:
            self.sent.append(data)

        def __aiter__(self):
            return self

        async def __anext__(self) -> str:
            raise StopAsyncIteration

    ws = EmptyRecvWS()

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def cm(*a, **k):
        yield ws

    args = SimpleNamespace(
        host="127.0.0.1",
        port=8163,
        client_name="t",
        token="",
        raw=False,
        command='{"version":"1","action":"list_devices"}',
        snapshot_device_id=None,
        snapshot_all=False,
        interactive=False,
        max_messages=None,
        oneshot=False,
    )

    async def run_client() -> None:
        await wdc._run(args)

    with patch.object(wdc.websockets, "connect", cm):
        asyncio.run(run_client())

    bodies = [json.loads(x) for x in ws.sent]
    assert bodies[0]["action"] == "hello"
    assert bodies[1]["action"] == "list_devices"
