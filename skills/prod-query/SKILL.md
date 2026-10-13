---
name: prod-query
description: Read-only querying of production data (prod MySQL, prod ClickHouse, GCloud logs). Covers the preflight, schema verification, filtered-estimate gating, which client to use, and how to present results. Use before any prod SELECT, export, count or data lookup.
---

# Querying prod

## Step zero: access

The SessionStart preflight already printed tunnel + gcloud status. If the item you need is
DOWN or EXPIRED, **stop and ask the user to open it**. Do not explore the codebase instead, do
not improvise another path, do not stall silently. Two sessions died this way (missing
sentry-cli + token, missing Linear access) and one was blocked until the tunnel was opened
by hand.

Re-check on the spot if the preflight is stale:
`lsof -nP -iTCP:<mysql-tunnel-port> -sTCP:LISTEN` (MySQL) · `-iTCP:<clickhouse-tunnel-port>` (ClickHouse) · `gcloud auth print-access-token`

## Which client

1. `mcp__mysql-prod__mysql_query`: read-only user, multi-DB.
2. `mcp__clickhouse-prod__run_query`: use `FINAL` on ReplacingMergeTree.
3. Bash + `uv run --with pymysql`: only for hosts or users the MCP does not cover, **or**
   when the MCP returns an error. Say so when you fall back; the mysql-prod MCP has broken
   before with a parse error on every query including `SELECT 1`.

Bash is the fallback, not the default. Reach for the MCP tool first.

## Schema before SQL

Never guess a column or table name. `DESCRIBE <table>` or an `information_schema.columns`
lookup first when unsure. Guessed columns and guessed ORM/GraphQL shapes have caused failed
queries and a full revert. The same rule applies to external API shapes: introspect, fetch a
real sample, or say you can't.

## Gating

Every query carries a `WHERE` or a `LIMIT`. Before a full or cross-tenant read, run the
narrow `COUNT` first and report the estimate. If a query hangs with no output, kill it and
rewrite tighter, never wait it out. The `guard-prod-sql.py` PreToolUse hook blocks
unfiltered reads mechanically; the escape hatch is a `-- allow-full-scan` comment, and it is
for genuinely small lookup tables only, not for getting past the guard.

Keep the list of your known-huge tables (event logs, task tables, crawl or import outputs,
media tables with no date index) in the project `CLAUDE.md`, and check it before any read.

## Presenting results

- **Values asked for → printed inline in the chat as a plain list.** URLs, ids, emails.
  Do not write a CSV, do not build a table, unless a file was explicitly asked for. Writing
  9 URLs into a CSV instead of listing them forced a re-ask.
- A file was asked for → **CSV goes to ~/Desktop**, never the scratchpad.
- **An export file built for someone else (xlsx, CSV, Google Sheet) starts with its method**,
  written after the schema checks and counts but before the data query: the source tables,
  the join keys, the filters (open, deleted, status, sub-groups), the geographic or date
  bounds, and one line per column saying what it holds. Show it, wait for the user's OK, then
  build. The file carries the same text: a `méthode` tab in an xlsx or a Sheet, a
  `<name>-methode.txt` next to a CSV. One export was rebuilt because an id was recomputed
  from another column instead of the join the requester used. Not concerned: values
  answered inline (the rule above), and recurring exports whose method is already written in
  the skill that runs them.
- Quote the actual rows for anything feeding a verdict. Paste output, don't paraphrase it.
- State the grain and the window of what you counted (per-event vs per-record, UTC vs
  local time, `FINAL` or not). A number without its grain has already been misread.

## Gotchas

Each one has produced a wrong answer at least once. Record the detail and the evidence of
your own in a memory, and name it in brackets next to the gotcha.

- **A table in one schema can be a VIEW over a table in another**: same rows, never write to
  both. Check `information_schema.TABLES.TABLE_TYPE` before treating two same-named tables as
  a mirror. Count on the detail table, never on its aggregate (~1 row per parent).
- **Task status codes are not guessable.** Read what each value means in the code before
  filtering on it, and which timestamp columns are filled on which path (an `end_at` filled on
  successes only, a `start_at` that is the insert time rather than an execution time).
- **A media or history table is a journal, not the current state**: check whether anything
  still sets `deleted_at` before reading it as live data.
- **ClickHouse `FINAL` is not reproducible when duplicates share the version.** If the version
  column ties (duplicates share `updated_at`), `FINAL` and a plain `argMax(col, updated_at)`
  both pick arbitrarily.
- **Replaying a query logged by a service**: a service that runs with `final=1` does not show
  it in the logged SQL, and a read-only MCP may refuse it as a setting. Add `FINAL` on each
  ReplacingMergeTree table by hand.
- **A mysql MCP can shift every DATETIME by the local offset**: the server is UTC
  (`@@system_time_zone = UTC`), but the MCP driver reads the naive value as local time and
  prints it with a `Z`, so it shows -2 h in summer and -1 h in winter on a machine set to Central European Time.
  Checked once: `NOW()` gave `06:12:44Z`, `CAST(NOW() AS CHAR)` gave `08:12:44`, and the
  machine clock read 08:12:47 UTC. Wrap times in `CAST(col AS CHAR)` before comparing with
  logs, ClickHouse (correct, `Etc/UTC`) or an event time.
- **mysql-prod MCP parser**: `DESCRIBE schema.table`, index hints and sometimes any query fail
  with `Parsing failed`. Use `information_schema.columns`, else pymysql.
- **pymysql re-read after a write**: `autocommit=False` + REPEATABLE READ freeze the snapshot,
  so the same connection still shows the old state. Verify from a new connection.
