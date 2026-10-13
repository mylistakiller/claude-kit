#!/usr/bin/env python3
"""PostToolUse hook: turn a prod access failure into its known diagnosis, immediately.

The SessionStart preflight is a snapshot. The failures that actually break an investigation
happen later: the tunnel dies, the day's gcloud token expires, the MySQL MCP server wedges
("reading 'trim'" on every query). Each has a known cause and a known fix, and each has
cost sessions that
guessed instead: retrying the same query, blaming the SQL, or tunnelling around the problem
through the codebase, which CLAUDE.md forbids ("stop and ask me to set it up").

So when a prod tool result carries one of those signatures, this hook re-runs the preflight
probes (prod-preflight.sh --check), names the failure, and gives the one action that fixes it.
Exit 2 feeds stderr back to Claude as feedback on the tool call; the call itself already ran.
Exit 0 on anything else, including every parse failure: a hook that breaks is worse than none.

Configuration (environment variables, all optional, shared with prod-preflight.sh):
  PROD_MYSQL_PORT              local port of the MySQL prod tunnel (default: 3306)
  PROD_CLICKHOUSE_PORT         local port of the ClickHouse prod tunnel (default: 8123)
  PROD_MYSQL_TUNNEL_HINT       how to open the MySQL tunnel
  PROD_CLICKHOUSE_TUNNEL_HINT  how to open the ClickHouse tunnel
"""

import json
import os
import re
import subprocess
import sys

PROD_TOOLS = ("mcp__mysql-prod__mysql_query", "mcp__clickhouse-prod__run_query")
MYSQL_PORT = os.environ.get("PROD_MYSQL_PORT", "3306")
CH_PORT = os.environ.get("PROD_CLICKHOUSE_PORT", "8123")
MYSQL_HINT = os.environ.get("PROD_MYSQL_TUNNEL_HINT") or "open the MySQL prod tunnel"
CH_HINT = os.environ.get("PROD_CLICKHOUSE_TUNNEL_HINT") or "open the ClickHouse prod tunnel"
# Bash only counts when the command visibly targets prod access; otherwise an unrelated
# "Connection refused" (a local dev server, a curl) would send the user to open tunnels for nothing.
_PORTS = "|".join(rf"\b{re.escape(p)}\b" for p in (MYSQL_PORT, CH_PORT) if p)
BASH_PROD_HINT = re.compile(
    (_PORTS + "|" if _PORTS else "")
    + r"cloud-sql|cloud_sql|clickhouse|pymysql|gcloud|kubectl|mysql\b",
    re.I,
)
SELF = re.compile(r"prod-preflight\.sh|prod-access-failure-hint", re.I)

SIGNATURES = [
    (
        "wedged",
        re.compile(r"Cannot read properties of undefined \(reading 'trim'\)"),
        "the mysql-prod MCP server process is wedged (this error on every query, tunnel and "
        "gcloud fine). A query retry changes nothing. Fix: the user runs /mcp and reconnects "
        "mysql-prod; until then the sanctioned fallback is a read-only pymysql script "
        f"through the tunnel on 127.0.0.1:{MYSQL_PORT}, credentials from your secret store.",
    ),
    (
        "gcloud",
        re.compile(
            r"reauth is required|Reauthentication|ReauthRequiredError|invalid_grant|"
            r"gcloud auth login|do not have valid credentials|credentials are invalid|"
            r"RefreshError|Token has been expired or revoked",
            re.I,
        ),
        "the gcloud credential is refused (typically an org reauth policy, once a day). Nothing "
        "on this side fixes it: stop and ask the user to run `gcloud auth login`, then retry the "
        "same call. No workaround through the codebase.",
    ),
    (
        "tunnel",
        re.compile(
            r"Failed to connect|ECONNREFUSED|Can't connect to MySQL server|"
            r"Connection refused|Max retries exceeded|Lost connection to MySQL server|"
            r"Connection reset by peer|getaddrinfo ENOTFOUND|Network is unreachable",
            re.I,
        ),
        "a prod tunnel is not accepting connections. It is never the SQL: do not rewrite "
        "the query, do not retry it. Stop and ask the user to open the tunnel (mysql-prod: "
        f"{MYSQL_HINT} on {MYSQL_PORT}; ClickHouse: {CH_HINT} on {CH_PORT}), then retry.",
    ),
    (
        "ch-auth",
        re.compile(r"Authentication failed|AUTHENTICATION_FAILED|Code: 516", re.I),
        "ClickHouse refused the credentials. The wrapper decrypts the password with KMS at "
        "launch, so this usually means gcloud was not valid when the MCP server started: "
        "ask the user to check `gcloud auth` and reconnect clickhouse-prod via /mcp.",
    ),
]


def flatten(obj) -> str:
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return str(obj)


def live_state() -> str:
    script = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "prod-preflight.sh"
    )
    try:
        out = subprocess.run(
            ["bash", script, "--check"], capture_output=True, text=True, timeout=12
        )
        return (out.stdout or "").strip() or "(preflight printed nothing)"
    except Exception as exc:  # the hint is still worth giving without the live state
        return f"(preflight unavailable: {exc})"


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    tool = payload.get("tool_name") or ""
    ti = payload.get("tool_input") or {}
    if tool == "Bash":
        cmd = flatten(ti.get("command"))
        if not BASH_PROD_HINT.search(cmd) or SELF.search(cmd):
            sys.exit(0)
    elif tool not in PROD_TOOLS:
        sys.exit(0)

    text = flatten(payload.get("tool_response"))
    if not text:
        sys.exit(0)

    for name, rx, hint in SIGNATURES:
        m = rx.search(text)
        if not m:
            continue
        sys.stderr.write(
            f"PROD ACCESS FAILURE ({name}) on {tool}: matched `{m.group(0)}`.\n"
            f"Diagnosis: {hint}\n"
            f"Live state right now:\n{live_state()}\n"
        )
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
