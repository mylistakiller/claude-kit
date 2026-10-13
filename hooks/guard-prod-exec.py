#!/usr/bin/env python3
"""PreToolUse hook on Bash: the prod write path that `guard-prod-sql.py` does not see.

`guard-prod-sql.py` covers the two prod MCP servers. But the real write path to prod is
`kubectl exec` into a pod that already holds the DB credentials: PDO/PHP, `mysql -e`,
`psql`, `artisan tinker`. That route has no guard at all, and the pod users are not
read-only: one of them held `UPDATE, DELETE, DROP ON *.* WITH GRANT OPTION`
(checked 2026-09-09). This hook closes that hole.

Blocked (exit 2):
  1. a `kubectl exec` whose command carries an SQL write verb;
  2. a `kubectl exec` whose payload this hook CANNOT read: a local file piped or
     redirected into the pod, or an interpreter invoked on a path inside the pod. That is
     exactly how a past prod fix was applied, and a regex on the command line would have
     seen nothing;
  3. a write-capable `artisan` / `console` command.

Not blocked: reading (`get`, `logs`, `describe`, `cp` out, `env`, `SHOW GRANTS`,
`SELECT`), staging/local namespaces, anything that does not actually invoke `kubectl`,
and any mention of it inside a heredoc body, which is data and not a command.

Exempt by design: the scripts matched by PROD_EXEC_ALLOWED_SCRIPTS (none by default). Meant
for a script that carries its own scope lock (explicit ids, a max count, closed list of
operations, dry run by default, before-state snapshot), so it needs neither the falsify pass
nor the INVARIANTS block. Its command line must never show `kubectl`, and ALLOWED_SCRIPTS
below makes that exemption explicit rather than an accident of the regex.

Configuration (environment variables, all optional):
  PROD_EXEC_ALLOWED_SCRIPTS   regex of exempt script paths (default: empty, nothing exempt),
                              e.g. `/scripts/safe-relaunch[.]py`
  PROD_EXEC_NONPROD_TARGETS   regex (full match) of kubectl namespaces/contexts and `--host`
                              values that are NOT prod (default:
                              `staging(?:[-.].*)?|.*-dev|kind-.*|minikube`)

Escape hatch, same shape as `-- allow-full-scan`: put `allow-prod-write` in the command
(a shell comment does the job). Until 2026-09-18 that keystroke alone was enough: the
/prod-run doc said the plan needed a /falsify pass first and a rollback snapshot, but
nothing checked either had actually happened, and both the deleted_at omission (14
wrongly-reactivated rows) and a slug-only matching bug went out under
exactly that unchecked escape hatch. Now the flag alone is not enough: this hook also
requires, in this session's transcript,
  1. the falsify skill's own mandated output line, `Relecteur: n'a pas pu casser...`, to
     be the LAST such line (not `Relecteur: a cassé le verdict.`, not absent);
  2. a successful Write or Bash call that produced a nonempty rollback snapshot at an absolute path (a
     `<ticket>-before-<date>.json`/`.csv` file);
  3. the LAST `INVARIANTS:` block in the transcript to carry an `expected_rows:` line and
     at least one `forbidden[...]:` line, with every forbidden count reading exactly 0.
     A nonzero forbidden count is not a missing check, it is the plan itself saying it
     would touch rows it should not (see /prod-run step 2, added 2026-09-19): the exact
     shape of the deleted_at bug, caught mechanically instead of by a human noticing after
     the fact.
Any of the three missing, and the write still blocks even with the flag present. Rerunning
/prod-run's plan/estimate/falsify steps (see SKILL.md) and getting past all three is the
only way through, not a stronger keystroke.

Exit codes: 0 = allow, 2 = block (stderr goes back to Claude). Fails open on anything
unexpected other than the transcript check itself, which fails CLOSED (no transcript or
unreadable file = no verdict = still blocked) since the whole point is not to trust
unverifiable claims.
"""

import glob
import json
import os
import re
import shlex
import sys
from pathlib import Path

RELECTEUR = re.compile(r"Relecteur\s*:\s*([^\n]+)", re.I)


RELECTEUR_FIRST_LINE = re.compile(r"\A\s*Relecteur\s*:", re.I)


def latest_falsify_verdict_is_clean(transcript_path: str) -> bool:
    """Last `Relecteur:` line (the falsify skill's own mandated output) anywhere in the
    session transcript. False (no pass) on: no transcript, no line, unreadable file, or a
    last line that says the reviewer broke the verdict. Never None, so the caller has one
    boolean to act on, not a missing-data branch to forget.

    Read from assistant text AND, since 2026-10-02, from Bash results, for the same reason
    as latest_invariants_block_is_clean: text written right before a tool call is not in
    the transcript when this hook runs. Unlike the invariants block, a quoted line here
    fails OPEN: falsify/SKILL.md lists `a cassé` then `n'a pas pu casser`, so a `cat` or a
    grep of it would read as a clean verdict. Hence the narrow rule: only a non-error Bash
    result whose first non-blank line starts with `Relecteur:` counts (the verdict printed
    on purpose), never an Agent result (the reviewer's own prose) nor a file dump."""
    if not transcript_path or not os.path.exists(transcript_path):
        return False
    last = None
    bash_ids = set()
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "Relecteur" not in line and '"tool_use"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                kind = entry.get("type")
                for block_ in (entry.get("message") or {}).get("content") or []:
                    if not isinstance(block_, dict):
                        continue
                    if kind == "assistant" and block_.get("type") == "tool_use":
                        if block_.get("name") == "Bash":
                            bash_ids.add(block_.get("id"))
                    elif kind == "assistant" and block_.get("type") == "text":
                        for m in RELECTEUR.finditer(block_.get("text") or ""):
                            last = m.group(1)
                    elif (
                        kind == "user"
                        and block_.get("type") == "tool_result"
                        and not block_.get("is_error")
                        and block_.get("tool_use_id") in bash_ids
                    ):
                        text = _tool_result_text(block_)
                        if RELECTEUR_FIRST_LINE.match(text):
                            for m in RELECTEUR.finditer(text):
                                last = m.group(1)
    except Exception:
        return False
    if not last:
        return False
    # Substring on "pas pu casser" alone, not the full phrase with its apostrophe: a smart
    # quote (U+2019) instead of a straight one must not turn a clean verdict into a block.
    return "pas pu casser" in last.lower()


SNAPSHOT_MARK = re.compile(r"before", re.I)


SNAPSHOT_PRINTED = re.compile(r"[^\s'\"`(),:=]*before[^\s'\"`(),:]*\.(?:json|csv)\b", re.I)


def _printed_snapshot_exists(text: str, bases: list[str]) -> bool:
    for path in SNAPSHOT_PRINTED.findall(text):
        path = os.path.expanduser(path)
        candidates = [path] if os.path.isabs(path) else [os.path.join(b, path) for b in bases]
        if any(os.path.isfile(c) and os.path.getsize(c) > 0 for c in candidates):
            return True
    return False


def _session_scratchpads(transcript_path: str) -> list[str]:
    """The harness scratchpad of this session: /private/tmp/claude-<uid>/<project>/<sid>/."""
    project = os.path.basename(os.path.dirname(transcript_path))
    sid = os.path.splitext(os.path.basename(transcript_path))[0]
    found = glob.glob(f"/private/tmp/claude-*/{glob.escape(project)}/{glob.escape(sid)}")
    return [p for d in found for p in (os.path.join(d, "scratchpad"), d)]


def snapshot_targets(name: str, inp: dict) -> list[str]:
    if name == "Write":
        return [str(inp.get("file_path") or "")]
    if name != "Bash":
        return []
    try:
        lexer = shlex.shlex(inp.get("command") or "", posix=True, punctuation_chars=">;&|")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return []
    return [tokens[i + 1] for i, token in enumerate(tokens[:-1]) if token in (">", ">>", "--out", "--output")]


def session_has_snapshot(transcript_path: str) -> bool:
    # A tool request can fail, and a filename in a read command is not a backup.
    if not transcript_path or not os.path.exists(transcript_path):
        return False
    pending = {}
    bash_ids = set()
    bases = _session_scratchpads(transcript_path)
    try:
        with open(transcript_path, encoding="utf-8") as fh:
            for line in fh:
                entry = json.loads(line)
                for item in (entry.get("message") or {}).get("content") or []:
                    if not isinstance(item, dict):
                        continue
                    if entry.get("type") == "assistant" and item.get("type") == "tool_use":
                        pending[item.get("id")] = snapshot_targets(item.get("name"), item.get("input") or {})
                        if item.get("name") == "Bash":
                            bash_ids.add(item.get("id"))
                    elif entry.get("type") == "user" and item.get("type") == "tool_result" and not item.get("is_error"):
                        if item.get("tool_use_id") in bash_ids:
                            declared = "\n".join(
                                line for line in _tool_result_text(item).splitlines() if re.match(r"\s*snapshot\s*:", line, re.I)
                            )
                            if _printed_snapshot_exists(declared, bases):
                                return True
                        for target in pending.get(item.get("tool_use_id"), []):
                            path = Path(os.path.expanduser(target))
                            if not SNAPSHOT_MARK.search(path.name) or path.suffix.lower() not in (".json", ".csv"):
                                continue
                            # Relative paths depend on Bash's working directory; demand an unambiguous artifact.
                            if path.is_absolute() and path.is_file() and path.stat().st_size > 0:
                                return True
    except (OSError, ValueError, TypeError):
        return False
    return False


INVARIANTS_HEADER = re.compile(r"INVARIANTS\s*:", re.I)
EXPECTED_ROWS = re.compile(r"expected_rows\s*:\s*(\d+)", re.I)
FORBIDDEN = re.compile(r"forbidden\[[^\]\n]{1,200}\]\s*:\s*(\d+)", re.I)


def _tool_result_text(block_: dict) -> str:
    content = block_.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text") or "" for c in content if isinstance(c, dict) and c.get("type") == "text")
    return ""


def latest_invariants_block_is_clean(transcript_path: str) -> bool:
    """Last `INVARIANTS:` block (see /prod-run SKILL.md step 2) anywhere in the session,
    concatenating in transcript order the assistant text blocks AND the non-error tool
    results, so a block split across two turns still reads as one. Requires at least one
    `expected_rows:` line and at least one `forbidden[...]:` line in that block, and every
    forbidden count found must be exactly 0. Same fail-closed contract as the other two
    checks: no block, an incomplete one, or any nonzero forbidden count all mean not clean.

    Why tool results too (2026-10-02): assistant text written in the same turn,
    right before the tool call, is not in the transcript when this hook runs, and sometimes
    never lands there at all (4 blocks out of 4 missing in that session, checked with a
    marker). A tool result is always persisted before the next call, so the block printed
    by the dry run itself is what makes the apply passable within the same turn.
    Error results are skipped: this hook's own block message quotes `INVARIANTS:` and would
    otherwise become the last header. Any other tool output that merely quotes the header
    (a grep of this file) still counts and fails closed: rerun the dry run to re-emit."""
    if not transcript_path or not os.path.exists(transcript_path):
        return False
    parts = []
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "INVARIANTS" not in line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                kind = entry.get("type")
                if kind not in ("assistant", "user"):
                    continue
                for block_ in (entry.get("message") or {}).get("content") or []:
                    if not isinstance(block_, dict):
                        continue
                    if kind == "assistant" and block_.get("type") == "text":
                        parts.append(block_.get("text") or "")
                    elif kind == "user" and block_.get("type") == "tool_result" and not block_.get("is_error"):
                        parts.append(_tool_result_text(block_))
    except Exception:
        return False
    full_text = "\n".join(parts)
    headers = list(INVARIANTS_HEADER.finditer(full_text))
    if not headers:
        return False
    # The block runs from the last header to the next blank line, or the end of the text.
    body = full_text[headers[-1].end() :].split("\n\n", 1)[0]
    if not EXPECTED_ROWS.search(body):
        return False
    forbidden = FORBIDDEN.findall(body)
    if not forbidden:
        return False
    return all(int(v) == 0 for v in forbidden)


# Un corps de heredoc est de la DONNEE, pas une commande : un fichier local dont le contenu
# cite `kubectl exec` faisait bloquer une simple ecriture locale (constate 2 fois le 10/09/2026).
HEREDOC_BODY = re.compile(r"<<-?\s*(['\"]?)(\w+)\1.*?^\s*\2\s*$", re.S | re.M)

# `kubectl` doit etre INVOQUE, pas seulement mentionne : debut de ligne, ou apres un separateur
# de commande, ou dans un `sh -c "..."`. Les enrobages courants sont traverses.
WRAPPERS = r"(?:(?:sudo|xargs|env|time|nohup|timeout|watch|command|stdbuf)\s+(?:-\S+\s+)*)*"
KUBECTL_INVOKED = re.compile(
    r"(?:^|[|;&(`'\"]|\$\()\s*" + WRAPPERS + r"(?:\w+=\S+\s+)*(?:[\w./~-]+/)?kubectl\b",
    re.M,
)


def executable_part(command):
    """La commande privee de ses corps de heredoc."""
    return HEREDOC_BODY.sub("<<HEREDOC_BODY_STRIPPED", command)


WRITE_SQL = re.compile(
    r"\b(insert\s+into|update\s+\w|delete\s+from|replace\s+into|truncate\b|drop\s+"
    r"(table|database|user|dictionary|index|trigger|view)|alter\s+table|create\s+"
    r"(table|database|user|index|trigger)|grant\b|revoke\b|rename\s+table)",
    re.I,
)

# Interpreters that can carry a payload we cannot see from the command line.
OPAQUE_RUNNER = re.compile(
    r"(?<![./\w-])(php|python3?|psql|mysql|mysqldump|node|(?:ba)?sh(?!\s+-c\b))\b[^|]*"
    r"(/tmp/|/home/|\.php|\.py|\.sql|\.sh|<\s*\S|-f\s+\S)",
    re.I,
)
# 2026-10-08 retro: 13 reads blocked in a week because the payload was a local file fed to
# the pod. When that file is readable here, read it instead of guessing.
LOCAL_PAYLOAD = re.compile(r"(?:(?<!<)<(?!<)\s*|\bcat\s+)([^\s|;&<>()'\"]+)")
PAYLOAD_WRITE = re.compile(
    r"\bcommit\s*\(|->\s*(save|delete|update|insert|forceDelete|truncate)\s*\(|"
    r"\.(save|delete|bulk_create|bulk_update|executemany)\s*\(",
    re.I,
)


def local_payload_is_read_only(cmd: str) -> bool:
    """True when every payload fed into the pod is a local file readable here, none of them
    carries a write, and the command stays transparent once those redirections are gone."""
    files = LOCAL_PAYLOAD.findall(cmd)
    if not files:
        return False
    for f in files:
        path = os.path.expanduser(f)
        if not os.path.isfile(path):
            return False
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read(2_000_000)
        except OSError:
            return False
        if WRITE_SQL.search(text) or PAYLOAD_WRITE.search(text) or WRITE_ARTISAN.search(text):
            return False
    rest = LOCAL_PAYLOAD.sub(" ", cmd)
    return not (TINKER.search(rest) or OPAQUE_RUNNER.search(rest))


PIPED_IN = re.compile(r"(\||<)\s*[^|]*kubectl\s+exec|cat\s+\S+\s*\|[^|]*kubectl", re.I)
STDIN_EXEC = re.compile(r"kubectl\s+exec\s+(-\S+\s+)*-\S*i", re.I)

WRITE_ARTISAN = re.compile(
    r"\b(artisan|console)\s+\S*(delete|purge|create|import|send|dispatch|migrate|flush|"
    r"reset|sync|fix|update|publish|invoke)",
    re.I,
)
TINKER = re.compile(r"artisan\s+tinker", re.I)

READ_ONLY_KUBECTL = re.compile(
    r"^\s*kubectl\s+(?:--\S+\s+|-\S+\s+)*(get|describe|logs|top|explain|api-resources|"
    r"config|version|auth|cluster-info)\b",
    re.I,
)
_allowed = os.environ.get("PROD_EXEC_ALLOWED_SCRIPTS", "")
ALLOWED_SCRIPTS = re.compile(_allowed) if _allowed else None
NONPROD_TARGETS = os.environ.get("PROD_EXEC_NONPROD_TARGETS") or r"staging(?:[-.].*)?|.*-dev|kind-.*|minikube"
# 2026-10-01: the OTHER prod write path, invisible to everything above. The `apply-*.py`
# scripts (pymysql, a DB user with full write grants) are driven by
# `DRY=0` or `--apply`/`--execute`; 23 of them exist in ~/.claude/scripts and not one went
# through this gate. A python script invoked with such a go flag is a prod write until proven
# otherwise; a dry run (no flag) is a read and passes.
PY_SCRIPT_INVOKED = re.compile(
    r"(?:^|[|;&(`\s])(?:\w+=\S+\s+)*(?:uv\s+run\b[^\n|;&]*?|python3?\s+(?:-\S+\s+)*)\S+\.py\b",
    re.I,
)
APPLY_GO = re.compile(r"(?:^|[\s;&(])DRY=0(?=[\s;&)]|$)|\s--(?:apply|execute|no-dry-run)\b", re.I)


def command_segments(command: str) -> list[list[str]]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
    lexer.whitespace_split = True
    lexer.commenters = "#"
    segments, current = [], []
    for token in lexer:
        if token and all(c in ";&|()" for c in token):
            if current:
                segments.append(current)
            current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    # Shell wrappers carry executable commands in a quoted argument.
    for segment in list(segments):
        for i, token in enumerate(segment[:-2]):
            if os.path.basename(token) in ("sh", "bash") and segment[i + 1] == "-c":
                segments.extend(command_segments(segment[i + 2]))
    return segments


def targets_are_non_prod(command: str, executable: str) -> bool:
    try:
        segments = command_segments(command)
    except ValueError:
        return False
    found = False
    for segment in segments:
        for i, token in enumerate(segment):
            matches = os.path.basename(token) == "kubectl" if executable == "kubectl" else token.endswith(".py")
            if not matches:
                continue
            found = True
            args = segment[i + 1 :]
            if "--" in args and executable == "kubectl":
                args = args[: args.index("--")]
            targets = []
            flags = ("-n", "--namespace", "--context") if executable == "kubectl" else ("--host",)
            for j, arg in enumerate(args):
                if arg in flags and j + 1 < len(args):
                    targets.append(args[j + 1])
                elif any(arg.startswith(flag + "=") for flag in flags):
                    targets.append(arg.split("=", 1)[1])
                elif executable == "kubectl" and arg.startswith("-n") and len(arg) > 2:
                    targets.append(arg[2:])
            if not targets or not all(re.fullmatch(rf"(?:{NONPROD_TARGETS})", t, re.I) for t in targets):
                return False
    return found


def block(reason: str) -> None:
    sys.stderr.write(reason)
    sys.exit(2)


def enforce_escape_hatch(payload: dict, bypass: bool, prefix: str) -> None:
    """With `allow-prod-write` present, let the command through only if the session holds
    the three /prod-run artefacts; otherwise block naming what is missing. Without the
    keyword, return and let the caller block with its own explanation."""
    if bypass:
        transcript = payload.get("transcript_path") or ""
        falsify_ok = latest_falsify_verdict_is_clean(transcript)
        snapshot_ok = session_has_snapshot(transcript)
        invariants_ok = latest_invariants_block_is_clean(transcript)
        if falsify_ok and snapshot_ok and invariants_ok:
            sys.exit(0)
        missing = []
        if not snapshot_ok:
            missing.append(
                "no rollback snapshot in this session (a Write or Bash call writing a "
                "`<ticket>-before-<date>.json`/`.csv` file, /prod-run step 2)"
            )
        if not invariants_ok:
            missing.append(
                "no clean `INVARIANTS:` block in this session (an `expected_rows:` line "
                "plus at least one `forbidden[...]:` line, every forbidden count reading "
                "0, /prod-run step 2) -- a nonzero forbidden count blocks even if present, "
                "since that is the plan itself reporting it would touch rows it should not"
            )
        if not falsify_ok:
            missing.append(
                "no clean falsify pass in this session: either /falsify's prod-run variant "
                "never ran on this plan, or its last verdict here was "
                "`Relecteur: a cassé le verdict.`"
            )
        block(
            f"{prefix}`# allow-prod-write` is present, but "
            + "; and ".join(missing)
            + ".\nFollow /prod-run step 2 (snapshot, invariants) and step 3 (`/falsify` on "
            "the plan) before re-sending this exact command.\n"
        )


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    if (payload.get("tool_name") or "") != "Bash":
        sys.exit(0)
    raw = (payload.get("tool_input") or {}).get("command") or ""
    bypass = "allow-prod-write" in raw.lower()
    cmd = executable_part(raw)
    if ALLOWED_SCRIPTS and ALLOWED_SCRIPTS.search(cmd) and not KUBECTL_INVOKED.search(cmd):
        sys.exit(0)
    go = APPLY_GO.search(cmd) if PY_SCRIPT_INVOKED.search(cmd) else None
    if go and not targets_are_non_prod(cmd, "python"):
        prefix = (
            f"BLOCKED: this runs a local script against production with the go flag "
            f"`{go.group(0).strip()}`: the pymysql apply path (a DB user with full write "
            "grants), which no other guard sees. The dry run without that flag is a read "
            "and passes.\n"
        )
        enforce_escape_hatch(payload, bypass, prefix)
        block(
            prefix + "\nFollow /prod-run first: plan -> pre-flight COUNT -> snapshot + invariants -> "
            "/falsify on the plan -> the user's explicit go-ahead -> apply -> verify.\nThen re-send "
            "the same command with `# allow-prod-write` appended, once the snapshot, a clean "
            "`INVARIANTS:` block and /falsify's last verdict are all in this session's "
            "transcript.\n"
        )
    if not KUBECTL_INVOKED.search(cmd):
        sys.exit(0)
    if READ_ONLY_KUBECTL.match(cmd) and "exec" not in cmd:
        sys.exit(0)
    if targets_are_non_prod(cmd, "kubectl"):
        sys.exit(0)
    if "exec" not in cmd:
        sys.exit(0)

    hit = WRITE_SQL.search(cmd)
    is_artisan = WRITE_ARTISAN.search(cmd)
    is_opaque = TINKER.search(cmd) or PIPED_IN.search(cmd) or STDIN_EXEC.search(cmd) or OPAQUE_RUNNER.search(cmd)
    if not (hit or is_artisan or is_opaque):
        sys.exit(0)
    if not (hit or is_artisan) and local_payload_is_read_only(cmd):
        sys.exit(0)

    prefix = (
        "BLOCKED: this is a write to production through `kubectl exec`, the path "
        "`guard-prod-sql.py` does not cover. The pod's DB user is NOT read-only.\n"
    )

    enforce_escape_hatch(payload, bypass, prefix)

    suffix = (
        "\nFollow /prod-run first: plan -> pre-flight COUNT -> snapshot + invariants -> "
        "/falsify on the plan -> the user's explicit go-ahead -> apply -> verify.\nThen re-send "
        "the same command with `# allow-prod-write` appended, once the snapshot, a clean "
        "`INVARIANTS:` block and /falsify's last verdict are all in this session's "
        "transcript.\n"
    )

    if hit:
        block(f"{prefix}Detected statement: `{hit.group(0)}`.{suffix}")

    if is_artisan:
        block(f"{prefix}The `artisan`/`console` command it runs writes (create/import/send/delete/…).{suffix}")

    block(
        f"{prefix}The payload is not visible from the command line: a file piped or "
        "redirected into the pod, or an interpreter run on a path inside it. This hook "
        "cannot tell a SELECT from an UPDATE here, so it asks rather than guesses.\nIf it only reads, say so and re-send with "
        f"`# allow-prod-write`.{suffix}"
    )


if __name__ == "__main__":
    main()
