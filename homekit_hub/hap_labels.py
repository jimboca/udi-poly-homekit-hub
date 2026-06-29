"""HAP characteristic label helpers (shared; avoids bridge ↔ inventory circular imports)."""

from __future__ import annotations

from aiohomekit.model.characteristics import CharacteristicsTypes
from aiohomekit.uuid import normalize_uuid

_UUID_TO_NAME: dict[str, str] = {}


def _build_uuid_to_name() -> dict[str, str]:
    out: dict[str, str] = {}
    for attr in dir(CharacteristicsTypes):
        if attr.startswith('_'):
            continue
        try:
            out[normalize_uuid(getattr(CharacteristicsTypes, attr))] = attr
        except Exception:
            continue
    return out


_UUID_TO_NAME = _build_uuid_to_name()


def characteristic_label(type_uuid: str) -> str:
    nu = normalize_uuid(type_uuid)
    return _UUID_TO_NAME.get(nu, nu)
