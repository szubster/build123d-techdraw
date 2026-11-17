#!/bin/bash
# PostToolUse hook: auto-fix and format a Python file right after Claude edits it,
# then report any remaining lint errors back to Claude (exit 2 = feedback to the model).
set -uo pipefail

file=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))' 2>/dev/null)
case "$file" in
  *.py|*.pyi) ;;
  *) exit 0 ;;
esac
[ -f "$file" ] || exit 0

cd "${CLAUDE_PROJECT_DIR:-.}"
if [ -x .venv/bin/ruff ]; then
  ruff=.venv/bin/ruff
elif command -v ruff >/dev/null 2>&1; then
  ruff=ruff
else
  exit 0
fi

"$ruff" format --quiet "$file" >/dev/null 2>&1
if ! out=$("$ruff" check --fix --quiet "$file" 2>&1); then
  echo "ruff found issues it could not auto-fix in $file:" >&2
  echo "$out" >&2
  exit 2
fi
exit 0
