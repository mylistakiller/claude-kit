---
name: investigate
description: Hypothesis-ledger discipline for "why did X happen" investigations against prod (support or Linear tickets, broken jobs, missing data, wrong values). Forces competing hypotheses and a falsifying query per hypothesis BEFORE any verdict. Use whenever the task is diagnostic — "pourquoi", "why did", "qui a fait ça", "d'où vient", a ticket id with no fix specified — or when about to state a root cause.
---

# Investigation discipline

The failure mode this exists to prevent: a clean-sounding root cause delivered before the
evidence supports it, which the user then has to overturn. It has happened repeatedly:
blaming the nightly cron when the real cause was the client's own "Delete" button (the cron
only replayed it); "no bug, just campaign settings" when the real cause was a device
fingerprint id collision falsely blocking users for 10 days; a mechanism stated backwards.
Each time the correction came from the user, not from the evidence. That work is what this skill moves upstream.

## The ledger

Keep it visible in the reply, updated as you go. Never collapse it into prose until the end.

```
H1  <hypothesis>          falsifier: <the query/log that would kill it>   status: open|killed|survived
H2  <hypothesis>          falsifier: ...                                  status: ...
```

Rules:

1. **Two hypotheses minimum before the first query.** One hypothesis is a conclusion
   wearing a disguise. If you can only think of one, the second is "the data was always
   like this / nothing broke" — check it, it is right more often than it feels.
2. **Write the falsifier before running it.** For each hypothesis: what result would prove
   it *wrong*? Run that. A query chosen to confirm a hypothesis will confirm it.
3. **A hypothesis is only "survived", never "proven".** Say what would still overturn it.
4. **No verdict without a quoted evidence line** — actual query output, an actual log line,
   an actual `file.go:123`. Not "the logs show" — the log line.
5. Anything not yet backed by output is labelled `HYPOTHESIS — not verified`. Explicitly.
6. **Before concluding "not a bug" / "working as intended", go find one contradicting
   sample in prod.** That check is what the "campaign settings" verdict was missing.

## Order of operations

Data first, code last. Standing rule: GCloud logs, ClickHouse, prod MySQL — then
the codebase only when the data is inconclusive. Do not open exploration agents as the
opening move on a diagnostic question a single query answers.

### Falsify the hypotheses in parallel, not in series

Running the falsifiers one after another is how a first plausible hypothesis wins: the query
that "works" ends the search, and H2 never gets tested. The "campaign settings" verdict died
that way: the settings explained enough of the symptom that nobody went looking for the
fingerprint collision. Serial order is itself a bias.

So once the ledger has two or more hypotheses **with their falsifiers written down**, dispatch
one agent per hypothesis, in a single message so they run concurrently. Each agent's mission
is adversarial: **kill its own hypothesis**, not support it.

```
Agent per hypothesis, prompt shape:
  Hypothesis: <H_n, stated as a mechanism>
  Your job is to KILL it. Run <the falsifier>, plus any query that would show the
  mechanism cannot have produced the symptom.
  Return: verdict killed|survived, the evidence lines (query + real output, log line,
  file.ext:123), and what would still overturn a "survived".
  Do not return raw dumps, do not propose a fix, do not write anything.
```

Then reconcile yourself: two "killed" means the ledger was wrong and needs new hypotheses;
two "survived" means neither falsifier bit and they were probably too weak — sharpen them
before believing either. Same fan-out when the answer genuinely needs three sources at once
(MySQL rows + ClickHouse events + the code path). Raw query dumps must not land in the main
context in any case.

This is worth it because it costs one wall-clock round instead of two or three, so there is
no incentive left to skip H2.

## Traps that have already cost a session

- **History / audit tables record EVENTS, not current state.** `*_history`,
  `access_status` history, task tables — read them as a timeline. Misreading one as a
  status snapshot broke one audit. Current state comes from the live row.
- **A cron that replays a bad state did not create it.** Find the *originating* write —
  the API call, the user action, the front button — before naming a scheduled job.
  Analytics events and application logs that carry the acting user id attribute writes to
  humans.
- **Absence from a log is not evidence of absence.** A row can be written after the fact
  and be undatable (an `email_sent_at` filled after the fact). Say "cannot be dated from logs"
  rather than inferring a negative.
- **MCP timestamps can be shifted by the local UTC offset** (see `prod-query`). Convert
  before comparing to a client's report.
- **Aggregates and paths double-count** (hierarchical category paths). Check the grain of
  a table before summing it.
- **A low count in an aggregate table ≠ no underlying data** (aggregate vs detail table).

## Why this discipline exists

Four cases where the first verdict was wrong and the user's pushback overturned it: the
cron, the campaign settings, the backwards mechanism, and the audit above. The lesson is not "be careful", it is that the
falsification work has to happen before the answer is given, not after it is challenged.

- **"No bug, just campaign settings" was wrong.** The real cause was a visitorId collision.
  Hence the rule: before concluding "not a bug" or "working as intended", find one
  contradicting prod sample first.
- **One ticket kept "5 to 20 pairs a day" for six months** with its method lost. Two rebuilt
  detection keys later returned 3 pairs where the original had found sixty, so nothing could be
  concluded either way, in either direction. Hence: a number never travels without the query
  that produced it, and a measurement that will be repeated goes into a script whose queries
  are readable (`~/.claude/scripts/probe-*.py`), not into prose.

## Scope, before anything runs

Restate an ambiguous filter and get it confirmed — "filter X from Y" is ambiguous about
whether X is kept or removed, and a reversed filter has sent whole sessions the wrong way.
Same for group vs sub-group, platform, table suffix. One line, then proceed.

## Closing

**Before the verdict block, run `/falsify`.** A fresh reviewer, never a fork, gets the verdict,
its quoted evidence and the queries, with the mandate to break it. Its outcome is stated in one
line above the verdict, and its two falsifying queries go in the evidence block. A verdict that
has not been through that pass is not ready to be given.

## Sizing the population, before the verdict

The ticket gives one sample. The verdict is about everyone the cause touched, so before
writing it:

- run the count: rows, companies, groups, platforms, with first and last occurrence
  (`MIN`/`MAX` on the event timestamp), and keep the query next to the number;
- pick a second sample that the ticket did not name and check that the mechanism explains
  it too. A mechanism that explains one row and not the second is not the cause yet;
- when the population cannot be counted (no event table, logs expired), say so and give the
  count you could get and over which window.

`verdict-evidence-guard.py` (Stop hook) blocks a verdict that has evidence but no line
carrying `Impact :` (or `périmètre`, `population`, `affected`) with a number in it. For a
"not a bug" verdict the line reads `Impact : 0 contre-exemple parmi <n> vérifiés`.

## Closing

The verdict block has fixed fields, in this order:

```
Ce qui s'est passé : <mechanism, one sentence>
Depuis : <first occurrence, from the timeline query>
Impact : <n> rows / <n> companies / <n> groups, platforms <...>, last occurrence <date>
Écriture d'origine : <API call, user action, cron, with its evidence line>
Pourquoi rien ne l'a vu : <missing test, alert, invariant, monitoring>
Non vérifié : <what is still a hypothesis, labelled>
Correctif : <what, through prod-run or a PR>
Prévention : <the test, alert or docs/debugging entry that comes out of it>
```

"Pourquoi rien ne l'a vu" is not a remark: it becomes a test, an alert or a `docs/debugging/`
entry in the same ticket, and the Linear comment says which. Remediation goes through
`prod-run` — never fold a write into the investigation reply. Posting to Linear is a
git-class action: draft it, let the user send it.

The investigation closes after the fix ships: re-run the falsifier and the population count,
and report the before/after counts. Zero, or every remaining row explained, closes it. A
deploy does not.
