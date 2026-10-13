---
name: prod-run
description: Any write or bulk change against production (UPDATE, DELETE, bulk INSERT, CSV import, sync-mode flip, cutover, backfill, mass push to an external platform, k8s console command). Enforces plan → estimate → confirm → apply → verify with a rollback snapshot. Use before touching prod state, not after.
---

# Production writes

Never skipped, never reordered, however small the change looks.

## 1. Plan

Write the exact statements you intend to run. Show them. No `UPDATE`/`DELETE` whose `WHERE`
the user has not read.

Open the plan with the action card (global CLAUDE.md):

```
ACTION: <verb_object, e.g. update_location_hours>
scope: <tenant / records / platform>
effect: internal_write | external_write
risk: low | medium | high      reversible: yes | no (how)
dry_run: yes | no              approval: <user>
```

`external_write` (a third-party API, a mail, a Slack post) is irreversible from our side: say
how we would undo it on the platform, or that we cannot. A plan without the card is not
announced as ready.

Scope on operational state (record open, page active, `deleted_at IS NULL`), not on
commercial status: one plan filtered on a commercial status column and pulled in 56 closed
records until the reviewer caught it. Name the deliberately disabled rows the filter keeps
out, and any tenant-specific column a copy would carry across.

## 2. Estimate

Run the scoping `SELECT` and report:
- the affected row count,
- 5-20 sample rows as they are **now**,
- what each will become.

Dump the full before-state to a snapshot file **in the session scratchpad**
(`<scratchpad>/<ticket>-before-<date>.json` or CSV; the exact `before` + `.json`/`.csv`
naming matters now, see below), never on `~/Desktop` and never in `memory/data/`. That file
is the rollback, and writing the revert statement next to it costs nothing. Several past
sessions had no way back because there was no snapshot: a results JSON overwritten by a
retry, a CSV import that half-failed on NOT NULL.

Mechanically enforced, same as the falsify pass below: `guard-prod-exec.py`
requires a successful `Write` or `Bash` call in this session and an existing, nonempty
file at an absolute path whose name contains `before` and ends in `.json`/`.csv`. Bash
must declare the output with `>`, `>>`, `--out` or `--output`. A dry-run script can also print `snapshot: <path>` in its successful Bash result. That
file must exist and be nonempty; printed relative paths are resolved only within this
session’s scratchpad. A read call, failed result or empty file does not count.

The same gate covers the other write path: a python script run with `DRY=0`, `--apply` or
`--execute` (the `apply-*.py` pymysql scripts, with full write grants). The dry run without
the flag passes as a read; the go flag needs the same three artefacts plus
`# allow-prod-write`.

Write down the invariants as an `INVARIANTS:` block, each count coming from a query, not
from reading the SQL:

```
INVARIANTS:
expected_rows: <n>
forbidden[reward linked outside the target campaigns]: <count>
forbidden[deleted_at IS NOT NULL in the target set]: <count>
```

(Placeholders, not digits, on purpose: a `cat` of this file must not read as a clean block.)

`expected_rows` is the count the apply must hit; each `forbidden[...]` counts rows the write
would touch and must not. The hook reads the LAST block of the session and blocks on a
missing `expected_rows`, no `forbidden` line, or any nonzero `forbidden`: a nonzero count is
the plan saying it would touch rows it should not.

**Have the dry run print that block itself**, computed by the script (the counts, then the
same assertions the apply makes). Prose written in the same turn, right before the apply
call, is not in the transcript when the hook runs, and sometimes never lands there (one
session: 4 blocks out of 4 missing). A tool result always is, so a block printed by the
dry run unblocks the apply in the same turn, and it is the script's numbers rather than a
copy of them. The hook skips error results, but any other output that quotes the header (a
grep of the hook, a `cat` of this file) becomes the last block and blocks: rerun the dry run.

Two things not to confuse: a **snapshot** is working material and belongs in the scratchpad,
while an **export the user asked for** is a deliverable and goes to `~/Desktop` (see
`prod-query`). And the snapshot is only as durable as the session: never present "the revert
is still replayable" as a lasting guarantee, that promise has been false every time it was
checked. If a revert must outlive the session, say so and put it where the user will find it.

Halt and surface anything that looks off: a count an order of magnitude from expectation,
rows outside the intended tenant, duplicates. Do not push through. That judgement is the
user's.

## 3. Confirm

**Run `/falsify` on the plan first.** A fresh reviewer, never a fork, gets the plan in one
sentence, the estimate query with its counts and the scope, with the mandate to attack the
scope: reversed filter, group vs sub-group, and what the complement of the filter contains. Its
counts go next to the confirmation request. A plan that has not been through that pass is not
announced as ready to apply.

This is no longer just discipline: for any write through `kubectl exec` (the only path to
prod, `mysql-prod`/`clickhouse-prod` MCP are read-only), `guard-prod-exec.py` mechanically
requires the falsify skill's exact output line (`Relecteur: n'a pas pu casser...`) to be
the LAST such line in the session transcript before the `# allow-prod-write` escape hatch
does anything. A missing pass, or a last verdict of `Relecteur: a cassé le verdict.`, blocks
the write regardless of the escape hatch. Added after a `deleted_at` omission (14 wrongly
reactivated rows) and a slug-only matching bug both went out under that keystroke with no
falsify pass behind them.

Same transcript gap as the `INVARIANTS:` block: if the apply follows in the same turn, print
the verdict with a Bash call (`printf 'Relecteur: ...\n'`) as well as writing it for the user.
Only a non-error Bash result whose first non-blank line starts with `Relecteur:` counts; the
reviewer agent's own report and file dumps quoting the line are ignored on purpose (this
file and falsify/SKILL.md list the clean line last, a `cat` would otherwise unblock).

`AskUserQuestion` with the count and the sample in front of it. Wait. "Seems complete" is
not confirmation, and neither is an earlier approval of a different batch.

The user's go means you run it. Never hand the commands back for the user to paste: on one
access copy the user ended up running them by hand after four rejected reviewer rounds.
If the reviewer cap (`guard-falsify-rounds.py`) is what blocks, say so and wait for the go.

## 4. Apply

- Wrap in a transaction; batch at ~500 rows for large sets.
- Make it idempotent: re-running must be a no-op, so an interrupted run is resumable.
- Keep a progress/checkpoint trail for anything over a few hundred rows, so an API error
  never leaves a half-applied state.
- **Apply exactly what was asked.** Do not normalize, reinterpret or silently fix data on
  the way through. Flag the anomaly, don't repair it unasked.
- Platform pushes (hours, closures, media, attributes) are irreversible from our side and
  often replace a whole field: a list field can be replaced *in its entirety*, an index 0 can
  mean Sunday. Confirm the payload shape against a real read first.

## 5. Verify

Re-query and report **before/after counts side by side**, plus the assertion that matters
(the row now reads X; the API returns Y; the widget shows Z). A run without a verification
query is not finished. For platform writes, verify from the platform, not from our DB: a 200
from the API, a published Pub/Sub message or a task in `done` is a step, not the final state.

Async work (backfill, crawl, AI generation, regeneration) is not verified when the session
ends on "still running". Before the turn ends, either schedule the completion check
(`ScheduleWakeup` or `CronCreate` with the exact query and the success threshold), or write a
`reprise-*` memory carrying the job ids, the query, the threshold and the Linear comment to
post once it is met.

When the run comes from a source of truth (client file, ticket list, expected id set),
"verified" means a row-by-row reconciliation of the final state against that source, both
directions, with every discrepancy listed. Counts that match are not that: one import was
announced done on matching counts and still had a missed row and wrong roles.

## Evidence

Prod ops usually can't be staged: staging has neither the data nor the tenants. The evidence
is therefore the trail itself: the dry-run output, the pre-flight COUNT, the post-apply
counts. That trail goes verbatim into the PR body or the Linear comment. Never write
"tested" for something that was not run.
