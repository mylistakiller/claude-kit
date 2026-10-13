#!/usr/bin/env python3
"""PreToolUse hook for the prod MySQL / ClickHouse MCP tools.

Enforces the CLAUDE.md "gate heavy queries" rule mechanically instead of relying on
Claude to remember it: a query that reads a table with neither a WHERE nor a LIMIT is
blocked before it can scan prod. Writes are blocked outright (both servers are meant to
be read-only; this catches a misconfigured server too).

Exit codes: 0 = allow, 2 = block (stderr is fed back to Claude as the reason).
Fails open on anything unexpected: a broken guard must not wedge the session.

Escape hatch: put `allow-full-scan` in a SQL comment to bypass the scan check, e.g.
    SELECT COUNT(*) FROM small_lookup_table -- allow-full-scan

Configuration (environment variable, optional):
  PROD_SQL_LARGE_TABLES  comma-separated table names named in the block message as
                         known-large (default: audit_logs,events,logs)
"""

import json
import os
import re
import sys

# Param name differs per server: mysql-prod takes `sql`, clickhouse-prod takes `query`.
PARAM_BY_TOOL = {
    "mcp__mysql-prod__mysql_query": "sql",
    "mcp__clickhouse-prod__run_query": "query",
}

WRITE_KEYWORDS = (
    "insert",
    "update",
    "delete",
    "replace",
    "truncate",
    "drop",
    "alter",
    "create",
    "rename",
    "grant",
    "revoke",
    "optimize",
    "attach",
    "detach",
)

# Metadata / tiny system tables are always cheap to scan.
CHEAP_SCHEMAS = ("information_schema", "system", "mysql.help", "performance_schema")

# Tables known to be large enough that an unfiltered read hangs the session.
HUGE_TABLES = tuple(
    t.strip().lower()
    for t in (os.environ.get("PROD_SQL_LARGE_TABLES") or "audit_logs,events,logs").split(",")
    if t.strip()
)


SQL_TOKEN = re.compile(
    r"/\*.*?\*/|--(?=\s|$)[^\n]*|\#[^\n]*|'(?:\\.|''|[^'\\])*'|"
    r'"(?:\\.|""|[^"\\])*"|`(?:``|[^`])*`|[A-Za-z_][\w]*|[^\s]',
    re.S,
)


def strip_comments(sql: str) -> str:
    # Literals cannot establish a WHERE/LIMIT or conceal a second statement.
    tokens = []
    for match in SQL_TOKEN.finditer(sql):
        token = match.group(0)
        if token.startswith(("/*", "--", "#")):
            continue
        tokens.append("?" if token.startswith(("'", '"', "`")) else token)
    return " ".join(tokens)


def block(reason: str) -> None:
    sys.stderr.write(reason)
    sys.exit(2)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    tool = payload.get("tool_name") or ""
    param = PARAM_BY_TOOL.get(tool)
    if not param:
        sys.exit(0)

    raw = (payload.get("tool_input") or {}).get(param) or ""
    if not raw.strip():
        sys.exit(0)

    bypass = "allow-full-scan" in raw.lower()
    sql = strip_comments(raw)
    low = sql.lower()

    # MySQL executable comments can contain statements even though they look like comments.
    if "/*!" in raw:
        block("BLOCKED: executable SQL comments are not supported by the read-only guard.\n")
    statements = [part.strip() for part in low.split(";") if part.strip()]
    if len(statements) != 1:
        block("BLOCKED: send exactly one read-only SQL statement per tool call.\n")
    low = statements[0]
    first = low.split(" ", 1)[0]
    words = set(re.findall(r"\b[a-z_]+\b", low))
    kind = first
    if first in ("with", "explain"):
        depth = 0
        for token in low.split():
            if token == "(":
                depth += 1
            elif token == ")":
                depth -= 1
            elif depth == 0 and token in ("select", *WRITE_KEYWORDS):
                kind = token
                break
    if kind in WRITE_KEYWORDS or words.intersection({"outfile", "dumpfile"}):
        block(f"BLOCKED: writes are not allowed through {tool}; use /prod-run.\n")
    if first not in ("select", "with", "show", "describe", "desc", "explain"):
        block(f"BLOCKED: unsupported SQL statement `{first}` on the read-only server.\n")

    if first in ("show", "describe", "desc", "explain"):
        sys.exit(0)
    if not re.search(r"\bfrom\b|\bjoin\b", low):
        sys.exit(0)
    if any(s in low for s in CHEAP_SCHEMAS):
        sys.exit(0)

    if bypass:
        sys.exit(0)

    has_where = bool(re.search(r"\bwhere\b", low))
    has_limit = bool(re.search(r"\blimit\b", low))
    if has_where or has_limit:
        sys.exit(0)

    hit = next((t for t in HUGE_TABLES if re.search(rf"\b{re.escape(t)}\b", low)), None)
    target = f"`{hit}` (known-large table)" if hit else "a prod table"

    block(
        f"BLOCKED: this query reads {target} with neither a WHERE clause nor a LIMIT: an "
        "unfiltered full scan on prod.\nPer CLAUDE.md: add a tenant/account/date filter and a "
        "LIMIT, and run a filtered pre-flight COUNT estimate first.\nIf the scan really is "
        "cheap and intended, re-send it with `-- allow-full-scan` appended.\n"
    )


if __name__ == "__main__":
    main()
