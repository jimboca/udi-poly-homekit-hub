"""Tests for controller helpers (no Polyglot / bridge loop)."""

from __future__ import annotations

from unittest.mock import MagicMock


from homekit_hub.bridge import DATA_KEY_LAST_HAP_DISCOVER, TYPED_PAIRING_SLOTS_KEY
from nodes.Controller import Controller


class FakeTypedData:
    def __init__(self, store: dict):
        self._store = dict(store)
        self.loads: list[tuple[dict, bool]] = []

    def get(self, key, default=None):
        return self._store.get(key, default)

    def keys(self):
        return list(self._store.keys())

    def __getitem__(self, k):
        return self._store[k]

    def load(self, data, save=True):
        self.loads.append((dict(data), save))
        self._store = dict(data)


class FakeData:
    def __init__(self, d: dict | None = None):
        self._d = dict(d or {})

    def get(self, key, default=None):
        return self._d.get(key, default)


def _bare_controller():
    c = Controller.__new__(Controller)
    c.report_error = MagicMock()
    c._maybe_restart_on_config_change = MagicMock()
    c.ready = False
    return c


def test_typed_update_needs_discover_false_when_no_pin():
    c = _bare_controller()
    c.TypedData = FakeTypedData({TYPED_PAIRING_SLOTS_KEY: [{"hap_pin": "", "accessory_id": ""}]})
    c.Data = FakeData({})
    assert c._typed_update_needs_discover() is False


def test_typed_update_needs_discover_true_when_pin_no_filter_and_no_last():
    c = _bare_controller()
    c.TypedData = FakeTypedData(
        {TYPED_PAIRING_SLOTS_KEY: [{"hap_pin": "12345678", "accessory_id": "", "accessory_name": ""}]}
    )
    c.Data = FakeData({DATA_KEY_LAST_HAP_DISCOVER: []})
    assert c._typed_update_needs_discover() is True


def test_typed_update_needs_discover_false_when_last_populated():
    c = _bare_controller()
    c.TypedData = FakeTypedData(
        {TYPED_PAIRING_SLOTS_KEY: [{"hap_pin": "12345678", "accessory_id": "", "accessory_name": ""}]}
    )
    c.Data = FakeData({DATA_KEY_LAST_HAP_DISCOVER: [{"id": "x", "paired": False}]})
    assert c._typed_update_needs_discover() is False


def test_typed_update_needs_discover_false_when_filter_set():
    c = _bare_controller()
    c.TypedData = FakeTypedData(
        {TYPED_PAIRING_SLOTS_KEY: [{"hap_pin": "12345678", "accessory_id": "aa:bb", "accessory_name": ""}]}
    )
    c.Data = FakeData({})
    assert c._typed_update_needs_discover() is False


def test_append_pairing_rows_for_discover_merge_fill_append():
    c = _bare_controller()
    c.TypedData = FakeTypedData(
        {
            TYPED_PAIRING_SLOTS_KEY: [
                {
                    "slot": "1",
                    "hap_pin": "11111111",
                    "accessory_id": "aa:bb:cc",
                    "accessory_name": "",
                    "discover_endpoint": "",
                },
                {"slot": "2", "hap_pin": "", "accessory_id": "", "accessory_name": ""},
            ],
            "other_key": "keep",
        }
    )
    discover_rows = [
        {"id": "AA:BB:CC", "name": "MergedName", "paired": False, "host": "10.0.0.1", "port": 99},
        {"id": "dd:ee:ff", "name": "FillDev", "paired": False, "host": "10.0.0.2", "port": 100},
        {"id": "11:22:33", "name": "Appended", "paired": False},
    ]
    n_app, n_fill, n_merge = c._append_pairing_rows_for_discover(discover_rows)
    assert (n_app, n_fill, n_merge) == (1, 1, 1)
    assert len(c.TypedData.loads) == 1
    saved, _save = c.TypedData.loads[0]
    assert saved["other_key"] == "keep"
    rows = saved[TYPED_PAIRING_SLOTS_KEY]
    by_id = {(r.get("accessory_id") or "").lower(): r for r in rows}
    assert by_id["aa:bb:cc"]["accessory_name"] == "MergedName"
    assert by_id["aa:bb:cc"]["discover_endpoint"] == "10.0.0.1:99"
    assert by_id["dd:ee:ff"]["accessory_id"] == "dd:ee:ff"
    assert by_id["dd:ee:ff"]["accessory_name"] == "FillDev"
    assert by_id["11:22:33"]["accessory_name"] == "Appended"
    assert "slot" in by_id["11:22:33"]


def test_append_pairing_rows_for_discover_empty():
    c = _bare_controller()
    c.TypedData = FakeTypedData({TYPED_PAIRING_SLOTS_KEY: []})
    assert c._append_pairing_rows_for_discover([]) == (0, 0, 0)


def test_append_pairing_rows_for_discover_load_failure():
    class BadTypedData(FakeTypedData):
        def load(self, data, save=True):
            raise RuntimeError("save failed")

    c = _bare_controller()
    c.TypedData = BadTypedData({TYPED_PAIRING_SLOTS_KEY: []})
    out = c._append_pairing_rows_for_discover(
        [{"id": "aa:bb", "name": "X", "paired": False}],
    )
    assert out == (0, 0, 0)
    c.report_error.assert_called()
