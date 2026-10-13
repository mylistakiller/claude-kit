#!/usr/bin/env python3
"""Skill usage over the last N days (default 30), from ~/.claude/logs/skill-usage.jsonl.

Lists every installed skill (user, this plugin, and the repo named by $MONOREPO when set),
including the ones at zero: those are the candidates for a better description, a merge, or
deletion.

    skill-usage-report.py [--days 30]
"""

import argparse
import collections
import json
import os
import time

LOG = os.path.expanduser("~/.claude/logs/skill-usage.jsonl")
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
ROOTS = [
    os.path.expanduser("~/.claude/skills"),
    os.path.join(PLUGIN_ROOT, "skills"),
]
if os.environ.get("MONOREPO"):
    ROOTS.append(os.path.join(os.path.expanduser(os.environ["MONOREPO"]), ".claude", "skills"))


def plugin_name() -> str:
    """The plugin's name from its manifest: skills are logged as `<plugin>:<skill>`."""
    try:
        with open(os.path.join(PLUGIN_ROOT, ".claude-plugin", "plugin.json")) as fh:
            return json.load(fh)["name"]
    except (OSError, ValueError, KeyError):
        return "jv"


def installed() -> set[str]:
    names = set()
    for root in ROOTS:
        if os.path.isdir(root):
            names |= {
                d
                for d in os.listdir(root)
                if os.path.isfile(os.path.join(root, d, "SKILL.md"))
            }
    return names


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()
    cutoff = time.strftime(
        "%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - args.days * 86400)
    )

    counts: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter
    )
    first = None
    prefix = plugin_name() + ":"
    if os.path.exists(LOG):
        for raw in open(LOG):
            try:
                e = json.loads(raw)
            except ValueError:
                continue
            first = first or e["ts"]
            if e["ts"] >= cutoff:
                counts[e["skill"].removeprefix(prefix)][e["source"]] += 1

    print(f"log: {LOG} (since {first or 'empty'}), window {args.days} d")
    for name in sorted(
        set(counts) | installed(), key=lambda n: (-sum(counts[n].values()), n)
    ):
        c = counts[name]
        print(
            f"{sum(c.values()):5d}  model={c['model']:<4d} slash={c['slash']:<4d} {name}"
        )


if __name__ == "__main__":
    main()
