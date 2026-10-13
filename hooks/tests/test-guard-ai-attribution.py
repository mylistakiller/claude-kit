#!/usr/bin/env python3
"""Suite de tests de guard-ai-attribution.py.

python3 hooks/tests/test-guard-ai-attribution.py
"""

import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = str(Path(__file__).resolve().parents[1] / "guard-ai-attribution.py")
LINEAR = "mcp__plugin_engineering_linear__save_comment"
PASS, BLOCK = "passe", "bloque"

TRAILER = "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
FOOTER = "🤖 Generated with [Claude Code](https://claude.com/claude-code)"

tmpdir = tempfile.mkdtemp()
atexit.register(shutil.rmtree, tmpdir, True)
with open(os.path.join(tmpdir, "body.md"), "w", encoding="utf-8") as fh:
    fh.write("## Summary\n\nfix\n\n" + FOOTER + "\n")
with open(os.path.join(tmpdir, "clean.md"), "w", encoding="utf-8") as fh:
    fh.write("## Summary\n\nfix\n")
os.makedirs(os.path.join(tmpdir, "sub"))
with open(os.path.join(tmpdir, "sub", "msg.txt"), "w", encoding="utf-8") as fh:
    fh.write("x\n\n" + TRAILER + "\n")

HEREDOC = "git commit -m \"$(cat <<'EOF'\napi: fix\n\n" + TRAILER + '\nEOF\n)"'

CASES = [
    (BLOCK, "cd sub && commit -F", "Bash", {"command": "cd sub && git commit -F msg.txt"}),
    (BLOCK, "git -C sub commit -F", "Bash", {"command": "git -C sub commit -F msg.txt"}),
    (BLOCK, "body-file - avec redirection", "Bash", {"command": "gh pr create --title t --body-file - < sub/msg.txt"}),
    (BLOCK, "redirection collee", "Bash", {"command": "gh pr create --title t --body-file - <sub/msg.txt"}),
    (BLOCK, "flag court colle -aF", "Bash", {"command": "git commit -aFsub/msg.txt"}),
    (BLOCK, "gh pr merge --body", "Bash", {"command": f'gh pr merge 1 --squash --body "x\n\n{TRAILER}"'}),
    (BLOCK, "gh release create --notes", "Bash", {"command": f'gh release create v1 --notes "x\n\n{FOOTER}"'}),
    (BLOCK, "git tag -m", "Bash", {"command": f'git tag -a v1 -m "x\n\n{TRAILER}"'}),
    (BLOCK, "fence orphelin ne masque pas", "Bash", {"command": 'gh pr create --body "see ``` here\nGenerated with [Claude Code](x)\n```bash\nls\n```"'}),
    (BLOCK, "linear save_document", "mcp__plugin_engineering_linear__save_document", {"content": "doc\n\n" + FOOTER}),
    (BLOCK, "linear status update imbrique", "mcp__plugin_engineering_linear__save_status_update", {"update": {"body": FOOTER}}),
    (PASS, "co-auteur Jean-Claude", "Bash", {"command": 'git commit -m "x\n\nCo-Authored-By: Jean-Claude Martin <jc@example.com>"'}),
    (PASS, "co-auteur Claude Dupont", "Bash", {"command": 'git commit -m "x\n\nCo-Authored-By: Claude Dupont <cd@example.com>"'}),
    (PASS, "linear get non ecrivant", "mcp__plugin_engineering_linear__get_issue", {"id": FOOTER}),
    (BLOCK, "heredoc commit avec trailer", "Bash", {"command": HEREDOC}),
    (
        BLOCK,
        "commit -m une ligne",
        "Bash",
        {"command": f'git commit -m "x" -m "{TRAILER}"'},
    ),
    (
        BLOCK,
        "git -C dir commit",
        "Bash",
        {"command": f'git -C /x commit -m "a\n\n{TRAILER}"'},
    ),
    (
        BLOCK,
        "gh pr create footer",
        "Bash",
        {"command": f'gh pr create --title t --body "b\n\n{FOOTER}"'},
    ),
    (
        BLOCK,
        "gh pr create --body-file",
        "Bash",
        {"command": "gh pr create --title t --body-file body.md"},
    ),
    (BLOCK, "git commit -F", "Bash", {"command": "git commit -F body.md"}),
    (
        BLOCK,
        "gh pr edit --body-file=",
        "Bash",
        {"command": "gh pr edit 12 --body-file=body.md"},
    ),
    (
        BLOCK,
        "co-authored minuscules",
        "Bash",
        {"command": 'git commit -m "x\n\nco-authored-by: claude <a@b>"'},
    ),
    (BLOCK, "linear comment", LINEAR, {"issueId": "ENG-1", "body": "ok\n\n" + FOOTER}),
    (
        PASS,
        "commit propre",
        "Bash",
        {"command": "git commit -m \"$(cat <<'EOF'\napi: fix\nEOF\n)\""},
    ),
    (
        PASS,
        "pr body-file propre",
        "Bash",
        {"command": "gh pr create --title t --body-file clean.md"},
    ),
    (
        PASS,
        "regle citee en inline code",
        "Bash",
        {"command": 'gh pr create --title t --body "blocks `Co-Authored-By: Claude`"'},
    ),
    (
        PASS,
        "regle citee en fence",
        LINEAR,
        {"issueId": "ENG-1", "body": "```\n" + TRAILER + "\n```"},
    ),
    (PASS, "commande non publiante", "Bash", {"command": f"echo '{TRAILER}' > /tmp/x"}),
    (
        PASS,
        "git log qui mentionne",
        "Bash",
        {"command": "git log --grep 'Co-Authored-By: Claude'"},
    ),
    (
        PASS,
        "trailer humain",
        "Bash",
        {"command": 'git commit -m "x\n\nCo-Authored-By: Alex <alex@example.com>"'},
    ),
    (PASS, "fichier absent", "Bash", {"command": "git commit -F missing.md"}),
    (PASS, "recherche du trailer avant un merge", "Bash", {"command": f"git log --grep='{TRAILER}' && git merge --no-edit origin/master"}),
    (PASS, "gh api GET", "Bash", {"command": f"gh api repos/o/r/pulls --jq 'map(select(.body | contains(\"{FOOTER}\")))'"}),
    (BLOCK, "gh api -F body=@fichier", "Bash", {"command": "gh api repos/o/r/issues/1/comments -F body=@body.md"}),
    (BLOCK, "--body $(cat fichier)", "Bash", {"command": 'gh pr create --title t --body "$(cat body.md)"'}),
    (BLOCK, "git --no-pager commit", "Bash", {"command": f'git --no-pager commit -m "x\n\n{TRAILER}"'}),
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
