"""Tests for pure helpers in ``homekit_hub.bridge``."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohomekit.model.characteristics import CharacteristicsTypes
from aiohomekit.model.services.service_types import ServicesTypes
from zeroconf import InterfaceChoice, IPVersion

from homekit_hub.bridge import (
    DATA_KEY_LAST_HAP_DISCOVER,
    HomeKitHubBridge,
    WS_NOTICE_CODE_GET_CHARACTERISTICS_FAILED,
    WS_NOTICE_LEVEL_ERROR,
    assign_pairing_slot_rows,
    normalize_hap_pin,
    _accessory_information_metadata,
    _accessory_information_metadata_needs_hap_reads,
    _accessory_information_metadata_with_reads,
    _device_list_entry_resolved,
    _list_devices_ws_message,
    _parse_slot_value,
    _accessories_imply_thermostat_class,
    _accessory_summaries_for_pairing,
    _infer_thermostat_category_from_services,
    _representative_accessory,
    _resolve_filters_from_last_discover,
    _row_pin_and_filters,
    _zeroconf_ctor_kwargs,
    normalize_discover_network_address,
    resolve_zeroconf_interface_ips,
    resolve_zeroconf_interface_bind,
    default_outbound_ip,
    discover_network_hints,
    discover_primary_timeout_sec,
    discover_probe_hints,
    ephemeral_discover_probe_overrides,
    format_discover_attempt_history_html,
    format_probe_success_explanation,
    format_zeroconf_diag_log_summary,
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
    assert sorted(out, key=lambda x: x[0]) == [
        (1, {"slot": 1, "k": "first"}),
        (2, {"slot": 1, "k": "dup"}),
    ]
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


def test_normalize_discover_network_address_broadcast_and_host():
    log = logging.getLogger("discover-net")
    assert normalize_discover_network_address("192.168.222.255", log) == "192.168.222.255"
    assert normalize_discover_network_address("192.168.222.1", log) == "192.168.222.255"
    assert normalize_discover_network_address("192.168.222.10", log) == "192.168.222.255"


def test_resolve_zeroconf_interface_ips_from_rows(monkeypatch):
    log = logging.getLogger("discover-net")
    params = {
        "_primary_network_broadcast": "192.168.1.255",
        "_discover_networks": [{"address": "192.168.222.255"}, {"address": "192.168.50.1"}],
    }
    monkeypatch.setattr(
        "homekit_hub.bridge.local_ip_for_discover_network",
        lambda hint, _log=None: {
            "192.168.1.255": "192.168.1.5",
            "192.168.222.255": "192.168.222.10",
            "192.168.50.255": "192.168.50.2",
        }.get(hint),
    )
    ips, source = resolve_zeroconf_interface_bind(params, log)
    assert source == "discover_network_rows"
    assert ips == [
        "192.168.1.5",
        "192.168.222.10",
        "192.168.50.2",
    ]
    assert resolve_zeroconf_interface_ips(params, log) == ips


def test_resolve_zeroconf_auto_primary_on_bsd(monkeypatch):
    log = logging.getLogger("discover-net")
    monkeypatch.setattr("homekit_hub.bridge.sys.platform", "freebsd14")
    monkeypatch.delenv("HOMEKIT_HUB_ZEROCONF_INTERFACES", raising=False)
    monkeypatch.setattr(
        "homekit_hub.bridge.local_ip_for_discover_network",
        lambda hint, _log=None: "192.168.1.5" if hint == "192.168.1.255" else None,
    )
    params = {
        "_primary_network_broadcast": "192.168.1.255",
        "_discover_networks": [],
        "_poly_network_interface": {"addr": "192.168.55.22", "broadcast": "192.168.1.255"},
    }
    ips, source = resolve_zeroconf_interface_bind(params, log)
    assert source == "auto_primary"
    assert ips == ["192.168.1.5"]


def test_resolve_zeroconf_auto_primary_outbound_fallback(monkeypatch):
    log = logging.getLogger("discover-net")
    monkeypatch.setattr("homekit_hub.bridge.sys.platform", "darwin")
    monkeypatch.delenv("HOMEKIT_HUB_ZEROCONF_INTERFACES", raising=False)
    monkeypatch.setattr(
        "homekit_hub.bridge.local_ip_for_discover_network",
        lambda _hint, _log=None: None,
    )
    monkeypatch.setattr(
        "homekit_hub.bridge.default_outbound_ip",
        lambda _rhost="8.8.8.8": "192.168.55.22",
    )
    params = {
        "_primary_network_broadcast": "192.168.1.255",
        "_discover_networks": [],
    }
    ips, source = resolve_zeroconf_interface_bind(params, log)
    assert source == "auto_primary_outbound"
    assert ips == ["192.168.55.22"]


def test_resolve_zeroconf_auto_primary_skipped_on_linux(monkeypatch):
    log = logging.getLogger("discover-net")
    monkeypatch.setattr("homekit_hub.bridge.sys.platform", "linux")
    params = {
        "_primary_network_broadcast": "192.168.1.255",
        "_discover_networks": [],
    }
    ips, source = resolve_zeroconf_interface_bind(params, log)
    assert source == "none"
    assert ips == []


def test_resolve_zeroconf_auto_primary_skipped_when_interfaces_all(monkeypatch):
    log = logging.getLogger("discover-net")
    monkeypatch.setattr("homekit_hub.bridge.sys.platform", "freebsd14")
    monkeypatch.setenv("HOMEKIT_HUB_ZEROCONF_INTERFACES", "all")
    params = {
        "_primary_network_broadcast": "192.168.1.255",
        "_discover_networks": [],
    }
    ips, source = resolve_zeroconf_interface_bind(params, log)
    assert source == "none"
    assert ips == []


def test_zeroconf_ctor_kwargs_uses_explicit_interface_ips(monkeypatch):
    log = logging.getLogger("zc")
    monkeypatch.delenv("HOMEKIT_HUB_ZEROCONF_INTERFACES", raising=False)
    monkeypatch.delenv("HOMEKIT_HUB_ZEROCONF_IP_VERSION", raising=False)
    monkeypatch.setattr("homekit_hub.bridge.sys.platform", "freebsd14")
    monkeypatch.setattr(
        "homekit_hub.bridge.resolve_zeroconf_interface_bind",
        lambda _params, _log=None: (["192.168.222.10"], "discover_network_rows"),
    )
    kw = _zeroconf_ctor_kwargs(log, unicast=True, params={"_discover_networks": []})
    assert kw["interfaces"] == ["192.168.222.10"]
    assert kw["ip_version"] is IPVersion.V4Only


@pytest.mark.parametrize(
    ("static", "expected"),
    [
        ({}, True),
        ({"manufacturer": ""}, True),
        ({"manufacturer": " "}, True),
        ({"manufacturer": "Ecobee"}, True),
        ({"manufacturer": "Ecobee", "category": 9}, True),
        ({"manufacturer": "Ecobee", "category": 9, "model": ""}, True),
        ({"manufacturer": "Ecobee", "category": 9, "model": "SmartTherm"}, False),
    ],
)
def test_accessory_information_metadata_needs_hap_reads(static, expected):
    assert _accessory_information_metadata_needs_hap_reads(static) is expected


@pytest.mark.asyncio
async def test_accessory_information_metadata_with_reads_fetches_when_category_missing(
    monkeypatch,
):
    """Manufacturer present in cache but category missing must still trigger HAP GETs."""

    def fake_label(ch):
        if getattr(ch, "iid", None) == 1:
            return "Manufacturer"
        if getattr(ch, "iid", None) == 2:
            return "Category"
        if getattr(ch, "iid", None) == 3:
            return "Model"
        return None

    monkeypatch.setattr("homekit_hub.bridge._accessory_info_char_label", fake_label)
    monkeypatch.setattr(
        "homekit_hub.bridge._accessory_information_metadata",
        lambda acc: {"manufacturer": "Ecobee"},
    )

    ch1, ch2, ch3 = MagicMock(), MagicMock(), MagicMock()
    ch1.iid, ch2.iid, ch3.iid = 1, 2, 3
    svc = MagicMock()
    svc.characteristics = [ch1, ch2, ch3]
    acc = MagicMock()
    acc.aid = 1
    acc.services = [svc]

    pairing = MagicMock()
    pairing.get_characteristics = AsyncMock(
        return_value={
            (1, 1): {"value": "Ecobee Inc"},
            (1, 2): {"value": 9},
            (1, 3): {"value": "SmartTherm"},
        }
    )

    notices: list = []
    meta = await _accessory_information_metadata_with_reads(
        pairing, acc, None, client_notices=notices, notice_device_id="aa:bb:cc:dd:ee:ff"
    )
    pairing.get_characteristics.assert_awaited_once()
    assert meta.get("category") == 9
    assert meta.get("manufacturer") == "Ecobee Inc"
    assert meta.get("model") == "SmartTherm"
    assert notices == []


@pytest.mark.asyncio
async def test_accessory_information_metadata_with_reads_skips_get_when_complete(
    monkeypatch,
):
    monkeypatch.setattr(
        "homekit_hub.bridge._accessory_information_metadata",
        lambda acc: {"manufacturer": "E", "category": 2, "model": "Bridge"},
    )

    acc = MagicMock()
    acc.aid = 1
    acc.services = []
    pairing = MagicMock()
    pairing.get_characteristics = AsyncMock(return_value={})

    meta = await _accessory_information_metadata_with_reads(pairing, acc, None)
    pairing.get_characteristics.assert_not_called()
    assert meta == {"manufacturer": "E", "category": 2, "model": "Bridge"}


@pytest.mark.asyncio
async def test_accessory_information_metadata_with_reads_notifies_on_get_characteristics_failure(
    monkeypatch,
):
    def fake_label(ch):
        if getattr(ch, "iid", None) == 1:
            return "Manufacturer"
        return None

    monkeypatch.setattr("homekit_hub.bridge._accessory_info_char_label", fake_label)
    monkeypatch.setattr("homekit_hub.bridge._accessory_information_metadata", lambda acc: {})

    ch1 = MagicMock()
    ch1.iid = 1
    svc = MagicMock()
    svc.characteristics = [ch1]
    acc = MagicMock()
    acc.aid = 1
    acc.services = [svc]

    pairing = MagicMock()
    pairing.get_characteristics = AsyncMock(side_effect=RuntimeError("hap"))

    notices: list = []
    meta = await _accessory_information_metadata_with_reads(
        pairing, acc, None, client_notices=notices, notice_device_id="dd:ee:ff:00:11:22"
    )
    assert meta == {}
    assert len(notices) == 1
    assert notices[0]["level"] == WS_NOTICE_LEVEL_ERROR
    assert notices[0]["code"] == WS_NOTICE_CODE_GET_CHARACTERISTICS_FAILED
    assert notices[0]["device_id"] == "dd:ee:ff:00:11:22"
    assert notices[0]["primary_aid"] == 1


def test_list_devices_ws_message_includes_empty_warnings():
    m = _list_devices_ws_message([{"device_id": "a"}], [])
    assert m["warnings"] == []
    assert m["devices"][0]["device_id"] == "a"


def test_list_devices_ws_message_includes_warnings():
    w = [{"level": "warning", "code": "x", "message": "y"}]
    m = _list_devices_ws_message([], w)
    assert m["warnings"] == w


def test_representative_accessory_ecobee_thermostat_lower_aid_than_occupancy(monkeypatch):
    """Ecobee: thermostat + Occupancy; lowest aid among non-bridge is the stat."""
    therm = MagicMock()
    therm.aid = 2
    occ = MagicMock()
    occ.aid = 7
    pairing = MagicMock()
    pairing.accessories = [occ, therm]

    def fake_cat(acc):
        if acc is occ:
            return 10
        if acc is therm:
            return 9
        return None

    monkeypatch.setattr("homekit_hub.bridge._accessory_info_category_value", fake_cat)
    monkeypatch.setattr("homekit_hub.bridge._hap_category_bridge_id", lambda: 2)

    assert _representative_accessory(pairing) is therm


def test_representative_accessory_lowest_aid_non_bridge_not_category(monkeypatch):
    """Representative is min aid among non-bridge when none expose thermostat services."""
    occ = MagicMock()
    occ.aid = 2
    occ.services = []
    therm = MagicMock()
    therm.aid = 5
    therm.services = []
    pairing = MagicMock()
    pairing.accessories = [occ, therm]

    def fake_cat(acc):
        if acc is occ:
            return 10
        if acc is therm:
            return 9
        return None

    monkeypatch.setattr("homekit_hub.bridge._accessory_info_category_value", fake_cat)
    monkeypatch.setattr("homekit_hub.bridge._hap_category_bridge_id", lambda: 2)

    assert _representative_accessory(pairing) is occ


def test_representative_accessory_prefers_thermostat_service_when_ai_category_missing(monkeypatch):
    """Ecobee: Occupancy at lower aid without AI Category; thermostat services identify climate aid."""
    occ = MagicMock()
    occ.aid = 1
    occ.services = []
    therm = MagicMock()
    therm.aid = 2
    svc = MagicMock()
    svc.type = ServicesTypes.THERMOSTAT
    therm.services = [svc]
    pairing = MagicMock()
    pairing.accessories = [occ, therm]

    monkeypatch.setattr("homekit_hub.bridge._accessory_info_category_value", lambda _acc: None)
    monkeypatch.setattr("homekit_hub.bridge._hap_category_bridge_id", lambda: 2)

    assert _representative_accessory(pairing) is therm


def test_accessory_summaries_for_pairing_infers_category_from_services(monkeypatch):
    occ = MagicMock()
    occ.aid = 1
    occ.services = []
    therm = MagicMock()
    therm.aid = 2
    svc = MagicMock()
    svc.type = ServicesTypes.THERMOSTAT
    therm.services = [svc]
    pairing = MagicMock()
    pairing.accessories = [occ, therm]

    monkeypatch.setattr("homekit_hub.bridge._accessory_information_metadata", lambda _acc: {})
    monkeypatch.setattr("homekit_hub.bridge._accessory_info_category_value", lambda _acc: None)

    rows = _accessory_summaries_for_pairing(pairing)
    assert len(rows) == 2
    assert rows[0]["aid"] == 1
    assert "category" not in rows[0]
    assert rows[1]["aid"] == 2
    assert rows[1]["category"] == 9
    assert rows[1].get("category_inferred") is True
    assert rows[1].get("thermostat_like") is True


def test_accessory_information_metadata_prefers_accessory_information_service_name():
    """Ecobee satellites expose **Name** on AI (room) and on Motion; ignore non-AI copies."""
    ch_ai = MagicMock()
    ch_ai.type = CharacteristicsTypes.NAME
    ch_ai.value = "Front Guest"
    ch_ai.perms = ["pr"]
    ai_svc = MagicMock()
    ai_svc.type = ServicesTypes.ACCESSORY_INFORMATION
    ai_svc.characteristics = [ch_ai]

    ch_motion = MagicMock()
    ch_motion.type = CharacteristicsTypes.NAME
    ch_motion.value = "Front Guest Motion"
    ch_motion.perms = ["pr"]
    motion_svc = MagicMock()
    motion_svc.type = ServicesTypes.MOTION_SENSOR
    motion_svc.characteristics = [ch_motion]

    acc = MagicMock()
    acc.services = [motion_svc, ai_svc]

    meta = _accessory_information_metadata(acc)
    assert meta.get("name") == "Front Guest"


def test_accessories_imply_thermostat_class():
    assert _accessories_imply_thermostat_class([]) is False
    assert _accessories_imply_thermostat_class([{"category": 9}]) is True
    assert _accessories_imply_thermostat_class([{"thermostat_like": True}]) is True
    assert _accessories_imply_thermostat_class([{"aid": 1}]) is False


def test_infer_thermostat_category_from_services_sets_category_9():
    pairing = MagicMock()
    occ = MagicMock()
    occ.aid = 1
    occ.services = []
    therm = MagicMock()
    therm.aid = 3
    svc = MagicMock()
    svc.type = ServicesTypes.THERMOSTAT
    therm.services = [svc]
    pairing.accessories = [occ, therm]
    entry: dict = {"device_id": "aa:bb:cc", "primary_aid": 1}
    _infer_thermostat_category_from_services(pairing, entry)
    assert entry.get("category") == 9
    assert entry.get("primary_aid") == 3


def test_representative_accessory_prefers_lowest_aid_among_thermostats(monkeypatch):
    hi = MagicMock()
    hi.aid = 12
    lo = MagicMock()
    lo.aid = 5
    pairing = MagicMock()
    pairing.accessories = [hi, lo]

    monkeypatch.setattr(
        "homekit_hub.bridge._accessory_info_category_value",
        lambda acc: 9,
    )
    monkeypatch.setattr("homekit_hub.bridge._hap_category_bridge_id", lambda: 2)

    assert _representative_accessory(pairing) is lo


@pytest.mark.asyncio
async def test_device_list_entry_resolved_no_pairing_normalizes_device_id():
    entry, warns = await _device_list_entry_resolved("AA:BB:CC", None, None)
    assert entry == {"device_id": "aa:bb:cc"}
    assert warns == []


@pytest.mark.asyncio
async def test_device_list_entry_resolved_retries_list_accessories_for_category(monkeypatch):
    log = MagicMock()

    acc1 = MagicMock()
    acc1.aid = 1
    acc1.services = []

    pairing = MagicMock()
    pairing.accessories = [acc1]
    pairing.list_accessories_and_characteristics = AsyncMock()

    meta_no_cat = {"manufacturer": "Ecobee"}
    meta_with_cat = {"manufacturer": "Ecobee", "category": 9, "category_label": "THERMOSTAT"}
    read_calls = {"n": 0}

    async def fake_reads(p, a, lg, **kwargs):
        read_calls["n"] += 1
        if read_calls["n"] == 1:
            return dict(meta_no_cat)
        return dict(meta_with_cat)

    monkeypatch.setattr(
        "homekit_hub.bridge._representative_accessory", lambda p: acc1 if p.accessories else None
    )
    monkeypatch.setattr(
        "homekit_hub.bridge._accessory_information_metadata_with_reads", fake_reads
    )

    entry, warns = await _device_list_entry_resolved("aa:bb:cc:dd:ee:ff", pairing, log)
    pairing.list_accessories_and_characteristics.assert_awaited_once()
    assert entry["device_id"] == "aa:bb:cc:dd:ee:ff"
    assert entry.get("category") == 9
    assert entry.get("primary_aid") == 1
    assert warns == []
    log.warning.assert_not_called()


@pytest.mark.asyncio
async def test_device_list_entry_resolved_skips_accessories_refresh_when_category_inferred(
    monkeypatch,
):
    """Ecobee-like: HAP GET omits Category but Thermostat service is present — no /accessories retry."""
    log = MagicMock()

    acc1 = MagicMock()
    acc1.aid = 1
    therm_svc = MagicMock()
    therm_svc.type = ServicesTypes.THERMOSTAT
    acc1.services = [therm_svc]

    pairing = MagicMock()
    pairing.accessories = [acc1]
    pairing.list_accessories_and_characteristics = AsyncMock()

    meta_no_cat = {"manufacturer": "Ecobee", "model": "SmartThermostat"}

    async def fake_reads(p, a, lg, **kwargs):
        return dict(meta_no_cat)

    monkeypatch.setattr(
        "homekit_hub.bridge._representative_accessory", lambda p: acc1 if p.accessories else None
    )
    monkeypatch.setattr(
        "homekit_hub.bridge._accessory_information_metadata_with_reads", fake_reads
    )

    entry, warns = await _device_list_entry_resolved("aa:bb:cc:dd:ee:ff", pairing, log)
    pairing.list_accessories_and_characteristics.assert_not_called()
    assert entry.get("category") == 9
    assert entry.get("primary_aid") == 1
    assert warns == []


def test_ensure_top_level_pairing_registered_mirrors_aggregate_controller():
    """Fresh PIN pairing only registers on the IP transport; hub mirrors HKController.load_pairing."""
    log = logging.getLogger("test_ehk")
    bridge = HomeKitHubBridge(
        log,
        get_params=lambda: {},
        get_pairing_slot_rows=lambda: [],
        get_custom_data=lambda: {},
        set_custom_data=lambda _d: None,
    )
    hk = MagicMock()
    hk.pairings = {}
    hk.aliases = {}
    bridge._hk = hk
    pairing = MagicMock()
    pairing.pairing_data = {"AccessoryPairingID": "DC:9B:CC:02:9A:FE"}
    bridge._ensure_top_level_pairing_registered("slot_1", pairing)
    assert hk.pairings["dc:9b:cc:02:9a:fe"] is pairing
    assert hk.aliases["slot_1"] is pairing


def test_ensure_top_level_pairing_registered_noops_without_hub_or_ids():
    log = logging.getLogger("test_ehk2")
    bridge = HomeKitHubBridge(
        log,
        get_params=lambda: {},
        get_pairing_slot_rows=lambda: [],
        get_custom_data=lambda: {},
        set_custom_data=lambda _d: None,
    )
    pairing = MagicMock()
    pairing.pairing_data = {}
    bridge._ensure_top_level_pairing_registered("slot_1", pairing)
    bridge._hk = MagicMock()
    bridge._hk.pairings = {}
    bridge._hk.aliases = {}
    bridge._ensure_top_level_pairing_registered("slot_1", pairing)
    assert bridge._hk.pairings == {}
    assert bridge._hk.aliases == {}


def test_default_outbound_ip_returns_string_or_none(monkeypatch):
    sock = MagicMock()
    sock.getsockname.return_value = ("192.168.1.5", 0)
    monkeypatch.setattr("homekit_hub.bridge.socket.socket", lambda *a, **k: sock)
    assert default_outbound_ip() == "192.168.1.5"


def test_format_zeroconf_diag_log_summary_includes_key_fields():
    diag = {
        "zeroconf_mode": "auto",
        "using_unicast": False,
        "using_explicit_interface_ips": True,
        "zeroconf_interface_ips": ["192.168.222.10"],
        "discover_network_rows": 1,
        "primary_network_broadcast": "192.168.1.255",
        "primary_network_local_ip": "192.168.1.5",
        "poly_network_interface": {"addr": "192.168.55.22"},
        "default_outbound_ip": "192.168.55.22",
        "mdns_5353_probe": "udp_5353_bind_ok",
        "platform": "freebsd14",
    }
    summary = format_zeroconf_diag_log_summary(diag)
    assert "mode=auto" in summary
    assert "iface_ips=['192.168.222.10']" in summary
    assert "poly_addr=192.168.55.22" in summary


def test_discover_network_hints_bsd_auto_bind_failed():
    diag = {
        "platform": "freebsd14",
        "discover_network_rows": 0,
        "using_explicit_interface_ips": False,
        "zeroconf_interface_bind_source": "none",
        "zeroconf_interface_ips": [],
        "hub_running": True,
        "using_unicast": False,
        "mdns_5353_probe": "udp_5353_bind_ok",
    }
    hints = discover_network_hints(diag, accessories_found=0)
    assert any("Automatic primary interface bind failed" in h for h in hints)


def test_discover_network_hints_bsd_auto_bind_active_other_vlan():
    diag = {
        "platform": "freebsd14",
        "discover_network_rows": 0,
        "using_explicit_interface_ips": True,
        "zeroconf_interface_bind_source": "auto_primary",
        "zeroconf_interface_ips": ["192.168.1.5"],
        "hub_running": True,
        "using_unicast": False,
        "mdns_5353_probe": "udp_5353_bind_ok",
    }
    hints = discover_network_hints(diag, accessories_found=0)
    assert any("Automatic primary interface bind is active" in h for h in hints)


def test_discover_network_hints_rows_but_no_iface_ips():
    diag = {
        "platform": "linux",
        "discover_network_rows": 2,
        "using_explicit_interface_ips": False,
        "zeroconf_interface_ips": [],
        "hub_running": True,
        "using_unicast": False,
        "mdns_5353_probe": "udp_5353_bind_ok",
    }
    hints = discover_network_hints(diag, accessories_found=0)
    assert any("zeroconf_interface_ips is empty" in h for h in hints)


def test_discover_network_hints_mismatch_poly_and_outbound():
    diag = {
        "platform": "freebsd14",
        "discover_network_rows": 0,
        "using_explicit_interface_ips": False,
        "zeroconf_interface_ips": [],
        "hub_running": True,
        "using_unicast": False,
        "mdns_5353_probe": "udp_5353_bind_ok",
        "primary_network_broadcast": "192.168.1.255",
        "poly_network_interface": {"addr": "192.168.1.5"},
        "default_outbound_ip": "192.168.55.22",
    }
    hints = discover_network_hints(diag, accessories_found=0)
    assert any("differs from default outbound IP" in h for h in hints)


def test_ephemeral_discover_probe_overrides_bsd_auto_primary():
    params = {"zeroconf_unicast": "on", "zeroconf_interfaces": ""}
    diag = {
        "platform": "freebsd14",
        "zeroconf_interface_bind_source": "auto_primary",
    }
    overrides = ephemeral_discover_probe_overrides(params, diag)
    assert overrides == [{"zeroconf_interfaces": "all"}]


def test_ephemeral_discover_probe_overrides_skips_when_already_all():
    params = {"zeroconf_unicast": "auto", "zeroconf_interfaces": "all"}
    diag = {"platform": "freebsd14", "zeroconf_interface_bind_source": "none"}
    assert ephemeral_discover_probe_overrides(params, diag) == []


def test_ephemeral_discover_probe_overrides_unicast_off():
    params = {"zeroconf_unicast": "off", "zeroconf_interfaces": ""}
    diag = {
        "platform": "linux",
        "zeroconf_interface_bind_source": "none",
    }
    overrides = ephemeral_discover_probe_overrides(params, diag)
    assert {"zeroconf_unicast": "auto"} in overrides


def test_discover_probe_hints_auto_applied():
    diag = {
        "zeroconf_interface_bind_source": "auto_primary",
        "zeroconf_interface_ips": ["192.168.1.25"],
    }
    hints = discover_probe_hints(
        [],
        auto_applied_override={"zeroconf_interfaces": "all"},
        primary_diag=diag,
    )
    assert any("auto_primary bind on 192.168.1.25" in h for h in hints)
    assert any("zeroconf_interfaces=all" in h for h in hints)


def test_format_probe_success_explanation_auto_primary():
    diag = {
        "zeroconf_interface_bind_source": "auto_primary",
        "zeroconf_interface_ips": ["192.168.1.25"],
    }
    text = format_probe_success_explanation(diag, {"zeroconf_interfaces": "all"})
    assert "192.168.1.25" in text
    assert "zeroconf_interfaces=all" in text


def test_discover_primary_timeout_sec_shortened_on_bsd():
    params = {"zeroconf_unicast": "on", "zeroconf_interfaces": ""}
    diag = {
        "platform": "freebsd14",
        "zeroconf_interface_bind_source": "auto_primary",
    }
    assert discover_primary_timeout_sec(params, diag, default_sec=12.0, shortened_sec=6.0) == 6.0


def test_discover_primary_timeout_sec_default_when_no_probes():
    params = {"zeroconf_unicast": "auto", "zeroconf_interfaces": "all"}
    diag = {"platform": "freebsd14", "zeroconf_interface_bind_source": "none"}
    assert discover_primary_timeout_sec(params, diag, default_sec=12.0, shortened_sec=6.0) == 12.0


def test_discover_probe_hints_all_failed():
    history = [
        {"label": "Primary", "accessories_found": 0},
        {"label": "Probe 1", "accessories_found": 0},
    ]
    hints = discover_probe_hints(history)
    assert any("All discover attempts" in h for h in hints)


def test_format_discover_attempt_history_html():
    html_out = format_discover_attempt_history_html(
        [{"label": "Primary scan", "accessories_found": 0}]
    )
    assert "Discover attempt history" in html_out
    assert "Primary scan" in html_out


def _health_probe_bridge() -> HomeKitHubBridge:
    bridge = HomeKitHubBridge(
        logging.getLogger("test_pairing_health"),
        get_params=lambda: {},
        get_pairing_slot_rows=lambda: [],
        get_custom_data=lambda: {},
        set_custom_data=lambda _d: None,
    )
    bridge._running = True
    bridge._broadcast_device_list_update = AsyncMock()
    bridge._on_pairing_ready = AsyncMock()
    bridge._resync_ip_pairing_zeroconf = AsyncMock()
    return bridge


def _pairing(device_id: str) -> MagicMock:
    pairing = MagicMock()
    pairing.id = device_id
    pairing.pairing_data = {}
    pairing.list_accessories_and_characteristics = AsyncMock(return_value=[])
    pairing.subscribe = AsyncMock()
    pairing.dispatcher_connect = MagicMock(return_value=lambda: None)
    return pairing


def _fast_health_probe(monkeypatch) -> None:
    for name, value in (
        ("PAIRING_HEALTH_POST_RESYNC_RETRIES", 1),
        ("PAIRING_HEALTH_POST_RESYNC_DELAY_SEC", 0),
        ("PAIRING_HEALTH_POST_RESYNC_INITIAL_SETTLE_SEC", 0),
        ("PAIRING_HEALTH_RETRY_LIST_SLEEP_SEC", 0),
        ("PAIRING_HEALTH_RELOAD_SETTLE_SEC", 0),
        ("PAIRING_HEALTH_RELOAD_LIST_TRIES", 1),
    ):
        monkeypatch.setattr(f"homekit_hub.bridge.{name}", value)


@pytest.mark.asyncio
async def test_health_probe_skips_restore_when_read_succeeds(monkeypatch):
    """A healthy probe must not re-subscribe or push list_devices every pass."""
    monkeypatch.setattr(
        "homekit_hub.bridge._subscribable_characteristics", lambda _p: [(1, 8)]
    )
    bridge = _health_probe_bridge()
    pairing = _pairing("96:c0:24:95:af:4e")
    hk = MagicMock()
    hk.aliases = {"slot_1": pairing}
    bridge._hk = hk

    await bridge._probe_pairings_health_once()

    pairing.subscribe.assert_not_called()
    bridge._broadcast_device_list_update.assert_not_called()
    assert "slot_1" not in bridge._pairing_unhealthy_aliases


@pytest.mark.asyncio
async def test_health_probe_restores_events_after_same_cycle_reload(monkeypatch):
    """Power-loss reload succeeds before the alias is marked unhealthy.

    That used to skip subscribe, so events stayed dead while snapshots worked.
    """
    _fast_health_probe(monkeypatch)
    monkeypatch.setattr(
        "homekit_hub.bridge._subscribable_characteristics", lambda _p: [(1, 12)]
    )
    bridge = _health_probe_bridge()
    down = _pairing("96:c0:24:95:af:4e")
    down.list_accessories_and_characteristics = AsyncMock(
        side_effect=RuntimeError("accessory unreachable")
    )
    restored = _pairing("96:c0:24:95:af:4e")
    hk = MagicMock()
    hk.aliases = {"slot_1": down}
    bridge._hk = hk

    async def reload(alias: str) -> bool:
        hk.aliases[alias] = restored
        return True

    bridge._reload_saved_pairing_for_alias = AsyncMock(side_effect=reload)

    await bridge._probe_pairings_health_once()

    restored.subscribe.assert_awaited_once_with([(1, 12)])
    restored.dispatcher_connect.assert_called_once()
    bridge._broadcast_device_list_update.assert_awaited_once_with(
        reason="health_recovered:slot_1"
    )
    assert "slot_1" not in bridge._pairing_unhealthy_aliases
    bridge._on_pairing_ready.assert_awaited()


@pytest.mark.asyncio
async def test_health_probe_restores_events_when_alias_already_unhealthy(monkeypatch):
    monkeypatch.setattr(
        "homekit_hub.bridge._subscribable_characteristics", lambda _p: [(1, 9)]
    )
    bridge = _health_probe_bridge()
    pairing = _pairing("5d:1a:eb:a5:3c:3e")
    hk = MagicMock()
    hk.aliases = {"slot_2": pairing}
    bridge._hk = hk
    bridge._pairing_unhealthy_aliases.add("slot_2")

    await bridge._probe_pairings_health_once()

    pairing.subscribe.assert_awaited_once_with([(1, 9)])
    bridge._broadcast_device_list_update.assert_awaited_once_with(
        reason="health_recovered:slot_2"
    )
    assert bridge._pairing_unhealthy_aliases == set()


@pytest.mark.asyncio
async def test_health_probe_retries_subscribe_after_reload_failure(monkeypatch):
    _fast_health_probe(monkeypatch)
    monkeypatch.setattr(
        "homekit_hub.bridge._subscribable_characteristics", lambda _p: [(1, 4)]
    )
    bridge = _health_probe_bridge()
    down = _pairing("96:c0:24:95:af:4e")
    down.list_accessories_and_characteristics = AsyncMock(
        side_effect=RuntimeError("accessory unreachable")
    )
    restored = _pairing("96:c0:24:95:af:4e")
    restored.subscribe = AsyncMock(side_effect=RuntimeError("subscribe rejected"))
    hk = MagicMock()
    hk.aliases = {"slot_1": down}
    bridge._hk = hk

    async def reload(alias: str) -> bool:
        hk.aliases[alias] = restored
        return True

    bridge._reload_saved_pairing_for_alias = AsyncMock(side_effect=reload)

    await bridge._probe_pairings_health_once()

    assert bridge._pairing_unhealthy_aliases == {"slot_1"}
    bridge._broadcast_device_list_update.assert_not_called()

    restored.list_accessories_and_characteristics = AsyncMock(return_value=[])
    restored.subscribe = AsyncMock()
    await bridge._probe_pairings_health_once()

    restored.subscribe.assert_awaited_once_with([(1, 4)])
    bridge._broadcast_device_list_update.assert_awaited_once_with(
        reason="health_recovered:slot_1"
    )
    assert bridge._pairing_unhealthy_aliases == set()

