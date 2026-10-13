---
name: git-workflow
description: The exact QCM option sets to present before any git operation (commit, push, PR, merge, tag, release), per branch context (master, Linear-linked branch, regular feature branch), plus the branch-naming follow-up question and its naming patterns. Use whenever you are about to commit, push or open a PR, mid-task included, or when you need the branch-detection rules or branch-name conventions. The rule that no git operation runs without the user's explicit choice lives in the monorepo CLAUDE.md (section Git) and applies whether or not this skill is loaded.
---

# Git workflow QCM templates

The rule (QCM before every commit, push, PR, merge, tag or release) lives in the monorepo
`CLAUDE.md`, section Git. This file carries the option lists. Run the checks first, then present
the Step 3 QCM and, when a branch is created, the Step 3b naming question. Commit only the files
you changed and name those left out.

## Step 1 — Branch context detection

1. **Check current branch**: `git branch --show-current`
2. **Detect Linear task linkage**: branch name patterns `feature/abc-XXXX-*`, `fix/xyz-XXXX-*`,
   …, i.e. regex `^(feature|fix|hotfix)/[a-z]+-\d+`. If detected, the user is
   likely working on that specific Linear task.
3. **Determine branch type**:
   - **master**: NEVER commit directly, only propose new-branch options.
   - **Linear-linked branch**: changes likely belong to this task — prioritize current-branch options.
   - **Regular feature branch**: all options available, neutral ordering.

## Step 2 — The workflow options

**Option 1: New Branch → Commit → Push → Create PR** — create a branch with a descriptive name,
commit, push, `gh pr create`. Use when: currently on master (required in that case), starting
fresh work separate from the current branch, or isolating changes for review.

**Option 2: Commit on Current Branch → Push → Create PR** — commit on the current feature branch,
push, `gh pr create`. Use when: already on a feature branch (NOT master), ready for review, and
the changes belong to that branch.

**Option 3: Commit Locally Only** — commit, do NOT push. Use when: mid-development and saving
progress, not ready to share, or continuing work on the feature.

**Option 4: "Instant Merge" → Complete Flow** — create branch (if on master), commit, push,
`gh pr create`, `gh pr merge`; **only if code changes**, then a new semver tag + GitHub release.
Use when: hotfix, urgent bug fix, or a quick iteration needing immediate deployment.

Tag/release scope is in the root `CLAUDE.md` (section Git, "Tag + release only for runtime code") — code changes yes,
shell/docs/config/IaC no.

## Step 3 — Presenting the QCM

Use the `AskUserQuestion` tool. **EVERY QCM must include a "Do nothing" option as the last
choice**, so the user can cancel or reconsider.

**When on master**, only options that create a new branch:

```
Question: "How would you like to proceed with these changes?"
Options:
1. "New branch → PR" - Create branch, commit, push, open PR (leave open for review)
2. "Instant merge" - Same as option 1 + auto-merge PR immediately (no review needed)
3. "Do nothing" - Stop here, don't perform any git operations
```

**When on a Linear-linked branch** (e.g. `feature/abc-1234-*`), prioritize the current branch:

```
Question: "How would you like to proceed with these changes?"
Options:
1. "Commit on current → PR" - Commit and push on current Linear task branch, then open PR
2. "Commit locally only" - Just commit changes without pushing (continue working on this task)
3. "Instant merge" - Full automated flow on current branch: commit, push, PR, merge
4. "New branch → PR" - Create NEW branch (separate from Linear task), commit, push, and open PR
5. "Do nothing" - Stop here, don't perform any git operations
```

**When on a regular feature branch**, all options, neutral ordering:

```
Question: "How would you like to proceed with these changes?"
Options:
1. "New branch → PR" - Create new branch, commit, push, and open PR
2. "Commit on current → PR" - Commit and push on current branch, then open PR
3. "Commit locally only" - Just commit changes without pushing (continue working)
4. "Instant merge" - Full automated flow: commit, push, PR, merge
5. "Do nothing" - Stop here, don't perform any git operations
```

## Step 3b — Branch naming (only if creating a new branch)

When the user picks an option that creates a branch (1 or 4), present a second QCM:

```
Question: "Do you want to specify a custom branch name?"
Options:
1. "No, let Claude generate" - Claude will generate a descriptive branch name based on changes (DEFAULT)
2. "Yes, custom name" - Enter a custom branch name (opens text input)
```

- **Custom name**: validate it (no spaces, valid git branch name) and use it as given.
- **Generated** (default): follow `feature/component-brief-description`,
  `fix/component-issue-description`, or `hotfix/critical-issue`. Examples:
  `billing/fix-sync-state`, `webhook/add-validation`, `infra/update-terraform`.

## Tools

`gh pr create` (title + body), `gh pr merge` (`--squash` / `--merge` / `--rebase`),
`gh release create` (tag + notes), and standard `git`. Commit message format, the no-AI-credit
and no-emoji rules, and force-push safety are in the root `CLAUDE.md`.

## PR body

`guard-pr-evidence.py` bloque un `gh pr create` dont le body n'a ni sortie collée ni compte.

- 10 à 15 lignes, pas de titres de section, pas de reformulation du diff : une ligne de
  contexte, la liste des changements, la sortie brute des tests dans un bloc de code, une ligne
  sur ce qui n'est pas couvert, une ligne sur le déploiement quand il est manuel, une ligne
  de risque au merge.
- Risque au merge : réversible (un revert suffit) ou à sens unique (migration de schéma,
  écriture ou purge de données, changement de contrat d'API, de route d'API gateway ou de topic
  Pub/Sub), puis le rayon d'impact en quelques mots (quel service, quels clients). Une PR à
  sens unique dit comment on revient en arrière, ou qu'on ne peut pas.
- Classe de risque sur la même ligne, `R0` à `R4` (CLAUDE.md global) : R0 copy, docs,
  tests ; R1 UI locale sans API ni schéma ; R2 petite feature isolée ; R3 logique métier ou API ; R4 auth, permissions, facturation, migration, purge de
  données, action externe. R0 à R2 : coller la sortie de `/code-review` pour que le dev puisse
  survoler. R3 et R4 : le plan de migration ou de retour arrière et le relecteur humain nommé.
  Une PR porte une seule classe : une PR qui mélange R0 et R3 se découpe.
- La preuve est collée, pas décrite : commandes exactes et compteurs pass/fail (pas « tests
  pass »). Pour le front, Go et Python, coller le bloc imprimé par le script `pre-pr-*`, et
  le bloc `red-green.sh` qui montre les tests du diff rouges sans le changement, verts avec.
  Un diff qui touche du code sans fichier de test est bloqué par le hook : écrire le test, ou
  dire pourquoi il ne peut pas exister avec `allow-untested`.
- Pour une opération de données prod : comptes avant/après et requête de vérification.
- Ce qui n'a pas été couvert et pourquoi (pas de données en staging, API externe non
  mockable). Si la validation est impossible, l'écrire. Jamais « testé » pour ce qui n'a pas tourné.
- Mêmes règles de style que les réponses (CLAUDE.md global, section Writing).
