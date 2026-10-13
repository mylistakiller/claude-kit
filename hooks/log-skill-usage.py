#!/usr/bin/env python3
"""Log every skill use to ~/.claude/logs/skill-usage.jsonl, to find skills that never trigger.

Two entry points, one line each:
- PreToolUse on Skill: the model loaded the skill (source "model").
- UserPromptSubmit: the user typed /<name> and <name> is a skill dir (source "slash"). Those never
  go through the Skill tool, so counting only tool calls undercounts the skills driven by hand.

Report: scripts/skill-usage-report.py in this kit.
Never blocks, never prints: exit 0 on anything, a broken log must not cost a turn.
"""

import json
import os
import re
import sys
import time

LOG = os.path.expanduser("~/.claude/logs/skill-usage.jsonl")
KIT_SKILLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills")
SLASH = re.compile(r"^/([A-Za-z0-9_:.-]+)")


def is_skill(name: str, cwd: str) -> bool:
    if ":" in name:  # plugin skill, plugin:skill
        return True
    for root in (
        os.path.expanduser("~/.claude/skills"),
        KIT_SKILLS,
        os.path.join(cwd, ".claude", "skills"),
    ):
        if os.path.isfile(os.path.join(root, name, "SKILL.md")):
            return True
    return False


def main() -> None:
    payload = json.load(sys.stdin)
    cwd = payload.get("cwd") or os.getcwd()
    event = payload.get("hook_event_name")
    if event == "PreToolUse":
        name = (payload.get("tool_input") or {}).get("skill") or ""
        source = "model"
    elif event == "UserPromptSubmit":
        m = SLASH.match((payload.get("prompt") or "").lstrip())
        if not m or not is_skill(m.group(1), cwd):
            return
        name, source = m.group(1), "slash"
    else:
        return
    if not name:
        return
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    line = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "skill": name,
        "source": source,
        "session": payload.get("session_id"),
        "cwd": cwd,
    }
    with open(LOG, "a") as f:
        f.write(json.dumps(line) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
