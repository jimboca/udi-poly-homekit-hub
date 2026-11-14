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
    accessory_name: str | None,
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


_THERMOSTAT_TITLE_ROLES = frozenset(
    {'thermostat', 'sensor', 'motion_sensor', 'binary_sensor'}
)


def choose_accessory_display_name(
    manufacturer_name: str | None,
    configured_name: str | None,
) -> tuple[str | None, bool]:
    """Prefer Home's Configured Name only when it differs from manufacturer Name.

    The manufacturer Name on a Honeywell T9 is the model (``T9 Thermostat``).
    Configured Name is the name the user assigned in the Home app.
    """
    manufacturer = str(manufacturer_name or '').strip() or None
    configured = str(configured_name or '').strip() or None
    if configured and configured != manufacturer:
        return configured, True
    return manufacturer, False


def keep_existing_thermostat_iox_title(
    proposed: str,
    *,
    role: str,
    accessory_name_configured: bool,
    existing_name: str | None,
) -> str:
    """Keep an IoX thermostat title when HomeKit only has the manufacturer Name.

    A Configured Name still replaces the title. Lights and other controls are
    unchanged. A node with no IoX name yet keeps ``proposed``.
    """
    proposed_title = str(proposed or '').strip()
    existing = str(existing_name or '').strip()
    role_name = str(role or '').strip().lower()
    if (
        role_name in _THERMOSTAT_TITLE_ROLES
        and not accessory_name_configured
        and existing
        and existing != proposed_title
    ):
        return existing
    return proposed_title


def generic_node_title(
    display_name: str,
    role: str,
    *,
    sibling_count: int,
    accessory_name: str | None = None,
) -> str:
    """IoX title for a generic control child.

    A bridged accessory keeps its HomeKit name. A single-role pairing (one
    thermostat) keeps the pairing display name when no accessory name is known.
    """
    acc = str(accessory_name or '').strip()
    if acc:
        if len(acc) > 80:
            return acc[:77] + '...'
        return acc
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
