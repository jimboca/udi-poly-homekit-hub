"""homekit_hub.device_inventory unit tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from homekit_hub.device_classifier import classify_accessories
from homekit_hub.device_inventory import (
    build_device_inventory,
    export_device_inventory,
    log_all_persistent_inventories,
)
from homekit_hub.paths import ensure_persistent_dir, inventory_json_path


class _Char:
    def __init__(self, iid: int, type_uuid: str, value=None):
        self.iid = iid
        self.type = type_uuid
        self.value = value
        self.perms = ['pr']
        self.format = 'bool'


class _Svc:
    def __init__(self, iid: int, type_uuid: str, chars):
        self.iid = iid
        self.type = type_uuid
        self.characteristics = chars


class _Acc:
    def __init__(self, aid: int, services):
        self.aid = aid
        self.services = services


def test_inventory_json_path_sanitizes_colons(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = inventory_json_path('aa:bb:cc:dd:ee:ff')
    assert p.name == 'aa_bb_cc_dd_ee_ff.json'
    assert p.parent == Path('persistent')


def test_ensure_persistent_dir_creates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    d = ensure_persistent_dir()
    assert d.is_dir()


def test_build_device_inventory_shape():
    pairing = MagicMock()
    pairing.accessories = [
        _Acc(
            1,
            [
                _Svc(
                    10,
                    '00000096-0000-1000-8000-0026BB765291',
                    [_Char(1, '00000025-0000-1000-8000-0026BB765291', True)],
                )
            ],
        )
    ]
    payload = build_device_inventory(
        device_id='aa:bb:cc:dd:ee:ff',
        alias='slot_1',
        pairing=pairing,
        reason='pairing_active',
    )
    assert payload['schema_version'] == 1
    assert payload['device_id'] == 'aa:bb:cc:dd:ee:ff'
    assert payload['reason'] == 'pairing_active'
    assert 'accessories' in payload
    assert isinstance(payload['detected_roles'], list)
    assert 'plugin_hints' in payload


def test_export_device_inventory_writes_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pairing = MagicMock()
    pairing.accessories = []
    path = export_device_inventory(
        device_id='11:22:33:44:55:66',
        alias='slot_2',
        pairing=pairing,
        reason='health_recovered',
    )
    assert path.is_file()
    text = path.read_text(encoding='utf-8')
    assert 'health_recovered' in text


def test_log_device_inventory_export_emits_inventory_lines(tmp_path, monkeypatch, caplog):
    import logging

    monkeypatch.chdir(tmp_path)
    pairing = MagicMock()
    pairing.accessories = []
    log = logging.getLogger('test.device_inventory')
    with caplog.at_level(logging.INFO, logger='test.device_inventory'):
        path = export_device_inventory(
            device_id='aa:bb:cc:dd:ee:ff',
            alias='slot_1',
            pairing=pairing,
            reason='pairing_active',
            log=log,
        )
    assert path.is_file()
    assert any('INVENTORY begin device_id=aa:bb:cc:dd:ee:ff' in r.message for r in caplog.records)
    assert any(r.message.startswith('INVENTORY ') and '"device_id"' in r.message for r in caplog.records)
    assert any('INVENTORY end device_id=aa:bb:cc:dd:ee:ff' in r.message for r in caplog.records)


def test_log_all_persistent_inventories_reads_json_files(tmp_path, monkeypatch, caplog):
    import logging

    monkeypatch.chdir(tmp_path)
    ensure_persistent_dir()
    inventory_json_path('9e:12:68:6a:6e:26').write_text(
        '{"device_id":"9e:12:68:6a:6e:26","reason":"pairing_active","accessories":[]}',
        encoding='utf-8',
    )
    log = logging.getLogger('test.device_inventory.all')
    with caplog.at_level(logging.INFO, logger='test.device_inventory.all'):
        n = log_all_persistent_inventories(log=log)
    assert n == 1
    assert any('INVENTORY persistent: 1 file(s)' in r.message for r in caplog.records)


def test_classify_empty_accessories():
    assert classify_accessories([]) == []
