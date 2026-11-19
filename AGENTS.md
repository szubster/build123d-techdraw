# AGENTS.md

Instructions for AI coding agents working in this repository (Claude Code, Codex, Cursor, etc.).
Human contributors: see `CONTRIBUTING.md`.

> `CLAUDE.md` is a symlink to this file so Claude Code loads it; edit `AGENTS.md` only and never
> replace the symlink with a separate `CLAUDE.md`. Agent skills live in `.agents/skills/`
> (`.claude/skills` is a symlink to it).

## Project

`build123d-techdraw` is a 2D technical-drawing engine for build123d (Python CAD). 3D shapes are
projected to 2D paths; annotations (dimensions, GD&T, surface finish, balloons, title blocks) are
drawn in **paper space, in millimetres**, so text height and arrowheads do not scale with the view.
Outputs: themed SVG (dark blueprint / ISO white via CSS custom properties), PDF, DXF.

Layout of `src/build123d_techdraw/`:

- `core/` – `DrawingSheet` (`sheet.py`), `SheetView` projection (`view.py`), scales, title block, booklets
- `annotations/` – dimensions, GD&T, callouts, threads, tables, centerlines, surface symbols;
  `anchors.py` + `resolver.py` map 3D topology / semantic anchors to 2D view coordinates
- `layout/` – auto multi-view layout, auto-dimensioning, sheet linter (`linter.py`)
- `renderers/` – SVG / PDF / DXF writers and `theme.py` colour tokens
- `recognizers/` – feature recognition (holes, etc.)
- `builder.py` – build123d builder-mode (`with` context) API

## Commands

Always go through `uv`; never `pip install` into the env.

```bash
uv sync --all-groups                   # install everything
uv run pytest                          # tests + coverage
uv run pytest tests/test_dimensions.py -k diameter -q --no-cov   # focused run
uv run ruff check --fix . && uv run ruff format .
uv run mypy src examples               # strict; must be clean
uv run pyright                         # must be clean too (CI type-check job); backs pyright-lsp
uv run python examples/generate_all.py # regenerate example drawings (see below)
```

For code navigation, `.claude/settings.json` enables the `pyright-lsp` plugin (definitions,
references, hover types, diagnostics). Prefer it over grep when tracing build123d/OCP types or
call sites.

## Standards policy: we follow ISO

When ISO and ASME/ANSI differ, the **ISO behaviour is the default** and the ASME/US behaviour is
an explicit opt-in. Never describe an ASME convention as "ISO" or "standard ISO".

- **Projection method:** first-angle (ISO 128-3 / ISO 5456-2, "method E") is the default:
  `add_standard_multiview(layout="first_angle")`, `StandardMultiview`,
  `TitleBlockInfo.third_angle = False`. Third-angle (ASME Y14.3) is opt-in via
  `layout="third_angle"` / `third_angle=True`.
- Hand-placed views in examples and docs (`FrontView`, `TopView`, `align_to`, ...) are arranged
  in first-angle (Top view *below* the Front view) unless they set `third_angle=True` on purpose.
- Cite the specific standard in docstrings and docs, and check the citation is right.

## Rules

- **Typing is strict.** Every function is fully annotated. build123d, OCP (via
  `cadquery-ocp-stubs`), ezdxf, matplotlib and Pillow are all typed — do not add them to
  `ignore_missing_imports`, and do not add `# type: ignore` without a specific error code and a
  reason it cannot be fixed. Only `svgelements` is untyped.
- **No duck typing on our own objects.** Do not probe project types with `hasattr`, `getattr(obj,
  "name", default)` or `setattr`, and do not type parameters `Any` only to probe them later. Type
  them as `SheetView` / `DrawingSheet` (import under `TYPE_CHECKING` to break import cycles), use
  the `Annotation` base (`annotations/base.py`) or `SheetDirective` (`core/directive.py`), or a
  `typing.Protocol`, and branch with `isinstance`. New annotations subclass `Annotation` and
  override `bounding_box_2d()` so the linter sees them. Dynamic access is only allowed for untyped
  third-party objects, behind one helper (`renderers/svg_elements.py` for `svgelements`).
- **Units:** annotation geometry is paper-space mm. Model geometry is model units; convert with the
  view's scale, never hard-code a scale factor.
- **Theme colours** come from `renderers/theme.py` tokens / CSS variables — never hard-code hex
  colours in SVG output.
- **Golden SVGs.** `examples/output/*.svg` and `docs/assets/examples/*.svg` are compared
  byte-for-byte in CI. Any change to rendering, layout or annotation code must be followed by
  regenerating them (skill: `regen-examples`) and committing the SVG diff together with the code.
  Never hand-edit these files to make CI pass.
- PDF/DXF outputs are not byte-deterministic (timestamps/handles). Do not commit PDF/DXF churn
  unless the change is meant to affect those formats.
- Generated, never edited by hand: `uv.lock` (use `uv add`/`uv lock`),
  `src/build123d_techdraw/_version.py` (hatch-vcs), everything in `examples/output/` and
  `docs/assets/examples/`.
- New public API needs a test in `tests/` and, if user-facing, a mention in `docs/user-guide/`.
- Drafting behaviour must follow the ISO standards listed in the `drafting-conventions` skill.
  Load it before touching `annotations/`, `layout/` or `renderers/`.

## Commits and PRs

Conventional Commits, enforced on PR titles: `feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert`,
optional scope (`core`, `annotations`, `layout`, `renderers`, `dimensions`, …), `!` for breaking
changes. `feat` → minor, `fix` → patch. Fill in `.github/PULL_REQUEST_TEMPLATE.md`.

Versioning follows Semantic Versioning. The project is **pre-1.0**: breaking API and behaviour
changes are allowed and preferred over compatibility shims, deprecation wrappers or aliases kept
only for old callers. Mark them with `!` / `BREAKING CHANGE:`; while on `0.x` they bump the minor
version, not the major.
