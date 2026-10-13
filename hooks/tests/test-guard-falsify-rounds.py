#!/usr/bin/env python3
"""Suite de tests de guard-falsify-rounds.py.

python3 hooks/tests/test-guard-falsify-rounds.py
"""

import itertools
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = str(Path(__file__).resolve().parents[1] / "guard-falsify-rounds.py")
PASS, BLOCK = "passe", "bloque"
_ids = itertools.count()


def spawn(desc, tid=None):
    tid = tid or f"toolu_{next(_ids)}"
    return {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": tid,
                    "name": "Agent",
                    "input": {"description": desc},
                }
            ]
        },
    }


def errored(tid):
    return {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tid,
                    "is_error": True,
                    "content": "blocked",
                }
            ]
        },
    }


def said(text):
    return {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": text}]},
    }


def bash_printed(text, tid="toolu_bash"):
    return [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "id": tid, "name": "Bash", "input": {}}
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": tid, "content": text}
                ]
            },
        },
    ]


def human(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def skill_load(text):
    return {
        "type": "user",
        "isMeta": True,
        "message": {"content": [{"type": "text", "text": text}]},
    }


def asked(answer, question="Je relance un relecteur ?", tid="toolu_ask"):
    return [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": tid,
                        "name": "AskUserQuestion",
                        "input": {},
                    }
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tid,
                        "content": f'Your questions have been answered: "{question}"="{answer}".',
                    }
                ]
            },
        },
    ]


BROKE = "Relecteur: a cassé le verdict. ligne 7481"
PASSED = "Relecteur: n'a pas pu casser, 2 requêtes falsifiantes."
SKILL_BODY = "- `Relecteur: a cassé le verdict.`\n- `Relecteur: n'a pas pu casser, 2 requêtes falsifiantes.`"
TWO_BREAKS = [spawn("Falsify A"), said(BROKE), spawn("Falsify B"), said(BROKE)]

CASES = [
    (PASS, "premier relecteur", "Falsify ENG-1 plan", []),
    (
        PASS,
        "deuxieme apres une casse",
        "Falsify ENG-1 delta",
        [spawn("Falsify A"), said(BROKE)],
    ),
    (BLOCK, "troisieme apres deux casses", "Falsify C", TWO_BREAKS),
    (
        BLOCK,
        "go dans la description ne suffit plus",
        "Falsify C (go user)",
        TWO_BREAKS,
    ),
    (
        PASS,
        "go de l'utilisateur apres le 2e relecteur",
        "Falsify C",
        TWO_BREAKS + [human("ok go, relance")],
    ),
    (
        BLOCK,
        "relance dans la demande initiale ne compte pas",
        "Falsify C",
        [human("relance le robot")] + TWO_BREAKS,
    ),
    (PASS, "go via AskUserQuestion", "Falsify C", TWO_BREAKS + asked("Oui, vas-y")),
    (
        BLOCK,
        "AskUserQuestion: 'relance' dans la question, reponse non",
        "Falsify C",
        TWO_BREAKS + asked("Non"),
    ),
    (PASS, "pass en texte remet a zero", "Falsify Z", TWO_BREAKS + [said(PASSED)]),
    (
        PASS,
        "pass apostrophe typographique",
        "Falsify Z",
        TWO_BREAKS + [said(PASSED.replace("'", "’"))],
    ),
    (
        PASS,
        "pass imprime par Bash remet a zero",
        "Falsify Z",
        TWO_BREAKS + bash_printed(PASSED),
    ),
    (
        BLOCK,
        "Bash qui cite le skill ne remet pas a zero",
        "Falsify Z",
        TWO_BREAKS + bash_printed("# falsify\n" + SKILL_BODY),
    ),
    (
        BLOCK,
        "chargement du skill (isMeta) ne remet pas a zero",
        "Falsify C",
        [
            spawn("Falsify A"),
            said(BROKE),
            skill_load(SKILL_BODY),
            spawn("Falsify B"),
            said(BROKE),
            skill_load(SKILL_BODY),
        ],
    ),
    (
        PASS,
        "spawn en erreur ne compte pas",
        "Falsify delta",
        [
            spawn("Falsify A", "t_err"),
            errored("t_err"),
            spawn("Falsify A"),
            said(BROKE),
        ],
    ),
    (
        BLOCK,
        "majuscules comptees",
        "falsify C",
        [spawn("FALSIFY A"), spawn("FALSIFY B")],
    ),
    (PASS, "agent non falsify ignore", "Explore schema", TWO_BREAKS),
]

_tmp = []


def transcript(records):
    fh = tempfile.NamedTemporaryFile(
        "w", suffix=".jsonl", delete=False, encoding="utf-8"
    )
    for r in records:
        fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    fh.close()
    _tmp.append(fh.name)
    return fh.name


def run(desc, path, tool_name="Agent"):
    payload = {
        "tool_name": tool_name,
        "tool_input": {"description": desc},
        "transcript_path": path,
    }
    return subprocess.run(
        [HOOK], input=json.dumps(payload), capture_output=True, text=True
    ).returncode


def main():
    fails = 0
    try:
        for expected, name, desc, records in CASES:
            got = BLOCK if run(desc, transcript(records)) == 2 else PASS
            ok = got == expected
            fails += not ok
            print(f"{'OK ' if ok else 'KO '} {expected:6} {name}")
        for name, rc in [
            ("transcript absent (fail open)", run("Falsify X", "/nonexistent.jsonl")),
            ("autre outil ignore", run("Falsify X", transcript(TWO_BREAKS), "Bash")),
        ]:
            print(f"{'OK ' if rc == 0 else 'KO '} passe  {name}")
            fails += rc != 0
    finally:
        for p in _tmp:
            os.unlink(p)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
