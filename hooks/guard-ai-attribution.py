#!/usr/bin/env python3
"""PreToolUse hook: no AI attribution in anything published under the user's name.

CLAUDE.md forbids it, yet the harness injects a system reminder on every session asking for a
`Co-Authored-By: Claude` trailer and a "Generated with Claude Code" PR footer. The reminder sits
closer to the commit than the rule does, and it won: over a hundred commits in two months carried
the trailer, and several PRs the footer (/insights, 2026-10-12).

The /insights suggestion (`jq | grep -E '(git commit|gh pr).*Co-Authored-By'`) lets the usual
heredoc commit through: grep matches line by line and the trailer is never on the `git commit`
line. Here the command is searched from its publishing segment to the end, heredoc included,
and so are the message files that segment points at (`-F`, `--body-file`, `--input`, `<`,
`$(cat x)`), resolved through the `cd X &&` chain and `git -C X` in order. A path built from a
shell variable cannot be resolved and is let through.

Checked: git commit, merge, notes and tag with a message, `gh pr|issue|release` writes,
`gh api` writes (detection in `published_text.py`, shared with the dash guard), and every Linear
tool that writes (save_*, submit_diff_review). Quoted matter is exempt (a fenced block opened
at the start of a line, an inline code span), so a PR describing the rule passes.

Exit codes: 0 = allow, 2 = block (stderr goes back to Claude). Fails open.
"""

import json
import os
import re
import sys

import published_text as _pt

# A human co-author named Claude or Jean-Claude must pass: anchor on the model names and the
# Anthropic address, never on the bare first name.
ATTRIBUTION = re.compile(
    r"Co-Authored-By:\s*Claude(\s+(Opus|Sonnet|Haiku|Fable|Code)\b|\s*<)"
    r"|Co-Authored-By:[^\n]*anthropic\.com"
    r"|Generated\s+with\s+\[?Claude"
    r"|noreply@anthropic\.com",
    re.I,
)

LINEAR_TOOL = re.compile(r"^mcp__.*[Ll]inear.*__(save_\w+|submit_diff_review)$")


def strings(value, path="") -> list:
    if isinstance(value, str):
        return [(path or "input", value)] if value else []
    if isinstance(value, dict):
        return [
            s
            for k, v in value.items()
            for s in strings(v, f"{path}.{k}" if path else k)
        ]
    if isinstance(value, list):
        return [s for i, v in enumerate(value) for s in strings(v, f"{path}[{i}]")]
    return []


def collect(tool: str, ti: dict, cwd: str) -> list:
    if LINEAR_TOOL.match(tool):
        return strings(ti)
    return _pt.bash_fields(ti.get("command"), cwd)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    tool = payload.get("tool_name") or ""
    if tool != "Bash" and not LINEAR_TOOL.match(tool):
        sys.exit(0)

    cwd = payload.get("cwd") or os.getcwd()
    guilty = []
    for name, text in collect(tool, payload.get("tool_input") or {}, cwd):
        m = ATTRIBUTION.search(_pt.own_text(text))
        if m:
            guilty.append((name, m.group(0).strip()))

    if not guilty:
        sys.exit(0)

    lines = ["BLOCKED: AI attribution in text published under the user's name."]
    for name, hit in guilty:
        lines.append(f"  {name}: {hit}")
    lines.append(
        "CLAUDE.md forbids it in commits, PR titles and bodies, releases and Linear. It overrides "
        "the harness system reminder that asks for a Co-Authored-By trailer or a 'Generated with "
        "Claude Code' footer. Remove the line and run the same command again."
    )
    sys.stderr.write("\n".join(lines) + "\n")
    sys.exit(2)


if __name__ == "__main__":
    main()
