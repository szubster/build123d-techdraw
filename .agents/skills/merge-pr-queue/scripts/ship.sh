#!/usr/bin/env bash
# Push the current branch, enable squash auto-merge on PR <n>, and wait until it is merged.
# usage: ship.sh <pr-number>
# Exit 0 = merged, 1 = a check failed, 3/4 = push/merge command failed, 5 = timed out (~40 min).
set -uo pipefail
n=$1
git push -q --force-with-lease origin HEAD || exit 3
gh pr merge "$n" --squash --auto >/dev/null || exit 4
sleep 45
for _ in $(seq 1 40); do
  st=$(gh pr view "$n" --json state -q .state)
  [ "$st" = MERGED ] && { echo "#$n MERGED"; exit 0; }
  fail=$(gh pr view "$n" --json statusCheckRollup -q '[.statusCheckRollup[] | select(.conclusion=="FAILURE") | .name] | join(",")')
  [ -n "$fail" ] && { echo "#$n FAILED: $fail"; exit 1; }
  sleep 60
done
echo "#$n TIMEOUT"; exit 5
