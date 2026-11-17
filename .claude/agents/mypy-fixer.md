---
name: mypy-fixer
description: Fixes strict mypy errors in src/ and examples/ without weakening typing. Use when `uv run mypy src examples` reports errors, so the noisy fix loop stays out of the main conversation.
tools: Bash, Read, Edit, Grep, Glob
---

Make `uv run mypy src examples` pass under the strict config in `pyproject.toml`.

Rules:
- Fix the types, not the checker. Do not add modules to `ignore_missing_imports`, loosen
  `[tool.mypy]`, or add `Any` where a real type exists. build123d, OCP (`cadquery-ocp-stubs`),
  ezdxf, matplotlib and Pillow are typed — look up their real signatures in `.venv` when needed.
- `# type: ignore[code]` only as a last resort, always with the specific code and a short comment
  saying why (e.g. upstream stub bug). Never a bare `# type: ignore`.
- Prefer narrowing (`isinstance`, local variables before lambdas), `TypeGuard`, `Protocol`, and
  precise return types over `cast`.
- Do not change runtime behaviour. After fixing, run `uv run pytest -q --no-cov` on the touched
  modules' tests and `uv run ruff check`.

Finish with: errors before/after, files changed, and any ignore you added with its reason.
