"""Thermostat CLISPH/CLISPC hub write path tests."""

from __future__ import annotations

from unittest.mock import MagicMock

from nodes.EcobeeThermostatNode import EcobeeThermostatNode
from nodes.ThermostatNode import ThermostatNode
import homekit_hub.hap_apply as hap_apply


def _make_node(cls, *, char_bindings=None):
    poly = MagicMock()
    ctrl = MagicMock()
    ctrl.poly = poly
    ctrl._bridge_get_params.return_value = {}
    ctrl.hub_write = MagicMock(return_value=True)
    ctrl.hub_write_by_iid = MagicMock(return_value=True)
    node = object.__new__(cls)
    node.controller = ctrl
    node.device_id = 'aa:bb:cc:dd:ee:ff'
    node.address = 'g2f5417254138d'
    node.aid = 2
    node.char_bindings = dict(char_bindings or {})
    node.use_celsius = False
    node._hap_cur_hc_four_value = False
    node._drivers = {
        'CLIMD': 1,
        'CLISPH': 68.0,
        'CLISPC': 74.0,
    }

    def _get_driver(key):
        return node._drivers.get(key)

    def _set_driver(key, val, report=True, force=False):
        node._drivers[key] = val

    node.getDriver = _get_driver
    node.setDriver = _set_driver
    node.set_driver_safe = lambda d, v, report=True: _set_driver(d, v)
    if cls is EcobeeThermostatNode:
        node._hk_last_comfort_byte = None
        node._hk_sp_sig_to_gv3 = {}
        node._hk_gv3_to_sp = {}
        node._hk_vendor_comfort_sp = {}
        node._hk_vendor_partial = {}
    return node


def test_honeywell_heat_mode_prefers_target_temperature_when_bound():
    """Honeywell T10-style: target + thresholds; heat mode uses TARGET_TEMPERATURE."""
    node = _make_node(
        ThermostatNode,
        char_bindings={
            'TARGET_TEMPERATURE': {'aid': 2, 'iid': 12},
            'HEATING_THRESHOLD': {'aid': 2, 'iid': 13},
            'COOLING_THRESHOLD': {'aid': 2, 'iid': 14},
        },
    )
    assert node._hap_char_for_heat_driver_write() == hap_apply.hap_name_target_temperature()


def test_threshold_only_heat_mode_uses_heating_threshold():
    node = _make_node(
        ThermostatNode,
        char_bindings={
            'HEATING_THRESHOLD': {'aid': 2, 'iid': 13},
            'COOLING_THRESHOLD': {'aid': 2, 'iid': 14},
        },
    )
    assert node._hap_char_for_heat_driver_write() == hap_apply.hap_name_heating_threshold()


def test_clisph_writes_bound_aid_iid_for_honeywell():
    node = _make_node(
        ThermostatNode,
        char_bindings={
            'TARGET_TEMPERATURE': {'aid': 2, 'iid': 12, 'minStep': 0.5},
            'HEATING_THRESHOLD': {'aid': 2, 'iid': 13},
            'COOLING_THRESHOLD': {'aid': 2, 'iid': 14},
        },
    )
    node.cmd_set_pf({'cmd': 'CLISPH', 'value': 70})
    node.controller.hub_write_by_iid.assert_called_once()
    args = node.controller.hub_write_by_iid.call_args[0]
    assert args[0] == 'aa:bb:cc:dd:ee:ff'
    assert args[1] == 2
    assert args[2] == 12
    assert args[3] == 21.5
    node.controller.hub_write.assert_not_called()
    assert node.getDriver('CLISPH') == 70


def test_clisph_writes_bound_aid_iid_for_honeywell_without_minstep_metadata():
    """Legacy bindings without minStep still route to the correct characteristic."""
    node = _make_node(
        ThermostatNode,
        char_bindings={
            'TARGET_TEMPERATURE': {'aid': 2, 'iid': 12},
            'HEATING_THRESHOLD': {'aid': 2, 'iid': 13},
            'COOLING_THRESHOLD': {'aid': 2, 'iid': 14},
        },
    )
    node.cmd_set_pf({'cmd': 'CLISPH', 'value': 70})
    node.controller.hub_write_by_iid.assert_called_once()
    args = node.controller.hub_write_by_iid.call_args[0]
    assert args[2] == 12
    assert args[3] == 21.2


def test_ecobee_clisph_writes_both_thresholds():
    node = _make_node(EcobeeThermostatNode)
    writes: list[tuple[str, float]] = []

    def _record(_did, char_spec, value):
        writes.append((char_spec, value))
        return True

    node.controller.hub_write.side_effect = _record
    node.cmd_set_pf({'cmd': 'CLISPH', 'value': 70})
    assert hap_apply.hap_name_heating_threshold() in {w[0] for w in writes}
    assert hap_apply.hap_name_cooling_threshold() in {w[0] for w in writes}
    assert node.getDriver('CLISPH') == 70


def test_brt_increments_heat_setpoint_via_target_temperature():
    node = _make_node(
        ThermostatNode,
        char_bindings={
            'TARGET_TEMPERATURE': {'aid': 2, 'iid': 12},
            'HEATING_THRESHOLD': {'aid': 2, 'iid': 13},
            'COOLING_THRESHOLD': {'aid': 2, 'iid': 14},
        },
    )
    node.set_point({'cmd': 'BRT', 'value': 2})
    node.controller.hub_write_by_iid.assert_called_once()
    assert node.getDriver('CLISPH') == 70


def test_dim_decrements_heat_setpoint():
    node = _make_node(
        ThermostatNode,
        char_bindings={
            'TARGET_TEMPERATURE': {'aid': 2, 'iid': 12},
        },
    )
    node.set_point({'cmd': 'DIM', 'value': 1})
    assert node.getDriver('CLISPH') == 67
