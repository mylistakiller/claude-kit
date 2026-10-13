#!/usr/bin/env bash
# Nomme la session Claude Code courante (equivalent de /title, que le modele ne peut pas taper).
# Ecrit une entree {"type":"custom-title"} dans le transcript : c'est la forme que lit le
# selecteur /resume et la liste des sessions.
# Usage : name-session.sh "fix-login-timeout" [session-id]
set -euo pipefail

name=${1:?usage: name-session.sh "<nom>" [session-id]}
sid=${2:-${CLAUDE_CODE_SESSION_ID:-}}
[ -n "$sid" ] || { echo "pas de session id (CLAUDE_CODE_SESSION_ID vide)" >&2; exit 1; }

proj=$(pwd | sed 's/[/.]/-/g')
t="$HOME/.claude/projects/$proj/$sid.jsonl"
# Le cwd du shell peut avoir bouge (cd, worktree) : on retombe sur une recherche par id.
[ -f "$t" ] || t=$(ls "$HOME"/.claude/projects/*/"$sid".jsonl 2>/dev/null | head -1)
[ -n "$t" ] && [ -f "$t" ] || { echo "transcript introuvable pour la session $sid" >&2; exit 1; }

python3 - "$t" "$sid" "$name" <<'PY'
import json,sys,os
t,sid,name=sys.argv[1:4]
line=json.dumps({"type":"custom-title","customTitle":name,"sessionId":sid},ensure_ascii=False)+"\n"
fd=os.open(t,os.O_WRONLY|os.O_APPEND)
try: os.write(fd,line.encode())
finally: os.close(fd)
PY
echo "session nommee : $name"
