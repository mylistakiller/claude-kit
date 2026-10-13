#!/usr/bin/env python3
"""Suite de tests de guard-agent-effort.py.

python3 hooks/tests/test-guard-agent-effort.py
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = str(Path(__file__).resolve().parents[1] / "guard-agent-effort.py")
PASS, BLOCK = "passe", "bloque"
_tmp = []


def transcript(*efforts):
    fh = tempfile.NamedTemporaryFile(
        "w", suffix=".jsonl", delete=False, encoding="utf-8"
    )
    for e in efforts:
        fh.write(json.dumps({"type": "user", "message": {"content": "x"}}) + "\n")
        fh.write(
            json.dumps(
                {"type": "assistant", "effort": e, "perTurnEffort": e, "message": {}}
            )
            + "\n"
        )
    fh.close()
    _tmp.append(fh.name)
    return fh.name


CASES = [
    (PASS, "general-purpose sans effort", {"subagent_type": "general-purpose"}, None),
    (PASS, "effort medium", {"effort": "medium"}, None),
    (PASS, "effort low", {"effort": "low"}, None),
    (BLOCK, "effort high", {"effort": "high"}, None),
    (BLOCK, "effort xhigh", {"effort": "xhigh"}, None),
    (BLOCK, "effort max majuscules", {"effort": "MAX"}, None),
    (PASS, "fork, parent medium", {"subagent_type": "fork"}, ("high", "medium")),
    (
        BLOCK,
        "fork, parent passe en high",
        {"subagent_type": "fork"},
        ("medium", "high"),
    ),
    (BLOCK, "general-purpose sans effort, parent en high", {"subagent_type": "general-purpose"}, ("high",)),
    (PASS, "general-purpose effort medium, parent en high", {"effort": "medium"}, ("high",)),
    (BLOCK, "model fable sans effort", {"model": "fable"}, ("medium",)),
    (PASS, "model fable avec effort medium", {"model": "fable", "effort": "medium"}, ("medium",)),
    (BLOCK, "fork avec effort medium, parent high (effort ignore par le fork)", {"subagent_type": "fork", "effort": "medium"}, ("high",)),
    (PASS, "fork, transcript absent", {"subagent_type": "fork"}, "missing"),
    (PASS, "fork, transcript vide", {"subagent_type": "fork"}, ()),
]


def run(tool_input, efforts, tool_name="Agent"):
    if efforts == "missing":
        path = "/nonexistent/t.jsonl"
    elif efforts is None:
        path = ""
    else:
        path = transcript(*efforts)
    payload = {
        "tool_name": tool_name,
        "tool_input": tool_input,
        "transcript_path": path,
    }
    p = subprocess.run(
        [HOOK], input=json.dumps(payload), capture_output=True, text=True
    )
    return p.returncode


def main():
    fails = 0
    for expected, name, inp, efforts in CASES:
        got = BLOCK if run(inp, efforts) == 2 else PASS
        ok = got == expected
        fails += not ok
        print(f"{'OK ' if ok else 'KO '} {expected:6} {name}")
    other = run({"effort": "max"}, None, tool_name="Bash")
    print(f"{'OK ' if other == 0 else 'KO '} passe  autre outil ignore")
    fails += other != 0
    bad = subprocess.run(
        [HOOK], input="not json", capture_output=True, text=True
    ).returncode
    print(f"{'OK ' if bad == 0 else 'KO '} passe  entree invalide (fail open)")
    fails += bad != 0
    for p in _tmp:
        os.unlink(p)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
