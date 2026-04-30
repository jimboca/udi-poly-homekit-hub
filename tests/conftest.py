"""Pytest configuration: repo root on ``sys.path``, stub ``udi_interface`` for controller imports."""

from __future__ import annotations

import logging
import sys
import types
from pathlib import Path

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
