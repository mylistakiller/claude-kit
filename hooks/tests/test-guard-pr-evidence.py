#!/usr/bin/env python3
"""Suite de tests de guard-pr-evidence.py.

    python3 hooks/tests/test-guard-pr-evidence.py

Couvre les deux chemins : un corps de PR conforme passe, et chacune des raisons de blocage
(pas de bloc de code, bloc sans chiffre, titre a emoji, en-tete de section, corps trop long)
bloque. Plus les cas ou le hook ne doit rien dire : autre commande, `gh pr view`, `--fill`,
echappatoire `allow-thin-pr`.

Chaque cas tourne dans un dossier hors de tout depot git (ou dans un depot jetable) : le hook lit
le diff du cwd, et lance depuis un checkout sale le cas conforme bloquait sur les fichiers
modifies du checkout, pas sur le corps teste.
"""

import atexit
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HOOK = str(Path(__file__).resolve().parents[1] / "guard-pr-evidence.py")
PASS, BLOCK = "passe", "bloque"

GOOD_BODY = """Le renderer PNG capturait un ref jamais attache, d'ou le null.

- garde sur chartRef avant capture
- test de non-regression sur le cas ref nul

```
$ pnpm test:changed:coverage
Test Suites: 3 passed, 3 total
Tests:       41 passed, 41 total
$ pnpm type-check    0 error
$ pnpm lint          0 problem
```

Preview: https://web-xxx.vercel.app/dashboard/projects/12/items/abc
Non couvert: le rendu Safari, pas de runner sur cette cible.
Risque au merge : reversible, R1, web seul.
code-review high : 2 findings, 2 fixed.
"""


def scratch_dir():
    d = tempfile.mkdtemp()
    atexit.register(shutil.rmtree, d, True)
    return d


NO_REPO = scratch_dir()
# A GIT_DIR or GIT_WORK_TREE inherited from a git hook would point every git call below, and the
# hook's own, at the real repo whatever the cwd: one run committed into the kit repo that way.
CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def run(cmd, cwd=None):
    cwd = cwd or NO_REPO
    payload = {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": cwd}
    p = subprocess.run(
        [sys.executable, HOOK], input=json.dumps(payload), capture_output=True, text=True, cwd=cwd, env=CLEAN_ENV
    )
    return (BLOCK if p.returncode == 2 else PASS), p.stderr.strip()


def heredoc(body, title="web/statistics: garde sur le ref du chart"):
    return f'gh pr create --title "{title}" --body "$(cat <<\'EOF\'\n{body}\nEOF\n)"'


CASES = [
    (PASS, "corps conforme, heredoc", heredoc(GOOD_BODY)),
    (PASS, "pas une commande gh pr", 'git commit -m "web: garde sur le ref"'),
    (PASS, "gh pr view", "gh pr view 5507 --json statusCheckRollup"),
    (PASS, "--fill, pas de corps sur la ligne", "gh pr create --fill"),
    (
        PASS,
        "echappatoire assumee",
        heredoc("Doc seule, rien a executer.\n\nallow-thin-pr"),
    ),
    (
        BLOCK,
        "aucune preuve collee",
        heredoc("Corrige le bug du renderer. Tests OK, lint OK."),
    ),
    (
        BLOCK,
        "bloc de code sans chiffre",
        heredoc("Corrige le renderer.\n\n```\npnpm test: tout vert\n```"),
    ),
    (
        BLOCK,
        "titre avec emoji",
        heredoc(GOOD_BODY, title="web: fix du renderer \U0001f680"),
    ),
    (
        BLOCK,
        "en-tete de section",
        heredoc("## Contexte\n\nLe renderer.\n\n```\nTests: 41 passed\n```"),
    ),
    (
        BLOCK,
        "corps trop long",
        heredoc("\n".join(f"ligne {i} de blabla" for i in range(20)) + "\n\n```\nTests: 41 passed\n```"),
    ),
    (
        BLOCK,
        "pas de ligne code-review",
        heredoc(GOOD_BODY.replace("code-review high : 2 findings, 2 fixed.", "")),
    ),
    (
        BLOCK,
        "pas de classe de risque R0-R4",
        heredoc(GOOD_BODY.replace("R1, ", "")),
    ),
    (
        BLOCK,
        "--body-file sans preuve",
        None,  # rempli plus bas, a besoin d'un fichier
    ),
]


def temp_repo(with_test):
    """A branch that changes one source file, with or without a test file beside it."""
    d = scratch_dir()
    g = lambda *a: subprocess.run(["git", *a], cwd=d, check=True, capture_output=True, env=CLEAN_ENV)
    g("init", "-q", "-b", "master")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    Path(d, "a.go").write_text("package a\n")
    g("add", "-A")
    g("commit", "-qm", "base")
    g("update-ref", "refs/remotes/origin/master", "HEAD")
    g("checkout", "-qb", "feat")
    Path(d, "a.go").write_text("package a\n// changed\n")
    if with_test:
        Path(d, "a_test.go").write_text("package a\n")
    return d


def main():
    tmp = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False)
    tmp.write("Corrige le renderer. Les tests passent.\n")
    tmp.close()
    CASES[-1] = (BLOCK, "--body-file sans preuve", f"gh pr create -F {tmp.name}")

    CASES.append((BLOCK, "source modifiee sans fichier de test dans le diff", (heredoc(GOOD_BODY), temp_repo(False))))
    CASES.append((PASS, "source modifiee avec un fichier de test", (heredoc(GOOD_BODY), temp_repo(True))))
    CASES.append((PASS, "source sans test mais allow-untested", (heredoc(GOOD_BODY + "\nallow-untested: script one-shot, rien a tester"), temp_repo(False))))

    failures = 0
    for expected, name, cmd in CASES:
        got, err = run(*cmd) if isinstance(cmd, tuple) else run(cmd)
        ok = got == expected
        failures += not ok
        print(f"{'OK  ' if ok else 'ECHEC'} [{expected}] {name}")
        if not ok:
            print(f"      obtenu: {got}\n      stderr: {err[:300]}")
    os.unlink(tmp.name)

    print(f"\n{len(CASES) - failures}/{len(CASES)} cas conformes")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
