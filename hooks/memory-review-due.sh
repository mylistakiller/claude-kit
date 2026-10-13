#!/usr/bin/env bash
# SessionStart hook: signal once a day that the memory review has not run yet.
#
# The store (222 files, 84 carrying a péremption marker) only decays silently: nothing
# surfaces a dead memory at the moment recall injects it. This puts the review in front of
# the user at the first session of the day, and stays quiet for the rest of it.
#
# Never blocks: always exits 0. Monorepo only.
#
# Configuration: MONOREPO, the project dir the hook runs for (default: $HOME/src/monorepo).
# The scripts are read from this kit's scripts/ directory.

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

kit="$(cd "$(dirname "$0")/.." && pwd)"
R="$kit/scripts/memory-review.py"
[ -x "$R" ] || exit 0

due="$("$R" due 2>/dev/null)" || exit 0   # exit 1 = already reviewed today

echo
echo "${due}"
echo "Offer the memory-review skill (one QCM per memory, never in bulk) when the user has a free"
echo "moment; do not start it mid-task, and do not decide anything alone. Next up:"
"$R" candidates --n 3 2>/dev/null | grep -E '^[0-9]+\.' | sed 's/^/  /'

retro="$("$kit/scripts/session-retro.py" due 2>/dev/null)" && {
  echo "${retro}: run the weekly session retro first (memory-review skill, section"
  echo "« Retro hebdomadaire des sessions »)."
}

linear="$("$kit/scripts/linear-complete.py" due 2>/dev/null)" && {
  echo "${linear}: offer the linear-complete skill (priority, label, customer on the user's open"
  echo "cards, plan shown before any write)."
}

exit 0
