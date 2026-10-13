#!/usr/bin/env python3
"""Falsify round cap: two reviewers in a row without a pass, then the user decides.

The user's rule (2026-10-09). On 2026-10-08 one session ran 7 falsify reviewers on the same sheet
plan, each breaking it on one more row of the same flaw; 45 reviewers cost 10 % of the day
(measured from the session transcripts). falsify/SKILL.md now asks to fix the whole failure
class after a break and to review only the delta.

PreToolUse on `Agent`, for a description containing "falsif": walks the session transcript
and counts the falsify reviewers that actually ran since the last pass, then blocks the third.
  - A pass is a `Relecteur:` line containing "pas pu casser", read with guard-prod-exec's rule
    (assistant text, or a non-error Bash result whose first line is the verdict). A skill load
    that quotes the line (isMeta user text) or an Agent result does not count, nor does a
    smart apostrophe change anything.
  - A spawn whose tool_result is an error (blocked by a hook, invalid input) is not a round.
  - the user's go unblocks: a message of theirs, or their answer to an AskUserQuestion, containing "go",
    "vas-y" or "relance", posted after the latest reviewer. The model cannot unblock itself
    through the description.
The count is per session, not per conclusion: breaks on an abandoned subject count against the
next one. The user's go is the way out for that case too.

Exit codes: 0 = allow, 2 = block (stderr goes back). Fails open.
"""

import importlib.util
import json
import re
import sys
from pathlib import Path

CAP = 2
FALSIFY = re.compile(r"falsif", re.I)
ASK_ANSWER = re.compile(r'"=\s*"([^"]*)"')
GO = re.compile(r"\b(go|vas-y|relance[rz]?)\b", re.I)

_spec = importlib.util.spec_from_file_location(
    "guard_prod_exec", Path(__file__).with_name("guard-prod-exec.py")
)
_gpe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gpe)


def is_pass(line: str) -> bool:
    return "pas pu casser" in line.lower()


def human_text(entry: dict) -> str | None:
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isSidechain"):
        return None
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return None if content.lstrip().startswith("<") else content
    if isinstance(content, list) and not any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content
    ):
        texts = [
            b.get("text") or ""
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "\n".join(t for t in texts if not t.lstrip().startswith("<")) or None
    return None


def scan(transcript_path: str) -> tuple[int, bool]:
    """(falsify reviewers that ran since the last pass, whether the user said go after the last one).

    The user's go is their own message, or their answer to an AskUserQuestion, posted after the latest
    counted reviewer: a "relance" in the request that opened the session does not count."""
    rounds: list[str] = []
    bash_ids: set[str] = set()
    ask_ids: set[str] = set()
    go_after_last = False
    with open(transcript_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("isSidechain"):
                continue
            text = human_text(entry)
            if text is not None:
                go_after_last = go_after_last or bool(GO.search(text))
                continue
            kind = entry.get("type")
            for b in (entry.get("message") or {}).get("content") or []:
                if not isinstance(b, dict):
                    continue
                if kind == "assistant" and b.get("type") == "tool_use":
                    if b.get("name") == "Bash":
                        bash_ids.add(b.get("id"))
                    elif b.get("name") == "AskUserQuestion":
                        ask_ids.add(b.get("id"))
                    elif b.get("name") == "Agent" and FALSIFY.search((b.get("input") or {}).get("description") or ""):
                        rounds.append(b.get("id"))
                        go_after_last = False
                elif kind == "assistant" and b.get("type") == "text":
                    if any(is_pass(m.group(1)) for m in _gpe.RELECTEUR.finditer(b.get("text") or "")):
                        rounds = []
                elif kind == "user" and b.get("type") == "tool_result":
                    tid = b.get("tool_use_id")
                    if b.get("is_error"):
                        if tid in rounds:
                            rounds.remove(tid)
                        continue
                    out = _gpe._tool_result_text(b)
                    if tid in ask_ids:
                        # only the chosen answers, not the question text the model wrote
                        answers = " ".join(ASK_ANSWER.findall(out))
                        go_after_last = go_after_last or bool(GO.search(answers))
                    elif tid in bash_ids and _gpe.RELECTEUR_FIRST_LINE.match(out):
                        if any(is_pass(m.group(1)) for m in _gpe.RELECTEUR.finditer(out)):
                            rounds = []
    return len(rounds), go_after_last


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    if data.get("tool_name") != "Agent":
        return 0
    if not FALSIFY.search((data.get("tool_input") or {}).get("description") or ""):
        return 0
    try:
        n, go = scan(data.get("transcript_path") or "")
    except OSError:
        return 0
    if n < CAP or go:
        return 0
    print(
        f"guard-falsify-rounds: {n} falsify reviewers since the last pass, cap is {CAP} "
        "(user rule 2026-10-09). Two breaks in a row mean the approach is wrong: show the user "
        "both breaks and the failure-class query, and ask whether to rethink or review again. "
        "Only their own 'go' unblocks it.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # fail open: a broken guard must not stop work
        sys.exit(0)
