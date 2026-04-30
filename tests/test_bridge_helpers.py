"""Tests for pure helpers in ``homekit_hub.bridge``."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from zeroconf import InterfaceChoice, IPVersion

from homekit_hub.bridge import (
    DATA_KEY_LAST_HAP_DISCOVER,
    assign_pairing_slot_rows,
    normalize_hap_pin,
    _parse_slot_value,
    _resolve_filters_from_last_discover,
    _row_pin_and_filters,
    _zeroconf_ctor_kwargs,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, ""),
        ("", ""),
        ("  ", ""),
        ("12345678", "123-45-678"),
        ("  123-45-678  ", "123-45-678"),
        ("123-45-678", "123-45-678"),
        ("01234567", "012-34-567"),
        ("1234567", "1234567"),
        ("123456789", "123456789"),
        ("12a45678", "12a45678"),
    ],
)
def test_normalize_hap_pin(raw, expected):
    assert normalize_hap_pin(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("  ", None),
        ("1", 1),
        ("  5 ", 5),
        (7, 7),
        ("0", None),
        ("-1", None),
        ("abc", None),
        ("auto", None),
    ],
)
def test_parse_slot_value(raw, expected):
    assert _parse_slot_value(raw) == expected


def test_assign_pairing_slot_rows_explicit_gap_and_auto():
    log = logging.getLogger("test")
    rows = [
        {"slot": 3, "k": "a"},
        {"slot": "", "k": "b"},
        {"slot": 1, "k": "c"},
    ]
    out = assign_pairing_slot_rows(rows, log)
    assert [s for s, _ in out] == [1, 2, 3]
    assert {s: r["k"] for s, r in out} == {1: "c", 2: "b", 3: "a"}


def test_assign_pairing_slot_rows_duplicate_slot_goes_auto():
    log = MagicMock()
    rows = [
        {"slot": 1, "k": "first"},
        {"slot": 1, "k": "dup"},
    ]
    out = assign_pairing_slot_rows(rows, log)
    assert sorted(out, key=lambda x: x[0]) == [(1, {"slot": 1, "k": "first"}), (2, {"slot": 1, "k": "dup"})]
    log.warning.assert_called()


def test_assign_pairing_slot_rows_non_list():
    assert assign_pairing_slot_rows("bad", logging.getLogger("t")) == []


def test_assign_pairing_slot_rows_skips_non_dict():
    log = logging.getLogger("test")
    out = assign_pairing_slot_rows([{"slot": 2}, "skip", {"slot": ""}], log)
    slots = [s for s, _ in out]
    assert slots == [1, 2]


def test_row_pin_and_filters():
    pin, aid, aname = _row_pin_and_filters(
        {"hap_pin": "12345678", "accessory_id": " AA:BB ", "accessory_name": "  Kitchen  "}
    )
    assert pin == "123-45-678"
    assert aid == "aa:bb"
    assert aname == "Kitchen"


def test_resolve_filters_from_last_discover_empty_cache():
    log = MagicMock()
    data: dict = {DATA_KEY_LAST_HAP_DISCOVER: []}
    aid, name = _resolve_filters_from_last_discover(data, "", "", log, slot_num=1)
    assert aid == "" and name == ""
    log.info.assert_called()


def test_resolve_filters_from_last_discover_paired_only():
    log = MagicMock()
    data = {
        DATA_KEY_LAST_HAP_DISCOVER: [
            {"id": "aa", "name": "X", "paired": True},
        ]
    }
    aid, name = _resolve_filters_from_last_discover(data, "", "", log, slot_num=2)
    assert aid == "" and name == ""


def test_resolve_filters_from_last_discover_single_unpaired():
    log = MagicMock()
    data = {
        DATA_KEY_LAST_HAP_DISCOVER: [
            {"id": "AA:BB:CC", "name": "Lamp", "paired": False},
        ]
    }
    aid, name = _resolve_filters_from_last_discover(data, "", "", log, slot_num=1)
    assert aid == "aa:bb:cc"
    assert name == "Lamp"


def test_resolve_filters_from_last_discover_multiple_unpaired_warns():
    log = MagicMock()
    data = {
        DATA_KEY_LAST_HAP_DISCOVER: [
            {"id": "11", "name": "A", "paired": False},
            {"id": "22", "name": "B", "paired": False},
        ]
    }
    aid, name = _resolve_filters_from_last_discover(data, "", "", log, slot_num=3)
    assert aid == "11"
    assert name == "A"
    log.warning.assert_called()


def test_resolve_filters_from_last_discover_keeps_explicit():
    log = MagicMock()
    data = {DATA_KEY_LAST_HAP_DISCOVER: [{"id": "ignored", "paired": False}]}
    aid, name = _resolve_filters_from_last_discover(data, "explicit", "", log, slot_num=1)
    assert aid == "explicit"
    assert name == ""
    log.warning.assert_not_called()


def test_zeroconf_ctor_kwargs_interfaces_and_ip(monkeypatch):
    log = logging.getLogger("zc")
    monkeypatch.delenv("HOMEKIT_HUB_ZEROCONF_INTERFACES", raising=False)
    monkeypatch.delenv("HOMEKIT_HUB_ZEROCONF_IP_VERSION", raising=False)
    monkeypatch.setattr("homekit_hub.bridge.sys.platform", "linux")

    monkeypatch.setenv("HOMEKIT_HUB_ZEROCONF_INTERFACES", "default")
    monkeypatch.setenv("HOMEKIT_HUB_ZEROCONF_IP_VERSION", "v4")
    kw = _zeroconf_ctor_kwargs(log, unicast=False)
    assert kw["interfaces"] is InterfaceChoice.Default
    assert kw["ip_version"] is IPVersion.V4Only


def test_zeroconf_ctor_kwargs_ip_variants(monkeypatch):
    log = logging.getLogger("zc")
    monkeypatch.delenv("HOMEKIT_HUB_ZEROCONF_INTERFACES", raising=False)
    monkeypatch.setattr("homekit_hub.bridge.sys.platform", "linux")

    monkeypatch.setenv("HOMEKIT_HUB_ZEROCONF_IP_VERSION", "all")
    assert _zeroconf_ctor_kwargs(log, unicast=False)["ip_version"] is IPVersion.All

    monkeypatch.setenv("HOMEKIT_HUB_ZEROCONF_IP_VERSION", "v6")
    assert _zeroconf_ctor_kwargs(log, unicast=False)["ip_version"] is IPVersion.V6Only


def test_zeroconf_ctor_kwargs_unicast_bsd_defaults(monkeypatch):
    log = logging.getLogger("zc")
    monkeypatch.delenv("HOMEKIT_HUB_ZEROCONF_INTERFACES", raising=False)
    monkeypatch.delenv("HOMEKIT_HUB_ZEROCONF_IP_VERSION", raising=False)
    monkeypatch.setattr("homekit_hub.bridge.sys.platform", "freebsd14")

    kw = _zeroconf_ctor_kwargs(log, unicast=True)
    assert kw["interfaces"] is InterfaceChoice.Default
    assert kw["ip_version"] is IPVersion.V4Only


def test_zeroconf_ctor_kwargs_unicast_bsd_respects_all_interfaces(monkeypatch):
    log = logging.getLogger("zc")
    monkeypatch.setenv("HOMEKIT_HUB_ZEROCONF_INTERFACES", "all")
    monkeypatch.delenv("HOMEKIT_HUB_ZEROCONF_IP_VERSION", raising=False)
    monkeypatch.setattr("homekit_hub.bridge.sys.platform", "darwin")

    kw = _zeroconf_ctor_kwargs(log, unicast=True)
    assert kw["interfaces"] is InterfaceChoice.All
    assert kw["ip_version"] is IPVersion.V4Only


def test_zeroconf_ctor_kwargs_params_when_no_env(monkeypatch):
    log = logging.getLogger("zc")
    monkeypatch.delenv("HOMEKIT_HUB_ZEROCONF_INTERFACES", raising=False)
    monkeypatch.delenv("HOMEKIT_HUB_ZEROCONF_IP_VERSION", raising=False)
    monkeypatch.setattr("homekit_hub.bridge.sys.platform", "linux")
    params = {"zeroconf_interfaces": "default", "zeroconf_ip_version": "v4"}
    kw = _zeroconf_ctor_kwargs(log, unicast=False, params=params)
    assert kw["interfaces"] is InterfaceChoice.Default
    assert kw["ip_version"] is IPVersion.V4Only


def test_zeroconf_ctor_kwargs_env_wins_params(monkeypatch):
    log = logging.getLogger("zc")
    monkeypatch.setenv("HOMEKIT_HUB_ZEROCONF_INTERFACES", "all")
    monkeypatch.delenv("HOMEKIT_HUB_ZEROCONF_IP_VERSION", raising=False)
    monkeypatch.setattr("homekit_hub.bridge.sys.platform", "linux")
    params = {"zeroconf_interfaces": "default"}
    kw = _zeroconf_ctor_kwargs(log, unicast=False, params=params)
    assert kw["interfaces"] is InterfaceChoice.All
