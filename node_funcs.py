"""IoX node address helpers (PG3 truncates to 20 chars including n###_ prefix)."""

from __future__ import annotations

import hashlib
import re

# Plugin-side address limit; PG3 prepends n{profile}_ (5 chars) before IoX REST calls.
IOX_MAX_NODE_ADDRESS_LEN = 14


def uuid_to_address(uuid: str) -> str:
    return str(uuid or '')[-12:]


def id_to_address(id: str, slen: int = IOX_MAX_NODE_ADDRESS_LEN) -> str:
    digest = hashlib.md5(str(id or '').encode(), usedforsecurity=False).hexdigest()
    return digest[-int(slen) :]


def get_valid_node_name(name: str) -> str:
    name = bytes(str(name or ''), 'utf-8').decode('utf-8', 'ignore')
    return re.sub(r"[<>`~!@#$%^&*(){}[\]?/\\;:\"']+", '', name)


def get_valid_node_address(name: str, max_length: int = IOX_MAX_NODE_ADDRESS_LEN) -> str:
    return get_valid_node_name(name)[: int(max_length)].lower()


def generic_node_address(device_id: str, aid: int, role: str) -> str:
    """Stable short address for a generic IoX child (device + accessory + role)."""
    key = f'{str(device_id or "").strip().lower()}:{int(aid)}:{str(role or "").strip().lower()}'
    return 'g' + id_to_address(key, slen=IOX_MAX_NODE_ADDRESS_LEN - 1)


def append_isy_node_suffix(name: str, suffix: str, max_len: int = 80) -> str:
    base = str(name or '').strip() or 'HK Device'
    if len(base) + len(suffix) > max_len:
        base = base[: max_len - len(suffix)]
    return base + suffix


def paired_slot_node_title(display_name: str, *, generic_nodes_enabled: bool) -> str:
    base = str(display_name or '').strip()
    if not base:
        base = 'HK Device'
    if len(base) > 80:
        base = base[:77] + '...'
    if generic_nodes_enabled:
        return append_isy_node_suffix(base, ' (Pairing)')
    return base


def sensor_node_title(
    display_name: str,
    accessory_name: Optional[str],
    role: str,
) -> str:
    """IoX title for a per-aid sensor child (room sensor or built-in motion)."""
    if str(role or '').strip().lower() == 'motion_sensor':
        base = str(accessory_name or display_name or 'Thermostat').strip() or 'Thermostat'
        if '· motion' in base:
            title = base
        else:
            title = f'{base} · motion'
    else:
        title = str(accessory_name or display_name or 'HK Sensor').strip() or 'HK Sensor'
    if len(title) > 80:
        return title[:77] + '...'
    return title


def generic_node_title(display_name: str, role: str, *, sibling_count: int) -> str:
    """IoX title for a generic control child; single-role devices keep the clean display name."""
    base = str(display_name or '').strip() or 'HK Device'
    if int(sibling_count) <= 1:
        if len(base) > 80:
            return base[:77] + '...'
        return base
    role_label = str(role or 'device').replace('_', ' ').title()
    title = f'{base} {role_label}'
    if len(title) > 80:
        return title[:77] + '...'
    return title


def legacy_generic_node_address(device_id: str, aid: int, role: str) -> str:
    """Pre-1.0.2 long address scheme (IoX-incompatible); used only for migration cleanup."""
    did = str(device_id or '').strip().lower().replace(':', '_')
    return f'hkg_{did}_{int(aid)}_{str(role or "node").strip().lower()}'
