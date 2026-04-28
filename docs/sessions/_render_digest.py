#!/usr/bin/env python3
"""One-shot helper: render an agent-transcripts JSONL into a readable Markdown digest.

Used once to generate ``2026-04-28_session.md`` from ``2026-04-28_session.jsonl``.
Kept in the repo so the digest can be regenerated if the JSONL is updated.

Usage::

    python docs/sessions/_render_digest.py docs/sessions/2026-04-28_session.jsonl docs/sessions/2026-04-28_session.md
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _block_to_md(block: dict) -> str:
    btype = block.get("type")
    if btype == "text":
        text = block.get("text") or ""
        return text.strip()
    if btype == "tool_use":
        name = block.get("name", "tool")
        inp = block.get("input") or {}
        if name in ("Read", "Glob", "Grep", "ReadLints", "Delete"):
            target = inp.get("path") or inp.get("glob_pattern") or inp.get("pattern") or ""
            return f"_[tool: {name} `{target}`]_"
        if name == "Shell":
            cmd = (inp.get("command") or "").splitlines()[0][:140]
            return f"_[tool: Shell `{cmd}`]_"
        if name == "Write":
            return f"_[tool: Write `{inp.get('path','')}` ({len(inp.get('contents') or '')} chars)]_"
        if name == "StrReplace":
            return f"_[tool: StrReplace `{inp.get('path','')}`]_"
        if name == "TodoWrite":
            todos = inp.get("todos") or []
            return f"_[tool: TodoWrite ({len(todos)} todos, merge={inp.get('merge')})]_"
        if name == "AskQuestion":
            qs = inp.get("questions") or []
            return f"_[tool: AskQuestion ({len(qs)} question(s))]_"
        if name == "WebFetch":
            return f"_[tool: WebFetch `{inp.get('url','')}`]_"
        if name == "WebSearch":
            return f"_[tool: WebSearch \"{inp.get('search_term','')}\"]_"
        if name == "SwitchMode":
            return f"_[tool: SwitchMode -> {inp.get('target_mode_id','')}]_"
        return f"_[tool: {name}]_"
    return ""


def render(jsonl_path: Path) -> str:
    out: list[str] = []
    out.append(f"# Session digest: `{jsonl_path.name}`\n")
    out.append(
        "Auto-generated from the JSONL transcript next to this file. "
        "Tool internals (file reads, shell calls, search results) are summarized; "
        "user/assistant prose is preserved verbatim.\n"
    )
    out.append("---\n")

    last_role: str | None = None
    turn = 0
    with jsonl_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            role = evt.get("role")
            msg = evt.get("message") or {}
            content = msg.get("content") or []
            if role != last_role:
                turn += 1
                if role == "user":
                    out.append(f"\n## Turn {turn} — user\n")
                elif role == "assistant":
                    out.append(f"\n## Turn {turn} — assistant\n")
                else:
                    out.append(f"\n## Turn {turn} — {role}\n")
                last_role = role
            for block in content:
                rendered = _block_to_md(block)
                if rendered:
                    out.append(rendered + "\n")
    return "\n".join(out)


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    md = render(src)
    dst.write_text(md, encoding="utf-8")
    print(f"wrote {dst} ({len(md)} chars)")


if __name__ == "__main__":
    main()
