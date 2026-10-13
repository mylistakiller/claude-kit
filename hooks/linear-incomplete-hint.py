#!/usr/bin/env python3
"""Linear card completeness on read: a card of the user's that the agent opens gets completed.

The user's rule (2026-10-09), on top of the 2026-10-08 one: guard-linear-issue.py only covers the
cards the agent creates, and the weekly linear-complete pass only catches the others once a week.
A card the agent reads during the work (e.g. one read on 2026-10-08 with no label) is completed
then, not left for the weekly pass.

PostToolUse on `get_issue`: when the card is assigned to the user, open and not archived, and has
priority 0, no label, or no useful text in its description (images and URLs stripped), the hook
returns a reminder as context, once per card per session. A field missing from the response
(a read with `fields`) is not judged. The reminder: in the main session, complete priority and
label (rules of linear-complete: `addLabels` only, Medium only over priority 0, assignee
untouched), link the customer only on a single match not already linked, and say it in the
answer; a subagent only reports it. A card of someone else is never touched.

Exit code 0 always; the reminder goes back as hookSpecificOutput.additionalContext. Fails open.

Configuration: LINEAR_ME_ID, the user's Linear user id (same variable as guard-linear-issue.py).
Unset, the hook stays silent.
"""

import json
import os
import re
import sys

ME_ID = os.environ.get("LINEAR_ME_ID", "").strip()
CLOSED = {"completed", "canceled", "duplicate"}
MIN_DESCRIPTION = 40
NOISE = re.compile(r"<linear-image>.*?</linear-image>|!\[[^\]]*\]\([^)]*\)|https?://\S+", re.S)


def issue_from(response):
    if isinstance(response, dict) and "id" in response:
        return response
    if isinstance(response, dict):
        response = response.get("content", response.get("text"))
    if isinstance(response, list):
        response = "".join(b.get("text", "") for b in response if isinstance(b, dict))
    if isinstance(response, str):
        try:
            d = json.loads(response)
        except ValueError:
            return None
        return d if isinstance(d, dict) else None
    return None


def already_hinted(transcript_path: str, issue_id: str) -> bool:
    """One reminder per card per session: the fix for a short description (a comment) never
    changes the description, so without this every later read would ask again."""
    mark = f"[linear-incomplete-hint] {issue_id} "
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as fh:
            return any(mark in line for line in fh)
    except OSError:
        return False


def missing_fields(issue: dict) -> list[str]:
    """Judge a field only when the response carries it: a get_issue with `fields` that leaves
    priority out must not read as 'no priority' (it would downgrade an Urgent card)."""
    missing = []
    if "priority" in issue:
        value = (issue.get("priority") or {}).get("value")
        if value == 0:
            missing.append("priorité à 0 (mettre 3 Medium ; une priorité déjà posée ne bouge jamais)")
    if "labels" in issue and not issue.get("labels"):
        missing.append(f"label (un label de l'équipe {issue.get('team', '?')}, via addLabels)")
    if "description" in issue and len(NOISE.sub("", issue.get("description") or "").strip()) < MIN_DESCRIPTION:
        missing.append(
            "description sans texte utile (poster une fois le contexte vérifié en commentaire : ids, "
            "client, plateforme, impact ; ne jamais réécrire la description, save_issue la remplace)"
        )
    return missing


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    if not (data.get("tool_name") or "").endswith("__get_issue"):
        return 0
    issue = issue_from(data.get("tool_response"))
    if (
        not ME_ID
        or not issue
        or issue.get("assigneeId") != ME_ID
        or (issue.get("statusType") or "") in CLOSED
        or issue.get("archivedAt")
    ):
        return 0
    missing = missing_fields(issue)
    if not missing or already_hinted(data.get("transcript_path") or "", issue.get("id")):
        return 0
    hint = (
        f"[linear-incomplete-hint] {issue.get('id')} (assignée à l'utilisateur, ouverte) est incomplète : "
        + " ; ".join(missing)
        + ". Règle de l'utilisateur (09/10) : dans la session principale, la compléter avec save_issue (règles "
        "du skill linear-complete : addLabels jamais labels, assignee inchangé), lier le customer "
        "seulement s'il existe un unique customer correspondant et qu'aucun de ses needs ne pointe "
        "déjà cette carte (list_customers includeNeeds), et le dire dans la réponse. Un subagent "
        "(lecture, revue, enquête) le signale dans son rapport sans rien écrire. Un commentaire "
        "porte des faits vérifiés, pas des hypothèses."
    )
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": hint}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # fail open
        sys.exit(0)
