"""homekit_hub.config_debug unit tests."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from homekit_hub import DATA_KEY_LAST_HAP_DISCOVER, TYPED_PAIRING_SLOTS_KEY
from homekit_hub.config_debug import (
    build_config_debug_snapshot,
    export_config_debug,
    format_config_debug_text,
    redact_value,
    write_config_debug_file,
)
from homekit_hub.paths import config_debug_path, ensure_persistent_dir


class _Store(dict):
    def keys(self):
        return super().keys()


def _mock_controller(**overrides):
    ctrl = MagicMock()
    ctrl.ready = True
    ctrl.change_node_names = True
    ctrl.handler_params_st = True
    ctrl.handler_data_st = True
    ctrl.handler_typedparams_st = True
    ctrl.handler_typed_data_st = True
    ctrl.handler_config_done_st = True
    ctrl.mainloop = object()
    ctrl.edition = 'professional'
    ctrl.Params = _Store(mqtt_enable='true', mqtt_password='secret123')
    ctrl.TypedParams = _Store(
        pairing_slots={
            'columns': [{'id': 'hap_pin', 'label': 'HomeKit pairing code'}],
        }
    )
    ctrl.TypedData = _Store(
        pairing_slots=[
            {
                'slot': 1,
                'hap_pin': '111-22-333',
                'accessory_id': 'aa:bb:cc:dd:ee:ff',
                'accessory_name': 'Ecobee',
                'generic_nodes': 'true',
            }
        ]
    )
    ctrl.Data = _Store(
        homekit_pairings={
            '1': {'AccessoryPairingID': 'AA:BB:CC:DD:EE:FF', 'token': 'x'},
        },
        **{DATA_KEY_LAST_HAP_DISCOVER: [{'id': 'aa:bb', 'name': 'Test', 'paired': 'false'}]},
    )
    ctrl._bridge_get_params.return_value = {
        'mqtt_enable': 'true',
        'mqtt_host': '127.0.0.1',
        'mqtt_password': 'bridge-secret',
    }
    ctrl._paired_slots_from_data.return_value = {1: 'aa:bb:cc:dd:ee:ff'}
    ctrl._current_paired_ids_from_data.return_value = ['aa:bb:cc:dd:ee:ff']
    ctrl.is_professional.return_value = True
    ctrl.getDriver.side_effect = lambda name: {'ST': 1, 'GV0': 1, 'GV1': 0, 'ERR': 0}.get(name)
    ctrl.poly.serverdata = {'version': '2.0.1'}
    for key, val in overrides.items():
        setattr(ctrl, key, val)
    return ctrl


def test_redact_value_masks_sensitive_keys():
    assert redact_value('mqtt_password', 'secret') == '(set)'
    assert redact_value('hap_pin', '') == '(empty)'
    assert redact_value('hap_pin', '111-22-333') == '(set)'
    assert redact_value('mqtt_host', '127.0.0.1') == '127.0.0.1'


def test_build_config_debug_snapshot_redacts_and_summarizes():
    snap = build_config_debug_snapshot(_mock_controller(), reason='test')
    assert snap['reason'] == 'test'
    assert snap['custom_params']['mqtt_password'] == '(set)'
    rows = snap['custom_typed_data'][TYPED_PAIRING_SLOTS_KEY]
    assert rows[0]['hap_pin'] == '(set)'
    assert 'homekit_pairings_summary' in snap['custom_data']
    assert snap['runtime']['paired_slots'] == {1: 'aa:bb:cc:dd:ee:ff'}


def test_format_config_debug_text_includes_sections():
    snap = build_config_debug_snapshot(_mock_controller(), reason='unit')
    text = format_config_debug_text(snap)
    assert 'HomeKit Hub configuration snapshot' in text
    assert '--- Custom configuration parameters ---' in text
    assert '--- Custom typed parameters (schema) ---' in text
    assert '--- Custom typed data ---' in text
    assert '--- Custom data ---' in text
    assert 'HomeKit pairing code = (set)' in text
    assert '111-22-333' not in text
    assert 'secret123' not in text


def test_write_config_debug_file_creates_persistent_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ensure_persistent_dir()
    path = write_config_debug_file('snapshot line 1\n')
    assert path == config_debug_path()
    assert path.read_text(encoding='utf-8') == 'snapshot line 1\n'


def test_export_config_debug_writes_and_logs(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    ctrl = _mock_controller()
    log = logging.getLogger('test.config_debug')
    with caplog.at_level(logging.INFO, logger='test.config_debug'):
        path = export_config_debug(ctrl, reason='export', log=log)
    assert path is not None
    assert path.exists()
    content = path.read_text(encoding='utf-8')
    assert 'Reason: export' in content
    assert any('CONFIG HomeKit Hub configuration snapshot' in r.message for r in caplog.records)
    assert any(r.message.startswith('CONFIG ') for r in caplog.records)
    assert '111-22-333' not in content
