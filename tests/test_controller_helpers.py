"""Tests for controller helpers (no Polyglot / bridge loop)."""

from __future__ import annotations

from unittest.mock import MagicMock


from homekit_hub.bridge import DATA_KEY_LAST_HAP_DISCOVER, TYPED_PAIRING_SLOTS_KEY
from nodes.Controller import ERR_ASYNC_LOOP_DEAD, Controller, _DEFAULT_BRIDGE_PARAMS


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


class FakeParams:
    def __init__(self, d: dict | None = None):
        self._d = dict(d or {})

    def get(self, key, default=None):
        return self._d.get(key, default)

    def __contains__(self, key):
        return key in self._d

    def __setitem__(self, key, value):
        self._d[key] = value


def _bare_controller():
    c = Controller.__new__(Controller)
    c.report_error = MagicMock()
    c._maybe_restart_on_config_change = MagicMock()
    c.ready = False
    c.setDriver = MagicMock()
    c.handler_params_st = None
    c.handler_data_st = None
    c.handler_typedparams_st = None
    c.handler_typed_data_st = None
    return c


def test_refresh_change_node_names_default_true():
    c = _bare_controller()
    c.Params = FakeParams({})
    c._refresh_change_node_names_flag()
    assert c.change_node_names is True


def test_refresh_change_node_names_custom_param_false():
    c = _bare_controller()
    c.Params = FakeParams({"change_node_names": "false"})
    c._refresh_change_node_names_flag()
    assert c.change_node_names is False


def test_ensure_default_custom_params_seeds_missing_keys():
    c = _bare_controller()
    c.Params = FakeParams({"ws_host": "127.0.0.1"})
    c._ensure_default_custom_params()
    for key in _DEFAULT_BRIDGE_PARAMS:
        assert key in c.Params
    assert c.Params.get("change_node_names") == "true"


def test_custom_handlers_have_run_false_until_all_set():
    c = _bare_controller()
    assert c._custom_handlers_have_run() is False
    c.handler_params_st = True
    c.handler_data_st = False
    c.handler_typedparams_st = True
    c.handler_typed_data_st = True
    assert c._custom_handlers_have_run() is True


def test_typed_update_needs_discover_false_when_no_pin():
    c = _bare_controller()
    c.TypedData = FakeTypedData({TYPED_PAIRING_SLOTS_KEY: [{"hap_pin": "", "accessory_id": ""}]})
    c.Data = FakeData({})
    assert c._typed_update_needs_discover() is False


def test_typed_update_needs_discover_true_when_pin_no_filter_and_no_last():
    c = _bare_controller()
    c.TypedData = FakeTypedData(
        {
            TYPED_PAIRING_SLOTS_KEY: [
                {"hap_pin": "12345678", "accessory_id": "", "accessory_name": ""}
            ]
        }
    )
    c.Data = FakeData({DATA_KEY_LAST_HAP_DISCOVER: []})
    assert c._typed_update_needs_discover() is True


def test_typed_update_needs_discover_false_when_last_populated():
    c = _bare_controller()
    c.TypedData = FakeTypedData(
        {
            TYPED_PAIRING_SLOTS_KEY: [
                {"hap_pin": "12345678", "accessory_id": "", "accessory_name": ""}
            ]
        }
    )
    c.Data = FakeData({DATA_KEY_LAST_HAP_DISCOVER: [{"id": "x", "paired": False}]})
    assert c._typed_update_needs_discover() is False


def test_typed_update_needs_discover_false_when_filter_set():
    c = _bare_controller()
    c.TypedData = FakeTypedData(
        {
            TYPED_PAIRING_SLOTS_KEY: [
                {"hap_pin": "12345678", "accessory_id": "aa:bb", "accessory_name": ""}
            ]
        }
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
    assert by_id["dd:ee:ff"]["generic_nodes"] == "false"
    assert by_id["11:22:33"]["generic_nodes"] == "false"


def test_ensure_pairing_row_generic_nodes_default_seeds_blank_rows():
    c = _bare_controller()
    c.TypedData = FakeTypedData(
        {
            TYPED_PAIRING_SLOTS_KEY: [
                {"hap_pin": "123-45-678", "generic_nodes": ""},
                {"hap_pin": "111-11-111", "generic_nodes": "true"},
            ]
        }
    )
    assert c._ensure_pairing_row_generic_nodes_default() is True
    rows = c.TypedData.loads[0][0][TYPED_PAIRING_SLOTS_KEY]
    assert rows[0]["generic_nodes"] == "false"
    assert rows[1]["generic_nodes"] == "true"


def test_ensure_pairing_row_generic_nodes_default_noop_when_set():
    c = _bare_controller()
    c.TypedData = FakeTypedData(
        {TYPED_PAIRING_SLOTS_KEY: [{"generic_nodes": "false"}]}
    )
    assert c._ensure_pairing_row_generic_nodes_default() is False
    assert not c.TypedData.loads


def test_append_pairing_rows_for_discover_empty():
    c = _bare_controller()
    c.TypedData = FakeTypedData({TYPED_PAIRING_SLOTS_KEY: []})
    assert c._append_pairing_rows_for_discover([]) == (0, 0, 0)


def test_check_asyncio_loop_thread_health_noop_when_alive():
    c = _bare_controller()
    c.ready = True
    alive = MagicMock()
    alive.is_alive.return_value = True
    c._loop_thread = alive
    c._check_asyncio_loop_thread_health()
    c.report_error.assert_not_called()
    assert c.ready is True


def test_check_asyncio_loop_thread_health_noop_when_dead_but_not_ready():
    c = _bare_controller()
    c.ready = False
    dead = MagicMock()
    dead.is_alive.return_value = False
    c._loop_thread = dead
    c._check_asyncio_loop_thread_health()
    c.report_error.assert_not_called()


def test_check_asyncio_loop_thread_health_reports_when_dead_and_ready():
    c = _bare_controller()
    c.ready = True
    c._async_loop_death_reported = False
    dead = MagicMock()
    dead.is_alive.return_value = False
    c._loop_thread = dead
    c._check_asyncio_loop_thread_health()
    c.report_error.assert_called_once()
    args, kwargs = c.report_error.call_args
    assert args[0] == ERR_ASYNC_LOOP_DEAD
    assert kwargs.get("set_st_error") is True
    assert c.ready is False
    c.setDriver.assert_not_called()
    c.report_error.reset_mock()
    c._check_asyncio_loop_thread_health()
    c.report_error.assert_not_called()


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


def test_handler_stop_clears_generic_nodes_without_deleting():
    c = _bare_controller()
    c._generic_nodes = {'ge3811269468c9': MagicMock(address='ge3811269468c9')}
    c._paired_nodes = {'c': MagicMock()}
    c.poly = MagicMock()
    c.bridge = None
    c.mainloop = None
    c._mqtt_transport_driver = 0
    c.handler_stop()
    assert c._generic_nodes == {}
    assert c._paired_nodes == {}
    c.poly.delNode.assert_not_called()
