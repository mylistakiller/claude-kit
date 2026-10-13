# claude-kit

A personal Claude Code plugin (`jv`) for working against live production systems: queries, backfills,
imports and investigations, where being fast matters less than being verified and auditable.

Skills hold the procedures, hooks enforce the hard rules (and say why when they block), and
`CLAUDE.md.example` holds the standing instructions a plugin cannot load by itself. Most skill
text is in French.

## Install

    git clone https://github.com/mylistakiller/claude-kit ~/.claude/skills/jv
    cp ~/.claude/skills/jv/CLAUDE.md.example ~/.claude/CLAUDE.md   # then adapt it
    claude plugin validate ~/.claude/skills/jv

The skills answer to `/prod-query` as well as `/jv:prod-query`, as long as no other
skill has the same name.

## Contents

**Skills**

| Skill | What it does |
|---|---|
| `prod-query` | Read-only prod queries: preflight, schema check, filtered estimate before heavy scans |
| `prod-run` | Prod writes: plan, estimate, confirm, apply, verify, with a rollback snapshot |
| `investigate` | Hypothesis ledger: two competing hypotheses and a falsifying query before any verdict |
| `falsify` | Adversarial second pass on a verdict or a write plan, by a fresh reviewer |
| `git-workflow` | The question set to ask before commit, push, PR, merge, tag; branch naming |
| `pre-pr-go`, `pre-pr-python` | Validation steps and the evidence a PR body must carry |
| `memory-review` | One-by-one review of the Claude Code memory store |
| `linear-card`, `linear-complete` | When a verified problem deserves a card; fill missing priority and labels |
| `writing-unslop` | Rewrite text so it reads like a colleague, not a generated summary |
| `doc-review` | Review the substance of a document against twelve criteria |

**Hooks** (`hooks/hooks.json`): SQL and exec guards on prod, no em/en dash in published text,
no AI attribution in commits and PRs, PR evidence (a test or a stated reason), verdict without
evidence, investigation discipline, unnamed session, agent effort, prod preflight at session
start, read-only Bash auto-approval, skill usage log.

**Scripts**: `red-green.sh` (runs the diff's tests without then with the source change),
`pre-pr-*.sh`, `name-session.sh`, `memory-review.py`, `check-skills.py`,
`skill-usage-report.py`, `session-retro.py`, `upstream-watch.py`.

## Configuration

Hooks that depend on your infrastructure read environment variables. Defaults are neutral.

| Variable | Default | Used by |
|---|---|---|
| `MONOREPO` | `$HOME/src/monorepo` | hooks gated to one repo, `check-skills.py` |
| `PROD_MYSQL_PORT`, `PROD_CLICKHOUSE_PORT` | `3306`, `8123` (empty skips) | `prod-preflight.sh`, `prod-access-failure-hint.py` |
| `PROD_MYSQL_TUNNEL_HINT`, `PROD_CLICKHOUSE_TUNNEL_HINT` | generic text | `prod-preflight.sh` |
| `PROD_EXEC_ALLOWED_SCRIPTS` | empty | `guard-prod-exec.py` |
| `PROD_EXEC_NONPROD_TARGETS` | `staging(?:[-.].*)?\|.*-dev\|kind-.*\|minikube` | `guard-prod-exec.py` |
| `PROD_SQL_LARGE_TABLES` | `audit_logs,events,logs` | `guard-prod-sql.py` |
| `LINEAR_ME_ALIASES`, `LINEAR_ME_ID` | empty | `guard-linear-issue.py`, `linear-incomplete-hint.py` |
| `INVESTIGATE_TICKET_PREFIXES` | `ENG\|OPS\|BUG` | `investigate-guard.py` |
| `CLAUDE_MEMORY_DIR` | `~/.claude/projects/<slug>/memory` | `memory-review.py` |
| `MEMORY_TICKET_PREFIXES` | any `KEY-123` | `memory-review.py` |
| `KIT_HEADLESS` | unset | `session-name-guard.py` |

The MCP matchers assume servers named `mysql-prod` and `clickhouse-prod`; rename them in
`hooks/hooks.json` if yours differ.

## Checks after a change

    claude plugin validate .
    python3 scripts/check-skills.py --repo "$MONOREPO"
    for t in hooks/tests/*.py scripts/tests/*.py; do python3 "$t" || echo "FAIL $t"; done

## License

MIT, see `LICENSE`.
