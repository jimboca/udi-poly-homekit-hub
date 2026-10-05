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
    # Snapshot marks aid 4 as control; classify_accessories also treats thermostat
    # aid 2 as a control accessory — neither becomes a room sensor.
    assert 4 not in sensor_aids
    assert 2 not in sensor_aids
    assert sensor_aids == {3}


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


def test_classify_accessories_ecobee_via_vendor_bindings():
    """Vendor chars on the thermostat service classify as HKHubEcobeeThermostat."""
    vendor_mode = 'B7DDB9A3-54BB-4572-91D2-F1F5B0510F8C'
    thermostat = _Acc(
        2,
        [
            _Svc(
                10,
                ServicesTypes.THERMOSTAT,
                [
                    _Char(11, CharacteristicsTypes.TEMPERATURE_CURRENT),
                    _Char(12, CharacteristicsTypes.HEATING_COOLING_TARGET),
                    _Char(13, CharacteristicsTypes.TEMPERATURE_HEATING_THRESHOLD),
                    _Char(14, CharacteristicsTypes.TEMPERATURE_COOLING_THRESHOLD),
                    _Char(15, vendor_mode),
                ],
            ),
        ],
    )
    rows = classify_accessories([thermostat])
    tstat = next(r for r in rows if r['role'] == 'thermostat')
    assert tstat['node_def_id'] == 'HKHubEcobeeThermostat'
    assert 'VENDOR_ECOBEE_CURRENT_MODE' in tstat['char_bindings']


def _honeywell_t10_room_sensor(aid: int, name: str):
    return _Acc(
        aid,
        [
            _Svc(1, ServicesTypes.ACCESSORY_INFORMATION, [_Char(1, CharacteristicsTypes.NAME, name)]),
            _Svc(
                40,
                ServicesTypes.TEMPERATURE_SENSOR,
                [
                    _Char(41, CharacteristicsTypes.TEMPERATURE_CURRENT),
                    _Char(42, CharacteristicsTypes.RELATIVE_HUMIDITY_CURRENT),
                    _Char(43, CharacteristicsTypes.OCCUPANCY_DETECTED),
                    _Char(44, CharacteristicsTypes.BATTERY_LEVEL),
                    _Char(45, CharacteristicsTypes.STATUS_LO_BATT),
                ],
            ),
        ],
    )


def test_classify_sensor_aids_honeywell_t10_redlink_sensors():
    """Honeywell T10 + RedLINK room sensors (aids 1,2,5,6,7 from field pairing)."""
    thermostat = _Acc(
        2,
        [
            _Svc(
                1,
                ServicesTypes.ACCESSORY_INFORMATION,
                [_Char(1, CharacteristicsTypes.NAME, 'T10 Thermostat')],
            ),
            _Svc(
                10,
                ServicesTypes.THERMOSTAT,
                [
                    _Char(11, CharacteristicsTypes.TEMPERATURE_CURRENT),
                    _Char(12, CharacteristicsTypes.TEMPERATURE_TARGET),
                    _Char(13, CharacteristicsTypes.TEMPERATURE_HEATING_THRESHOLD),
                    _Char(14, CharacteristicsTypes.TEMPERATURE_COOLING_THRESHOLD),
                    _Char(15, CharacteristicsTypes.HEATING_COOLING_TARGET),
                    _Char(16, CharacteristicsTypes.RELATIVE_HUMIDITY_CURRENT),
                    _Char(17, CharacteristicsTypes.OCCUPANCY_DETECTED),
                ],
            ),
        ],
    )
    accessories = [
        _honeywell_t10_room_sensor(1, 'TSTAT-434E76'),
        thermostat,
        _honeywell_t10_room_sensor(5, 'Fireplace Air Sensor 01'),
        _honeywell_t10_room_sensor(6, 'Master Bedroom Air Sensor 02'),
        _honeywell_t10_room_sensor(7, 'Lwr Bed Air Sensor 03'),
    ]
    tstat_rows = classify_accessories(accessories)
    assert len(tstat_rows) == 1
    assert tstat_rows[0]['role'] == 'thermostat'
    assert tstat_rows[0]['node_def_id'] == 'HKHubThermostat'
    assert 'TARGET_TEMPERATURE' in tstat_rows[0]['char_bindings']

    sensor_rows = classify_sensor_aids(accessories, control_aid=2)
    room = [r for r in sensor_rows if r['role'] == 'sensor']
    assert {r['aid'] for r in room} == {1, 5, 6, 7}
    assert all(r['node_def_id'] == 'HKHubSensor' for r in room)
    assert all('RELATIVE_HUMIDITY' in r['char_bindings'] for r in room)
    motion = next(r for r in sensor_rows if r['role'] == 'motion_sensor')
    assert motion['aid'] == 2
    assert motion['node_def_id'] == 'HKHubMotionSensor'
    assert 'OCCUPANCY_DETECTED' in motion['char_bindings']


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


def _hunter_simpleconnect_fan_light():
    """Hunter SIMPLEconnect Fan M2: Fan v2 + Lightbulb on aid=1 (from field inventory)."""
    return [
        _Acc(
            1,
            [
                _Svc(
                    1,
                    ServicesTypes.ACCESSORY_INFORMATION,
                    [
                        _Char(3, CharacteristicsTypes.MANUFACTURER, 'Hunter Fan'),
                        _Char(4, CharacteristicsTypes.MODEL, 'SIMPLEconnect'),
                        _Char(5, CharacteristicsTypes.NAME, 'SIMPLEconnect Fan M2-334f8b'),
                    ],
                ),
                _Svc(
                    64,
                    ServicesTypes.FAN_V2,
                    [
                        _Char(66, CharacteristicsTypes.NAME, 'Hunter Fan'),
                        _Char(67, CharacteristicsTypes.ON, False),
                        _Char(68, CharacteristicsTypes.ACTIVE, 0),
                        _Char(69, CharacteristicsTypes.ROTATION_SPEED, 66),
                        _Char(70, CharacteristicsTypes.ROTATION_DIRECTION, 1),
                    ],
                ),
                _Svc(
                    48,
                    ServicesTypes.LIGHTBULB,
                    [
                        _Char(50, CharacteristicsTypes.NAME, 'Hunter Light'),
                        _Char(51, CharacteristicsTypes.ON, False),
                        _Char(52, CharacteristicsTypes.BRIGHTNESS, 0),
                    ],
                ),
            ],
        )
    ]


def test_classify_hunter_simpleconnect_fan_light():
    accessories = _hunter_simpleconnect_fan_light()
    rows = classify_accessories(accessories)
    roles = {r['role'] for r in rows}
    assert roles == {'fan', 'light'}
    fan = next(r for r in rows if r['role'] == 'fan')
    light = next(r for r in rows if r['role'] == 'light')
    assert fan['node_def_id'] == 'HKHubFan'
    assert light['node_def_id'] == 'HKHubLight'
    assert fan['char_bindings']['ON']['iid'] == 67
    assert fan['char_bindings']['ACTIVE']['iid'] == 68
    assert fan['char_bindings']['ROTATION_SPEED']['iid'] == 69
    assert fan['char_bindings']['ROTATION_DIRECTION']['iid'] == 70
    assert light['char_bindings']['ON']['iid'] == 51
    assert light['char_bindings']['BRIGHTNESS']['iid'] == 52
    # Must not invent a sensor from the accessory name alone.
    assert classify_sensor_aids(accessories) == []


def test_hap_event_matches_bound_iid_only():
    from hub_node_funcs import hap_event_matches_node

    class _Node:
        aid = 1
        char_bindings = {'ON': {'aid': 1, 'iid': 51}}

    node = _Node()
    assert hap_event_matches_node(1, 51, node) is True
    assert hap_event_matches_node(1, 67, node) is False


def test_hub_write_bound_characteristic_prefers_iid():
    from hub_node_funcs import hub_write_bound_characteristic

    writes = []

    class _Ctrl:
        def hub_write_by_iid(self, device_id, aid, iid, value):
            writes.append(('iid', device_id, aid, iid, value))
            return True

        def hub_write(self, device_id, name, value):
            writes.append(('name', device_id, name, value))
            return True

    ok = hub_write_bound_characteristic(
        _Ctrl(),
        'dev1',
        {'ON': {'aid': 1, 'iid': 51}},
        'ON',
        True,
        hap_name_fallback='ON',
    )
    assert ok is True
    assert writes == [('iid', 'dev1', 1, 51, True)]
