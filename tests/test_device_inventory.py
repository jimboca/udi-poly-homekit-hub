"""homekit_hub.device_inventory unit tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from homekit_hub.device_classifier import classify_accessories
from homekit_hub.device_inventory import build_device_inventory, export_device_inventory
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


def test_classify_empty_accessories():
    assert classify_accessories([]) == []
