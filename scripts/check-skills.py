#!/usr/bin/env python3
"""Staleness tests for this plugin's skills against the repo they operate on.

These skills encode procedures: console commands, file paths, classes, table and column
names. The code moves, the skill does not, and a rotted skill runs the wrong command against
prod. Nothing tested them, so this does:

  1. frontmatter parses, `name` matches the directory, description non-empty,
     allowed-tools are real tool names
  2. every repo-relative path quoted in the body still exists
  3. every `php console <command>` still exists in the console sources
     (--console-src or $CONSOLE_SRC, a repo-relative dir; the whole repo when unset)
  4. every `Class::method` still resolves to a definition somewhere in the repo
  5. stale markers in the body (PÉRIMÉ / TODO / FIXME) are surfaced as warnings

The repo is --repo, else $MONOREPO, else the current directory.
Exit 0 = clean, 1 = at least one broken reference. --json for automation.
Placeholders (<gid>, {date}, $VAR, *) are skipped, not guessed at, and so are
gitignored paths (a skill may point at a local projects/*/.env).
"""

import argparse
import json
import os
import re
import subprocess
import sys

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
REPO = os.path.expanduser(os.environ.get("MONOREPO", os.getcwd()))
CONSOLE_SRC = os.environ.get("CONSOLE_SRC", "")
SKILL_DIRS = [os.path.join(PLUGIN_ROOT, "skills")]

TOP_DIRS = (
    "projects",
    "docs",
    "infra",
    "tools",
    "go-utils",
    "db-schemas",
    "experiments",
    "proto",
    ".claude",
    ".github",
)

KNOWN_TOOLS = {
    "Bash",
    "Read",
    "Write",
    "Edit",
    "MultiEdit",
    "Glob",
    "Grep",
    "LS",
    "Task",
    "Agent",
    "AskUserQuestion",
    "WebFetch",
    "WebSearch",
    "TodoWrite",
    "NotebookEdit",
    "Skill",
    "SlashCommand",
    "ExitPlanMode",
    "BashOutput",
    "KillShell",
    "Artifact",
    "ToolSearch",
    "Monitor",
    "EnterPlanMode",
    "TaskOutput",
    "TaskStop",
}

PATH_RE = re.compile(r"(?<![\w./-])(" + "|".join(TOP_DIRS) + r")/[\w./@+-]*[\w/]")
CONSOLE_RE = re.compile(r"php\s+console\s+([a-z][\w.:-]*:[\w.:-]+)")
SYMBOL_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]{2,})::([a-zA-Z_][\w]*)\b")
STALE_RE = re.compile(r"\b(P[EÉ]RIM[EÉ]|TODO|FIXME|DEPRECATED)\b")
PLACEHOLDER = re.compile(r"[<>{}*$]|XXXX|\.\.\.")


def grep(pattern: str, *paths: str) -> bool:
    """True if the pattern is found in the repo's TRACKED files."""
    return bool(grep_out(pattern, *paths))


def grep_out(pattern: str, *paths: str) -> str:
    """git grep over tracked files only. `rg` is a zsh alias here, not a binary, so calling
    it from a subprocess silently fell through to `grep -r` over 3 GB of node_modules and
    took minutes; git grep skips everything untracked and returns in under a second."""
    cmd = ["git", "-C", REPO, "grep", "--no-color", "-h", "-o", "-E", "-e", pattern]
    if paths:
        cmd += ["--", *[os.path.relpath(p, REPO) if p.startswith(REPO) else p for p in paths]]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return r.stdout


def gitignored(ref: str) -> bool:
    """A skill may legitimately point at a local, gitignored file (projects/*/.env).
    Its absence on this machine is expected, not skill rot."""
    try:
        r = subprocess.run(
            ["git", "-C", REPO, "check-ignore", "-q", ref],
            capture_output=True,
            timeout=15,
        )
        return r.returncode == 0
    except Exception:
        return False


def strip_fences(text: str) -> str:
    return re.sub(r"```.*?```", " ", text, flags=re.S)


def parse_frontmatter(text: str):
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not m:
        return None
    fm = {}
    for line in m.group(1).splitlines():
        if re.match(r"^\w[\w-]*:", line):
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm


def check_skill(path: str, deep: bool):
    """Returns (errors, warnings) for one SKILL.md."""
    errors, warnings = [], []
    name = os.path.basename(os.path.dirname(path))
    text = open(path, encoding="utf-8", errors="replace").read()

    fm = parse_frontmatter(text)
    if fm is None:
        errors.append("no YAML frontmatter")
        fm = {}
    else:
        if fm.get("name") != name:
            errors.append(f"frontmatter name={fm.get('name')!r} != directory {name!r}")
        if not fm.get("description"):
            errors.append("empty or missing description")
        for tool in re.split(r"[,\s]+", fm.get("allowed-tools", "")):
            tool = tool.strip().strip("[]'\"")
            if tool and not tool.startswith("mcp__") and tool not in KNOWN_TOOLS:
                warnings.append(f"allowed-tools: unknown tool {tool!r}")

    body = text
    seen = set()
    for m in PATH_RE.finditer(body):
        ref = m.group(0).rstrip(".,;:)`'\"")
        if ref in seen or PLACEHOLDER.search(ref):
            continue
        seen.add(ref)
        # Only assert on refs that look like a real file or a directory, not prose.
        if not (re.search(r"\.\w{1,5}$", ref) or ref.count("/") >= 2):
            continue
        if not os.path.exists(os.path.join(REPO, ref)) and not gitignored(ref):
            errors.append(f"missing path: {ref}")

    prose = strip_fences(body)
    console_paths = [os.path.join(REPO, CONSOLE_SRC)] if CONSOLE_SRC else []
    for cmd in sorted({m.group(1) for m in CONSOLE_RE.finditer(body)}):
        if not grep(re.escape(cmd), *console_paths):
            errors.append(f"console command not found in {CONSOLE_SRC or 'the repo'}: {cmd}")

    symbols = sorted({m.group(1) for m in SYMBOL_RE.finditer(prose)}) if deep else []

    # A marker quoted as code is being discussed, not declared: strip inline spans first.
    for m in sorted({m.group(1) for m in STALE_RE.finditer(re.sub(r"`[^`]*`", " ", prose))}):
        warnings.append(f"stale marker in body: {m}")

    return name, errors, warnings, symbols


def resolve_symbols(symbols: set) -> set:
    """One git grep pass for all symbols at once, instead of one pass per symbol."""
    if not symbols:
        return set()
    alt = "|".join(sorted(re.escape(x) for x in symbols))
    # POSIX ERE (git grep -E) has no \b, so spell the word boundaries out.
    out = grep_out(rf"(^|[^A-Za-z0-9_])(class|interface|trait|struct|func|type)[[:space:]]+({alt})([^A-Za-z0-9_]|$)")
    found = {tok for line in out.splitlines() for tok in line.split()}
    return {x for x in symbols if x in found}


def main() -> None:
    ap = argparse.ArgumentParser()
    global REPO, CONSOLE_SRC
    ap.add_argument("--repo", default=REPO, help="repo whose paths and symbols are checked")
    ap.add_argument(
        "--console-src", default=CONSOLE_SRC, help="repo-relative dir holding the `php console` commands"
    )
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--deep", action="store_true", help="also resolve Class::method symbols (slow)")
    ap.add_argument("--only", help="check a single skill by name")
    args = ap.parse_args()
    REPO = os.path.abspath(os.path.expanduser(args.repo))
    CONSOLE_SRC = args.console_src
    if not os.path.isdir(REPO):
        ap.error(f"repo not found: {REPO}; use --repo or MONOREPO")

    results, files = [], []
    for root in SKILL_DIRS:
        if not os.path.isdir(root):
            continue
        for entry in sorted(os.listdir(root)):
            f = os.path.join(root, entry, "SKILL.md")
            if os.path.exists(f) and (not args.only or entry == args.only):
                real = os.path.realpath(f)
                if real not in [os.path.realpath(x) for x in files]:
                    files.append(f)

    if not files:
        ap.error("no matching skills found in this plugin")
    raw = [check_skill(f, args.deep) for f in files]
    known = resolve_symbols({s for _, _, _, syms in raw for s in syms})
    for name, errs, warns, syms in raw:
        for sym in syms:
            if sym not in known:
                warns.append(f"symbol not found in repo: {sym}")
        results.append((name, errs, warns))

    if args.json:
        print(json.dumps([{"skill": n, "errors": e, "warnings": w} for n, e, w in results], indent=2, ensure_ascii=False))
    else:
        broken = 0
        for name, errs, warns in results:
            if not errs and not warns:
                print(f"PASS  {name}")
                continue
            print(f"{'FAIL' if errs else 'WARN'}  {name}")
            for e in errs:
                print(f"        error: {e}")
            for w in warns:
                print(f"        warn : {w}")
            broken += bool(errs)
        print(f"\n{len(results)} skills checked, {broken} with broken references")

    sys.exit(1 if any(e for _, e, _ in results) else 0)


if __name__ == "__main__":
    main()
