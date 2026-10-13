#!/usr/bin/env python3
"""Linear card completeness: a card created by the agent goes out assigned, prioritised and labelled.

The user's rule (2026-10-08): every card is assigned to the user, carries a priority (Medium
when nobody set one), a label, and the customer when one exists. Over 30 days, 5 of the user's
100 cards had no priority and 11 no label.

PreToolUse on `save_issue`, creation only (no `id`): an update leaves the other fields alone,
and the weekly `linear-complete` pass covers existing cards.
  - assignee: the user (`me`, or one of LINEAR_ME_ALIASES: handle, email, name, id). Another
    person passes only when the user named that person in one of their last messages: a card
    for a dev is the user's call, not the agent's.
  - priority: 1 to 4. Missing or 0 blocks, with Medium (3) as the default to set.
  - label: `labels` or `addLabels` non-empty, or a `template` (it brings its own labels).

PostToolUse on the same tool, after a creation: a reminder to link the customer, which
`save_issue` cannot set (it goes through `save_customer_need`).

Exit codes: 0 = allow (stdout goes back as context), 2 = block (stderr goes back). Fails open.

Configuration (environment variables, optional):
  LINEAR_ME_ALIASES  comma-separated values that mean "the user" as an assignee: handle,
                     email, full name (accents ignored). `me` is always accepted.
  LINEAR_ME_ID       the user's Linear user id, also accepted as an assignee.
"""

import json
import os
import re
import sys
import unicodedata

RECENT_USER_MESSAGES = 6


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return s.strip().lower()


ME = {"me"} | {
    fold(a)
    for a in (os.environ.get("LINEAR_ME_ALIASES", "").split(",") + [os.environ.get("LINEAR_ME_ID", "")])
    if a.strip()
}


def recent_user_text(transcript_path: str) -> str:
    texts = []
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"type":"user"' not in line.replace(" ", ""):
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("isMeta") or d.get("isSidechain"):
                    continue
                c = (d.get("message") or {}).get("content")
                if isinstance(c, str):
                    t = c
                elif isinstance(c, list):
                    t = "\n".join(
                        b.get("text", "")
                        for b in c
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                else:
                    t = ""
                if t.strip() and not t.lstrip().startswith("<"):
                    texts.append(t)
    except OSError:
        return ""
    return "\n".join(texts[-RECENT_USER_MESSAGES:])


def named_by_user(assignee: str, transcript_path: str) -> bool:
    # Only the local part of an email: the company domain is in half of the user's messages.
    who = fold(assignee).split("@", 1)[0]
    words = [w for w in re.split(r"[\s.]+", who) if len(w) >= 3]
    if not words or not transcript_path:
        return False
    said = fold(recent_user_text(transcript_path))
    return any(re.search(rf"\b{re.escape(w)}\b", said) for w in words)


def pre(payload: dict) -> None:
    inp = payload.get("tool_input") or {}
    if inp.get("id"):
        return
    missing = []
    assignee = inp.get("assignee")
    if not assignee:
        missing.append('assignee: none set. Set `assignee: "me"` (the user).')
    elif fold(assignee) not in ME and not named_by_user(
        assignee, payload.get("transcript_path") or ""
    ):
        missing.append(
            f"assignee: `{assignee}` is not the user, and the user did not name that person in "
            'their last messages. Set `assignee: "me"`, or ask the user first.'
        )
    prio = inp.get("priority")
    if prio in (None, 0, "0"):
        missing.append(
            "priority: none set. Use `priority: 3` (Medium) unless the user or the request gave one."
        )
    if not (inp.get("labels") or inp.get("addLabels") or inp.get("template")):
        missing.append(
            "label: none set. Pick one from the team's labels "
            "(`list_issue_labels` with `team`), e.g. Bug, Feature, Improvement."
        )
    if missing:
        sys.stderr.write(
            "BLOCKED: a Linear card goes out complete (the user's rule: assignee the user, a priority, "
            "a label, the customer when it exists).\n- "
            + "\n- ".join(missing)
            + "\nThen re-send. The customer is linked after creation with `save_customer_need`.\n"
        )
        sys.exit(2)


IDENT = re.compile(r"\b([A-Z]{2,6}-\d+)\b")


def post(payload: dict) -> None:
    inp = payload.get("tool_input") or {}
    if inp.get("id"):
        return
    resp = payload.get("tool_response")
    text = resp if isinstance(resp, str) else json.dumps(resp, ensure_ascii=False)
    m = IDENT.search(text or "")
    card = m.group(1) if m else "the new card"
    print(
        f"[linear] {card} created. If it concerns a customer, link the customer now: "
        "`list_customers` with `query` set to the customer name; when exactly one existing "
        f"customer matches, `save_customer_need` with `issue: {card}` and that `customer`. "
        "Never create a customer (the user's rule); when none or several match, say so in the answer."
    )


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    if not re.search(r"linear.*__save_issue$", payload.get("tool_name") or ""):
        return
    if payload.get("hook_event_name") == "PostToolUse":
        post(payload)
    else:
        pre(payload)


if __name__ == "__main__":
    main()
