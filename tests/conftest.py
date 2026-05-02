"""Pytest configuration: repo root on ``sys.path``, stub ``udi_interface`` for controller imports."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import types
from pathlib import Path

import pytest
import websockets

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

if "udi_interface" not in sys.modules:
    _udi = types.ModuleType("udi_interface")
    _udi.LOGGER = logging.getLogger("udi_interface_stub")

    class _Custom:
        def __init__(self, *args, **kwargs):
            pass

    class _Node:
        def __init__(self, *args, **kwargs):
            pass

    _udi.Custom = _Custom
    _udi.Node = _Node
    sys.modules["udi_interface"] = _udi


def _integration_env_disabled() -> bool:
    v = os.environ.get("HOMEKIT_WS_INTEGRATION", "").strip().lower()
    return v in ("0", "no", "false", "skip")


def _integration_env_required() -> bool:
    v = os.environ.get("HOMEKIT_WS_INTEGRATION", "").strip().lower()
    return v in ("1", "true", "yes")


async def _probe_hub(uri: str, token: str) -> bool:
    try:
        async with websockets.connect(
            uri,
            open_timeout=4,
            close_timeout=2,
            ping_interval=20,
            ping_timeout=20,
        ) as ws:
            hello: dict = {"version": "1", "action": "hello", "client": "pytest-hub-probe"}
            if token:
                hello["token"] = token
            await ws.send(json.dumps(hello))
            raw = await asyncio.wait_for(ws.recv(), timeout=6)
            msg = json.loads(raw)
            return msg.get("action") == "ack" and msg.get("for") == "hello"
    except Exception:
        return False


@pytest.fixture(scope="session")
def live_hub():
    """Reachable hub endpoint for ``@pytest.mark.integration`` WebSocket tests.

    Skip/fail rules:

    - ``HOMEKIT_WS_INTEGRATION`` in ``0|no|false|skip`` → skip all live tests.
    - Hub hello/ack probe fails and env **not** required → skip.
    - Probe fails and ``HOMEKIT_WS_INTEGRATION`` in ``1|true|yes`` → fail fast.

    Env: ``HOMEKIT_WS_HOST``, ``HOMEKIT_WS_PORT`` (8163), optional ``HOMEKIT_WS_TOKEN``.
    """
    if _integration_env_disabled():
        pytest.skip("Live WebSocket tests disabled (HOMEKIT_WS_INTEGRATION).")

    host = os.environ.get("HOMEKIT_WS_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.environ.get("HOMEKIT_WS_PORT", "8163"))
    token = os.environ.get("HOMEKIT_WS_TOKEN", "").strip()
    uri = f"ws://{host}:{port}"

    ok = asyncio.run(_probe_hub(uri, token))
    if not ok:
        if _integration_env_required():
            pytest.fail(
                f"HOMEKIT_WS_INTEGRATION requires a reachable hub at {uri} (hello/ack probe failed)."
            )
        pytest.skip(f"hub not reachable at {uri} (start plugin; or set HOMEKIT_WS_INTEGRATION=1 to fail if down).")

    return {"uri": uri, "host": host, "port": port, "token": token}
