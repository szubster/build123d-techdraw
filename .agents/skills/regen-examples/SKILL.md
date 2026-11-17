---
name: regen-examples
description: Regenerate the example drawings (SVG/PDF/DXF), check which golden SVGs changed, and render the changed sheets to PNG for visual review. Use after any change to rendering, layout, annotation or view code, and before committing such a change.
---

# Regenerate and review example drawings

CI (`examples` job in `.github/workflows/ci.yml`) re-runs the generator and fails if
`examples/output/*.svg` or `docs/assets/examples/*.svg` differ from what is committed. So every
code change that affects output ships together with its regenerated SVGs — and those SVG changes
must be *looked at*, not just committed.

## Steps

1. Regenerate (also syncs SVGs into `docs/assets/examples/`):
   ```bash
   uv run python examples/generate_all.py
   ```
2. See what changed:
   ```bash
   git status --porcelain -- examples/output docs/assets/examples
   git diff --stat -- 'examples/output/*.svg'
   ```
   - No SVG changes but you expected some → the change isn't reaching the examples; say so.
   - SVG changes you did *not* expect → treat as a regression until explained.
3. Render the changed sheets to PNG (defaults to every changed SVG under `examples/output/`):
   ```bash
   uv run --with playwright python .agents/skills/regen-examples/scripts/render_svg.py --scale 1
   ```
   To compare with the committed version, render it too:
   ```bash
   git show HEAD:examples/output/NAME.svg > /tmp/NAME.before.svg
   uv run --with playwright python .agents/skills/regen-examples/scripts/render_svg.py --scale 1 /tmp/NAME.before.svg
   ```
   Open the PNGs (they are printed to stdout) and inspect them. Prefer the `*_white.svg`
   variants — higher contrast. Check against the `drafting-conventions` skill: overlaps, text
   collisions, arrowheads, leader placement, title-block collisions, sheet margins.
4. PDF/DXF files are rewritten on every run and are not byte-deterministic. Unless the change is
   meant to affect PDF/DXF output, discard them:
   ```bash
   git checkout -- 'examples/output/*.pdf' 'examples/output/*.dxf'
   ```
5. Commit the SVG changes in the same commit as the code that caused them.

Never edit an SVG in `examples/output/` or `docs/assets/examples/` by hand.

## Renderer notes

`scripts/render_svg.py` needs a Chromium for Playwright (the SVGs use CSS custom properties, so
simple rasterizers render them wrong). It uses `$CHROMIUM_PATH`, then `/opt/pw-browsers/chromium`
(Claude Code cloud), then `chromium` on PATH, else Playwright's own download
(`uvx playwright install chromium` once).
