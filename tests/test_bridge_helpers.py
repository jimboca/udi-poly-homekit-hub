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


def test_list_devices_ws_message_omits_empty_warnings():
    m = _list_devices_ws_message([{"device_id": "a"}], [])
    assert "warnings" not in m
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
