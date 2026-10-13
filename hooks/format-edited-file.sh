#!/usr/bin/env bash
# PostToolUse (Edit|Write|MultiEdit) hook: format ONLY the file that was just edited.
# Deliberately lightweight: no repo-wide `go build ./...` (too slow in this monorepo).
# Never blocks: always exits 0, all tools optional, silent when there is nothing to do.

set -u

# The hook receives the tool call as JSON on stdin. Pull out the edited file path.
payload="$(cat)"
file="$(printf '%s' "$payload" | python3 -c '
import json,sys
try:
    d=json.load(sys.stdin)
except Exception:
    sys.exit(0)
ti=d.get("tool_input") or {}
p=ti.get("file_path") or ti.get("path") or ""
print(p)
' 2>/dev/null)"

[ -n "$file" ] || exit 0
[ -f "$file" ] || exit 0

GOFUMPT="$HOME/go/bin/gofumpt"

case "$file" in
  *.go)
    if [ -x "$GOFUMPT" ]; then "$GOFUMPT" -w "$file" >/dev/null 2>&1
    elif command -v gofmt >/dev/null 2>&1; then gofmt -w "$file" >/dev/null 2>&1
    fi
    ;;
  *.py)
    if command -v ruff >/dev/null 2>&1; then ruff format "$file" >/dev/null 2>&1
    elif command -v uvx >/dev/null 2>&1; then uvx ruff format "$file" >/dev/null 2>&1
    fi
    ;;
  *.ts|*.tsx|*.js|*.jsx|*.mjs|*.cjs|*.json|*.css|*.scss|*.md|*.yaml|*.yml)
    # --no-install: only format if the project already has prettier; never auto-download.
    if command -v npx >/dev/null 2>&1; then npx --no-install prettier --write "$file" >/dev/null 2>&1; fi
    ;;
esac

exit 0
