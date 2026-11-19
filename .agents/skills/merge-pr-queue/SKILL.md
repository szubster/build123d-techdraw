---
name: merge-pr-queue
description: Merge several open PRs into main one at a time — rebase each onto the latest main, regenerate golden SVGs, run all checks, push, squash auto-merge, wait, repeat. Use when asked to "submit/merge/land all PRs", merge a PR queue or stack, or when PRs conflict with main after other PRs merged.
---

# Merge a queue of PRs into main

`main` is protected by a ruleset: only **squash or rebase** merges, linear history, and the
required checks `lint`, `type-check` (mypy + pyright), `test (3.11)`, `test (3.12)`,
`test (3.13)` must pass on a branch that is **up to date with main** (strict). So PRs cannot be
merged in parallel: each one must be rebased after the previous one lands. The `examples` job is
not required but fails on golden-SVG drift, so keep it green too.

Rebasing and force-pushing PR branches is fine here (the ruleset only protects `main`), but only
do it when the user asked to land the PRs.

## Steps

0. Plan the order. Independent PRs first; a stacked PR goes right after its base. PRs that change
   the same example sheet will need their SVGs regenerated after each other — that's expected.
   `export PATH=$HOME/.local/bin:$PATH` if `uv` isn't on PATH.
1. Prepare the PR branch (rebase + all checks + golden regeneration):
   ```bash
   .agents/skills/merge-pr-queue/scripts/prep.sh <branch>                 # normal PR
   .agents/skills/merge-pr-queue/scripts/prep.sh <branch> <old-base-sha>  # stacked PR
   ```
   For a stacked PR, `<old-base-sha>` is the commit of the base PR it was built on (now
   squash-merged under a different SHA): `git log --oneline origin/<branch>` shows it. The script
   uses `rebase --onto origin/main <old-base-sha>` so only the PR's own commits remain.
2. If the rebase stops on conflicts:
   - Generated SVGs only (`examples/output/*.svg`, `docs/assets/examples/*.svg`): take main's
     side and regenerate, never hand-merge them:
     ```bash
     U=$(git diff --name-only --diff-filter=U); git checkout --ours -- $U; git add $U
     uv run python examples/generate_all.py
     git checkout -- 'examples/output/*.pdf' 'examples/output/*.dxf'
     git add examples/output/*.svg docs/assets/examples/*.svg
     GIT_EDITOR=true git rebase --continue
     ```
     (During a rebase `--ours` is the new base, i.e. main.)
   - Docs where both sides appended a section: keep both.
   - Code: resolve by hand keeping both intents.
   Then re-run the checks (ruff, `mypy src examples`, `pyright`, `pytest`) and regenerate once
   more; `git status` must be clean (no golden drift).
3. When the shared sheets changed, render them and look (skill `regen-examples`, step 3).
4. Push, enable squash auto-merge and wait (run it in the background; it exits when merged, on
   the first failed check, or after ~40 min):
   ```bash
   .agents/skills/merge-pr-queue/scripts/ship.sh <pr-number>
   ```
   If it reports FAILED, fix on the branch, re-run step 1 and ship again. If the PR shows
   `BEHIND` (someone else merged to main meanwhile), go back to step 1.
5. Next PR.
6. After the queue: on `origin/main` run all checks, `uv run python examples/generate_all.py`
   (expect no SVG changes), render the sheets the queue touched, and check
   `gh run list --branch main --limit 3`.

## Gotchas

- Auto-fix `<ci-monitor-event>` "merge conflict" alerts can refer to a PR's **old** head after
  you already rebased it. Compare with `gh pr view N --json headRefOid,mergeable` before acting.
- A failed `gh pr checks --watch` can return immediately when checks haven't registered yet;
  `ship.sh` sleeps first and checks `state` instead.
- Other people's PRs can land on main mid-queue (and bring new errors). If pyright/mypy fails on
  a file the PR doesn't touch, check `origin/main` itself and fix it in a separate PR first.
- The repo does not delete branches on merge; clean up local/remote PR branches only when asked.
