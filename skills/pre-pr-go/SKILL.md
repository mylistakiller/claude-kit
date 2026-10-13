---
name: pre-pr-go
description: The validation steps to run on the Go modules of the monorepo before announcing a PR ready, and the evidence each one must leave in the PR body. Covers the scripted per-module run of go build, go test -race, gofumpt and the linter, why make all is the wrong command here, how to tell an environment failure from a real one, and what to do for a change no unit test can exercise. Use before opening or announcing a PR that touches projects/*, go-utils/* or tools/*, when asked to "check the Go service before the PR", "valider le service Go", "run the pre-PR checks", or when a Go change is finished and a PR is the next step.
---

# Go pre-PR checks

## 0. The diff's own tests bite: red-green proof

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/red-green.sh --cwd projects/<service> -- go test ./internal/<pkg>/... -run 'TestX|TestY'
```

It stashes the source files of the diff (test files stay), runs the command (expected red),
restores, runs again (expected green), and prints the block for the PR body. A test that
passes without the change does not exercise it: rewrite the test, not the block. Exit 2 when
the diff has no test file: then write one, or justify `allow-untested` in the PR body. A bug
fix runs this on the reproducing test, built from the ticket's prod sample, committed before
the fix.

## 1. Build, race tests, format and lint: one script

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/pre-pr-go.sh [--only <module dir>]... [--base <ref>] [--short] [--no-lint]
```

With no `--only` it maps every changed `.go`, `go.mod` and `go.sum` file (committed on the
branch, staged, unstaged, untracked) to the module that owns it, and runs per module:

```
go build ./...
go test -race ./...
gofumpt -l .
make lint        (or golangci-lint run when the module has no lint target)
```

It prints on stdout a code block with the real counts, ready to paste. On a red run it prints
nothing on stdout, sends the counts and the log paths to stderr and exits 1.

Above 6 modules it stops and asks for `--all` or a narrower `--only`: a race suite per module
is not something to launch by accident.

## 2. Why not `make all`

`make all` is `tidy format lint test build`, and the first two rewrite `go.mod` and your
sources. A pre-PR check reads. The script checks formatting with `gofumpt -l` and reports the
files, it never applies. Run `make format` yourself if the list is not empty, in its own
commit when it touches code you did not otherwise change.

## 3. Reading a failure before writing about it

`go test -race ./...` pulls the integration tests too, and most services have suites that need
containers (MySQL, ClickHouse) or prod-shaped data. Without Docker they fail on
`[setup failed]` or a refused connection, which is the environment, not the diff. The script
says so when it sees that signature. Three consequences:

- `--short` skips what the suite guards with `testing.Short()`, when that is enough.
- A failure is only "pre-existing" after a stash of the diff and a re-run, never by assertion.
- The PR says which suites did not run and why. "There is no test environment" is not an
  acceptable line: stand up the throwaway infra instead.

The linter is pinned per project (`GOLANGCI_LINT_VERSION` in the Makefile) and `make lint`
installs that version into `GOPATH/bin`, which swaps the shared binary for every other module.
The script warns when it is about to. An issue count that jumps on a module you did not touch
is usually that swap, not rot.

## 4. What no unit test can reach

A change that no unit test exercises goes to staging and is exercised there (staging trigger,
staging namespace), and the PR carries that trail. Cloud Build triggers
are regional:

```bash
gcloud builds triggers run <repo>-<service>-staging --project=<gcp-project> --region=<region>
```

## What lands in the PR body

The block printed by the script, one line on what was not covered and why, and the staging
trail when there is one. A PR body stays at 10 to 15 lines, with no section headers.

The `guard-pr-evidence.py` hook blocks `gh pr create` and `gh pr edit` when the body has no
pasted output, no count in it, a section header, more than 15 lines of prose, no R0-R4 risk
class, or an emoji in the title, and when the diff changes source files without a test file
(unless the body says why and carries `allow-untested`). For a change with genuinely nothing
to run, say so in the body and add `allow-thin-pr`.
