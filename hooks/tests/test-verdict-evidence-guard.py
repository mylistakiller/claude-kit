#!/usr/bin/env python3
"""Suite de tests de verdict-evidence-guard.py (hook Stop).

    python3 hooks/tests/test-verdict-evidence-guard.py

Construit un transcript jsonl minimal (un prompt utilisateur, une reponse assistant) et verifie
les quatre sorties : verdict avec preuve et ligne d'impact passe, verdict avec preuve mais sans
impact chiffre bloque, verdict sans preuve bloque, hypothese etiquetee passe, tiret cadratin
bloque, et le hook se tait hors monorepo.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = str(Path(__file__).resolve().parents[1] / "verdict-evidence-guard.py")
PASS, BLOCK = "passe", "bloque"
# Le perimetre du hook vient de MONOREPO, fixe ici a un chemin factice.
MONOREPO = "/tmp/test-monorepo"
ENV = {**os.environ, "MONOREPO": MONOREPO}

EVIDENCE = "```\nSELECT COUNT(*) FROM orders WHERE ...;\n+----------+\n| 1532     |\n+----------+\n```\n"


def transcript(assistant_text):
    entries = [
        {
            "type": "user",
            "message": {"role": "user", "content": "pourquoi les commandes manquent ?"},
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": assistant_text}],
            },
        },
    ]
    tmp = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    for e in entries:
        tmp.write(json.dumps(e, ensure_ascii=False) + "\n")
    tmp.close()
    return tmp.name


def run(text, cwd=MONOREPO):
    path = transcript(text)
    payload = {"transcript_path": path, "cwd": cwd, "stop_hook_active": False}
    p = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=ENV,
    )
    os.unlink(path)
    return (BLOCK if p.returncode == 2 else PASS), p.stderr.strip()


CASES = [
    (
        PASS,
        "verdict + preuve + impact chiffre",
        "La cause racine est le filtre deleted_at absent.\n"
        + EVIDENCE
        + "Impact : 1532 commandes sur 41 clients, depuis le 2026-09-12.",
    ),
    (
        BLOCK,
        "verdict + preuve, pas de ligne d'impact",
        "La cause racine est le filtre deleted_at absent.\n"
        + EVIDENCE
        + "Le fix est une ligne dans le WHERE.",
    ),
    (
        BLOCK,
        "verdict sans preuve",
        "Ce n'est pas un bug, c'est le comportement attendu de la synchro.",
    ),
    (
        PASS,
        "not a bug + preuve + impact zero",
        "Ce n'est pas un bug.\n"
        + EVIDENCE
        + "Impact : 0 contre-exemple parmi 1532 commandes verifiees.",
    ),
    (
        PASS,
        "hypothese etiquetee",
        "HYPOTHESIS: not verified. La cause racine est peut-etre le cron, la requete qui tranche est ...",
    ),
    (
        PASS,
        "pas de verdict, simple reponse",
        "Voici les 12 ids demandes :\n```\n1, 2, 3\n```",
    ),
    (
        BLOCK,
        "tiret cadratin dans la prose",
        "Les commandes manquent — le cron a tourne.\n" + EVIDENCE + "Impact : 3 commandes.",
    ),
]


def main():
    failures = 0
    for expected, name, text in CASES:
        got, err = run(text)
        ok = got == expected
        failures += not ok
        print(f"{'OK  ' if ok else 'ECHEC'} [{expected}] {name}")
        if not ok:
            print(f"      obtenu: {got}\n      stderr: {err[:300]}")
    got, _ = run("La cause racine est X.", cwd="/tmp/elsewhere")
    ok = got == PASS
    failures += not ok
    print(f"{'OK  ' if ok else 'ECHEC'} [passe] hors monorepo, le hook se tait")
    total = len(CASES) + 1
    print(f"\n{total - failures}/{total} cas conformes")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
