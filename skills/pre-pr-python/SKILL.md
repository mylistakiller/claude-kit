---
name: pre-pr-python
description: The validation steps to run on the Python projects of the monorepo before announcing a PR ready, and the evidence each one must leave in the PR body. Covers the scripted per-project run of ruff check, ruff format --check and pytest, why uv run --frozen, why a project with no .venv is skipped rather than built, and how to read a pytest count under --exitfirst. Use before opening or announcing a PR that touches Python, when asked to "check the Python service before the PR", "valider le projet Python", "run the pre-PR checks", or when a Python change is finished and a PR is the next step.
---

# Python pre-PR checks

## 0. The diff's own tests bite: red-green proof

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/red-green.sh --cwd projects/<project> -- uv run --frozen pytest tests/test_<module>.py
```

It stashes the source files of the diff (test files stay), runs the command (expected red),
restores, runs again (expected green), and prints the block for the PR body. A test that
passes without the change does not exercise it: rewrite the test, not the block. Exit 2 when
the diff has no test file: then write one, or justify `allow-untested` in the PR body.

## 1. Lint, format and tests: one script

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/pre-pr-python.sh [--only <project dir>]... [--base <ref>] [--allow-sync]
```

With no `--only` it maps every changed `.py`, `pyproject.toml`, `uv.lock` and
`requirements.txt` to the project that owns it (the nearest `pyproject.toml`), and runs per
project:

```
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen pytest
```

Stdout carries the evidence block only when everything passed. A red run prints nothing there,
sends the counts and the log paths to stderr and exits 1.

## 2. What the flags protect

`--frozen` keeps uv from re-resolving and rewriting `uv.lock`. A check that edits the repo is
not a check, and a stale lock deserves its own commit rather than a silent refresh inside a
test run.

A project with no `.venv` is skipped unless `--allow-sync`: building one downloads the whole
dependency tree (a project with ML dependencies can weigh 1 GB). When every project of a run was skipped the
script exits 2 and prints no block, because nothing was verified.

A changed `.py` file with no `pyproject.toml` above it (a standalone script under `tools/`)
belongs to no project. The script lists those files and says they were not checked, rather
than passing over them in silence.

## 3. Reading the counts

`ruff format --check` counts files, not lines: 19 files to reformat on a branch that changed
no Python is the state of master, not something the branch broke. Prove it that way (no Python
in the diff) or by a stash and a re-run, never by assertion.

When a project sets `--exitfirst` in its pytest `addopts`, a failing run stops at the first
failure and "1 failed" is where it stopped, not a total. The script says so on a red run.

The project's own ruff is used when it declares one, an ephemeral `uvx ruff` otherwise, and
which one ran appears in the block. A lint count without its linter is not a number.

## What lands in the PR body

The block printed by the script, one line on what was not covered and why, and the staging
trail when a change no test can exercise went there. A PR body stays at 10 to 15 lines, with
no section headers.

The `guard-pr-evidence.py` hook blocks `gh pr create` and `gh pr edit` when the body has no
pasted output, no count in it, a section header, more than 15 lines of prose, no R0-R4 risk
class, or an emoji in the title, and when the diff changes source files without a test file
(unless the body says why and carries `allow-untested`). For a change with genuinely nothing
to run, say so in the body and add `allow-thin-pr`.
