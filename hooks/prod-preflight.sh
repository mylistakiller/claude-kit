#!/usr/bin/env bash
# SessionStart hook: report prod access readiness up front, so a missing tunnel or an
# expired gcloud token is visible before any query is attempted rather than ten minutes in.
#
# Never blocks longer than the grace window below; always exits 0. Stdout becomes session
# context for Claude. Only runs for the monorepo, other projects need no prod tunnels.
#
# Configuration (environment variables, all optional):
#   MONOREPO                     project dir the hook runs for (default: $HOME/src/monorepo)
#   PROD_MYSQL_PORT              local port of the MySQL prod tunnel (default: 3306, empty = skip)
#   PROD_CLICKHOUSE_PORT         local port of the ClickHouse prod tunnel (default: 8123, empty = skip)
#   PROD_MYSQL_TUNNEL_HINT       how to open the MySQL tunnel, printed when it is down
#   PROD_CLICKHOUSE_TUNNEL_HINT  how to open the ClickHouse tunnel, printed when it is down
#   PROD_PREFLIGHT_GRACE         seconds to wait for the tunnels (default: 45)
#
# 2026-09-14: the report is a SNAPSHOT, not a standing fact. The user routinely opens the tunnels
# in the seconds after launching the session (measured that day: session 08:58:12, cloud-sql
# LISTEN 08:58:41, IAP tunnel LISTEN 08:58:48), so a DOWN line went stale within the minute
# and was still believed 20 minutes later. Hence the grace window, the timestamp, and --check.

set -u

# --check re-runs the probes on demand: no stdin payload, no cwd gate, no waiting. This is
# what Claude must run before telling the user anything is missing.
recheck_mode=0
[ "${1:-}" = "--check" ] && recheck_mode=1

if [ "$recheck_mode" -eq 0 ]; then
  payload="$(cat 2>/dev/null || true)"
  cwd="$(printf '%s' "$payload" | python3 -c '
import json,sys
try: print((json.load(sys.stdin).get("cwd") or ""))
except Exception: print("")
' 2>/dev/null)"
  [ -n "$cwd" ] || cwd="${CLAUDE_PROJECT_DIR:-$PWD}"

  monorepo="${MONOREPO:-$HOME/src/monorepo}"
  case "$cwd" in
    "$monorepo"*) ;;
    *) exit 0 ;;
  esac
fi

mysql_port="${PROD_MYSQL_PORT-3306}"
ch_port="${PROD_CLICKHOUSE_PORT-8123}"
mysql_hint="${PROD_MYSQL_TUNNEL_HINT:-open the MySQL prod tunnel}"
ch_hint="${PROD_CLICKHOUSE_TUNNEL_HINT:-open the ClickHouse prod tunnel}"
self_path="$(cd "$(dirname "$0")" 2>/dev/null && pwd)/$(basename "$0")"

# Seconds to keep re-probing the tunnels before calling them down, with an early exit the
# instant both answer. 45 covers the +36 s worst case measured on 2026-09-14. The full cost
# is only paid when the tunnels never come up at all. PROD_PREFLIGHT_GRACE overrides it;
# whatever you set here, keep the hook's "timeout" in settings.json comfortably above it,
# or the hook gets killed and prints NOTHING, which is worse than a stale line.
grace="${PROD_PREFLIGHT_GRACE:-45}"
case "$grace" in ''|*[!0-9]*) grace=45 ;; esac
[ "$recheck_mode" -eq 1 ] && grace=0

start_ts="$(date +%s)"
deadline=$(( start_ts + grace ))

# A real loopback connect, not an lsof listing. Two reasons: it needs no external binary
# (lsof lives in /usr/sbin, which is not always on a hook's PATH, and a missing lsof reported
# every tunnel as DOWN), and it proves the socket actually accepts, which is what a query needs.
port_listening() {
  (exec 3<>/dev/tcp/127.0.0.1/"$1") 2>/dev/null || return 1
  exec 3<&- 3>&-
  return 0
}

# print-access-token fails (or refreshes) when the credential is stale; that is the signal.
# gcloud_ok: 1 valid, 2 not installed, 3 credential REALLY rejected, 0 inconclusive.
#
# 2026-09-17: 3 and 0 used to be the same state, both printed as "EXPIRED, ask the user to run
# gcloud auth login". The user only needs that login once a morning (the org enforces a reauth
# policy: the day's first gcloud call raises ReauthRequiredError('reauth is required.'), and
# a login at ~08:55 covers the rest of the day), so any probe that merely failed to answer
# spent the rest of the day sending me back to ask for a login that was already done. Only
# gcloud SAYING the credential is refused earns that ask; anything else is "unknown, retry".
gcloud_ok=0
gcloud_detail=""
probe_gcloud() {
  if ! command -v gcloud >/dev/null 2>&1; then
    gcloud_ok=2
    return
  fi
  # Floor, not the tunnel grace window: this is one credential call that can legitimately go
  # to the network to refresh. --check computes a budget of 0 and a 1 s cut-off made a valid
  # token a coin flip (measured 1.2 s for the whole hook on the happy path). The floor stays
  # small on purpose: first probe (<= 10 s) + grace (45 s) + retry (6 s) must stay under the
  # hook timeout in settings.json (55 s), or the hook is killed and prints NOTHING.
  budget="$1"
  [ "$budget" -lt 6 ] && budget=6

  # Same lesson as lsof above: no external binary. /usr/local/bin/timeout is an x86_64
  # coreutils build left over from the Intel machine, so on this arm64 Mac every call exits
  # 127 ("bad CPU type in executable") and the probe reported a perfectly valid token as
  # EXPIRED, on every single session. The watchdog below is bash-only.
  errfile="$(mktemp -t prodpreflight 2>/dev/null)" || errfile=""
  if [ -n "$errfile" ]; then
    gcloud auth print-access-token >/dev/null 2>"$errfile" &
  else
    gcloud auth print-access-token >/dev/null 2>&1 &
  fi
  probe_pid=$!
  waited_probe=0
  while kill -0 "$probe_pid" 2>/dev/null; do
    if [ "$waited_probe" -ge "$budget" ]; then
      kill "$probe_pid" 2>/dev/null
      wait "$probe_pid" 2>/dev/null
      gcloud_ok=0
      gcloud_detail="probe killed after ${budget}s"
      [ -n "$errfile" ] && rm -f "$errfile"
      return
    fi
    sleep 1
    waited_probe=$(( waited_probe + 1 ))
  done

  if wait "$probe_pid"; then
    gcloud_ok=1
    [ -n "$errfile" ] && rm -f "$errfile"
    return
  fi

  # Non-zero exit is not enough to send the user to a login. gcloud says so explicitly when the
  # credential is refused ("reauth is required", "invalid_grant", "Please run $ gcloud auth
  # login"); everything else here is a local or network failure that a login would not fix.
  stderr_text=""
  [ -n "$errfile" ] && stderr_text="$(tr '\n' ' ' < "$errfile" 2>/dev/null)"
  [ -n "$errfile" ] && rm -f "$errfile"

  case "$stderr_text" in
    *reauth*|*Reauth*|*invalid_grant*|*"gcloud auth login"*|*"credentials are invalid"*|*"do not have valid credentials"*|*"Reauthentication"*)
      gcloud_ok=3
      ;;
    *)
      gcloud_ok=0
      gcloud_detail="${stderr_text:-no stderr captured}"
      ;;
  esac
}

# An empty port disables that probe (counted as up, reported as skipped).
mysql_ok=0; { [ -z "$mysql_port" ] || port_listening "$mysql_port"; } && mysql_ok=1
ch_ok=0;    { [ -z "$ch_port" ] || port_listening "$ch_port"; } && ch_ok=1

gcloud_budget=$(( deadline - $(date +%s) ))
[ "$gcloud_budget" -gt 10 ] && gcloud_budget=10
probe_gcloud "$gcloud_budget"

# Grace window on the TUNNELS only. An expired token needs `gcloud auth login`, which takes
# far longer than this window, so waiting on it would just tax every session for nothing.
# It does get one free retry below if the tunnels made us wait anyway.
while [ "$mysql_ok" -eq 0 ] || [ "$ch_ok" -eq 0 ]; do
  [ "$(date +%s)" -ge "$deadline" ] && break
  sleep 1
  [ "$mysql_ok" -eq 1 ] || { port_listening "$mysql_port" && mysql_ok=1; }
  [ "$ch_ok" -eq 1 ]    || { port_listening "$ch_port" && ch_ok=1; }
done

waited=$(( $(date +%s) - start_ts ))

if [ "$gcloud_ok" -eq 0 ] && [ "$waited" -ge 5 ]; then
  probe_gcloud 5
fi

mysql_status="DOWN: ${mysql_hint} (127.0.0.1:${mysql_port})"
[ "$mysql_ok" -eq 1 ] && mysql_status="up"
[ -z "$mysql_port" ] && mysql_status="skipped (PROD_MYSQL_PORT empty)"

ch_status="DOWN: ${ch_hint} (127.0.0.1:${ch_port})"
[ "$ch_ok" -eq 1 ] && ch_status="up"
[ -z "$ch_port" ] && ch_status="skipped (PROD_CLICKHOUSE_PORT empty)"

case "$gcloud_ok" in
  1) gcloud_status="valid" ;;
  2) gcloud_status="gcloud not on PATH" ;;
  3) gcloud_status="REFUSED by gcloud: the user needs gcloud auth login (once a day covers it)" ;;
  *)
    detail="$(printf '%s' "$gcloud_detail" | cut -c1-120)"
    gcloud_status="UNKNOWN, probe did not answer (${detail}); retry --check, do NOT ask the user to re-login"
    ;;
esac

stamp="$(date '+%H:%M:%S')"
if [ "$waited" -ge 2 ]; then
  header="Prod access preflight (snapshot at ${stamp}, after waiting ${waited}s for the tunnels):"
else
  header="Prod access preflight (snapshot at ${stamp}, goes stale within seconds):"
fi

echo "$header"
echo "  mysql-prod tunnel  (${mysql_port:-n/a}): ${mysql_status}"
echo "  clickhouse tunnel  (${ch_port:-n/a}): ${ch_status}"
echo "  gcloud auth        : ${gcloud_status}"

if [ "$recheck_mode" -eq 0 ]; then
  # Personal skills can be symlinks into a working copy; a deleted or moved
  # target leaves a dangling link that silently drops the skill from the session with no
  # error anywhere. That went unnoticed for weeks (investigate/prod-query/prod-run).
  broken=""
  for link in "$HOME"/.claude/skills/*; do
    [ -L "$link" ] || continue
    [ -e "$link" ] || broken="${broken} $(basename "$link")"
  done

  if [ -n "$broken" ]; then
    echo
    echo "BROKEN SKILL SYMLINKS (target missing, skill NOT loaded):${broken}"
    echo "Tell the user before relying on those skills: the discipline they encode is absent."
  fi
fi

case "${mysql_status}${ch_status}${gcloud_status}" in
  *DOWN*|*REFUSED*|*UNKNOWN*|*"not on PATH"*)
    if [ "$recheck_mode" -eq 0 ]; then
      echo
      echo "NOT ready AS OF ${stamp} ONLY, after a ${grace}s grace window. That window can still"
      echo "miss a tunnel opened late, so before telling the user anything is missing, or abandoning a"
      echo "prod task, re-run the probes yourself:"
      echo "  ${self_path} --check"
      echo "Only if THAT still fails, stop and ask the user to set it up, do not work around it by"
      echo "exploring the codebase. Non-prod work can proceed normally."
    fi
    ;;
esac

exit 0
