#!/usr/bin/env bash
# PreToolUse: refuse direct edits to generated files. Exit 2 = block, stderr goes back to the agent.
f=$(jq -r '.tool_input.file_path // .tool_input.notebook_path // empty')
[ -n "$f" ] || exit 0
rel=${f#"${CLAUDE_PROJECT_DIR:-$PWD}"/}
case "$rel" in
  uv.lock)
    echo "uv.lock is generated. Use 'uv add', 'uv remove' or 'uv lock' instead of editing it." >&2; exit 2 ;;
  src/build123d_techdraw/_version.py)
    echo "_version.py is written by hatch-vcs at build time; do not edit it." >&2; exit 2 ;;
  examples/output/*|docs/assets/examples/*)
    echo "$rel is a generated golden file. Change the code, then run 'uv run python examples/generate_all.py' (see the regen-examples skill)." >&2; exit 2 ;;
  CLAUDE.md|*/CLAUDE.md)
    echo "This repo keeps agent instructions in AGENTS.md. A CLAUDE.md would stop Claude Code from loading AGENTS.md; edit AGENTS.md instead." >&2; exit 2 ;;
esac
exit 0
