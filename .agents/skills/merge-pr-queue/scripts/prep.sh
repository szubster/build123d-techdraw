#!/usr/bin/env bash
# Rebase a PR branch onto origin/main, run all checks, regenerate examples.
# usage: prep.sh <branch> [<old-base-sha>]   (old-base-sha: for a stacked PR, the base commit to drop)
# Exit 2 = rebase stopped on conflicts (resolve, `git rebase --continue`, then re-run the checks).
set -euo pipefail
br=$1; drop=${2:-}
git fetch -q origin
git switch -q "$br"
git reset -q --hard "origin/$br"
if [ -n "$drop" ]; then
  git rebase -q --onto origin/main "$drop" "$br" || { echo "REBASE CONFLICT"; git status --short; exit 2; }
else
  git rebase -q origin/main || { echo "REBASE CONFLICT"; git status --short; exit 2; }
fi
uv sync -q --all-groups
uv run ruff check . -q
uv run ruff format --check . -q
uv run mypy src examples | tail -1
uv run pyright | tail -1
uv run pytest --no-cov -p no:warnings 2>&1 | tail -1
uv run python examples/generate_all.py >/dev/null
git checkout -q -- 'examples/output/*.pdf' 'examples/output/*.dxf'
echo "Commits on top of main:"; git log --oneline origin/main..HEAD
echo "Uncommitted changes after regeneration (should be empty):"; git status --short
