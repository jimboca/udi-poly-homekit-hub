"""MQTT topic helpers for the HomeKit hub (per-client trees; see PROTOCOL.md)."""

from __future__ import annotations

import re
from typing import Any, Final

ROOT: Final[str] = "udi/homekit/hubs"

# Defaults align with Polisy/eISY general MQTT (PG3 / Tasmota-style brokers on 1884).
DEFAULT_MQTT_BROKER_HOST: Final[str] = "localhost"
DEFAULT_MQTT_BROKER_PORT: Final[int] = 1884

# aiomqtt 2.x ``subscribe`` / ``publish`` take ``qos: int`` (0/1/2). Older docs used ``aiomqtt.QoS``.
MQTT_QOS_AT_LEAST_ONCE: Final[int] = 1

# IoX hub controller driver **GV1** (UOM 25 index); see profile NLS **MQTTS-***.
MQTT_TRANSPORT_STATUS_DISABLED: Final[int] = 0
MQTT_TRANSPORT_STATUS_NOT_CONNECTED: Final[int] = 1
MQTT_TRANSPORT_STATUS_CONNECTED: Final[int] = 2

_TOPIC_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def is_valid_topic_slug(segment: str) -> bool:
    return bool(segment and _TOPIC_SLUG_RE.match(segment))


def sanitize_client_slug(raw: object) -> str | None:
    """Map a logical client name to a broker-safe slug, or None if unusable.

    Allowed output characters: ``[A-Za-z0-9_-]`` (length 1..128). Spaces and
    common punctuation collapse to ``_``; characters outside the allowed set
    make the value invalid (None).
    """
    s = "" if raw is None else str(raw).strip()
    if not s:
        return None
    out: list[str] = []
    for ch in s:
        if ch.isalnum() or ch in "_-":
            out.append(ch)
        elif ch in " .:@/":
            out.append("_")
        else:
            return None
    slug = "".join(out)
    if not slug or len(slug) > 128:
        return None
    return slug


def normalize_hub_slug_param(raw: object) -> str:
    slug = sanitize_client_slug(raw)
    if slug:
        return slug
    return "default"


def mqtt_transport_enabled(params: dict[str, Any] | None) -> bool:
    if not params:
        return False
    v = str(params.get("mqtt_enable") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def hub_root_topic(hub_slug: str) -> str:
    return f"{ROOT}/{hub_slug}"


def clients_ingress_subscribe_pattern(hub_slug: str) -> str:
    return f"{hub_root_topic(hub_slug)}/clients/+/in"


def client_ingress_topic(hub_slug: str, client_slug: str) -> str:
    return f"{hub_root_topic(hub_slug)}/clients/{client_slug}/in"


def client_out_rpc_topic(hub_slug: str, client_slug: str) -> str:
    return f"{hub_root_topic(hub_slug)}/clients/{client_slug}/out/rpc"


def client_out_event_topic(hub_slug: str, client_slug: str) -> str:
    return f"{hub_root_topic(hub_slug)}/clients/{client_slug}/out/event"


def parse_ingress_client_slug(topic: str, expected_hub_slug: str) -> str | None:
    """Return ``client_slug`` from ``.../hubs/<hub>/clients/<slug>/in`` or None."""
    parts = topic.split("/")
    if len(parts) != 7:
        return None
    if parts[0:3] != ["udi", "homekit", "hubs"]:
        return None
    hub = parts[3]
    if hub != expected_hub_slug:
        return None
    if parts[4] != "clients" or parts[6] != "in":
        return None
    slug = parts[5]
    if not is_valid_topic_slug(slug):
        return None
    return slug
