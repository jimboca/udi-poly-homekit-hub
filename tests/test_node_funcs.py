"""Tests for IoX node address helpers."""

from __future__ import annotations

from node_funcs import (
    IOX_MAX_NODE_ADDRESS_LEN,
    generic_node_address,
    generic_node_title,
    id_to_address,
    legacy_generic_node_address,
    paired_slot_node_title,
    sensor_node_title,
    uuid_to_address,
)


def test_uuid_to_address_last_12():
    assert uuid_to_address('abcdef0123456789abcd') == '23456789abcd'


def test_id_to_address_default_length():
    addr = id_to_address('44:be:73:09:47:20:1:thermostat')
    assert len(addr) == IOX_MAX_NODE_ADDRESS_LEN
    assert addr == id_to_address('44:be:73:09:47:20:1:thermostat', slen=14)


def test_generic_node_address_within_iox_limit():
    addr = generic_node_address('44:be:73:09:47:20', 1, 'thermostat')
    assert len(addr) <= IOX_MAX_NODE_ADDRESS_LEN
    assert addr.startswith('g')
    assert addr == generic_node_address('44:be:73:09:47:20', 1, 'thermostat')


def test_generic_node_address_differs_by_role_and_aid():
    a = generic_node_address('44:be:73:09:47:20', 1, 'thermostat')
    b = generic_node_address('44:be:73:09:47:20', 1, 'light')
    c = generic_node_address('44:be:73:09:47:20', 2, 'thermostat')
    assert len({a, b, c}) == 3


def test_legacy_generic_node_address_long_form():
    addr = legacy_generic_node_address('44:be:73:09:47:20', 1, 'thermostat')
    assert addr == 'hkg_44_be_73_09_47_20_1_thermostat'
    assert addr != generic_node_address('44:be:73:09:47:20', 1, 'thermostat')


def test_paired_slot_node_title_with_generic_nodes():
    assert paired_slot_node_title('JimBo Dev', generic_nodes_enabled=True) == 'JimBo Dev (Pairing)'
    assert paired_slot_node_title('JimBo Dev', generic_nodes_enabled=False) == 'JimBo Dev'


def test_generic_node_title_single_role_uses_display_name():
    assert generic_node_title('JimBo Dev', 'thermostat', sibling_count=1) == 'JimBo Dev'


def test_generic_node_title_multi_role_adds_role_suffix():
    assert (
        generic_node_title('JimBo Dev', 'thermostat', sibling_count=2)
        == 'JimBo Dev Thermostat'
    )


def test_sensor_node_address_stable_per_aid():
    a = generic_node_address('44:be:73:09:47:20', 3, 'sensor')
    b = generic_node_address('44:be:73:09:47:20', 3, 'sensor')
    c = generic_node_address('44:be:73:09:47:20', 4, 'sensor')
    d = generic_node_address('44:be:73:09:47:20', 2, 'motion_sensor')
    assert a == b
    assert a != c
    assert a != d


def test_sensor_node_title_room_and_motion():
    assert sensor_node_title('Ecobee', 'Master Bedroom', 'sensor') == 'Master Bedroom'
    assert sensor_node_title('Ecobee', 'Downstairs', 'motion_sensor') == 'Downstairs · motion'
