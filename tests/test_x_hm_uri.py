"""Tests for ``homekit_hub.x_hm_uri``."""

from __future__ import annotations

import pytest

from homekit_hub.x_hm_uri import decode_x_hm_setup_uri


def _b36(n: int) -> str:
    digs = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if n == 0:
        return "0"
    out: list[str] = []
    while n:
        n, r = divmod(n, 36)
        out.append(digs[r])
    return "".join(reversed(out))


def _encode_uri(*, pin: int, category: int = 2, flags: int = 2, setup_id: str = "") -> str:
    payload = 0 & 0x7
    payload = (payload << 4) | (0 & 0xF)
    payload = (payload << 8) | (category & 0xFF)
    payload = (payload << 4) | (flags & 0xF)
    payload = (payload << 27) | (pin & ((1 << 27) - 1))
    b36 = _b36(payload).rjust(9, "0")
    return f"X-HM://{b36}{setup_id}"


def test_decode_roundtrip_random_pin():
    pin = 12_345_678
    uri = _encode_uri(pin=pin)
    out = decode_x_hm_setup_uri(uri)
    assert out["setup_code_raw"] == f"{pin:08d}"
    assert out["setup_code"] == "123-45-678"


def test_decode_public_vector_46226308():
    """Community example: ``X-HM://00248GCJO`` ↔ ``46226308``."""
    out = decode_x_hm_setup_uri("X-HM://00248GCJO")
    assert out["setup_code_raw"] == "46226308"
    assert out["setup_code"] == "462-26-308"


def test_decode_with_setup_id_suffix():
    out = decode_x_hm_setup_uri("X-HM://0009N5SP0JJ3K")
    assert out.get("setup_id") == "JJ3K"
    assert "payload_base36" in out


def test_decode_rejects_bad_scheme():
    with pytest.raises(ValueError, match="X-HM"):
        decode_x_hm_setup_uri("https://example.invalid")
