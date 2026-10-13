#!/usr/bin/env python3
"""PostToolUse hook: a prod apply script prints its INVARIANTS block, said when it is written.

guard-prod-exec.py refuses the go flag (`--apply`, `DRY=0`, `--execute`) until the session
holds a clean `INVARIANTS:` block. The 2026-10-08 retro counted 32 such blocks in a week, 10 of
the 13 checkable ones on a script that never printed the block: the gap surfaced after the
user's go, and the user waited while the script was patched and the dry run re-run. This moves the warning
to the moment the script is written.

In scope: a `.py` written or edited whose name starts with `apply-`, or whose source both
talks to MySQL (pymysql) and reads a go flag. It must print `INVARIANTS:`, `expected_rows:`
and at least one `forbidden[`.

Advisory only: always exits 0, stdout goes back as context.
"""

import json
import os
import re
import sys

GO_FLAG = re.compile(r"--apply\b|--execute\b|\bDRY\b")
NEEDED = {
    "INVARIANTS:": re.compile(r"INVARIANTS:"),
    "expected_rows:": re.compile(r"expected_rows:"),
    "forbidden[...]:": re.compile(r"forbidden\["),
}


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        path = (payload.get("tool_input") or {}).get("file_path") or ""
    except Exception:
        return
    if not path.endswith(".py") or not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            src = fh.read()
    except OSError:
        return
    named = os.path.basename(path).startswith("apply-")
    if not (named or ("pymysql" in src and GO_FLAG.search(src))):
        return
    missing = [k for k, rx in NEEDED.items() if not rx.search(src)]
    if not missing:
        return
    print(
        f"[check-apply-script] {os.path.basename(path)} is a prod apply script but never prints "
        f"{', '.join(missing)}.\nguard-prod-exec.py will refuse its go flag after the user's go until "
        "the session holds that block (/prod-run step 2). Print it in the dry run now: "
        "`INVARIANTS:`, `expected_rows: <n from a query>`, one `forbidden[<what>]: <count>` "
        "per thing the plan must not touch."
    )


if __name__ == "__main__":
    main()
