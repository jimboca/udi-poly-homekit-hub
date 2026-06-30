"""Human-readable configuration snapshots for support / debugging."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from homekit_hub import DATA_KEY_LAST_HAP_DISCOVER, TYPED_PAIRING_SLOTS_KEY
from homekit_hub.paths import config_debug_path, ensure_persistent_dir

_LOG = logging.getLogger(__name__)

_SENSITIVE_KEY_FRAGMENTS = (
    'password',
    'token',
    'hap_pin',
    'secret',
)

_PAIRING_ROW_LABELS: dict[str, str] = {
    'slot': 'Slot number',
    'hap_pin': 'HomeKit pairing code',
    'accessory_id': 'Accessory id',
    'accessory_name': 'Accessory name',
    'discover_endpoint': 'LAN host:port (last DISCOVER)',
    'node_key': 'Stable node key',
    'generic_nodes': 'Create generic IoX control nodes',
}


def _is_sensitive_key(key: str) -> bool:
    k = str(key or '').strip().lower()
    return any(frag in k for frag in _SENSITIVE_KEY_FRAGMENTS)


def redact_value(key: str, value: Any) -> Any:
    """Return a log-safe representation (pairing codes and secrets are never echoed)."""
    if value is None:
        return None
    if _is_sensitive_key(key):
        if isinstance(value, str):
            return '(set)' if value.strip() else '(empty)'
        return '(redacted)'
    if isinstance(value, dict):
        return {str(k): redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_nested(item) for item in value]
    return value


def redact_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_nested(item) for item in value]
    return value


def _custom_store_dict(store: Any) -> dict[str, Any]:
    if store is None:
        return {}
    try:
        keys = store.keys()
    except Exception:
        return {}
    out: dict[str, Any] = {}
    for key in keys:
        try:
            out[str(key)] = store[key]
        except Exception:
            continue
    return out


def _summarize_custom_data(data: Mapping[str, Any]) -> dict[str, Any]:
    out = redact_nested(dict(data))
    pairings = data.get('homekit_pairings')
    if isinstance(pairings, dict):
        slots: list[dict[str, Any]] = []
        for raw_slot, item in sorted(pairings.items(), key=lambda kv: str(kv[0])):
            if not isinstance(item, dict):
                continue
            slots.append(
                {
                    'slot': raw_slot,
                    'AccessoryPairingID': str(item.get('AccessoryPairingID') or '').strip().lower(),
                    'keys': sorted(str(k) for k in item.keys()),
                }
            )
        out['homekit_pairings_summary'] = slots
    discover = data.get(DATA_KEY_LAST_HAP_DISCOVER)
    if isinstance(discover, list):
        out[f'{DATA_KEY_LAST_HAP_DISCOVER}_count'] = len(discover)
        preview: list[dict[str, str]] = []
        for row in discover[:12]:
            if not isinstance(row, dict):
                continue
            preview.append(
                {
                    'id': str(row.get('id') or '').strip(),
                    'name': str(row.get('name') or '').strip(),
                    'paired': str(row.get('paired') or ''),
                }
            )
        out[f'{DATA_KEY_LAST_HAP_DISCOVER}_preview'] = preview
        if len(discover) > len(preview):
            out[f'{DATA_KEY_LAST_HAP_DISCOVER}_truncated'] = len(discover) - len(preview)
    return out


def build_config_debug_snapshot(controller: Any, *, reason: str) -> dict[str, Any]:
    """Collect custom params/data/typed state and runtime flags from the controller."""
    bridge_params = {}
    if hasattr(controller, '_bridge_get_params'):
        try:
            bridge_params = dict(controller._bridge_get_params() or {})
        except Exception:
            _LOG.debug('bridge_get_params for config debug failed', exc_info=True)

    custom_params = _custom_store_dict(getattr(controller, 'Params', None))
    merged_params = dict(custom_params)
    for key, val in bridge_params.items():
        merged_params.setdefault(key, val)
    for key, val in bridge_params.items():
        merged_params[key] = val

    typed_data = _custom_store_dict(getattr(controller, 'TypedData', None))
    typed_params = _custom_store_dict(getattr(controller, 'TypedParams', None))
    custom_data = _custom_store_dict(getattr(controller, 'Data', None))

    runtime: dict[str, Any] = {
        'ready': bool(getattr(controller, 'ready', False)),
        'change_node_names': bool(getattr(controller, 'change_node_names', True)),
        'handler_customparams': getattr(controller, 'handler_params_st', None),
        'handler_customdata': getattr(controller, 'handler_data_st', None),
        'handler_customtypedparams': getattr(controller, 'handler_typedparams_st', None),
        'handler_customtypeddata': getattr(controller, 'handler_typed_data_st', None),
        'handler_configdone': getattr(controller, 'handler_config_done_st', None),
        'bridge_loop_running': getattr(controller, 'mainloop', None) is not None,
    }
    try:
        runtime['driver_ST'] = controller.getDriver('ST')
        runtime['driver_GV0'] = controller.getDriver('GV0')
        runtime['driver_GV1'] = controller.getDriver('GV1')
        runtime['driver_ERR'] = controller.getDriver('ERR')
    except Exception:
        pass

    if hasattr(controller, 'is_professional'):
        try:
            runtime['edition'] = str(getattr(controller, 'edition', '') or '')
            runtime['is_professional'] = bool(controller.is_professional())
        except Exception:
            pass

    if hasattr(controller, '_paired_slots_from_data'):
        try:
            runtime['paired_slots'] = dict(controller._paired_slots_from_data())
        except Exception:
            pass

    if hasattr(controller, '_current_paired_ids_from_data'):
        try:
            runtime['paired_device_ids'] = sorted(controller._current_paired_ids_from_data())
        except Exception:
            pass

    plugin_version = ''
    try:
        from nodes import VERSION as plugin_version  # noqa: WPS433
    except Exception:
        plugin_version = ''

    poly = getattr(controller, 'poly', None)
    serverdata = getattr(poly, 'serverdata', None) if poly is not None else None
    profile_version = ''
    if isinstance(serverdata, dict):
        profile_version = str(serverdata.get('version') or '')

    return {
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'reason': str(reason or 'update'),
        'plugin_version': plugin_version,
        'profile_version': profile_version,
        'runtime': runtime,
        'custom_params': redact_nested(merged_params),
        'custom_params_raw_keys': sorted(merged_params.keys()),
        'custom_typed_params': redact_nested(typed_params),
        'custom_typed_data': redact_nested(typed_data),
        'custom_data': _summarize_custom_data(custom_data),
    }


def _format_scalar(value: Any) -> str:
    if value is None:
        return '(none)'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value if value else '(empty)'
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _format_mapping_block(title: str, mapping: Mapping[str, Any], *, indent: str = '') -> list[str]:
    lines = [title]
    if not mapping:
        lines.append(f'{indent}(empty)')
        return lines
    width = max(len(str(k)) for k in mapping.keys()) if mapping else 0
    for key in sorted(mapping.keys(), key=lambda k: str(k).lower()):
        val = mapping[key]
        if isinstance(val, (dict, list)):
            lines.append(f'{indent}{key}:')
            lines.extend(_format_nested(val, indent=indent + '  '))
            continue
        lines.append(f'{indent}{str(key).ljust(width)} = {_format_scalar(val)}')
    return lines


def _format_nested(value: Any, *, indent: str = '') -> list[str]:
    lines: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            label = _PAIRING_ROW_LABELS.get(str(key), str(key))
            if isinstance(item, (dict, list)):
                lines.append(f'{indent}{label}:')
                lines.extend(_format_nested(item, indent=indent + '  '))
            else:
                lines.append(f'{indent}{label} = {_format_scalar(item)}')
        return lines
    if isinstance(value, list):
        if not value:
            lines.append(f'{indent}(empty list)')
            return lines
        for idx, item in enumerate(value, start=1):
            if isinstance(item, dict):
                lines.append(f'{indent}[{idx}]')
                lines.extend(_format_nested(item, indent=indent + '  '))
            else:
                lines.append(f'{indent}[{idx}] {_format_scalar(item)}')
        return lines
    lines.append(f'{indent}{_format_scalar(value)}')
    return lines


def format_config_debug_text(snapshot: Mapping[str, Any]) -> str:
    """Render a support-friendly plain-text configuration report."""
    sep = '=' * 78
    lines: list[str] = [
        sep,
        'HomeKit Hub configuration snapshot',
        sep,
        f"Generated (UTC): {snapshot.get('generated_at_utc', '')}",
        f"Reason: {snapshot.get('reason', '')}",
        f"Plugin version: {snapshot.get('plugin_version', '')}",
        f"Profile version: {snapshot.get('profile_version', '')}",
        '',
        '--- Runtime ---',
    ]
    runtime = snapshot.get('runtime')
    if isinstance(runtime, dict):
        lines.extend(_format_mapping_block('', runtime)[1:])
    else:
        lines.append('(unavailable)')

    lines.extend(['', '--- Custom configuration parameters ---'])
    custom_params = snapshot.get('custom_params')
    if isinstance(custom_params, dict):
        lines.extend(_format_mapping_block('', custom_params)[1:])
    else:
        lines.append('(unavailable)')

    lines.extend(['', '--- Custom typed parameters (schema) ---'])
    typed_params = snapshot.get('custom_typed_params')
    if isinstance(typed_params, dict) and typed_params:
        lines.extend(_format_nested(typed_params, indent=''))
    else:
        lines.append('(empty)')

    lines.extend(['', '--- Custom typed data ---'])
    typed_data = snapshot.get('custom_typed_data')
    if isinstance(typed_data, dict) and typed_data:
        pairing_rows = typed_data.get(TYPED_PAIRING_SLOTS_KEY)
        if isinstance(pairing_rows, list):
            lines.append(f'{TYPED_PAIRING_SLOTS_KEY}: {len(pairing_rows)} row(s)')
            for idx, row in enumerate(pairing_rows, start=1):
                lines.append(f'  Row {idx}')
                if isinstance(row, dict):
                    lines.extend(_format_nested(row, indent='    '))
                else:
                    lines.append(f'    {_format_scalar(row)}')
            other = {k: v for k, v in typed_data.items() if k != TYPED_PAIRING_SLOTS_KEY}
            if other:
                lines.append('Other typed data keys:')
                lines.extend(_format_nested(other, indent='  '))
        else:
            lines.extend(_format_nested(typed_data, indent=''))
    else:
        lines.append('(empty)')

    lines.extend(['', '--- Custom data ---'])
    custom_data = snapshot.get('custom_data')
    if isinstance(custom_data, dict) and custom_data:
        lines.extend(_format_nested(custom_data, indent=''))
    else:
        lines.append('(empty)')

    lines.extend(['', sep, 'End of configuration snapshot', sep, ''])
    return '\n'.join(lines)


def write_config_debug_file(text: str) -> Path:
    ensure_persistent_dir()
    path = config_debug_path()
    path.write_text(text, encoding='utf-8')
    return path


def export_config_debug(
    controller: Any,
    *,
    reason: str,
    log: Optional[logging.Logger] = None,
) -> Optional[Path]:
    """
    Build a redacted configuration snapshot, write ``persistent/hub_config_debug.txt``,
    and emit the same text to the Node Server log.
    """
    lg = log or _LOG
    try:
        snapshot = build_config_debug_snapshot(controller, reason=reason)
        text = format_config_debug_text(snapshot)
        path = write_config_debug_file(text)
        lg.info('HomeKit Hub configuration snapshot (%s) written to %s', reason, path)
        for line in text.splitlines():
            lg.info('CONFIG %s', line)
        return path
    except Exception:
        lg.exception('HomeKit Hub configuration snapshot export failed (%s)', reason)
        return None
