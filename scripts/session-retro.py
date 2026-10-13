#!/usr/bin/env python3
"""Weekly retro over the recent sessions: what should change in the environment, not the code.

The idea comes from mattpocock/skills `/retro`: a mistake an automated check could catch
becomes a check, a judgement call becomes a rule. Reading whole transcripts costs too much,
so this pulls the three signals that point at a gap and leaves the judgement to the
`memory-review` skill, case per case:

  - hook blocks, grouped by hook and reason: the same block repeated across sessions means
    the rule is not where the agent reads it before acting (or the hook is too strict)
  - user corrections: short user messages that open on "non", "pas", "stop", "je t'ai dit"...
  - tool errors per session, hook blocks excluded: a session that fought its tools

  signals [--days 7]   print the three signals for transcripts touched in that window
  due                  exit 0 when the last retro is older than 7 days (SessionStart hook)
  done                 stamp today as the last retro

State lives in ~/.claude/logs/session-retro.json.
"""

import argparse
import glob
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import date

PROJECTS = os.path.expanduser("~/.claude/projects")
STATE = os.path.expanduser("~/.claude/logs/session-retro.json")
PERIOD_DAYS = 7

HOOK = re.compile(r"^(\w+(?::\S+)?) hook error: \[\"[^\"]*/([\w.-]+)\"\]: (.*)", re.S)
CORRECTION = re.compile(
    r"^\s*(non\b|nan\b|pas (ça|comme)|stop\b|attention\b|arr[eê]te|je t'ai (dit|demand)|"
    r"j'ai dit|tu as oubli|tu oublies|c'est faux|faux\b|tu te trompes|pourquoi tu|"
    r"refais|recommence|on avait dit|ce n'est pas|c'est pas|no\b|wrong\b)",
    re.I,
)


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if b.get("type") == "text")
    return ""


def transcripts(days: int) -> list[str]:
    cutoff = time.time() - days * 86400
    files = glob.glob(os.path.join(PROJECTS, "*", "*.jsonl"))
    return sorted(f for f in files if os.path.getmtime(f) >= cutoff)


def scan(path: str, blocks, corrections, errors):
    title = os.path.basename(path)[:8]
    hits = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") == "custom-title":
                title = d.get("customTitle") or title
            if d.get("type") != "user" or d.get("isSidechain") or d.get("isMeta"):
                continue
            content = d.get("message", {}).get("content")
            if isinstance(content, list):
                for b in content:
                    if b.get("type") != "tool_result" or not b.get("is_error"):
                        continue
                    t = b.get("content")
                    t = t if isinstance(t, str) else text_of(t)
                    m = HOOK.match(t)
                    if m:
                        reason = m.group(3).strip().splitlines()[0][:140]
                        hits.append(("block", (m.group(2), reason)))
                    else:
                        hits.append(("error", None))
            msg = text_of(content).strip()
            if (
                msg
                and not msg.startswith("<")
                and len(msg) < 600
                and CORRECTION.match(msg)
            ):
                hits.append(("correction", msg.replace("\n", " ")[:220]))
    for kind, val in hits:
        if kind == "block":
            blocks[val].append(title)
        elif kind == "correction":
            corrections.append((title, val))
        else:
            errors[title] += 1


def cmd_signals(args):
    files = transcripts(args.days)
    blocks, corrections, errors = defaultdict(list), [], defaultdict(int)
    for f in files:
        scan(f, blocks, corrections, errors)
    print(f"{len(files)} transcripts touched in the last {args.days} days\n")

    print(f"## Hook blocks ({sum(len(v) for v in blocks.values())})")
    for (hook, reason), sessions in sorted(blocks.items(), key=lambda kv: -len(kv[1])):
        uniq = sorted(set(sessions))
        print(f"- {len(sessions)}x in {len(uniq)} session(s)  {hook}: {reason}")
        print(f"    sessions: {', '.join(uniq[:6])}{' ...' if len(uniq) > 6 else ''}")

    # A /loop or a scheduled prompt repeats the same message: count it once, with its tally.
    seen = defaultdict(int)
    for c in corrections:
        seen[c] += 1
    print(f"\n## User corrections ({len(seen)})")
    for (title, msg), n in seen.items():
        print(f"- [{title}]{f' {n}x' if n > 1 else ''} {msg}")

    print("\n## Tool errors outside hooks, top sessions")
    for title, n in sorted(errors.items(), key=lambda kv: -kv[1])[:8]:
        print(f"- {n:3d}  {title}")


def last_done() -> date | None:
    try:
        with open(STATE) as fh:
            return date.fromisoformat(json.load(fh)["last"])
    except (OSError, ValueError, KeyError):
        return None


def cmd_due(_):
    last = last_done()
    if last and (date.today() - last).days < PERIOD_DAYS:
        sys.exit(1)
    print(f"session retro due: last one {last.isoformat() if last else 'never'}")


def cmd_done(_):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as fh:
        json.dump({"last": date.today().isoformat()}, fh)
    print(f"session retro stamped {date.today().isoformat()}")


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("signals")
    s.add_argument("--days", type=int, default=PERIOD_DAYS)
    s.set_defaults(fn=cmd_signals)
    sub.add_parser("due").set_defaults(fn=cmd_due)
    sub.add_parser("done").set_defaults(fn=cmd_done)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
