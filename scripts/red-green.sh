#!/usr/bin/env bash
# Red-green proof for the tests a branch adds or changes: run them WITHOUT the branch's
# source changes (expected: red), then WITH them (expected: green), and print one evidence
# block ready to paste in the PR body.
#
# Why: a test that has never failed proves nothing about the change it ships with. The
# pre-pr-* scripts show that the suite is green; this one shows that the new tests actually
# bite. The source files of the diff are stashed, the test files stay in place, the command
# runs, the stash is restored, the command runs again. Nothing is edited by hand.
#
# Usage:
#   red-green.sh [--base <ref>] [--cwd <dir>] -- <test command...>
#
#   red-green.sh -- go test ./internal/sync/... -run TestResolveConflict
#   red-green.sh --cwd web -- pnpm jest src/components/Chart.test.tsx
#   red-green.sh --cwd services/api -- uv run --frozen pytest tests/test_parser.py
#
# Env: BASE_REF (default origin/master).
#
# Exit: 0 when red then green, 1 when the proof fails (green without the change, or red with
# it), 2 on a setup problem (dirty stash state, no source file in the diff, no test file).
# The stash is always popped, even on failure, and the script refuses to start if a stash
# named by it is already present (a previous run that was interrupted).

set -uo pipefail

BASE_REF="${BASE_REF:-origin/master}"
RUN_DIR=""
while [ $# -gt 0 ]; do
  case "$1" in
    --base) BASE_REF="${2:-}"; shift 2 ;;
    --cwd) RUN_DIR="${2:-}"; shift 2 ;;
    --) shift; break ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "red-green.sh: unknown option $1" >&2; exit 2 ;;
  esac
done
[ $# -gt 0 ] || { echo "red-green.sh: no test command after --" >&2; exit 2; }

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "red-green.sh: not in a git repo" >&2; exit 2; }
cd "$ROOT" || exit 2
STASH_MSG="red-green.sh $(date +%s)"

if git stash list | grep -q 'red-green.sh'; then
  echo "red-green.sh: a stash from a previous run is still there (git stash list). Restore or drop it first." >&2
  exit 2
fi

is_test_file() {
  case "$1" in
    *_test.go|*.test.ts|*.test.tsx|*.test.js|*.test.jsx|*.spec.ts|*.spec.tsx|*.spec.js) return 0 ;;
    */tests/*|*/__tests__/*|*/test/*|*/testdata/*|*/__snapshots__/*|*/fixtures/*) return 0 ;;
    test_*.py|*/test_*.py|*_test.py|conftest.py|*/conftest.py) return 0 ;;
  esac
  return 1
}

is_source_file() {
  case "$1" in
    *.go|*.ts|*.tsx|*.js|*.jsx|*.py|*.php|*.sql) return 0 ;;
  esac
  return 1
}

BASE="$(git merge-base "$BASE_REF" HEAD 2>/dev/null || echo "$BASE_REF")"
# No mapfile: macOS ships bash 3.2.
declare -a SRC=() TESTS=()
while IFS= read -r f; do
  [ -n "$f" ] || continue
  if is_test_file "$f"; then
    TESTS+=("$f")
  elif is_source_file "$f"; then
    SRC+=("$f")
  fi
done < <({ git diff --name-only "$BASE" HEAD; git diff --name-only HEAD; git ls-files --others --exclude-standard; } | sort -u)

if [ ${#SRC[@]} -eq 0 ]; then
  echo "red-green.sh: no source file in the diff against $BASE_REF, nothing to stash. A test-only change has no red-green proof." >&2
  exit 2
fi
if [ ${#TESTS[@]} -eq 0 ]; then
  echo "red-green.sh: no test file in the diff against $BASE_REF. The PR needs a test that fails without the change." >&2
  exit 2
fi

# The branch's committed source changes are part of what must disappear for the red run, so
# the stash alone is not enough when the branch already has commits: we check out the base
# version of each source file, then restore the working tree from the stash afterwards.
git stash push --include-untracked -m "$STASH_MSG" -- "${SRC[@]}" >/dev/null 2>&1 || {
  echo "red-green.sh: git stash of the source files failed" >&2; exit 2; }
# When every source change is already committed there is nothing to stash and git creates
# no entry: popping would then take someone else's stash.
STASHED=0
git stash list | grep -q 'red-green.sh' && STASHED=1
restore() {
  for f in "${SRC[@]}"; do git checkout -q HEAD -- "$f" 2>/dev/null || true; done
  if [ "$STASHED" -eq 1 ]; then
    git stash pop -q >/dev/null 2>&1 || echo "red-green.sh: WARNING stash pop failed, run git stash list" >&2
  fi
}
trap restore EXIT
for f in "${SRC[@]}"; do
  if git cat-file -e "$BASE:$f" 2>/dev/null; then
    git checkout -q "$BASE" -- "$f"
  else
    rm -f "$f"
  fi
done

run_dir="${RUN_DIR:-.}"
RED_LOG="$(mktemp)"; GREEN_LOG="$(mktemp)"
( cd "$run_dir" && "$@" ) >"$RED_LOG" 2>&1; RED_RC=$?

trap - EXIT
restore

( cd "$run_dir" && "$@" ) >"$GREEN_LOG" 2>&1; GREEN_RC=$?

count_line() {
  grep -E -m1 -o '([0-9]+ (passed|failed|passing|failing|tests?)[^|]*|--- (FAIL|PASS):[^\n]*|^(ok|FAIL)[[:space:]]+[^[:space:]]+|Tests:[^\n]*)' "$1" | head -1
}
RED_SUM="$(count_line "$RED_LOG")"; GREEN_SUM="$(count_line "$GREEN_LOG")"

printf '```\n$ red-green.sh -- %s\n' "$*"
printf 'tests in diff : %s\n' "${TESTS[*]}"
printf 'source stashed: %s\n' "${SRC[*]}"
printf 'without change: exit %s  %s\n' "$RED_RC" "${RED_SUM:-$(tail -1 "$RED_LOG")}"
printf 'with change   : exit %s  %s\n' "$GREEN_RC" "${GREEN_SUM:-$(tail -1 "$GREEN_LOG")}"
if [ "$RED_RC" -ne 0 ] && [ "$GREEN_RC" -eq 0 ]; then
  printf 'red-green: OK\n```\n'
  rm -f "$RED_LOG" "$GREEN_LOG"
  exit 0
fi
printf 'red-green: FAILED\n```\n'
if [ "$RED_RC" -eq 0 ]; then
  echo "red-green.sh: the tests pass WITHOUT the source change: they do not exercise it. Log: $RED_LOG" >&2
fi
if [ "$GREEN_RC" -ne 0 ]; then
  echo "red-green.sh: the tests fail WITH the source change. Log: $GREEN_LOG" >&2
fi
exit 1
