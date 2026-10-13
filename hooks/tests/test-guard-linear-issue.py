#!/usr/bin/env python3
"""Suite de tests de guard-linear-issue.py.

python3 hooks/tests/test-guard-linear-issue.py
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = str(Path(__file__).resolve().parents[1] / "guard-linear-issue.py")
# L'identite de l'utilisateur vient de l'environnement, comme en vrai.
ENV = {
    **os.environ,
    "LINEAR_ME_ALIASES": "dev,dev@acme.test,Jérôme Dupont",
    "LINEAR_ME_ID": "00000000-0000-4000-8000-000000000001",
}
TOOL = "mcp__plugin_engineering_linear__save_issue"
PASS, BLOCK = "passe", "bloque"


def transcript(*user_messages):
    fh = tempfile.NamedTemporaryFile(
        "w", suffix=".jsonl", delete=False, encoding="utf-8"
    )
    for m in user_messages:
        fh.write(
            json.dumps({"type": "user", "message": {"role": "user", "content": m}})
            + "\n"
        )
    fh.close()
    return fh.name


FULL = {
    "team": "Support",
    "title": "t",
    "assignee": "me",
    "priority": 3,
    "labels": ["Bug"],
}

CASES = [
    (PASS, "creation complete", FULL, None),
    (PASS, "assignee par email", {**FULL, "assignee": "dev@acme.test"}, None),
    (
        PASS,
        "assignee par nom accentue",
        {**FULL, "assignee": "Jérôme Dupont"},
        None,
    ),
    (
        PASS,
        "assignee par id",
        {**FULL, "assignee": "00000000-0000-4000-8000-000000000001"},
        None,
    ),
    (
        PASS,
        "label via addLabels",
        {**{k: v for k, v in FULL.items() if k != "labels"}, "addLabels": ["Bug"]},
        None,
    ),
    (
        PASS,
        "label via template",
        {**{k: v for k, v in FULL.items() if k != "labels"}, "template": "Bug"},
        None,
    ),
    (PASS, "mise a jour (id) incomplete", {"id": "ENG-1", "priority": 0}, None),
    (PASS, "autre outil Linear", None, None),
    (BLOCK, "sans assignee", {k: v for k, v in FULL.items() if k != "assignee"}, None),
    (BLOCK, "sans priorite", {k: v for k, v in FULL.items() if k != "priority"}, None),
    (BLOCK, "priorite 0", {**FULL, "priority": 0}, None),
    (BLOCK, "sans label", {k: v for k, v in FULL.items() if k != "labels"}, None),
    (
        BLOCK,
        "autre assignee non nomme par l'utilisateur",
        {**FULL, "assignee": "alex martin"},
        lambda: transcript("crée la carte"),
    ),
    (
        PASS,
        "autre assignee nomme par l'utilisateur",
        {**FULL, "assignee": "alex martin"},
        lambda: transcript("crée une carte ENG et assigne-la à Alex"),
    ),
    (
        BLOCK,
        "email autre, l'utilisateur dit seulement acme",
        {**FULL, "assignee": "sam@acme.test"},
        lambda: transcript("regarde la fiche acme de ce client"),
    ),
    (
        PASS,
        "email autre, l'utilisateur nomme sam",
        {**FULL, "assignee": "sam@acme.test"},
        lambda: transcript("assigne a Sam"),
    ),
    (
        BLOCK,
        "autre assignee sans transcript",
        {**FULL, "assignee": "sam@acme.test"},
        None,
    ),
]


def run(tool_input, transcript_path, event="PreToolUse", tool=TOOL, response=None):
    payload = {
        "hook_event_name": event,
        "tool_name": tool,
        "tool_input": tool_input or {},
    }
    if transcript_path:
        payload["transcript_path"] = transcript_path
    if response is not None:
        payload["tool_response"] = response
    return subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=ENV,
    )


def main():
    fails = 0
    for expected, name, tool_input, tr in CASES:
        tool = (
            TOOL
            if tool_input is not None
            else "mcp__plugin_engineering_linear__get_issue"
        )
        p = run(tool_input, tr() if tr else None, tool=tool)
        got = BLOCK if p.returncode == 2 else PASS
        ok = got == expected
        fails += not ok
        print(
            f"{'OK  ' if ok else 'FAIL'}  {name:40} attendu={expected:6} obtenu={got}"
        )

    p = run(
        FULL,
        None,
        event="PostToolUse",
        response={"id": "ENG-1250", "url": "https://linear.app/x"},
    )
    ok = (
        p.returncode == 0
        and "ENG-1250" in p.stdout
        and "save_customer_need" in p.stdout
    )
    fails += not ok
    print(f"{'OK  ' if ok else 'FAIL'}  {'post: rappel customer avec la carte':40}")
    p = run(
        {"id": "ENG-1250", "priority": 3},
        None,
        event="PostToolUse",
        response={"id": "ENG-1250"},
    )
    ok = p.returncode == 0 and p.stdout == ""
    fails += not ok
    print(f"{'OK  ' if ok else 'FAIL'}  {'post: silencieux sur une mise a jour':40}")
    p = subprocess.run(
        [sys.executable, HOOK], input="pas du json", capture_output=True, text=True
    )
    ok = p.returncode == 0
    fails += not ok
    print(f"{'OK  ' if ok else 'FAIL'}  {'entree invalide: laisse passer':40}")

    total = len(CASES) + 3
    print(f"\n{total - fails}/{total}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
