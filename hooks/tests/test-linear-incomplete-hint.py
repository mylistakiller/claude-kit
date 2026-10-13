#!/usr/bin/env python3
"""Suite de tests de linear-incomplete-hint.py (clone courant du plugin).

python3 hooks/tests/test-linear-incomplete-hint.py
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = str(Path(__file__).resolve().parents[1] / "linear-incomplete-hint.py")
TOOL = "mcp__plugin_engineering_linear__get_issue"
ME = "00000000-0000-4000-8000-000000000001"
ENV = {**os.environ, "LINEAR_ME_ID": ME}
HINT, SILENT = "rappel", "silence"

# Forme reelle d'une reponse get_issue relevee le 09/10 (texte anonymise).
CARD = {
    "id": "ENG-1231",
    "title": "Gros problème sur l'export CSV",
    "description": "tu as la capture d'écran associée ?\n\nAB\n\nEn attendant je relance l'export à la main",
    "priority": {"value": 1, "name": "Urgent"},
    "statusType": "backlog",
    "archivedAt": None,
    "labels": [],
    "assigneeId": ME,
    "team": "Support",
}
FULL = {**CARD, "labels": ["Bug"]}
IMAGE_ONLY = (
    '<linear-image>{"src":"https://uploads.linear.app/'
    + "x" * 400
    + '","signature":"abc"}</linear-image>'
)


def mcp_text(issue):
    return [{"type": "text", "text": json.dumps(issue, ensure_ascii=False)}]


CASES = [
    (HINT, "carte reelle sans label", mcp_text(CARD), "label", None),
    (SILENT, "carte complete", mcp_text(FULL), None, None),
    (
        HINT,
        "priorite 0",
        mcp_text({**FULL, "priority": {"value": 0, "name": "No priority"}}),
        "priorité à 0",
        None,
    ),
    (SILENT, "priorite Urgent jamais touchee", mcp_text(FULL), None, None),
    (
        SILENT,
        "lecture partielle (fields) : rien n'est juge",
        mcp_text({"id": "ENG-1231", "assigneeId": ME, "statusType": "backlog"}),
        None,
        None,
    ),
    (
        HINT,
        "lecture partielle : seul le champ present est juge",
        mcp_text(
            {"id": "ENG-1231", "assigneeId": ME, "statusType": "backlog", "labels": []}
        ),
        "label",
        None,
    ),
    (
        HINT,
        "description vide",
        mcp_text({**FULL, "description": ""}),
        "description",
        None,
    ),
    (
        HINT,
        "description = une image seule",
        mcp_text({**FULL, "description": IMAGE_ONLY}),
        "description",
        None,
    ),
    (
        SILENT,
        "carte d'un autre",
        mcp_text({**CARD, "assigneeId": "other"}),
        None,
        None,
    ),
    (
        SILENT,
        "carte terminee",
        mcp_text({**CARD, "statusType": "completed"}),
        None,
        None,
    ),
    (
        SILENT,
        "carte annulee",
        mcp_text({**CARD, "statusType": "canceled"}),
        None,
        None,
    ),
    (
        SILENT,
        "carte archivee",
        mcp_text({**CARD, "archivedAt": "2026-10-01T00:00:00Z"}),
        None,
        None,
    ),
    (HINT, "reponse en chaine JSON", json.dumps(CARD), "label", None),
    (
        HINT,
        "reponse en dict sans title",
        {k: v for k, v in CARD.items() if k != "title"},
        "label",
        None,
    ),
    (SILENT, "reponse non JSON", "Error: not found", None, None),
    (SILENT, "reponse vide", [], None, None),
    (
        SILENT,
        "deja rappelee dans la session",
        mcp_text(CARD),
        None,
        "[linear-incomplete-hint] ENG-1231 (assignée",
    ),
    (
        HINT,
        "rappel d'une autre carte n'empeche pas",
        mcp_text(CARD),
        "label",
        "[linear-incomplete-hint] ENG-9 (assignée",
    ),
]

_tmp = []


def transcript(text):
    if text is None:
        return ""
    fh = tempfile.NamedTemporaryFile(
        "w", suffix=".jsonl", delete=False, encoding="utf-8"
    )
    fh.write(
        json.dumps({"type": "attachment", "content": text}, ensure_ascii=False) + "\n"
    )
    fh.close()
    _tmp.append(fh.name)
    return fh.name


def run(response, tool=TOOL, prior=None):
    payload = {
        "tool_name": tool,
        "tool_input": {"id": "ENG-1231"},
        "tool_response": response,
        "transcript_path": transcript(prior),
    }
    p = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=ENV,
    )
    ctx = ""
    if p.stdout.strip():
        ctx = json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
    return p.returncode, ctx


def main():
    fails = 0
    try:
        for expected, name, response, needle, prior in CASES:
            rc, ctx = run(response, prior=prior)
            got = HINT if ctx else SILENT
            ok = rc == 0 and got == expected and (needle is None or needle in ctx)
            fails += not ok
            print(f"{'OK ' if ok else 'KO '} {expected:7} {name}")
        rc, ctx = run(
            mcp_text(CARD), tool="mcp__plugin_engineering_linear__save_issue"
        )
        ok = rc == 0 and not ctx
        print(f"{'OK ' if ok else 'KO '} silence autre outil ignore")
        fails += not ok
        p = subprocess.run(
            [sys.executable, HOOK], input="not json", capture_output=True, text=True
        )
        ok = p.returncode == 0 and not p.stdout.strip()
        print(f"{'OK ' if ok else 'KO '} silence entree invalide (fail open)")
        fails += not ok
        bare = {k: v for k, v in ENV.items() if k != "LINEAR_ME_ID"}
        p = subprocess.run(
            [sys.executable, HOOK],
            input=json.dumps({"tool_name": TOOL, "tool_response": mcp_text(CARD)}),
            capture_output=True,
            text=True,
            env=bare,
        )
        ok = p.returncode == 0 and not p.stdout.strip()
        print(f"{'OK ' if ok else 'KO '} silence sans LINEAR_ME_ID")
        fails += not ok
    finally:
        for f in _tmp:
            os.unlink(f)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
