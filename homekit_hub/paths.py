"""Persistent storage paths for Professional device inventory export."""

from __future__ import annotations

from pathlib import Path

PERSISTENT_DIR = Path('persistent')


def ensure_persistent_dir() -> Path:
    PERSISTENT_DIR.mkdir(parents=True, exist_ok=True)
    return PERSISTENT_DIR


def inventory_json_path(device_id: str) -> Path:
    safe = str(device_id or '').strip().replace(':', '_')
    return ensure_persistent_dir() / f'{safe}.json'


def config_debug_path() -> Path:
    """Latest human-readable hub configuration snapshot for support."""
    return ensure_persistent_dir() / 'hub_config_debug.txt'
