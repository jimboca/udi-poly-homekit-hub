#!/usr/bin/env python3
"""Remove %% professional-only begin/end blocks from source files (Standard zip build)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

MARKER_BEGIN = '# %% professional-only begin'
MARKER_END = '# %% professional-only end'


def strip_professional_blocks(text: str) -> str:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    skip = False
    for line in lines:
        if MARKER_BEGIN in line:
            skip = True
            continue
        if MARKER_END in line:
            skip = False
            continue
        if not skip:
            out.append(line)
    return ''.join(out)


def strip_file(path: Path) -> bool:
    original = path.read_text(encoding='utf-8')
    stripped = strip_professional_blocks(original)
    if stripped == original:
        return False
    path.write_text(stripped, encoding='utf-8')
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Strip Professional-only marker blocks from sources.')
    parser.add_argument('files', nargs='+', help='Files to strip in place')
    args = parser.parse_args(argv)
    changed = 0
    for raw in args.files:
        path = Path(raw)
        if not path.is_file():
            print(f'missing: {path}', file=sys.stderr)
            return 1
        if strip_file(path):
            changed += 1
            print(f'stripped: {path}')
    print(f'done ({changed} file(s) modified)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
