#!/usr/bin/env python3
"""Subagent effort cap: no subagent runs above medium.

The user's rule (2026-10-09). On 2026-10-08 subagents were 33% of the spend (1831 API calls, all
at medium, measured from the session transcripts); a subagent at high would cost
more for work that is mostly reading and querying.

PreToolUse on `Agent`:
  - an explicit `effort` of high, xhigh or max blocks, with medium as the value to pass;
  - a fork ignores `effort` and inherits the parent's level, so a fork blocks when the last
    assistant turn of the parent transcript ran above medium (main session switched to high
    with /effort or a model whose modelSettings says high). The way out is a non-fork agent
    with `effort: "medium"`.
A missing `effort` on a non-fork agent passes only when the session runs at medium or below
and no `model` is given (another model may carry its own effortLevel in modelSettings).
Agent definitions with `effort:` in their frontmatter are not checked: there are none today.

Exit codes: 0 = allow, 2 = block (stderr goes back). Fails open.
"""

import json
import sys

ABOVE_MEDIUM = {"high", "xhigh", "max"}
TAIL_BYTES = 400_000


def parent_effort(transcript_path: str) -> str | None:
    try:
        with open(transcript_path, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - TAIL_BYTES))
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if '"effort"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") == "assistant" and d.get("effort"):
            return d.get("perTurnEffort") or d["effort"]
    return None


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    if data.get("tool_name") != "Agent":
        return 0
    inp = data.get("tool_input") or {}
    effort = (inp.get("effort") or "").lower()
    if effort in ABOVE_MEDIUM:
        print(
            f"guard-agent-effort: effort '{effort}' refused, subagents are capped at medium "
            '(user rule 2026-10-09). Relaunch with effort: "medium" or omit it.',
            file=sys.stderr,
        )
        return 2
    is_fork = inp.get("subagent_type") == "fork"
    if is_fork or not effort:
        # no effort of its own: the agent inherits the session's level (a fork always does)
        parent = (parent_effort(data.get("transcript_path") or "") or "").lower()
        if parent in ABOVE_MEDIUM:
            fix = (
                "Use a non-fork agent (general-purpose) with effort: \"medium\" and a self-contained prompt."
                if is_fork
                else "Pass effort: \"medium\" explicitly."
            )
            print(
                f"guard-agent-effort: this agent would inherit the session's effort ('{parent}'), "
                f"above the medium cap (user rule 2026-10-09). {fix}",
                file=sys.stderr,
            )
            return 2
        if not is_fork and inp.get("model"):
            print(
                f"guard-agent-effort: model '{inp['model']}' without an effort may pick up that "
                "model's own effortLevel (modelSettings sets claude-fable-5 to high). Pass "
                'effort: "medium" explicitly (user rule 2026-10-09).',
                file=sys.stderr,
            )
            return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # fail open: a broken guard must not stop work
        sys.exit(0)
