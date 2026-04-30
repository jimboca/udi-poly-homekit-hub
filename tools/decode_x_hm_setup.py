#!/usr/bin/env python3
"""CLI: decode a HomeKit ``X-HM://`` setup URI to JSON (setup code + metadata)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from homekit_hub.x_hm_uri import decode_x_hm_setup_uri  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "uri",
        nargs="?",
        help="Full X-HM:// URI (or pass via stdin if omitted)",
    )
    args = p.parse_args()
    raw = args.uri
    if raw is None:
        raw = sys.stdin.read().strip()
    try:
        out = decode_x_hm_setup_uri(raw)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
