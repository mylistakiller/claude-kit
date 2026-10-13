#!/usr/bin/env python3
"""Suite de tests de guard-published-dashes.py.

python3 hooks/tests/test-guard-published-dashes.py
"""

import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = str(Path(__file__).resolve().parents[1] / "guard-published-dashes.py")
LINEAR = "mcp__plugin_engineering_linear__save_comment"
PASS, BLOCK = "passe", "bloque"
EM, EN = "—", "–"

tmpdir = tempfile.mkdtemp()
atexit.register(shutil.rmtree, tmpdir, True)
for name, text in {
    "body.md": f"## Summary\n\nle fix {EM} enfin\n",
    "clean.md": "## Summary\n\nle fix, enfin. Fenetre 7-30 jours.\n",
    "fenced.md": f"cite :\n\n```\nlog {EM} ligne\n```\n",
    "notes.md": f"- v1 {EM} premiere\n",
    # A file left by a blocked attempt, and a clean one of the same name elsewhere.
    "msg.txt": f"api: fix\n\nancien {EM} message\n",
    "sub/msg.txt": "api: fix\n\nla cause, le cron\n",
    "dir2/msg.txt": f"api: fix\n\nla cause {EN} le cron\n",
    "a/b/m.txt": f"chaine {EM} cd\n",
}.items():
    path = os.path.join(tmpdir, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)

CASES = [
    (
        BLOCK,
        "gh pr create --body-file",
        "Bash",
        {"command": "gh pr create --title t --body-file body.md"},
    ),
    (
        BLOCK,
        "gh pr edit --body-file=",
        "Bash",
        {"command": "gh pr edit 3 --body-file=body.md"},
    ),
    (BLOCK, "git commit -F", "Bash", {"command": "git commit -F body.md"}),
    (
        BLOCK,
        "cd dir2 && commit -F",
        "Bash",
        {"command": "cd dir2 && git commit -F msg.txt"},
    ),
    (
        BLOCK,
        "body-file - avec redirection",
        "Bash",
        {"command": "gh pr create --title t --body-file - < dir2/msg.txt"},
    ),
    (
        BLOCK,
        "gh pr merge --body",
        "Bash",
        {"command": f'gh pr merge 3 --squash --body "a {EM} b"'},
    ),
    (
        BLOCK,
        "gh release create --notes",
        "Bash",
        {"command": f'gh release create v1 --notes "a {EN} b"'},
    ),
    (BLOCK, "git tag -m", "Bash", {"command": f'git tag -a v1 -m "a {EM} b"'}),
    (BLOCK, "commit -m inline", "Bash", {"command": f'git commit -m "a {EM} b"'}),
    (BLOCK, "linear label", LINEAR, {"issueId": "ENG-1", "body": f"a {EM} b"}),
    (
        PASS,
        "body-file propre",
        "Bash",
        {"command": "gh pr create --title t --body-file clean.md"},
    ),
    (
        PASS,
        "dash dans un fence du fichier",
        "Bash",
        {"command": "gh pr create --title t --body-file fenced.md"},
    ),
    (
        PASS,
        "fichier absent",
        "Bash",
        {"command": "gh pr create --title t --body-file missing.md"},
    ),
    (PASS, "chemin en variable", "Bash", {"command": 'git commit -F "$D/msg.txt"'}),
    (
        PASS,
        "allow-dash",
        "Bash",
        {"command": f'gh pr create --title t --body "citation {EM} exacte allow-dash"'},
    ),
    (PASS, "commande non publiante", "Bash", {"command": f"echo 'a {EM} b' > /tmp/x"}),
    (BLOCK, "cd a && cd b chaine", "Bash", {"command": "cd a && cd b && git commit -F m.txt"}),
    (BLOCK, "msg.txt de la racine", "Bash", {"command": "git commit -F msg.txt"}),
    (PASS, "cd sub lit sub/msg.txt, pas la racine", "Bash", {"command": "cd sub && git commit -F msg.txt"}),
    (PASS, "git tag -l en lecture", "Bash", {"command": f"git tag -l --format='%(refname) {EM} %(subject)'"}),
    (PASS, "git merge-base", "Bash", {"command": f"git merge-base HEAD origin/master # {EM}"}),
    (PASS, "lecture avant un merge", "Bash", {"command": f"git log --grep='{EM}' && git merge --no-edit origin/master"}),
    (PASS, "< d'une autre commande", "Bash", {"command": "git tag -l 'v*' && wc -l < notes.md"}),
    (PASS, "gh api GET", "Bash", {"command": f"gh api repos/o/r/pulls/1/comments --jq '.[] | select(.body | contains(\"{EM}\"))'"}),
    (PASS, "gh api graphql query", "Bash", {"command": f"gh api graphql -f query='query {{ x(s: \"{EM}\") }}'"}),
    (BLOCK, "gh api graphql mutation", "Bash", {"command": f"gh api graphql -f query='mutation {{ add(body: \"a {EM} b\") }}'"}),
    (BLOCK, "gh api -f body", "Bash", {"command": f'gh api repos/o/r/issues/1/comments -f body="a {EM} b"'}),
    (BLOCK, "gh api -F body=@fichier", "Bash", {"command": "gh api repos/o/r/issues/1/comments -F body=@body.md"}),
    (BLOCK, "gh api --input", "Bash", {"command": "gh api repos/o/r/issues/1/comments --input body.md"}),
    (BLOCK, "--body $(cat fichier)", "Bash", {"command": 'gh pr create --title t --body "$(cat body.md)"'}),
    (BLOCK, "git merge -m", "Bash", {"command": f'git merge --no-ff -m "a {EM} b" feat'}),
    (BLOCK, "git --no-pager commit", "Bash", {"command": f'git --no-pager commit -m "a {EM} b"'}),
    (BLOCK, "git -c -c commit", "Bash", {"command": f'git -c a=b -c c=d commit -m "a {EM} b"'}),
    (BLOCK, "gh issue close -c", "Bash", {"command": f'gh issue close 3 -c "a {EM} b"'}),
    (BLOCK, "git notes add", "Bash", {"command": f'git notes add -m "a {EM} b"'}),
    (PASS, "heredoc reecrit le fichier perime", "Bash", {"command": "cat > msg.txt <<'EOF'\nclean now\nEOF\ngit commit -F msg.txt"}),
    (BLOCK, "heredoc vers fichier avec tiret", "Bash", {"command": f"cat > fresh.txt <<'EOF'\nnew {EM} text\nEOF\ngit commit -F fresh.txt"}),
    (PASS, "autre outil", "Read", {"file_path": "/x"}),
]


def run(tool, ti):
    payload = {"tool_name": tool, "tool_input": ti, "cwd": tmpdir}
    r = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )
    return BLOCK if r.returncode == 2 else PASS, r


fails = 0
for want, name, tool, ti in CASES:
    got, r = run(tool, ti)
    ok = got == want
    fails += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {name}: attendu {want}, obtenu {got}")
    if not ok and r.stderr:
        print("     " + r.stderr.strip().replace("\n", "\n     "))

r = subprocess.run(
    [sys.executable, HOOK], input="pas du json", capture_output=True, text=True
)
ok = r.returncode == 0
fails += not ok
print(f"{'ok  ' if ok else 'FAIL'} entree invalide: fail open (exit {r.returncode})")

print(f"\n{len(CASES) + 1 - fails}/{len(CASES) + 1} ok")
sys.exit(1 if fails else 0)
