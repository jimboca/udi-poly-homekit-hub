"""homekit_hub.device_classifier sensor classification tests."""

from __future__ import annotations

from aiohomekit.model.characteristics import CharacteristicsTypes
from aiohomekit.model.services.service_types import ServicesTypes

from homekit_hub.device_classifier import classify_accessories, classify_sensor_aids

class _Char:
    def __init__(self, iid: int, type_uuid: str, value=None):
        self.iid = iid
        self.type = type_uuid
        self.value = value


class _Svc:
    def __init__(self, iid: int, type_uuid: str, chars):
        self.iid = iid
        self.type = type_uuid
        self.characteristics = chars


class _Acc:
    def __init__(self, aid: int, services):
        self.aid = aid
        self.services = services


def _ecobee_fixture():
    """Thermostat aid=2, room sensors aid=3 and aid=4, motion on primary aid."""
    thermostat = _Acc(
        2,
        [
            _Svc(
                1,
                ServicesTypes.ACCESSORY_INFORMATION,
                [_Char(1, CharacteristicsTypes.NAME, 'Downstairs')],
            ),
                _Svc(
                    10,
                    ServicesTypes.THERMOSTAT,
                    [
                        _Char(11, CharacteristicsTypes.TEMPERATURE_CURRENT),
                        _Char(12, CharacteristicsTypes.RELATIVE_HUMIDITY_CURRENT),
                        _Char(13, CharacteristicsTypes.HEATING_COOLING_TARGET),
                        _Char(14, CharacteristicsTypes.MOTION_DETECTED),
                    ],
                ),
        ],
    )
    bedroom = _Acc(
        3,
        [
            _Svc(1, ServicesTypes.ACCESSORY_INFORMATION, [_Char(1, CharacteristicsTypes.NAME, 'Master Bedroom')]),
                _Svc(
                    20,
                    ServicesTypes.TEMPERATURE_SENSOR,
                    [
                        _Char(21, CharacteristicsTypes.TEMPERATURE_CURRENT),
                        _Char(22, CharacteristicsTypes.RELATIVE_HUMIDITY_CURRENT),
                        _Char(23, CharacteristicsTypes.BATTERY_LEVEL),
                    ],
                ),
        ],
    )
    kitchen = _Acc(
        4,
        [
            _Svc(1, ServicesTypes.ACCESSORY_INFORMATION, [_Char(1, CharacteristicsTypes.NAME, 'Kitchen')]),
                _Svc(
                    30,
                    ServicesTypes.TEMPERATURE_SENSOR,
                    [
                        _Char(31, CharacteristicsTypes.TEMPERATURE_CURRENT),
                        _Char(32, CharacteristicsTypes.RELATIVE_HUMIDITY_CURRENT),
                    ],
                ),
        ],
    )
    return [thermostat, bedroom, kitchen]


def test_classify_accessories_no_per_service_binary_sensor_rows():
    rows = classify_accessories(_ecobee_fixture())
    roles = {r['role'] for r in rows}
    assert 'binary_sensor' not in roles
    assert 'thermostat' in roles


def test_classify_sensor_aids_emits_room_sensors_and_motion_child():
    accessories = _ecobee_fixture()
    rows = classify_sensor_aids(accessories, control_aid=2)
    sensor_rows = [r for r in rows if r['role'] == 'sensor']
    assert len(sensor_rows) == 2
    assert sensor_rows[0]['node_def_id'] == 'HKHubSensor'
    assert {r['aid'] for r in sensor_rows} == {3, 4}
    motion = next(r for r in rows if r['role'] == 'motion_sensor')
    assert motion['aid'] == 2
    assert motion['node_def_id'] == 'HKHubMotionSensor'
    assert 'MOTION_DETECTED' in motion['char_bindings']


def test_classify_sensor_aids_dry_room_sensor():
    dry = _Acc(
        5,
        [
            _Svc(1, ServicesTypes.ACCESSORY_INFORMATION, [_Char(1, CharacteristicsTypes.NAME, 'Foyer')]),
            _Svc(
                40,
                ServicesTypes.TEMPERATURE_SENSOR,
                [
                    _Char(41, CharacteristicsTypes.TEMPERATURE_CURRENT),
                    _Char(42, CharacteristicsTypes.BATTERY_LEVEL),
                ],
            ),
        ],
    )
    rows = classify_sensor_aids([dry], control_aid=2)
    sensor = next(r for r in rows if r['role'] == 'sensor')
    assert sensor['node_def_id'] == 'HKHubSensorDry'
    assert 'RELATIVE_HUMIDITY' not in sensor['char_bindings']


def test_classify_sensor_aids_uses_snapshot_control_aid():
    accessories = _ecobee_fixture()
    snapshot = [
        {
            'aid': 4,
            'iid': 1,
            'characteristic': CharacteristicsTypes.HEATING_COOLING_TARGET,
            'value': 3,
        },
    ]
    rows = classify_sensor_aids(accessories, snapshot_values=snapshot)
    sensor_aids = {r['aid'] for r in rows if r['role'] == 'sensor'}
    assert 4 not in sensor_aids
    assert sensor_aids == {2, 3}


def test_classify_accessories_accepts_characteristic_name_strings():
    thermostat = _Acc(
        1,
        [
            _Svc(
                10,
                ServicesTypes.THERMOSTAT,
                [
                    _Char(11, 'TEMPERATURE_CURRENT'),
                    _Char(12, 'HEATING_COOLING_TARGET'),
                ],
            ),
        ],
    )
    rows = classify_accessories([thermostat])
    assert len(rows) == 1
    assert rows[0]['role'] == 'thermostat'
    assert 'CURRENT_TEMPERATURE' in rows[0]['char_bindings']


def test_classify_accessories_from_inventory_label_fixture():
    import json
    from pathlib import Path

    inv_path = Path(__file__).resolve().parent / 'fixtures' / '44_be_73_09_47_20.json'
    inv = json.loads(inv_path.read_text(encoding='utf-8'))

    class _InvChar:
        def __init__(self, row):
            self.iid = row['iid']
            self.type = row.get('type')
            self.value = row.get('value')

    class _InvSvc:
        def __init__(self, row):
            self.iid = row['iid']
            self.type = row.get('type')
            self.characteristics = [_InvChar(c) for c in row.get('characteristics', [])]

    class _InvAcc:
        def __init__(self, row):
            self.aid = row['aid']
            self.services = [_InvSvc(s) for s in row.get('services', [])]

    accessories = [_InvAcc(a) for a in inv['accessories']]
    rows = classify_accessories(accessories)
    assert any(r['role'] == 'thermostat' for r in rows)
    sensor_rows = classify_sensor_aids(accessories, control_aid=1)
    assert sensor_rows
