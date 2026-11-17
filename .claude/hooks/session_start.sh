#!/usr/bin/env bash
# SessionStart: in Claude Code cloud sessions, install dependencies so tests and mypy work at once.
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
uv sync --all-groups --quiet >&2 || echo "uv sync failed; run it manually" >&2
exit 0
