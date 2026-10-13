#!/usr/bin/env python3
"""Weekly watch on third-party skill repos you want to follow, to catch what is worth taking.

Each repo has a "seen" commit: the HEAD at the last evaluation. Once a week the SessionStart
hook asks GitHub (one `git ls-remote`, no clone) whether HEAD moved. When it did, the session
offers an evaluation: `show` prints the commits and the skill files changed since the seen
commit, the agent compares them with this plugin, records the verdict wherever you keep
such evaluations (a memory, a note), then `seen` moves the marker.

  due                  exit 0 and print the repos with new commits (weekly, for the hook)
  show <repo>          fetch into the cache, print log and diffstat since the seen commit
  seen <repo> [<sha>]  mark <sha> (default: fetched HEAD) as evaluated
  list                 repos watched, seen commit and last check

The clones under ~/.cache/claude-kit-upstream-watch are third-party content: read them,
never run anything from them. State lives in ~/.claude/logs/upstream-watch.json.
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import date

STATE = os.path.expanduser("~/.claude/logs/upstream-watch.json")
CACHE = os.path.expanduser("~/.cache/claude-kit-upstream-watch")
PERIOD_DAYS = 7
# Repo -> path whose changes matter. Add a repo here with the HEAD already evaluated.
WATCHED = {
    "mattpocock/skills": {
        "path": "skills",
        "seen": "b0618bc436ad893b3c5e84e55fba86586d34a404",
    },
}


def load() -> dict:
    try:
        with open(STATE) as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        state = {}
    for repo, cfg in WATCHED.items():
        state.setdefault(repo, {"seen": cfg["seen"], "checked": None})
    return state


def save(state: dict):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as fh:
        json.dump(state, fh, indent=2)


def git(*args, cwd=None, timeout=60) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    ).stdout


def remote_head(repo: str) -> str:
    return git(
        "ls-remote", f"https://github.com/{repo}.git", "HEAD", timeout=8
    ).split()[0]


def clone(repo: str) -> str:
    path = os.path.join(CACHE, repo.replace("/", "__"))
    if os.path.isdir(os.path.join(path, ".git")):
        git("fetch", "-q", "origin", cwd=path)
        git("reset", "-q", "--hard", "origin/HEAD", cwd=path)
    else:
        os.makedirs(CACHE, exist_ok=True)
        git("clone", "-q", f"https://github.com/{repo}.git", path, timeout=120)
    return path


def cmd_due(_):
    state, today, moved = load(), date.today(), []
    for repo in WATCHED:
        checked = state[repo].get("checked")
        if checked and (today - date.fromisoformat(checked)).days < PERIOD_DAYS:
            continue
        try:
            head = remote_head(repo)
        except (subprocess.SubprocessError, OSError, IndexError):
            continue  # offline or GitHub down: retry next session, do not stamp
        state[repo]["checked"] = today.isoformat()
        if head != state[repo]["seen"]:
            moved.append(f"{repo} (seen {state[repo]['seen'][:7]}, now {head[:7]})")
    save(state)
    if not moved:
        sys.exit(1)
    print("upstream skill repos with new commits: " + ", ".join(moved))


def cmd_show(args):
    state = load()
    seen, path = state[args.repo]["seen"], WATCHED[args.repo]["path"]
    work = clone(args.repo)
    print(
        f"# {args.repo}: {seen[:7]}..{git('rev-parse', '--short', 'HEAD', cwd=work).strip()}\n"
    )
    print(git("log", "--oneline", "--no-merges", f"{seen}..HEAD", "--", path, cwd=work))
    print(git("diff", "--stat=120", seen, "HEAD", "--", path, cwd=work))
    added = git("diff", "--name-status", seen, "HEAD", "--", path, cwd=work)
    print("New or renamed SKILL.md:")
    for line in added.splitlines():
        if line[0] in "AR" and line.endswith("SKILL.md"):
            print("  " + line)
    print(
        f"\nRead the files in {work}; full diff: git -C {work} diff {seen[:7]} HEAD -- {path}"
    )


def cmd_seen(args):
    state = load()
    sha = args.sha or git("rev-parse", "HEAD", cwd=clone(args.repo)).strip()
    state[args.repo]["seen"] = sha
    save(state)
    print(f"{args.repo}: seen = {sha[:7]}")


def cmd_list(_):
    for repo, s in load().items():
        print(f"{repo}  seen {s['seen'][:7]}  checked {s.get('checked') or 'never'}")


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("due").set_defaults(fn=cmd_due)
    s = sub.add_parser("show")
    s.add_argument("repo", choices=list(WATCHED))
    s.set_defaults(fn=cmd_show)
    s = sub.add_parser("seen")
    s.add_argument("repo", choices=list(WATCHED))
    s.add_argument("sha", nargs="?")
    s.set_defaults(fn=cmd_seen)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
