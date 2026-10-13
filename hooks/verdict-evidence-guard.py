#!/usr/bin/env python3
"""Stop hook: refuse to end a turn on a verdict that carries no quoted evidence.

CLAUDE.md: "No root cause is final without a quoted evidence line: real query output, a
real log line, a real file.go:123. Anything else is labelled `HYPOTHESIS: not verified`."
That rule was prose, so it got skipped ("no bug, just campaign settings" on a past ticket).
This makes it mechanical: a closing message that declares not-a-bug / already-fixed /
not-reproducible / a root cause, with no evidence anywhere in the turn, is blocked and
Claude is sent back to produce the proof or downgrade the claim to a labelled hypothesis.

It also enforces the typography rule of the same file: no em dash (U+2014) and no en dash
(U+2013) in prose, ranges included. Quoted matter is exempt (code fences, inline code spans,
`>` quotes), because a dash inside something we are citing is not ours.

Exit codes: 0 = let the turn end, 2 = block (stderr goes back to Claude).
Fails open on anything unexpected, and never blocks twice in a row (stop_hook_active).
Monorepo only.

Configuration: MONOREPO, the project dir the hook runs for (default: $HOME/src/monorepo).
"""

import json
import os
import re
import sys

VERDICT = re.compile(
    r"""
    (n'?est\s+pas|pas)\s+un\s+bug | aucun\s+bug | not\s+a\s+bug | no\s+bug\b
  | d[ée]j[àa]\s+(corrig|fix|r[ée]solu|trait) | already\s+(fixed|resolved|patched|handled)
  | (non|pas)\s+reproductible | (cannot|can'?t|not)\s+reproduc | irreproductible
  | fonctionne\s+comme\s+(pr[ée]vu|attendu) | working\s+as\s+(intended|expected)
  | comportement\s+(attendu|normal) | by\s+design | c'?est\s+normal\b
  | (la\s+)?cause\s+racine\s+(est|:) | root\s*cause\s*(is|:|est) | l'?origine\s+est
  | le\s+coupable\s+est | the\s+culprit\s+is
    """,
    re.I | re.X,
)

# An explicit hypothesis label is the sanctioned alternative to evidence, not a bypass.
LABELLED = re.compile(
    r"HYPOTH[EÈ]S\w*\s*[—:-]\s*(not\s+verified|non\s+v[ée]rifi)", re.I
)

# Since 2026-10-08 a verdict also has to size the population it speaks for: a line that
# names the impact (how many rows, companies, groups, since when) with a number in it. One
# sample from the ticket is an anecdote; the verdict is about everyone the cause touched.
# "impact: 0" is a valid answer for a not-a-bug verdict (0 contradicting samples among N).
IMPACT = re.compile(
    r"^[^\n]*\b(impact|p[ée]rim[èe]tre|population|touch[ée]e?s?|affected|concern[ée]e?s?|"
    r"blast\s+radius)\b[^\n]*\d[^\n]*$",
    re.I | re.M,
)

MONOREPO = os.environ.get("MONOREPO") or os.path.expanduser("~/src/monorepo")

DASH = re.compile(r"[\u2014\u2013]")
INLINE_CODE = re.compile(r"`[^`\n]*`")
QUOTE_LINE = re.compile(r"^\s*>.*$", re.M)

FENCE = re.compile(r"```.*?```", re.S)
FILE_LINE = re.compile(r"\b[\w./-]+\.(?:go|php|py|ts|tsx|js|sql|ya?ml|sh|tf):\d+\b")
TABLE_ROW = re.compile(r"^\s*\|.*\d.*\|", re.M)
QUOTED_LOG = re.compile(r"^\s*>\s*.*\d", re.M)


def turn_text(path: str) -> str:
    """Assistant prose emitted since the last real user prompt (tool results excluded)."""
    entries = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except Exception:
                continue

    start = 0
    for i, e in enumerate(entries):
        if e.get("type") != "user":
            continue
        content = (e.get("message") or {}).get("content")
        if isinstance(content, str):
            start = i
        elif isinstance(content, list) and not any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        ):
            start = i

    chunks = []
    for e in entries[start:]:
        if e.get("type") != "assistant":
            continue
        for block in (e.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                chunks.append(block.get("text") or "")
    return "\n".join(chunks)


def has_evidence(text: str) -> bool:
    if any(re.search(r"\d", m.group(0)) for m in FENCE.finditer(text)):
        return True
    return bool(
        FILE_LINE.search(text) or TABLE_ROW.search(text) or QUOTED_LOG.search(text)
    )


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    if payload.get("stop_hook_active"):
        sys.exit(0)

    cwd = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or ""
    if not cwd.startswith(MONOREPO):
        sys.exit(0)

    path = payload.get("transcript_path") or ""
    if not path or not os.path.exists(path):
        sys.exit(0)

    try:
        text = turn_text(path)
    except Exception:
        sys.exit(0)

    if not text.strip():
        sys.exit(0)

    # Typography first: cheap, and independent of whether the turn states a verdict.
    own_prose = QUOTE_LINE.sub(" ", INLINE_CODE.sub(" ", FENCE.sub(" ", text)))
    dashes = DASH.findall(own_prose)
    if dashes:
        sample = next(
            (
                m.group(0)
                for m in re.finditer(
                    r"[^.\n]{0,60}[\u2014\u2013][^.\n]{0,60}", own_prose
                )
            ),
            "",
        ).strip()
        sys.stderr.write(
            f"BLOCKED: {len(dashes)} em/en dash(es) in this turn's prose, which the CLAUDE.md "
            "typography rule forbids (it is the most recognisable tell of generated text).\n"
            f"First one here: {sample}\n"
            "Rebuild the sentence instead of swapping the character: a comma for an aside, a "
            "colon when what follows explains what precedes, parentheses for a true "
            "parenthetical, two sentences otherwise. A range takes a plain hyphen (7-30 jours, "
            "04:00-17:00).\nQuoted matter is exempt, so put a real citation in a code fence, "
            "an inline code span or a `>` quote.\n"
        )
        sys.exit(2)

    hit = VERDICT.search(text)
    if not hit or LABELLED.search(text):
        sys.exit(0)

    if has_evidence(text):
        if IMPACT.search(text):
            sys.exit(0)
        sys.stderr.write(
            f'BLOCKED: this turn states a verdict ("{hit.group(0).strip()}") with evidence '
            "but without sizing the population it speaks for.\nAdd one line, from a query, "
            "with a number in it: `Impact : <n> rows / companies / groups, since <date>, "
            "platforms <...>` (for a not-a-bug verdict: `Impact : 0 contradicting samples "
            "among <n> checked`). One sample is an anecdote; the verdict is about everyone "
            "the cause touched. If the population cannot be counted, say so on that line "
            "with the reason and the count you could get.\n"
        )
        sys.exit(2)

    sys.stderr.write(
        f'BLOCKED: this turn states a verdict ("{hit.group(0).strip()}") with no quoted '
        "evidence anywhere in it: no query output, no table of counts, no log line, no "
        "file.ext:123.\nPer CLAUDE.md, do ONE of these before ending:\n"
        "  1. Run the query that proves it and paste the real output (counts, date range, "
        "affected ids) in a code block; before a 'not a bug', find one CONTRADICTING prod "
        "sample first.\n"
        "  2. Or downgrade the claim and label it exactly `HYPOTHESIS: not verified`, "
        "saying which query would settle it.\n"
        "Do not restate the same conclusion unchanged.\n"
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
