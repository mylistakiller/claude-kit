"""Shared by the guards on text published under the user's name (dashes, AI attribution).

A Bash command that publishes rarely carries its text inline only: a PR body often sits in a
file (`--body-file`, `--input`, `-F body=@x`, `$(cat x)`), a commit message in `-F`, sometimes
behind a `<` redirect, and the path is relative to a `cd X &&` or a `git -C X` earlier in the
command. Each guard reading those on its own is how they drifted: the dash guard never read
`--body-file` while the attribution guard did (code review of 2026-10-12).

Only the publishing segment counts. `git tag -l`, `git merge-base`, a `gh api` GET, or a
`git log --grep` chained before a `git merge` publish nothing, and a `< notes.md` feeding
another command in the same line is not a message file (second code review, 2026-10-12).

Imported as a plain module: hooks.json runs each hook by path, so its directory is on
sys.path.
"""

import os
import re
import shlex

GIT = r"\bgit(?:\s+(?:-[Cc]\s+\S+|--no-pager|-P|--git-dir=\S+|--work-tree=\S+))*\s+"
PUBLISHING_BASH = re.compile(
    GIT + r"(?:commit|merge|tag|notes\s+(?:add|append|edit))(?![\w-])"
    r"|\bgh\s+(?:pr|issue)\s+(?:create|edit|comment|review|merge|close)(?![\w-])"
    r"|\bgh\s+release\s+(?:create|edit)(?![\w-])"
    r"|\bgh\s+api(?![\w-])"
)
SEPARATORS = {";", "&&", "||", "|", "&", ";;"}
FILE_FLAGS = ("--file", "--body-file", "--notes-file", "--input")
FIELD_FLAGS = ("-F", "--field")
COMMAND_SUBST_FILE = re.compile(r"\$\(\s*(?:cat\s+|<\s*)(['\"]?)([^\s'\")]+)\1\s*\)")
WRITE_TARGET = re.compile(r"(?:>>?|\btee(?:\s+-a)?)\s*(['\"]?)([^\s'\";&|)<>]+)\1")

# Quoted matter is someone else's text: a fence opened at the start of a line, an inline span.
FENCE = re.compile(r"^[ \t]*```[^\n]*\n.*?^[ \t]*```", re.S | re.M)
INLINE_CODE = re.compile(r"`[^`\n]*`")


def own_text(text: str) -> str:
    return INLINE_CODE.sub(" ", FENCE.sub(" ", text))


def _tokens(text: str) -> list:
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return text.split()


def _segment(cmd: str, start: int) -> tuple:
    """Raw text and tokens of the command that starts at `start`, up to its separator."""
    line = cmd[start:].split("\n", 1)[0]
    seg = []
    for tok in _tokens(line):
        if tok in SEPARATORS:
            break
        seg.append(tok)
    return line, seg


def _publishes(seg: list, raw: str) -> bool:
    words = [t for t in seg if not t.startswith("-")]
    if "tag" in words[:4] and words[0] == "git":
        # A lightweight tag, a listing or a deletion carries no message.
        return any(
            re.match(r"^-[a-zA-Z]*[mFas]", t)
            or re.match(r"^--(message|file|annotate|sign)\b", t)
            for t in seg
        )
    if seg[:2] == ["gh", "api"]:
        method = None
        has_body = False
        for i, tok in enumerate(seg):
            nxt = seg[i + 1] if i + 1 < len(seg) else ""
            if tok in ("-X", "--method"):
                method = nxt.upper()
            elif tok.startswith(("-X", "--method=")) and tok not in ("-X", "--method"):
                method = tok.split("=", 1)[-1].removeprefix("-X").upper()
            elif tok in (
                "-f",
                "-F",
                "--field",
                "--raw-field",
                "--input",
            ) or tok.startswith(("--field=", "--raw-field=", "--input=")):
                has_body = True
        if "graphql" in seg:
            return "mutation" in raw
        return (method or ("POST" if has_body else "GET")) in ("POST", "PATCH", "PUT")
    return True


def _resolve(base: str, path: str) -> str:
    path = os.path.expanduser(path)
    return os.path.normpath(path if os.path.isabs(path) else os.path.join(base, path))


def _workdir(cmd: str, start: int, seg: list, cwd: str):
    """The directory the segment runs in: the `cd` chain before it, in order, then `-C`."""
    cur = cwd
    toks = _tokens(cmd[:start].replace("\n", " ; "))
    for i, tok in enumerate(toks):
        prev = toks[i - 1] if i else ";"
        if tok == "cd" and prev in SEPARATORS | {"(", "{"} and i + 1 < len(toks):
            nxt = toks[i + 1]
            if "$" in nxt:
                return None
            if nxt not in SEPARATORS and nxt != "-":
                cur = _resolve(cur, nxt)
    for i, tok in enumerate(seg[:-1]):
        if tok == "-C" and seg[0] == "git":
            if "$" in seg[i + 1]:
                return None
            cur = _resolve(cur, seg[i + 1])
    return cur


def _file_args(seg: list, raw: str) -> list:
    paths = []
    for i, tok in enumerate(seg):
        nxt = seg[i + 1] if i + 1 < len(seg) else None
        if tok in FIELD_FLAGS and nxt:
            # gh api: `-F key=@file` reads a file, `-F key=value` is inline. git: `-F file`.
            m = re.match(r"^[\w.\[\]-]+=(@?)(.*)$", nxt)
            if not m:
                paths.append(nxt)
            elif m.group(1):
                paths.append(m.group(2))
        elif tok.startswith("--field=") and "=@" in tok[8:]:
            paths.append(tok.split("=@", 1)[1])
        elif (tok in FILE_FLAGS or tok == "<") and nxt:
            paths.append(nxt)
        elif any(tok.startswith(f + "=") for f in FILE_FLAGS):
            paths.append(tok.split("=", 1)[1])
        elif re.match(r"^-[a-zA-Z]*F.", tok) and not tok.startswith("--"):
            paths.append(tok[tok.index("F") + 1 :])
    paths += [m.group(2) for m in COMMAND_SUBST_FILE.finditer(raw)]
    # A path built from a shell variable cannot be resolved here: let it through.
    return [p for p in paths if p and p != "-" and "$" not in p]


def bash_fields(cmd: str, cwd: str) -> list:
    """(label, text) to check when the command publishes: its text and its message files.

    The command text starts at the first publishing segment, so what a read run before it
    prints does not count. A message file the same command writes first (heredoc into a file,
    then `-F` it) is read from the command text, never from disk: this is a PreToolUse hook and
    the file on disk is the stale one.
    """
    if not isinstance(cmd, str):
        return []
    region, files, seen = None, [], set()
    writes = {m.group(2) for m in WRITE_TARGET.finditer(cmd)}
    for m in PUBLISHING_BASH.finditer(cmd):
        raw, seg = _segment(cmd, m.start())
        if not seg or not _publishes(seg, raw):
            continue
        region = m.start() if region is None else region
        wd = _workdir(cmd, m.start(), seg, cwd)
        for p in _file_args(seg, raw):
            full = _resolve(wd, p) if wd else None
            written = p in writes or any(_resolve(wd or cwd, w) == full for w in writes)
            if written:
                region = 0
                continue
            if not full or full in seen or not os.path.isfile(full):
                continue
            seen.add(full)
            try:
                with open(full, encoding="utf-8", errors="replace") as fh:
                    files.append((f"file {p}", fh.read(200_000)))
            except OSError:
                pass
    if region is None:
        return []
    return [("command", cmd[region:])] + files
