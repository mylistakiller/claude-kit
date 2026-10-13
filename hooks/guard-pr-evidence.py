#!/usr/bin/env python3
"""PreToolUse hook: a PR body carries its evidence, pasted, or it does not go out.

CLAUDE.md asks for the exact commands and their result in every PR body, pass/fail counts
rather than "tests pass", and a body of 10 to 15 lines with no section headers. That rule is
the one that costs the most when it slips: a PR that claims a test run nobody did is only
caught in review, or not at all.

Checked on `gh pr create` and `gh pr edit` when they carry a body:
  - at least one fenced code block, and at least one digit inside it (a count, not a claim)
  - no "tested / tests pass" style claim outside a code block when no count backs it
  - prose (everything outside the fences) at most 15 lines
  - no markdown section header
  - no emoji in the title

Since 2026-10-08 it also wants the R0-R4 risk class and a code-review line in the body, and blocks a diff that
changes source files without touching a single test file unless the body carries
`allow-untested` with the reason (the red-green proof lives in scripts/red-green.sh).

Escape hatch: `allow-thin-pr` anywhere in the body, for a change with genuinely nothing to
run (docs, a config file). It is a statement that there is no evidence, not a way to skip
producing it.

Exit codes: 0 = allow, 2 = block (stderr goes back to Claude). Fails open.
"""

import json
import re
import shlex
import sys
from pathlib import Path

KIT_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
PR_WRITE = re.compile(r"\bgh\s+pr\s+(create|edit)\b")
FENCE = re.compile(r"```.*?```", re.S)
HEADER = re.compile(r"^\s{0,3}#{1,6}\s+\S", re.M)
CLAIM = re.compile(
    r"\b(tested|tests? (pass|passing|green|ok)|testé|testée|tout passe|ça passe|"
    r"suite verte|lint ok|build ok)\b",
    re.I,
)
EMOJI = re.compile(
    "[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f000-\U0001f2ff⬀-⯿]"
)
MAX_PROSE_LINES = 15

# The risk class line CLAUDE.md asks for (section Direction produit): R0 copy/docs/tests,
# R1 local UI, R2 small isolated feature, R3 business logic/API, R4 auth/permissions/
# billing/migration/data purge/external action.
RISK_CLASS = re.compile(r"\bR[0-4]\b")

# The agent review runs on every PR before a human sees it (CLAUDE.md, section Evidence):
# the body says so with its finding count, e.g. "code-review high : 3 findings, 3 fixed".
CODE_REVIEW = re.compile(r"code[- ]review", re.I)

SOURCE_EXT = (".go", ".ts", ".tsx", ".js", ".jsx", ".py", ".php")
TEST_MARKERS = re.compile(
    r"(_test\.go$|\.(test|spec)\.[jt]sx?$|(^|/)test_[^/]*\.py$|_test\.py$|(^|/)conftest\.py$"
    r"|(^|/)(tests?|__tests__|testdata|__snapshots__|fixtures)/)"
)


def changed_files(cwd: str) -> list[str] | None:
    """Files the PR will carry: committed on the branch, staged, unstaged, untracked. None
    when git cannot answer (not a repo, no origin/master), so the check is skipped rather
    than guessed."""
    import subprocess

    def git(*args: str) -> str | None:
        try:
            p = subprocess.run(
                ["git", *args],
                cwd=cwd or None,
                capture_output=True,
                text=True,
                timeout=8,
            )
        except Exception:
            return None
        return p.stdout if p.returncode == 0 else None

    base = (git("merge-base", "origin/master", "HEAD") or "").strip()
    if not base:
        return None
    parts = [
        git("diff", "--name-only", base, "HEAD"),
        git("diff", "--name-only", "HEAD"),
        git("ls-files", "--others", "--exclude-standard"),
    ]
    if any(p is None for p in parts):
        return None
    return sorted(
        {ln.strip() for p in parts for ln in (p or "").splitlines() if ln.strip()}
    )


def untested_source(files: list[str]) -> list[str]:
    """Source files in the diff when the diff carries no test file at all."""
    tests = [f for f in files if TEST_MARKERS.search(f)]
    if tests:
        return []
    return [f for f in files if f.endswith(SOURCE_EXT)]


def body_from_heredoc(cmd: str, flag: str) -> str | None:
    """`--body "$(cat <<'EOF' ... EOF)"`, the usual shape. shlex keeps it in one token but
    with the wrapper still around it."""
    m = re.search(
        rf"{flag}\s+\"?\$\(\s*cat\s*<<-?\s*'?(\w+)'?\s*\n(.*?)\n\s*\1\b",
        cmd,
        re.S,
    )
    return m.group(2) if m else None


def extract(cmd: str) -> tuple[str | None, str | None]:
    """(body, title) as they will be published, or None when not given on the line."""
    for flag in ("--body", "-b"):
        body = body_from_heredoc(cmd, flag)
        if body is not None:
            break
    else:
        body = None

    title = None
    try:
        args = shlex.split(cmd)
    except ValueError:
        return body, None

    for i, a in enumerate(args):
        nxt = args[i + 1] if i + 1 < len(args) else None
        if a in ("--body", "-b") and nxt and body is None:
            body = nxt
        elif a.startswith("--body="):
            body = body or a.split("=", 1)[1]
        elif a in ("--body-file", "-F") and nxt and body is None:
            try:
                body = Path(nxt).read_text(encoding="utf-8")
            except OSError:
                pass
        elif a in ("--title", "-t") and nxt:
            title = nxt
        elif a.startswith("--title="):
            title = a.split("=", 1)[1]
    return body, title


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    if (payload.get("tool_name") or "") != "Bash":
        sys.exit(0)
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    if not isinstance(cmd, str) or not PR_WRITE.search(cmd):
        sys.exit(0)

    body, title = extract(cmd)
    problems = []

    if title and EMOJI.search(title):
        problems.append(
            "the title carries an emoji, CLAUDE.md keeps PR titles text only"
        )

    if body is None or not body.strip():
        # `gh pr create --fill`, or a body typed in the editor: nothing to judge here.
        if not problems:
            sys.exit(0)
    else:
        if "allow-thin-pr" in body.lower():
            sys.exit(0)

        blocks = FENCE.findall(body)
        prose = FENCE.sub("", body)
        prose_lines = [ln for ln in prose.splitlines() if ln.strip()]

        if not blocks:
            problems.append(
                "no code block: the commands run and their output are described rather than "
                "pasted"
            )
        elif not any(re.search(r"\d", b) for b in blocks):
            problems.append(
                "the code block carries no number: CLAUDE.md asks for pass/fail counts, not "
                '"tests pass"'
            )

        if (
            blocks
            and CLAIM.search(prose)
            and not any(re.search(r"\d", b) for b in blocks)
        ):
            problems.append(
                "a test claim in the prose that no count in the output backs"
            )

        if HEADER.search(prose):
            problems.append(
                "a markdown section header, a 10 to 15 line body does not need one"
            )

        if len(prose_lines) > MAX_PROSE_LINES:
            problems.append(
                f"{len(prose_lines)} lines of prose outside the code blocks, the limit is "
                f"{MAX_PROSE_LINES}"
            )

        if not RISK_CLASS.search(prose):
            problems.append(
                "no risk class: the merge-risk line names R0 to R4 (R0 copy/docs/tests, R1 "
                "local UI, R2 small isolated feature, R3 business logic/API, R4 auth, "
                "permissions, billing, migration, data purge, external action)"
            )

        if not CODE_REVIEW.search(prose):
            problems.append(
                "no code-review line: run /code-review (high) before the PR and write its "
                'result in the body, e.g. "code-review high : 3 findings, 3 fixed"'
            )

        if "allow-untested" not in body.lower():
            files = changed_files(payload.get("cwd") or "")
            untested = untested_source(files) if files else []
            if untested:
                shown = ", ".join(untested[:4]) + (" ..." if len(untested) > 4 else "")
                problems.append(
                    f"source files changed with no test file in the diff ({shown}): a "
                    "behaviour change ships with a test that fails without it "
                    f"({KIT_SCRIPTS / 'red-green.sh'}), or the body says why none "
                    "can exist and carries `allow-untested`"
                )

    if not problems:
        sys.exit(0)

    out = ["BLOCKED: this PR does not meet the PR rules in CLAUDE.md."]
    out += [f"  - {p}" for p in problems]
    out.append(
        f"Paste the real output (the pre-PR scripts in {KIT_SCRIPTS}, e.g. pre-pr-go.sh, print a "
        "ready block), one line on what is not covered and why, and never write that "
        "something was tested when it was not run."
    )
    out.append(
        "For a change with genuinely nothing to run (docs, config), say so in the body and "
        "add `allow-thin-pr`."
    )
    sys.stderr.write("\n".join(out) + "\n")
    sys.exit(2)


if __name__ == "__main__":
    main()
