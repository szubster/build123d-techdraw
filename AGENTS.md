# AGENTS.md

Guidance for AI coding agents (and humans) working on `build123d-techdraw`.

## Standards policy: we follow ISO

This project follows **ISO** technical drawing standards. When ISO and ASME/ANSI
differ, the **ISO behaviour is the default**. The ASME/US behaviour is offered
only as an explicit opt-in.

- **Projection method:** first-angle projection (ISO 128-30 / ISO 5456-2,
  "method E") is the default (`add_standard_multiview(layout="first_angle")`,
  `StandardMultiview`, `TitleBlockInfo.third_angle = False`). Third-angle
  (ASME Y14.3) is opt-in via `layout="third_angle"` / `third_angle=True`.
  Never describe third-angle as "ISO" or "standard ISO".
- **Projection symbol:** ISO 5456-2. The truncated cone is drawn with its large end
  on the left. The end-view circles sit to the left of it for first-angle and to the
  right for third-angle. The title block symbol must always match the view arrangement.
- Other standards in use: ISO 5457 (sheets/borders), ISO 7200 (title blocks),
  ISO 5455 (scales), ISO 128 (lines), ISO 129 (dimensions), ISO 1101 (GD&T),
  ISO 1302 (surface texture), ISO 2768 (general tolerances).
- Cite the specific standard in docstrings and docs when describing
  standard-defined behaviour, and check that the citation is correct.
- Hand-placed example views (`FrontView`, `TopView`, `align_to`, ...) must be
  arranged in first-angle projection (e.g. Top view *below* Front view), unless the
  example sets `third_angle=True` on purpose.

## Workflow

- Setup: `uv sync --all-groups`
- Tests: `uv run pytest`
- Types: `uv run mypy src examples`
- Lint/format: `uv run ruff check` and `uv run ruff format --check`
- Any change to rendered output must come with regenerated golden drawings:
  `uv run python examples/generate_all.py` (updates `examples/output/` and
  `docs/assets/examples/`). Commit the regenerated files.
- Commits and PR titles follow Conventional Commits. Mark breaking changes with `!`
  (e.g. `feat(layout)!: ...`). See `CONTRIBUTING.md`.
