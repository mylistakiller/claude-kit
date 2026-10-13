#!/usr/bin/env python3
"""UserPromptSubmit hook: force the investigation discipline on diagnostic prompts.

The `investigate` skill and the "two competing hypotheses + a falsifying query" rule
already existed when two tickets got a wrong first verdict; nothing made Claude
load them. This injects the reminder into the turn where it matters, instead of relying
on the model noticing the prompt was diagnostic.

Fires on a Linear ticket id, or on FR/EN diagnostic phrasing. At most once per ticket id
per session (and once per session for a ticket-less "pourquoi"), so a long investigation
does not get the same paragraph on every turn.

Never blocks: always exits 0. Stdout is appended to the turn's context.
Monorepo only: other projects have no prod to query.

Configuration (environment variables, optional):
  MONOREPO                     project dir the hook runs for (default: $HOME/src/monorepo)
  INVESTIGATE_TICKET_PREFIXES  `|`-separated ticket key prefixes (default: ENG|OPS|BUG)
"""

import json
import os
import re
import sys

MONOREPO = os.environ.get("MONOREPO") or os.path.expanduser("~/src/monorepo")
_PREFIXES = os.environ.get("INVESTIGATE_TICKET_PREFIXES") or "ENG|OPS|BUG"
TICKET = re.compile(rf"\b(?:{_PREFIXES})-\d+\b", re.I)

DIAGNOSTIC = re.compile(
    r"""
    pourquoi | pour\s+quelle\s+raison | d'?o[uù]\s+(vient|viennent|sort) | qui\s+a\s+(fait|modifi)
  | comment\s+se\s+fait | c'?est\s+quoi\s+le\s+probl | ça\s+vient\s+d'?o[uù]
  | (encore|toujours)\s+(valide|d'?actualit) | still\s+valid
  | why\s+(did|is|are|was|does|do|isn'?t|aren'?t|hasn'?t) | what\s+caused
  | root\s*cause | cause\s+racine | regarde\s+(ce|le)\s+ticket
  | ne\s+(marche|fonctionne)\s+pas | doesn'?t\s+work | investigate | investigu
    """,
    re.I | re.X,
)

REMINDER = """\
[investigate-guard] This prompt is diagnostic{scope}. Before answering:

1. Load the `investigate` skill (Skill tool, skill: "investigate"): it holds the
   hypothesis-ledger flow. Do not improvise the investigation shape.
2. Write down TWO competing hypotheses BEFORE the first query, and for each the query
   that would prove it WRONG. Run the falsifying query, not the confirming one.
3. No verdict, and especially no "not a bug" / "already fixed" / "not reproducible",
   without a quoted evidence line: real query output, a real log line, or a real
   file.ext:123. Anything else is labelled `HYPOTHESIS: not verified`.
4. Quantify the blast radius (rows, distinct companies, date range) as part of the answer.

Past sessions where this was skipped landed an inverted or premature verdict that the user
had to overturn."""


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    cwd = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or ""
    if not cwd.startswith(MONOREPO):
        sys.exit(0)

    prompt = payload.get("prompt") or ""
    if not prompt.strip():
        sys.exit(0)

    tickets = {m.group(0).upper() for m in TICKET.finditer(prompt)}
    if not tickets and not DIAGNOSTIC.search(prompt):
        sys.exit(0)

    # Dedupe per session so a multi-turn investigation is nudged once, not every turn.
    session = re.sub(r"[^A-Za-z0-9_-]", "", payload.get("session_id") or "nosession")
    marker_dir = "/tmp/claude-investigate-guard"
    keys = sorted(tickets) or ["_generic"]
    fresh = []
    try:
        os.makedirs(marker_dir, exist_ok=True)
        for key in keys:
            path = os.path.join(marker_dir, f"{session}.{key}")
            if not os.path.exists(path):
                open(path, "w").close()
                fresh.append(key)
    except Exception:
        fresh = keys  # cannot dedupe -> still remind, noise beats a missed rule

    if not fresh:
        sys.exit(0)

    named = [k for k in fresh if k != "_generic"]
    scope = f" and concerns {', '.join(named)}" if named else ""
    print(REMINDER.format(scope=scope))
    sys.exit(0)


if __name__ == "__main__":
    main()
