#!/usr/bin/env bash
# SessionStart hook: once a week, signal that a watched skill repo (mattpocock/skills...)
# moved since its last evaluation, so the useful changes get picked up without the user pasting
# the URL again. One `git ls-remote` per repo per week; silent the rest of the time.
#
# Never blocks: always exits 0. Monorepo only.
#
# Configuration: MONOREPO, the project dir the hook runs for (default: $HOME/src/monorepo).
# The script is read from this kit's scripts/ directory.

set -u

payload="$(cat 2>/dev/null || true)"
cwd="$(printf '%s' "$payload" | python3 -c '
import json,sys
try: print((json.load(sys.stdin).get("cwd") or ""))
except Exception: print("")
' 2>/dev/null)"
[ -n "$cwd" ] || cwd="${CLAUDE_PROJECT_DIR:-$PWD}"

case "$cwd" in
  "${MONOREPO:-$HOME/src/monorepo}"*) ;;
  *) exit 0 ;;
esac

W="$(cd "$(dirname "$0")/.." && pwd)/scripts/upstream-watch.py"
[ -x "$W" ] || exit 0

due="$("$W" due 2>/dev/null)" || exit 0

echo
echo "${due}"
echo "Offer to evaluate the delta when the user has a free moment, not mid-task: upstream-watch.py"
echo "show <repo>, compare with this kit's skills, record the verdict in memory,"
echo "then upstream-watch.py seen <repo> once the user has seen it."

exit 0
