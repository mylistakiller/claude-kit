#!/usr/bin/env python3
"""Auto-approve strictly local read-only Bash commands, so the permission prompt stays
reserved for what can actually change something.

Contract (read from the 2.1.267 binary's embedded hook docs, event PermissionRequest):
  allow -> {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                   "decision": {"behavior": "allow"}}}
  fall through to the normal prompt -> no output at all.

This hook never denies. Anything it does not recognize is left to the prompt, so an unrecognized command
falls back to the normal permission prompt. It is allowlist-based on purpose: the
prod clients (mysql, clickhouse, gcloud, kubectl, psql, redis-cli, php) are simply absent
from the list, and so is every writing git subcommand.

Every approval is appended to ~/.claude/logs/auto-approve.log for after-the-fact audit.
"""

import json
import os
import re
import shlex
import sys
import time

LOG = os.path.expanduser("~/.claude/logs/auto-approve.log")

# Interpreters and readers with output-file modes stay behind the normal prompt.
READ_ONLY = {
    "basename",
    "cat",
    "column",
    "comm",
    "cut",
    "diff",
    "dirname",
    "du",
    "echo",
    "egrep",
    "false",
    "fgrep",
    "grep",
    "head",
    "jq",
    "ls",
    "nl",
    "pwd",
    "realpath",
    "rg",
    "stat",
    "tail",
    "tr",
    "true",
    "wc",
    "which",
}

GIT_READ_ONLY = {
    "describe",
    "ls-files",
    "rev-parse",
    "shortlog",
    "status",
}

# Shell metacharacters that could chain, redirect or substitute a second command.
FORBIDDEN = re.compile(r"[>;&`\n]|\$\(|\|\||<\(")

# Private key material and credential stores: worth a conscious click even to read.
SECRET_PATH = re.compile(r"(\.ssh/|id_rsa|id_ed25519|\.gnupg|\.aws/credentials|\.kube/config)")


def segment_is_read_only(argv):
    if not argv:
        return False
    # A basename cannot establish that an arbitrary executable is trusted.
    if "/" in argv[0]:
        return False
    cmd = argv[0]
    args = argv[1:]

    if cmd == "git":
        # Global options can configure executable helpers such as core.fsmonitor.
        if not args or args[0].startswith("-"):
            return False
        sub = args[0]
        if sub in GIT_READ_ONLY:
            return True
        # branch / tag / worktree / stash / remote / config only read when bare or with
        # an explicit listing flag; any other argument can create or delete.
        if sub == "branch":
            return all(a in ("branch", "-a", "-r", "-v", "-vv", "--all", "--show-current") for a in args)
        if sub == "tag":
            return args == ["tag"] or all(a in ("tag", "-l", "--list", "-n") for a in args)
        if sub in ("worktree", "stash"):
            return args == [sub, "list"]
        if sub == "remote":
            return args in (["remote"], ["remote", "-v"])
        if sub == "config":
            return args == ["config", "--list"] or (
                len(args) == 3 and args[:2] == ["config", "--get"] and not args[2].startswith("-")
            )
        return False

    if cmd == "rg" and any(a.split("=", 1)[0] in ("--pre", "--hostname-bin") for a in args):
        return False
    if cmd not in READ_ONLY:
        return False
    return True


def is_read_only(command):
    if FORBIDDEN.search(command):
        return False
    if SECRET_PATH.search(command):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if not tokens:
        return False
    segment = []
    for tok in tokens:
        if tok == "|":
            if not segment_is_read_only(segment):
                return False
            segment = []
        else:
            segment.append(tok)
    return segment_is_read_only(segment)


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    if payload.get("tool_name") != "Bash":
        return
    command = (payload.get("tool_input") or {}).get("command") or ""
    if not is_read_only(command):
        return

    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as fh:
        fh.write("%s\t%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), command))

    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "allow"},
            },
            "suppressOutput": True,
        },
        sys.stdout,
    )


if __name__ == "__main__":
    main()
