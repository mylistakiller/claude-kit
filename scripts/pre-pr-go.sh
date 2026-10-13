#!/usr/bin/env bash
# Pre-PR validation for the Go modules touched by the current branch: build, race tests,
# formatting and lint, module by module, ending on an evidence block ready to paste into the
# PR body.
#
# Why a script: counts re-read and re-summarised by hand are how a run
# becomes "tests pass" in a PR body. Here they come out of the real output, and a red run
# prints nothing on stdout and exits 1.
#
# What it does NOT do, on purpose: `make all` runs `tidy` and `format`, which rewrite go.mod
# and your sources. A pre-PR check reads, it does not edit. Formatting is checked with
# `gofumpt -l` and reported, never applied.
#
# Usage:
#   pre-pr-go.sh [--only <module dir>]... [--base <ref>] [--short] [--no-lint] [--all]
#                [--keep-logs]
#
# Env:
#   BASE_REF  base ref for the changed-files diff (default origin/master)
#
# Exit: 0 when every step of every module passes, 1 otherwise, 2 on a setup problem.

set -uo pipefail

BASE_REF="${BASE_REF:-origin/master}"
SHORT=""
RUN_LINT=1
KEEP_LOGS=0
ALLOW_MANY=0
MAX_MODULES=6
declare -a ONLY=()

while [ $# -gt 0 ]; do
  case "$1" in
    --only) ONLY+=("${2:-}"); shift 2 ;;
    --base) BASE_REF="${2:-}"; shift 2 ;;
    --short) SHORT="-short"; shift ;;
    --no-lint) RUN_LINT=0; shift ;;
    --all) ALLOW_MANY=1; shift ;;
    --keep-logs) KEEP_LOGS=1; shift ;;
    -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

REPO="$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "not in a git repo" >&2; exit 2; }
cd "$REPO" || exit 2
command -v go >/dev/null || { echo "go not on PATH" >&2; exit 2; }
git rev-parse --verify "$BASE_REF" >/dev/null 2>&1 || {
  echo "base ref $BASE_REF not found, run git fetch origin or pass --base" >&2; exit 2; }

# Nearest enclosing go.mod, so a changed file maps to the module that owns it.
module_of() {
  local dir; dir="$(dirname "$1")"
  while [ "$dir" != "." ] && [ "$dir" != "/" ]; do
    [ -f "$REPO/$dir/go.mod" ] && { echo "$dir"; return 0; }
    dir="$(dirname "$dir")"
  done
  return 1
}

declare -a MODULES=()
if [ "${#ONLY[@]}" -gt 0 ]; then
  for m in "${ONLY[@]}"; do
    m="${m#./}"; m="${m%/}"
    [ -f "$REPO/$m/go.mod" ] || { echo "no go.mod in $m" >&2; exit 2; }
    MODULES+=("$m")
  done
else
  # Committed on the branch, staged, unstaged and untracked, so the script behaves the same
  # mid-task as it does right before the PR.
  changed="$(
    { git diff --name-only "$BASE_REF...HEAD"
      git diff --name-only HEAD
      git diff --name-only --cached
      git ls-files --others --exclude-standard
    } 2>/dev/null | sort -u
  )"
  while IFS= read -r f; do
    case "$f" in
      .worktrees/*|*/vendor/*|vendor/*) continue ;;
      *.go|*/go.mod|*/go.sum|go.mod|go.sum) ;;
      *) continue ;;
    esac
    mod="$(module_of "$f")" && MODULES+=("$mod")
  done <<< "$changed"
  # shellcheck disable=SC2207
  [ "${#MODULES[@]}" -gt 0 ] && MODULES=($(printf '%s\n' "${MODULES[@]}" | sort -u))
fi

if [ "${#MODULES[@]}" -eq 0 ]; then
  echo "No Go module touched between $BASE_REF and the working tree. Nothing to check." >&2
  exit 0
fi
if [ "${#MODULES[@]}" -gt "$MAX_MODULES" ] && [ "$ALLOW_MANY" -eq 0 ]; then
  echo "${#MODULES[@]} modules touched, above the $MAX_MODULES cap:" >&2
  printf '  %s\n' "${MODULES[@]}" >&2
  echo "Re-run with --all, or narrow with --only <module dir>. A race suite per module is not" >&2
  echo "something to launch by accident." >&2
  exit 2
fi

LOGDIR="$(mktemp -d "${TMPDIR:-/tmp}/pre-pr-go.XXXXXX")"
failed=0
declare -a BLOCK=()

echo "Go pre-PR checks   base $BASE_REF, ${#MODULES[@]} module(s)" >&2
echo >&2

for mod in "${MODULES[@]}"; do
  echo "$mod" >&2
  tag="$(echo "$mod" | tr '/' '_')"
  mod_failed=0
  cd "$REPO/$mod" || { failed=1; continue; }

  printf '  %-22s ' "go build ./..." >&2
  if go build ./... >"$LOGDIR/$tag.build.log" 2>&1; then
    build_res="ok"; echo "ok" >&2
  else
    build_res="FAILED"; echo "FAILED" >&2; mod_failed=1
  fi

  printf '  %-22s ' "go test -race ./..." >&2
  go test -race $SHORT ./... >"$LOGDIR/$tag.test.log" 2>&1
  test_rc=$?
  # `go test` prints both `FAIL\tpkg` and a bare `FAIL` line, so counting on ^FAIL alone
  # doubles the packages. Test cases are counted separately: a package number alone hides
  # whether one assertion broke or the whole suite could not start.
  pkg_ok="$(grep -cE '^ok[[:space:]]' "$LOGDIR/$tag.test.log")"
  pkg_fail="$(grep -cE '^FAIL[[:space:]]+[^[:space:]]+' "$LOGDIR/$tag.test.log")"
  pkg_none="$(grep -c 'no test files' "$LOGDIR/$tag.test.log")"
  case_fail="$(grep -c '^--- FAIL' "$LOGDIR/$tag.test.log")"
  test_res="$pkg_ok package(s) ok, $pkg_fail failed, $pkg_none without tests"
  [ "$case_fail" -gt 0 ] && test_res="$test_res ($case_fail failing test case(s))"
  if [ $test_rc -eq 0 ]; then echo "ok" >&2; else echo "FAILED" >&2; mod_failed=1; fi

  printf '  %-22s ' "gofumpt -l ." >&2
  if command -v gofumpt >/dev/null; then
    gofumpt -l . >"$LOGDIR/$tag.fmt.log" 2>&1
    fmt_n="$(grep -c '\.go$' "$LOGDIR/$tag.fmt.log")"
    fmt_res="$fmt_n file(s) to reformat"
    if [ "$fmt_n" -eq 0 ]; then echo "ok" >&2; else echo "FAILED" >&2; mod_failed=1; fi
  else
    fmt_res="skipped, gofumpt not installed"; echo "skipped" >&2
  fi

  # Lint last: `make lint` reinstalls the pinned golangci-lint into GOPATH/bin, which swaps
  # the shared binary. Better to pay that after the fast feedback, and to say so.
  lint_res="skipped"
  if [ "$RUN_LINT" -eq 1 ]; then
    printf '  %-22s ' "lint" >&2
    if [ -f Makefile ] && grep -qE '^lint:' Makefile; then
      pinned="$(grep -m1 'GOLANGCI_LINT_VERSION *:*=' Makefile | sed 's/.*= *//')"
      installed="$(golangci-lint version 2>/dev/null | grep -oE 'v?[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
      if [ -n "$pinned" ] && [ -n "$installed" ] && [ "${pinned#v}" != "${installed#v}" ]; then
        echo >&2
        printf '  %-22s ' "  (installing $pinned over $installed)" >&2
      fi
      make lint >"$LOGDIR/$tag.lint.log" 2>&1
      lint_rc=$?
      lint_cmd="make lint"
    elif command -v golangci-lint >/dev/null; then
      golangci-lint run --timeout 5m >"$LOGDIR/$tag.lint.log" 2>&1
      lint_rc=$?
      lint_cmd="golangci-lint run"
    else
      lint_rc=0; lint_cmd=""; echo "skipped, no target and no binary" >&2
    fi
    if [ -n "$lint_cmd" ]; then
      issues="$(grep -cE '\.go:[0-9]+:[0-9]+:' "$LOGDIR/$tag.lint.log")"
      lint_res="$issues issue(s)"
      if [ $lint_rc -eq 0 ]; then echo "ok" >&2; else echo "FAILED" >&2; mod_failed=1; fi
    fi
  fi

  row() { BLOCK+=("$(printf '  $ %-22s %s' "$1" "$2")"); }
  BLOCK+=("$mod")
  row "go build ./..." "$build_res"
  row "go test -race ./..." "$test_res"
  row "gofumpt -l ." "$fmt_res"
  [ "$RUN_LINT" -eq 1 ] && row "${lint_cmd:-lint}" "$lint_res"
  [ "$mod_failed" -ne 0 ] && failed=1
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
    if grep -qriE 'setup failed|connection refused|cannot connect to the docker|no required module provides' "$LOGDIR" 2>/dev/null; then
      echo
      echo "Some failures look like the environment rather than the diff (missing container,"
      echo "refused connection, missing module). Establish which before writing anything about"
      echo "them in the PR, and never label a failure pre-existing without a stash and a re-run."
    fi
    echo "Fix them, or establish they are pre-existing by stashing the diff and re-running,"
    echo "then re-run this script. Do not announce the PR ready on this run."
  } >&2
  exit 1
fi

echo "--- evidence block for the PR body ---" >&2
{
  echo '```'
  printf '%s\n' "${BLOCK[@]}"
  echo '```'
}
echo >&2
echo "Green. What the script does not cover and the PR must say:" >&2
echo "  - a change that no unit test exercises goes to staging and is exercised there" >&2
echo "  - integration tests needing containers or prod-shaped data, if they were skipped" >&2
if [ "$KEEP_LOGS" -eq 1 ]; then echo "Logs kept in $LOGDIR" >&2; else rm -rf "$LOGDIR"; fi
