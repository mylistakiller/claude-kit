#!/usr/bin/env python3
"""Weekly stamp for the `linear-complete` pass over your open Linear cards.

There is no Linear API key on this machine: the pass itself runs through the Linear MCP,
driven by the skill. This only remembers when it last ran, for the SessionStart signal.

  due    exit 0 when the last pass is older than 7 days
  done   stamp today

State lives in ~/.claude/logs/linear-complete.json.
"""

import json
import os
import sys
from datetime import date

STATE = os.path.expanduser("~/.claude/logs/linear-complete.json")
PERIOD_DAYS = 7


def last_done():
    try:
        with open(STATE) as fh:
            return date.fromisoformat(json.load(fh)["last"])
    except (OSError, ValueError, KeyError):
        return None


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "due":
        last = last_done()
        if last and (date.today() - last).days < PERIOD_DAYS:
            sys.exit(1)
        print(f"linear-complete due: last pass {last.isoformat() if last else 'never'}")
    elif cmd == "done":
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        with open(STATE, "w") as fh:
            json.dump({"last": date.today().isoformat()}, fh)
        print(f"linear-complete stamped {date.today().isoformat()}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
