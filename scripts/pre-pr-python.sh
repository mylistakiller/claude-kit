#!/usr/bin/env bash
# Pre-PR validation for the Python projects touched by the current branch: ruff, formatting
# and pytest, project by project, ending on an evidence block ready to paste into the PR body.
#
# Same contract as pre-pr-go.sh: the counts come out of the real
# output, a red run prints nothing on stdout and exits 1.
#
# `uv run --frozen` on purpose: without it uv may resolve and rewrite uv.lock, which is a
# change to the repo made by a check that is supposed to read. A stale lock shows up as a
# setup failure, which is the right place to see it.
#
# A project with no .venv is skipped unless --allow-sync: creating one downloads the whole
# dependency tree (an ML project can weigh 1 GB), not something a pre-PR check does by surprise.
#
# Usage:
#   pre-pr-python.sh [--only <project dir>]... [--base <ref>] [--allow-sync] [--no-tests]
#                    [--all] [--keep-logs]
#
# Env:
#   BASE_REF  base ref for the changed-files diff (default origin/master)
#
# Exit: 0 when every step of every project passes, 1 otherwise, 2 on a setup problem.

set -uo pipefail

BASE_REF="${BASE_REF:-origin/master}"
ALLOW_SYNC=0
RUN_TESTS=1
KEEP_LOGS=0
ALLOW_MANY=0
MAX_PROJECTS=6
declare -a ONLY=()

while [ $# -gt 0 ]; do
  case "$1" in
    --only) ONLY+=("${2:-}"); shift 2 ;;
    --base) BASE_REF="${2:-}"; shift 2 ;;
    --allow-sync) ALLOW_SYNC=1; shift ;;
    --no-tests) RUN_TESTS=0; shift ;;
    --all) ALLOW_MANY=1; shift ;;
    --keep-logs) KEEP_LOGS=1; shift ;;
    -h|--help) sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

REPO="$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "not in a git repo" >&2; exit 2; }
cd "$REPO" || exit 2
command -v uv >/dev/null || { echo "uv not on PATH" >&2; exit 2; }
git rev-parse --verify "$BASE_REF" >/dev/null 2>&1 || {
  echo "base ref $BASE_REF not found, run git fetch origin or pass --base" >&2; exit 2; }

project_of() {
  local dir; dir="$(dirname "$1")"
  while [ "$dir" != "." ] && [ "$dir" != "/" ]; do
    [ -f "$REPO/$dir/pyproject.toml" ] && { echo "$dir"; return 0; }
    dir="$(dirname "$dir")"
  done
  return 1
}

declare -a PROJECTS=()
declare -a ORPHANS=()
if [ "${#ONLY[@]}" -gt 0 ]; then
  for p in "${ONLY[@]}"; do
    p="${p#./}"; p="${p%/}"
    [ -f "$REPO/$p/pyproject.toml" ] || { echo "no pyproject.toml in $p" >&2; exit 2; }
    PROJECTS+=("$p")
  done
else
  changed="$(
    { git diff --name-only "$BASE_REF...HEAD"
      git diff --name-only HEAD
      git diff --name-only --cached
      git ls-files --others --exclude-standard
    } 2>/dev/null | sort -u
  )"
  while IFS= read -r f; do
    case "$f" in
      .worktrees/*|*/.venv/*) continue ;;
      *.py|*/pyproject.toml|*/uv.lock|*/requirements.txt) ;;
      *) continue ;;
    esac
    if proj="$(project_of "$f")"; then PROJECTS+=("$proj"); else ORPHANS+=("$f"); fi
  done <<< "$changed"
  # shellcheck disable=SC2207
  [ "${#PROJECTS[@]}" -gt 0 ] && PROJECTS=($(printf '%s\n' "${PROJECTS[@]}" | sort -u))
fi

# A standalone script with no pyproject.toml above it would otherwise be skipped in silence,
# which reads like "checked and clean".
if [ "${#ORPHANS[@]}" -gt 0 ]; then
  echo "Changed Python files that belong to no project, NOT checked here:" >&2
  printf '  %s\n' "${ORPHANS[@]}" >&2
  echo >&2
fi

if [ "${#PROJECTS[@]}" -eq 0 ]; then
  echo "No Python project touched between $BASE_REF and the working tree. Nothing to check." >&2
  exit 0
fi
if [ "${#PROJECTS[@]}" -gt "$MAX_PROJECTS" ] && [ "$ALLOW_MANY" -eq 0 ]; then
  echo "${#PROJECTS[@]} projects touched, above the $MAX_PROJECTS cap:" >&2
  printf '  %s\n' "${PROJECTS[@]}" >&2
  echo "Re-run with --all, or narrow with --only <project dir>." >&2
  exit 2
fi

LOGDIR="$(mktemp -d "${TMPDIR:-/tmp}/pre-pr-python.XXXXXX")"
failed=0
checked=0
declare -a BLOCK=()
row() { BLOCK+=("$(printf '  $ %-30s %s' "$1" "$2")"); }

echo "Python pre-PR checks   base $BASE_REF, ${#PROJECTS[@]} project(s)" >&2
echo >&2

for proj in "${PROJECTS[@]}"; do
  echo "$proj" >&2
  tag="$(echo "$proj" | tr '/' '_')"
  proj_failed=0
  cd "$REPO/$proj" || { failed=1; continue; }

  if [ ! -d .venv ] && [ "$ALLOW_SYNC" -eq 0 ]; then
    echo "  no .venv, skipped. Re-run with --allow-sync to let uv build it." >&2
    BLOCK+=("$proj")
    BLOCK+=("  skipped, no .venv (would have to be created from scratch)")
    cd "$REPO" || exit 2
    echo >&2
    continue
  fi

  # The project's own ruff when it declares one, an ephemeral uvx ruff otherwise. Which one
  # ran ends up in the evidence block: a lint count without its linter is not a number.
  if uv run --frozen ruff --version >/dev/null 2>&1; then
    RUFF=(uv run --frozen ruff); ruff_src="uv run ruff"
  else
    RUFF=(uvx ruff); ruff_src="uvx ruff"
  fi

  printf '  %-30s ' "$ruff_src check ." >&2
  "${RUFF[@]}" check . >"$LOGDIR/$tag.ruff.log" 2>&1
  ruff_rc=$?
  ruff_n="$(grep -oE 'Found [0-9]+ error' "$LOGDIR/$tag.ruff.log" | grep -oE '[0-9]+' | head -1)"
  [ -n "$ruff_n" ] || ruff_n=0
  ruff_res="$ruff_n error(s)"
  if [ $ruff_rc -eq 0 ]; then echo "ok" >&2; else echo "FAILED" >&2; proj_failed=1; fi

  printf '  %-30s ' "$ruff_src format --check ." >&2
  "${RUFF[@]}" format --check . >"$LOGDIR/$tag.fmt.log" 2>&1
  fmt_rc=$?
  fmt_n="$(grep -oE '[0-9]+ file[s]? would be reformatted' "$LOGDIR/$tag.fmt.log" | grep -oE '^[0-9]+')"
  [ -n "$fmt_n" ] || fmt_n=0
  fmt_res="$fmt_n file(s) to reformat"
  if [ $fmt_rc -eq 0 ]; then echo "ok" >&2; else echo "FAILED" >&2; proj_failed=1; fi

  test_res="not run"
  if [ "$RUN_TESTS" -eq 1 ]; then
    if [ -z "$(find . -maxdepth 3 -name 'test_*.py' -not -path './.venv/*' -print -quit 2>/dev/null)" ]; then
      test_res="no test file found"
      printf '  %-30s %s\n' "uv run pytest" "skipped, $test_res" >&2
    else
      printf '  %-30s ' "uv run pytest" >&2
      uv run --frozen pytest >"$LOGDIR/$tag.pytest.log" 2>&1
      pytest_rc=$?
      summary="$(grep -oE '[0-9]+ (passed|failed|error|errors|skipped|xfailed)' "$LOGDIR/$tag.pytest.log" | tail -6 | paste -sd, - | sed 's/,/, /g')"
      [ -n "$summary" ] || summary="no summary line, see the log"
      test_res="$summary"
      if [ $pytest_rc -eq 0 ]; then
        echo "ok" >&2
      else
        echo "FAILED" >&2; proj_failed=1
        # addopts often carries --exitfirst, so "1 failed" is where it stopped, not a total.
        if grep -qE '(^|[^-])--exitfirst|(^|[[:space:]])-x([[:space:]]|$)' pyproject.toml 2>/dev/null; then
          test_res="$test_res (--exitfirst: stopped at the first failure, not a total)"
        fi
      fi
    fi
  fi

  checked=$((checked + 1))
  BLOCK+=("$proj")
  row "$ruff_src check ." "$ruff_res"
  row "$ruff_src format --check ." "$fmt_res"
  [ "$RUN_TESTS" -eq 1 ] && row "uv run pytest" "$test_res"
  [ "$proj_failed" -ne 0 ] && failed=1
  cd "$REPO" || exit 2
  echo >&2
done

if [ "$failed" -ne 0 ]; then
  {
    echo "Counts from this run, for the record, NOT a PR block:"
    printf '%s\n' "${BLOCK[@]}"
    echo
    echo "At least one step failed. Logs in $LOGDIR:"
    ls "$LOGDIR" | sed 's/^/  /'
    if grep -qriE 'is not up-to-date|--frozen|No solution found|ModuleNotFoundError' "$LOGDIR" 2>/dev/null; then
      echo
      echo "Some failures look like the environment rather than the diff (stale uv.lock, missing"
      echo "module). Settle that first: a lock to refresh is a commit of its own, not a test result."
    fi
    echo "Fix them, or establish they are pre-existing by stashing the diff and re-running,"
    echo "then re-run this script. Do not announce the PR ready on this run."
  } >&2
  exit 1
fi

# Every project skipped is not a green run, and must not produce a pasteable block.
if [ "$checked" -eq 0 ]; then
  {
    echo "Nothing was verified: every project was skipped."
    printf '%s\n' "${BLOCK[@]}"
    echo "Re-run with --allow-sync if you want uv to build the environment."
  } >&2
  rm -rf "$LOGDIR"
  exit 2
fi

echo "--- evidence block for the PR body ---" >&2
{
  echo '```'
  printf '%s\n' "${BLOCK[@]}"
  echo '```'
}
echo >&2
echo "Green. What the script does not cover and the PR must say:" >&2
echo "  - a change no test exercises goes to staging and is exercised there" >&2
echo "  - a project skipped for want of a .venv is not a project that passed" >&2
if [ "$KEEP_LOGS" -eq 1 ]; then echo "Logs kept in $LOGDIR" >&2; else rm -rf "$LOGDIR"; fi
