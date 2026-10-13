#!/usr/bin/env bash
# PostToolUse hook: re-test a skill right after it is edited.
#
# A skill that quotes a path, a console command or a class it no longer has is how a prod
# procedure rots unnoticed. Running the suite is optional and therefore skipped; running it
# on the file just touched is not.
#
# Advisory only: always exits 0, stdout goes back as context.

set -u

payload="$(cat 2>/dev/null || true)"
path="$(printf '%s' "$payload" | python3 -c '
import json,sys
try: print(((json.load(sys.stdin).get("tool_input") or {}).get("file_path") or ""))
except Exception: print("")
' 2>/dev/null)"

case "$path" in
  */SKILL.md) ;;
  *) exit 0 ;;
esac

skill="$(basename "$(dirname "$path")")"
kit="$(cd "$(dirname "$0")/.." && pwd)"
checker="$kit/scripts/check-skills.py"
[ -x "$checker" ] || exit 0

out="$("$checker" --only "$skill" 2>&1)" || true
case "$out" in
  *"error:"*)
    echo "[check-skills] ${skill} has broken references after this edit:"
    printf '%s\n' "$out" | grep -E "error:|warn :"
    echo "Fix them before considering the skill done: a dead path or command in a prod skill"
    echo "runs the wrong thing against production."
    ;;
  *"warn :"*)
    echo "[check-skills] ${skill}: $(printf '%s' "$out" | grep -c 'warn :') warning(s)"
    printf '%s\n' "$out" | grep -E "warn :"
    ;;
esac

exit 0
