# AGENTS.md

Guidance for AI coding agents (Claude Code, Codex, etc.) working in this repo.
`CLAUDE.md` is a symlink to this file — edit `AGENTS.md` only.

## Project

`build123d-techdraw` is a 2D technical-drawing engine for [build123d](https://github.com/gumyr/build123d)
(OpenCASCADE via `OCP`). 3D shapes are projected to 2D paths; all annotations (dimensions, GD&T,
title blocks, BOMs…) are drawn directly in **2D paper space, in millimetres**, so text and arrow
sizes stay invariant regardless of view scale. Outputs: SVG (themed), PDF, DXF.

## Commands

Environment is managed with `uv` (`uv sync --all-groups`). CI runs all of these:

```bash
uv run ruff check .            # lint (ruff config in pyproject.toml, line length 100)
uv run ruff format --check .   # format
uv run mypy src examples       # strict mypy — the type-checking gate in CI
uv run pytest                  # tests (+ coverage); pass --no-cov for quick single-file runs
uv run pytest tests/test_dimensions.py -q --no-cov
uv run python examples/generate_all.py   # regenerate example outputs
```

`pyright` is also installed (dev group) and configured in `pyproject.toml` for the language
server; mypy strict remains the authoritative check.

## Layout (`src/build123d_techdraw/`)

- `core/` — `sheet.py` (`DrawingSheet`, ISO 5457 sizes/borders), `view.py` (`SheetView`, all
  projection math: section/offset/aligned/detail/auxiliary views, breaks), `title_block.py`
  (ISO 7200), `scale.py` (ISO 5455 scales), `booklet.py` (multi-page).
- `annotations/` — `dimensions.py` (ISO 129: linear/radial/diameter/angular/ordinate/chain/baseline),
  `gdt.py` (ISO 1101 FCFs, datums), `callouts.py` (leaders, hole/thread callouts, balloons, section
  lines, welding), `centerlines.py`, `surface.py` (ISO 1302), `tables.py`, `threads.py`
  (`bd_warehouse`-backed thread DB), `anchors.py` + `resolver.py` (semantic topological anchoring
  and 3D→sheet resolution; see below).
- `layout/` — `multiview.py` (auto multiview + assembly BOM), `auto_dimension.py`
  (collision-avoiding placement), `linter.py` (`run_sheet_lint`).
- `recognizers/features.py` — B-Rep feature recognition (holes, bolt circles, bosses, steps).
- `renderers/` — `svg.py`, `pdf.py`, `dxf.py`, `theme.py` (CSS-variable dark/white themes).
- `builder.py` + `context.py` — Builder mode (`with BuildDrawing(): with FrontView(): ...`) and
  algebraic mode (`sheet += view`), with a thread-safe context stack.
- `__init__.py` — the public API surface; export new public names there.

## Conventions

- **Typing:** full strict annotations on everything (mypy `strict = true`). Third-party CAD libs
  (`build123d`, `OCP`, `bd_warehouse`, `ezdxf`, …) are untyped — `ignore_missing_imports` is set.
- **Units:** sheet/paper-space values are mm; model-space values are scaled by the view scale.
  Keep the two clearly separated.
- **Topological references:** annotations may target build123d `Edge`/`Face`/`Vertex` objects.
  These go through `annotations/anchors.py` (semantic fingerprints that survive model rebuilds,
  i.e. defense against the Topological Naming Problem) and `annotations/resolver.py`. Don't
  resolve topology to coordinates ad hoc elsewhere.
- **Examples are tested:** `tests/test_examples.py` requires every `examples/*.py` to be wired
  into `examples/generate_all.py`, and CI fails if committed SVGs in `examples/output/` and
  `docs/assets/examples/` differ from regenerated ones. If you change rendering output, regenerate
  and commit them.
- **Commits / PR titles:** Conventional Commits, e.g. `feat(core): …`, `fix(renderers): …`
  (enforced by `semantic-pr.yml`; drives semantic-release versioning).
- Docs live in `docs/` (mkdocs-material + mkdocstrings); update user-guide pages for new features.

## Agent tooling (`.claude/`)

- `settings.json` enables the `pyright-lsp` plugin (go-to-definition, find-references, hover
  types, diagnostics). Prefer it over grep when tracing build123d/OCP types or call sites.
- `hooks/ruff-on-edit.sh` runs `ruff format` + `ruff check --fix` on every edited `.py` file and
  reports unfixable lint back to the agent.
- `hooks/session-start.sh` (cloud sessions only) installs OCP's system libs and runs
  `uv sync --all-groups`, skipping work when `uv.lock`/`pyproject.toml` are unchanged.
