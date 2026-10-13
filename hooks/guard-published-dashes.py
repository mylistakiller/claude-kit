#!/usr/bin/env python3
"""PreToolUse hook: no em/en dash in anything published under the user's name.

The Stop hook (`verdict-evidence-guard.py`) only sees the prose of the closing message.
A Linear card body, a comment, a PR title or a commit message never appears there: it
travels as a tool input. So the typography rule of CLAUDE.md needs a second guard, on the
tools that actually publish.

Checked: the Linear write tools (title, description, patch replacements, comment body) and
the Bash commands that publish (git commit, merge, notes and tag with a message,
`gh pr|issue|release` writes, `gh api` writes) and the message files they point at (`-F`,
`--body-file`, `--input`, `<`, `$(cat x)`), read through `published_text.py`, shared with
guard-ai-attribution.py.

Quoted matter is exempt: dashes inside a code fence opened at the start of a line, an inline
code span or a `>` quote are someone else's text, not ours. Escape hatch for a verbatim citation that cannot be fenced:
put `allow-dash` in the payload.

Exit codes: 0 = allow, 2 = block (stderr goes back to Claude). Fails open.
"""

import json
import os
import re
import sys

import published_text as _pt

DASH = re.compile(r"[—–]")
QUOTE_LINE = re.compile(r"^\s*>.*$", re.M)

# Two spellings: the claude.ai connector and the engineering plugin (renamed 2026-09).
LINEAR_TOOLS = (
    "mcp__claude_ai_Linear__save_issue",
    "mcp__claude_ai_Linear__save_comment",
    "mcp__plugin_engineering_linear__save_issue",
    "mcp__plugin_engineering_linear__save_comment",
)


def own_prose(text: str) -> str:
    return QUOTE_LINE.sub(" ", _pt.own_text(text))


def collect(tool: str, ti: dict, cwd: str) -> list:
    """(field name, text) pairs that will be published."""
    out = []
    if tool in LINEAR_TOOLS:
        for key in ("title", "description", "body", "text", "label"):
            v = ti.get(key)
            if isinstance(v, str) and v:
                out.append((key, v))
        for i, op in enumerate(ti.get("patch") or []):
            for key in ("new_string", "text"):
                v = (op or {}).get(key)
                if isinstance(v, str) and v:
                    out.append((f"patch[{i}].{key}", v))
    else:
        out.extend(_pt.bash_fields(ti.get("command"), cwd))
    return out


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    tool = payload.get("tool_name") or ""
    if tool not in LINEAR_TOOLS and tool != "Bash":
        sys.exit(0)

    ti = payload.get("tool_input") or {}
    fields = collect(tool, ti, payload.get("cwd") or os.getcwd())
    if not fields:
        sys.exit(0)
    if any("allow-dash" in t.lower() for _, t in fields):
        sys.exit(0)

    guilty = []
    for name, text in fields:
        prose = own_prose(text)
        hits = DASH.findall(prose)
        if not hits:
            continue
        m = re.search(r"[^.\n]{0,60}[—–][^.\n]{0,60}", prose)
        guilty.append((name, len(hits), (m.group(0).strip() if m else "")))

    if not guilty:
        sys.exit(0)

    lines = [
        "BLOCKED: em/en dash in text about to be published under the user's name. CLAUDE.md "
        "forbids it there without exception (cards, comments, PR, commits, docs).",
    ]
    for name, n, sample in guilty:
        lines.append(f"  {name}: {n} occurrence(s), first one -> {sample}")
    lines.append(
        "Rebuild the sentence, do not swap the character: a comma for an aside, a colon when "
        "what follows explains what precedes, parentheses for a true parenthetical, or two "
        "sentences. A range takes a plain hyphen (7-30 jours, 04:00-17:00)."
    )
    lines.append(
        "Quoted matter is exempt when it sits in a code fence, an inline code span or a `>` "
        "quote. For a verbatim citation that cannot be fenced, add `allow-dash` to the payload."
    )
    sys.stderr.write("\n".join(lines) + "\n")
    sys.exit(2)


if __name__ == "__main__":
    main()
