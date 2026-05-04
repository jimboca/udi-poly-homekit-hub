"""Unit tests for MQTT topic helpers (no broker required)."""

from __future__ import annotations

import pytest

from homekit_hub.mqtt_topics import (
    DEFAULT_MQTT_BROKER_HOST,
    DEFAULT_MQTT_BROKER_PORT,
    MQTT_QOS_AT_LEAST_ONCE,
    MQTT_TRANSPORT_STATUS_CONNECTED,
    MQTT_TRANSPORT_STATUS_DISABLED,
    MQTT_TRANSPORT_STATUS_NOT_CONNECTED,
    clients_ingress_subscribe_pattern,
    normalize_hub_slug_param,
    parse_ingress_client_slug,
    sanitize_client_slug,
)


def test_default_mqtt_broker_matches_polisy_general_mqtt() -> None:
    assert DEFAULT_MQTT_BROKER_HOST == "localhost"
    assert DEFAULT_MQTT_BROKER_PORT == 1884
    assert MQTT_QOS_AT_LEAST_ONCE == 1


def test_mqtt_transport_status_constants_for_controller_gv1() -> None:
    assert MQTT_TRANSPORT_STATUS_DISABLED == 0
    assert MQTT_TRANSPORT_STATUS_NOT_CONNECTED == 1
    assert MQTT_TRANSPORT_STATUS_CONNECTED == 2


def test_sanitize_client_slug_basic() -> None:
    assert sanitize_client_slug("udi-poly-ecobee") == "udi-poly-ecobee"
    assert sanitize_client_slug("my client") == "my_client"
    assert sanitize_client_slug("") is None


def test_sanitize_client_slug_invalid_char() -> None:
    assert sanitize_client_slug("bad!") is None


def test_normalize_hub_slug_fallback() -> None:
    assert normalize_hub_slug_param("") == "default"
    assert normalize_hub_slug_param("hub1") == "hub1"


def test_parse_ingress_client_slug() -> None:
    hub = "default"
    t = "udi/homekit/hubs/default/clients/foo/in"
    assert parse_ingress_client_slug(t, hub) == "foo"
    assert parse_ingress_client_slug("udi/homekit/hubs/other/clients/foo/in", hub) is None
    assert parse_ingress_client_slug("udi/homekit/hubs/default/clients/foo/out/rpc", hub) is None


def test_clients_ingress_subscribe_pattern() -> None:
    assert clients_ingress_subscribe_pattern("myhub") == "udi/homekit/hubs/myhub/clients/+/in"


@pytest.mark.mqtt_integration
def test_mqtt_integration_placeholder() -> None:
    """Marker hook for future mosquitto-backed tests (opt-in: ``pytest -m mqtt_integration``)."""
    pytest.skip("mosquitto integration not wired in CI by default")
