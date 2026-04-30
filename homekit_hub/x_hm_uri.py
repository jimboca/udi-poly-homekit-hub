"""Decode HomeKit ``X-HM://`` setup URIs (QR / vendor app payloads) for operator support."""

from __future__ import annotations

import re
from typing import Any


def decode_x_hm_setup_uri(uri: str) -> dict[str, Any]:
    """
    Parse a HomeKit setup URI into setup code and metadata.

    Typical form: ``X-HM://`` + nine base-36 digits + four-character setup id (e.g. ``HSPN``).
    Some vendor URIs append extra base-36 segments; those are returned as ``extra``.
    """
    s = (uri or "").strip()
    if not s:
        raise ValueError("empty URI")
    head, sep, rest = s.partition("://")
    if sep != "://" or not head.upper().startswith("X-HM"):
        raise ValueError("expected scheme X-HM://")
    body = rest.strip()
    m = re.match(r"(?i)^([0-9a-z]{9})([0-9a-z]{4})(.*)$", body)
    setup_id = ""
    extra = ""
    if m:
        b36 = m.group(1).upper()
        setup_id = m.group(2).upper()
        extra = (m.group(3) or "").strip()
    else:
        m2 = re.match(r"(?i)^([0-9a-z]{9})", body)
        if not m2:
            raise ValueError("expected nine base-36 payload digits after X-HM://")
        b36 = m2.group(1).upper()
        extra = body[len(m2.group(1)) :].strip()

    word = int(b36, 36)
    # HAP payload: setup code occupies the low **27** bits (not 31); see HomeSpan QRCodes.md.
    pin = word & ((1 << 27) - 1)
    x = word >> 27
    flags = x & 0xF
    x >>= 4
    category = x & 0xFF
    x >>= 8
    reserved = x & 0xF
    x >>= 4
    version = x & 0x7

    digits = f"{pin:08d}"
    dashed = f"{digits[:3]}-{digits[3:5]}-{digits[5:8]}"
    out: dict[str, Any] = {
        "setup_uri": s,
        "payload_base36": b36,
        "setup_code": dashed,
        "setup_code_raw": digits,
        "category": category,
        "flags": flags,
        "reserved": reserved,
        "version": version,
    }
    if setup_id:
        out["setup_id"] = setup_id
    if extra:
        out["extra"] = extra
    return out
