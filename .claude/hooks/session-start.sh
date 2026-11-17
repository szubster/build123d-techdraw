#!/bin/bash
# SessionStart hook: install the project's dev environment in Claude Code cloud sessions.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"

# System libraries OCP (build123d's OpenCASCADE bindings) needs at import time, as in CI.
missing=()
for pkg in libgl1 libegl1 libxrender1 libxcursor1 libxft2 libxinerama1; do
  dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
done
if [ ${#missing[@]} -gt 0 ]; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${missing[@]}"
fi

# Sync the uv environment only when the lockfile, project config or this script changed.
hashes=.venv/.session-start.sha256
if ! sha256sum --check --status "$hashes" 2>/dev/null; then
  rm -f "$hashes"
  uv sync --all-groups
  sha256sum .claude/hooks/session-start.sh pyproject.toml uv.lock .python-version > "$hashes"
fi

# Put the venv's tools (ruff, mypy, pytest, pyright) first on PATH for the session.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export PATH=\"$PWD/.venv/bin:\$PATH\"" >> "$CLAUDE_ENV_FILE"
fi
