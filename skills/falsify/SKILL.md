---
name: falsify
description: Adversarial second pass on a conclusion before the user sees it. Send a draft verdict, root cause, or prod-run plan to a fresh reviewer whose only mandate is to break it, and report what the reviewer found. Use right before stating a root cause or a "not a bug" conclusion, before announcing a prod-run plan as ready to apply, and whenever a count or a claim is about to go into a Linear card or a comment. Not for code review, use /code-review for that.
---

# Falsify before you conclude

The point of this skill is that the falsification work happens before the user sees the answer,
not after they push back. Pushback overturned the first verdict on four past investigations.
Every one of those was a verdict that felt solid when it was
written.

## The rule

**A fresh reviewer, never a fork.** A fork of the current session inherits the reasoning that
produced the verdict and will agree with it. Spawn a general-purpose agent and hand it only:

- the verdict, in one sentence,
- the evidence lines it rests on, quoted verbatim (query output, log line, `file.go:123`),
- the exact queries that produced them,
- the scope: group, sub-group, platform, date window, table.

Nothing else. Not the narrative, not the hypotheses that were discarded, not the ticket
history. The reviewer has to find its own path or fail honestly.

**At most two rounds, never a loop.** Each reviewer gets one pass. If it breaks the verdict, the
investigation reopens with what it found. If it does not, its failed attempts become part of
the answer. The agent's `description` starts with `Falsify` (the round guard counts on it).

**After a break, fix the class, not the row.** The reviewer names one contradicting row; the
same flaw usually hides in others. Before any new review, write the query that finds every row
of that failure class (same comparison, same grain, same timezone or date rule), run it, and
fix the plan for all of them. On 2026-10-08 one sheet plan went through 7 reviewers that each
found one more row of the same "date earlier than the group's creation" class.

**The second pass reviews the delta.** Hand it the previous break, the class query with its
output, and the change, not the whole plan again. Two reviewers in a row without a pass is the
cap: `guard-falsify-rounds.py` blocks the third until the user says go, in a message or an
answer to an AskUserQuestion. A third break means the plan is wrong in its approach, which is
the user's call: show both breaks and the failure-class query, then ask.

**Read-only.** The reviewer queries prod through the same MCP servers and follows the same
gating (filtered estimate first, no unfiltered scan). It never writes.

## The reviewer's mandate

Give it this, verbatim:

> Your mandate is to break this conclusion, not to confirm it. Three ways to succeed, in
> order of value:
> 1. Find one prod sample that contradicts it. One row is enough.
> 2. Show that the quoted evidence line does not actually support the conclusion: wrong grain,
>    wrong table, an event log read as current state, a timezone offset, an aggregate that
>    double-counts, a filter that was reversed.
> 3. Produce a second cause that fits the same evidence just as well.
>
> If you cannot do any of the three, say so explicitly and give the two falsifying queries you
> ran, with their output. "I agree" without those two queries is a failed review.
>
> Budget: at most 12 queries. Stop at the first contradiction that holds and report it, with
> the query that finds every row of the same class. Answer in at most 30 lines: the first line
> is `Relecteur: a cassé le verdict.` or `Relecteur: n'a pas pu casser, 2 requêtes
> falsifiantes.`, then the queries and their output, nothing else.

(A reviewer averaged 21 API calls and 1.4 $ on 2026-10-08, 45 reviewers, 10 % of the day.)

## What comes back to the user

One line, before the verdict, never omitted:

- `Relecteur: a cassé le verdict.` plus what it found. The investigation reopens, there is no
  verdict to give yet.
- `Relecteur: n'a pas pu casser, 2 requêtes falsifiantes.` plus the two queries and their
  output, in the evidence block.

Do not paraphrase the reviewer. If it produced no query, say that the review was empty rather
than presenting it as a validation.

## The prod-run variant

Same mechanism on a plan, before apply. What the reviewer gets: the plan in one sentence, the
estimate query and its counts, and the scope. Its mandate becomes:

> Attack the scope of this plan. Is the filter reversed (is X kept or removed)? Is it the group
> or the sub-group? Does the row count match the stated intent, and what does the complement of
> the filter contain? Name the rows that would be touched and should not be.
> Check the four scoping traps by query, not by reading the SQL: soft-deleted rows
> (`deleted_at`, `is_active`, archived states) inside the target set; matching done on one
> name field only (`name` without `brand_name` / `short_name`, or the reverse); rows the
> source file lists that the target set misses; another session or cron writing the same
> table meanwhile.

An apply is announced as ready only after that pass, and the reviewer's counts go in the plan
next to the user's confirmation request.

This is mechanically enforced, not just prose: `guard-prod-exec.py` (PreToolUse on Bash)
scans the session transcript for the exact line under "What comes back to the user" and requires
the LAST one to read `Relecteur: n'a pas pu casser...` before it lets a `kubectl exec` write
through, even with `# allow-prod-write` appended. Write those two lines verbatim, every
time — a paraphrase or a summary of the verdict does not match and blocks the apply.

## When to skip it

A lookup is not a verdict. Skip this for "give me the ids", a count that is the answer itself
and not an argument, and anything the user already knows the cause of and asked to fix. Use it when a
sentence of the form "the cause is X" or "this is working as intended" is about to be written.
