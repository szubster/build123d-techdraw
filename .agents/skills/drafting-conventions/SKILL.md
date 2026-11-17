---
name: drafting-conventions
description: ISO drafting rules and this project's paper-space conventions for dimensions, GD&T, line types, views, title blocks and themes. Load before changing anything in annotations/, layout/, renderers/ or core/sheet.py, or when reviewing a rendered drawing.
user-invocable: false
---

# Drafting conventions for build123d-techdraw

## Paper space

- All annotation geometry, text heights, offsets and line widths are **millimetres on the
  sheet**, independent of view scale. Model coordinates go through the view (`SheetView`) and
  `annotations/resolver.py`; never multiply by a literal scale.
- SVG `viewBox` is the sheet in mm (e.g. `0 0 420 297` for A3 landscape). PDF converts with
  `MM_TO_PT = 72 / 25.4` (`renderers/pdf.py`). DXF is 1:1 model units for flat patterns.
- Lettering (ISO 3098): dimension text 2.5 mm, headings 3.5 mm. Text reads from the bottom or
  right of the sheet (aligned system) — never upside down.

## Lines (ISO 128-2)

| Use | Type | Width (mm) | Theme token |
|---|---|---|---|
| Visible outline | continuous wide | 0.5–0.7 | `line_visible` |
| Hidden edges | dashed narrow | 0.25–0.35 | `line_hidden` |
| Centre / axis lines | long-dash-dot narrow, extends 2–3 mm beyond feature | 0.25 | `line_center` |
| Dimension / extension / leader lines | continuous narrow | 0.25 | `dim_line` |
| Cutting plane | dash-dot, wide at ends and direction changes | 0.35/0.7 | `line_section` |
| Hatching | continuous narrow, 45°, uniform spacing per part | 0.25–0.35 | `hatch` |
| Sheet frame | continuous wide | 0.7 | `border` |

Colours always via `renderers/theme.py` → CSS variables (`var(--dim-color)` etc.); every
change must look right in both `DARK_BLUEPRINT` and `ISO_WHITE`.

## Dimensions (ISO 129-1)

- Extension lines start with a small gap (~1 mm) from the outline and run ~2 mm past the
  dimension line.
- First dimension row ≥ 10 mm from the outline, further rows 7 mm apart
  (`FIRST_DIM_OFFSET_MM`, `DIM_STACK_STEP_MM` in `layout/auto_dimension.py`). Smaller dimensions
  inside, larger outside; dimension lines must not cross each other or extension lines where
  avoidable.
- Each feature is dimensioned once (no redundant dims); place it in the view that shows the
  feature's true shape.
- Prefixes: `⌀` diameter, `R` radius, `□` square, `SR`/`S⌀` sphere. Tolerances per ISO 286
  fits (e.g. `⌀11 H11`) or ISO 2768 general tolerances (title-block note).
- Zero-length or degenerate dimensions are a bug (`ZERO_LENGTH_DIMENSION` lint).

## GD&T (ISO 1101 / ISO 5459)

- Feature control frame: symbol | tolerance (with `⌀` for cylindrical zones, modifiers Ⓜ Ⓛ) |
  datum letters in order of precedence (primary, secondary, tertiary).
- Leader arrow ends on the outline/extension line for a surface; aligned with the dimension line
  when the tolerance applies to the feature axis/median.
- Datum feature symbol: capital letter in a square, joined to a filled or open triangle on the
  feature. Letters I, O, Q are not used.
- Form tolerances (flatness, straightness, circularity, cylindricity) take no datum.

## Surface texture (ISO 1302 / ISO 21920-1)

Tick symbol on the surface or a leader, readable from bottom/right; "material removal required"
closes the V with a bar, "prohibited" adds a circle. Ra value above the long leg.

## Views and projection (ISO 5456-2, ISO 128-3)

- **First-angle** projection is the ISO default (Europe); **third-angle** is the ASME/US default.
  The projection symbol in the title block must match the actual view arrangement
  (`layout/multiview.py`, `sheet.title_info.third_angle`).
- Views are aligned with the principal view; labels (`FRONT`, `SECTION A-A`, `DETAIL B (2:1)`)
  sit under the view with the scale when it differs from the sheet scale.
- Section and detail views carry matching letters on cutting planes / detail circles.

## Sheet (ISO 5457, ISO 7200, ISO 5455)

- Sizes A0–A4 portrait/landscape; drawing frame, centring marks and grid reference zones
  (letters along the vertical edges, numbers along the horizontal edges), as drawn in
  `core/sheet.py`.
- Title block bottom-right, with drawing number, title, scale, projection symbol, sheet n of m,
  material, revision. Nothing may overlap it (`COLLISION_TITLE_BLOCK` lint).
- Scales only from ISO 5455 series (`core/scale.py` `STANDARD_SCALES`): 1:1, 1:2, 1:5, 1:10 …,
  2:1, 5:1, 10:1 …

## Linter

`layout/linter.py` `run_sheet_lint(sheet)` flags `COLLISION_TITLE_BLOCK`, `COLLISION_MARGIN`,
`STALE_ANCHOR`, `ZERO_LENGTH_DIMENSION`, `OVERLAP_ANNOTATION`. New annotation types must
provide a 2D bounding box (`bounding_box_2d()` or handled in `get_annotation_bbox_2d`) so the
linter can see them.
