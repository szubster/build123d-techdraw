#!/usr/bin/env bash
# PostToolUse: ruff-fix and ruff-format the Python file that was just edited (same ruff as CI).
f=$(jq -r '.tool_input.file_path // empty')
case "$f" in
  *.py|*.pyi) ;;
  *) exit 0 ;;
esac
[ -f "$f" ] || exit 0
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
uv run --quiet ruff check --fix --unfixable F401,F841 --quiet "$f" >/dev/null 2>&1
uv run --quiet ruff format --quiet "$f" >/dev/null 2>&1
# Surface what ruff could not fix. Unused imports/variables are left alone and not reported:
# they are usually mid-edit (import added before the code that uses it); CI still catches them.
out=$(uv run --quiet ruff check --extend-ignore F401,F841 --output-format=concise "$f" 2>/dev/null)
if [ -n "$out" ] && ! printf '%s' "$out" | grep -q "All checks passed"; then
  printf 'ruff found issues it could not auto-fix in %s:\n%s\n' "$f" "$out" >&2
  exit 2
fi
exit 0
