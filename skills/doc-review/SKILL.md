---
name: doc-review
description: Review a documentation page (README, CLAUDE.md, runbook, debugging note, spec, HTML doc) for substance before it is published, against twelve criteria drawn from a reference set of service docs. Checks thesis, rule-shaped headings, reasons and expiry conditions on every choice, failure modes, misleading symptoms, reader split, fixed vocabulary, end-to-end path, sentence-shaped tables, named anti-patterns, doc-in-the-PR and agent-executable rules. Reports a per-criterion verdict with quoted lines and the edits to make; never rewrites. Use before publishing or merging any doc, when the user asks "relis cette doc", "passe la doc en revue", "doc-review", "est-ce que cette doc tient la route", or at the end of a task that produced a doc. For phrasing and slop, use /writing-unslop; the two run one after the other.
allowed-tools: Read, Bash, Grep, Glob
argument-hint: "<file path or doc text> [--against <code dir>]"
---

# Doc review: substance, not phrasing

`/writing-unslop` makes a text read smooth. This skill asks whether the doc is worth reading:
does it carry a thesis, the reasons behind each choice, the ways things fail, and the traps a
reader will fall into. Pick a reference set in your own repo (a README, a service
`CLAUDE.md` and a runbook that you consider good) and read one of them again when a criterion
below feels abstract; the example column below shows the kind of line each criterion asks for.

The skill produces a review. It never edits the doc: the user decides what to apply, then
`/writing-unslop` runs on the result.

## Input

- A file path (`.md`, `.html`): read it whole. An HTML doc is read for its text and headings.
- Pasted text: review it as is.
- `--against <dir>`: the code the doc describes. Used to verify that paths, symbols, flags,
  commands and numbers quoted in the doc exist (criterion 11, drift).
- No argument: the docs touched in the current git diff (`git diff --name-only` plus staged,
  filtered on `.md`/`.html`). None found: ask for a path.

## The twelve criteria

Each criterion gets one of three verdicts: **met** (quote the line that meets it), **missing**
(say where it would go and draft the sentence in one line), **not applicable** (say why in five
words, e.g. "incident note, no settings"). A verdict without a quoted line is not a verdict.

| # | Criterion | What to look for | Example of a line that meets it |
|---|---|---|---|
| 1 | **Thesis in one sentence** | The first paragraph states, in one sentence someone can repeat in a meeting, what the thing is and what it is not. | "roots in, typed documents out, no result database inside" |
| 2 | **Headings and rules are sentences with a verb** | Section titles and bold lead-ins assert something ("The cron never retries a win past 24 h"), not name a topic ("Cron window"). A reader who reads only the bold lines knows the rules. | "The dead-letter queue is forensic, never a source of work"; "Concurrency is a window, not a partition count" |
| 3 | **Every number and every choice carries its reason and its expiry** | Pattern "X because Y; revisit when Z". A number has its date. A deliberate absence (no staging, no retry, no index) says why and what would change the decision. | a retention per topic with the volume that justifies it; "no staging, on purpose, until ..."; "427k against 33k in September 2026" |
| 4 | **Failure modes are first-class content** | Each setting, step and command says what happens when it is wrong or fails, under the exact name the failure takes (error code, metric label, log line, status). A table of settings has a failure case in its Meaning column. | "a missing or invalid point fails the whole root before any unit leaves"; a list of named failure labels (`fetch_blocked`, `codec`, `schema_drift`); a "What a handler must respect" section |
| 5 | **Misleading symptoms are named** | At least one "looks like X, is Y, check here" where the doc has operational scope. A Gotchas or Pièges section at the end gives them a home. | "answers Go's default TLS handshake with a 429 ... which reads exactly like an IP throttle"; "a VM where docker ps says the daemon is not running is a VM whose boot script stopped at the disk step" |
| 6 | **One doc per reader, each pointing to the others** | The doc says in its first lines who it is for and what it does not cover, and points to the sibling doc instead of repeating it. Invariants (for who modifies), paths and subsystems (for who uses), operations (for who is on call). A paragraph copied from a sibling is a defect. | "The architecture's invariants are in CLAUDE.md; the sections below describe each subsystem"; "see the runbook's Operations section, Dead letters" |
| 7 | **Fixed vocabulary** | One word per concept, defined at first use, never a synonym afterwards. List the concepts the doc uses and flag every pair of words used for the same thing (fiche / établissement / location; run / exécution / job). | root, run, unit, kind, chain, ledger, watermark, each used in one sense throughout |
| 8 | **An end-to-end path before the first concept** | A copy-pasteable sequence that goes from nothing to a visible result, each command with a comment saying what appears on screen, placed before the detailed sections. | a "Run one job end to end" block near the top |
| 9 | **Tables carry sentences** | In a table, the meaning column holds a sentence with its edge cases, never a label. Three parallel items or more go in a table, two stay in prose. | settings rows such as "never 0,0", "odd, 3 to 15"; a runbook table "What changed / Dispatch with / Then" |
| 10 | **Anti-patterns are named with the temptation** | The doc says what not to do, why someone would be tempted, and what breaks: "never a replace => ../ : the image build copies only the service's go.mod". A bare "do not" without the mechanism is half a criterion. | "Never encode meaning into the id to make it readable", followed by what breaks |
| 11 | **The doc is true to the code it describes** | With `--against`, every path, symbol, flag, command, table or column quoted exists in the tree; every number has the query or script that produced it next to it or linked. Without `--against`, list what would need checking. A doc that ships in a PR other than the one that changed the behaviour is flagged. | a "fix doc drift" commit of its own is the symptom this criterion prevents |
| 12 | **Rules an agent can execute** | Where the doc governs code, it ends with (or is) a block of imperative rules with file paths and symbol names, the way a CLAUDE.md reads: "Return handler.Permanent(err) for a 404; never loop pages inside one unit". A debugging note gains a "for whoever touches this code" block. | a "Core principles (do not violate)" block at the top of a CLAUDE.md |

Weigh them by doc type. A debugging or incident note is judged first on 3, 4, 5, 11 and 12; a
README or service guide on 1, 2, 6, 7, 8; a runbook on 4, 5, 8, 9, 10; a spec on 1, 3, 6, 10.
Say which profile was applied.

## Meeting the criteria is not enough

A doc can meet all twelve and still have to be read whole rather than scanned: 40-line
paragraphs, a single CLAUDE.md bullet running 60 lines. Meeting the twelve criteria does not
excuse that. When a paragraph exceeds about twelve lines or
chains more than three clauses on semicolons, flag it under criterion 9 (it is a table or a list
that was written as prose) or under `/writing-unslop`.

## Procedure

1. Read the whole doc. Name its type and its intended reader in one line; if the doc does not
   say, that is already a criterion 6 miss.
2. Fill the twelve verdicts with their quoted lines. For a **missing** verdict, draft the
   sentence or the table row that would meet it, using the doc's own facts; never invent a
   reason or a number. When the fact is unknown, write the question the user has to answer
   ("why 24 h and not 48 h?").
3. With `--against`, run the drift check: `grep -rn` each quoted path, symbol and flag in the
   tree; list the ones that do not resolve with the doc line that quotes them.
4. Count the vocabulary doubles (criterion 7) and the paragraphs over the length bound.
5. Write the report.

## Output format

```
Doc : <path>  Type : <readme | service guide | runbook | debugging note | spec | html doc>
Reader : <who the doc says it is for, or "not stated">
Profile : <criteria weighted first for this type>

| # | Criterion | Verdict | Evidence or draft |
| 1 | Thesis | met | "..." (l.3) |
| 2 | Rule-shaped headings | missing | l.12 "Root cause" → "GRPC_HOST pointed at a recycled pod IP" |
...

Drift (criterion 11) : <n> references checked, <m> unresolved : <list with lines>
Vocabulary doubles : <pairs with lines>, or none
Paragraphs over the bound : <lines>, or none

Three edits with the most effect :
1. ...
2. ...
3. ...

Then : /writing-unslop <path>
```

Score nothing. Twelve verdicts and three ranked edits are the whole output. A report that
says "good doc overall" without a quoted line per criterion is a failed review.

## When the doc is one the agent wrote in this session

Run the review before announcing the doc as done, without being asked, and apply the three
ranked edits yourself when they need no fact the user alone holds. Then re-run the review once: a
second pass that still misses a criterion is reported, not hidden. A doc announced as finished
without a review in the transcript is not finished.
