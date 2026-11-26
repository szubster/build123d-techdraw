# Interactive 3D-in-2D HTML5 Blueprints

`build123d-techdraw` can export any `DrawingSheet` to a **single, self-contained HTML5 file** that pairs your full dual-theme 2D ISO engineering drawing with a **live WebGL2 3D CAD viewport**—with **zero CDN dependencies**, zero external scripts, and full offline support.

---

## Live Interactive 3D Blueprint

Try the live Flanged Bearing Housing Assembly below: orbit/pan/zoom the 3D viewport, toggle **Dark Blueprint ↔ ISO White** themes, switch GLSL shader modes, scrub the **X / Y / Z Section Cut** plane, drag the **Assembly Explode** slider, or hover BOM rows to highlight parts in both 2D and 3D.

<div class="td-interactive-wrap">
  <div class="td-interactive-bar">
    <span><strong>Flanged Bearing Housing Assembly</strong> — Self-contained HTML5 + WebGL2 + ISO 2D Blueprint</span>
    <a class="td-fullscreen-btn" href="../../assets/examples/interactive_3d_blueprint.html" target="_blank" rel="noopener">
      Open Interactive Blueprint Fullscreen &#8599;
    </a>
  </div>
  <iframe
    class="td-interactive-frame"
    src="../../assets/examples/interactive_3d_blueprint.html"
    title="Interactive 3D-in-2D HTML5 Blueprint — Flanged Bearing Housing Assembly"
    loading="lazy"
  ></iframe>
</div>

---

## Key Capabilities

- **Zero-CDN Self-Contained HTML5:** All SVG vector geometry, base64-encoded triangle meshes, sharp crease edges, BOM metadata, CSS, and WebGL2 GLSL shaders are bundled into a single `.html` file you can email, archive, or host statically.
- **Custom WebGL2 GLSL Shaders:** Switch instantaneously between four engineering visualization modes:
  - **Blueprint Technical** — Stylized dark/light blueprint shading with crisp crease lines.
  - **ISO Shaded + Edges** — Smooth Phong/hemisphere shading with sharp CAD feature edges.
  - **X-Ray Fresnel** — Translucent rim-weighted view exposing internal stepped bores and bushings.
  - **Wireframe** — Clean architectural/engineering crease-edge view.
- **Synchronized Dual Themes:** One click toggles both the 2D ISO drawing sheet and the 3D WebGL2 viewport between **Dark Blueprint** and **Clean White ISO** palettes.
- **Live X / Y / Z Section-Plane Cut:** Interactively slice through the 3D assembly along any axis with procedural ISO cross-hatching on the cut plane.
- **BOM Hover/Click Linking & Assembly Explode:** Hovering or clicking a BOM table item or 3D component highlights the matching part across views; the radial/axial explode slider separates assembly components along computed explode vectors.

---

## Exporting an Interactive HTML Blueprint

Use `sheet.export_html(...)` directly on any `DrawingSheet` (or call `HTMLRenderer` / `export_interactive_html` / `render_interactive_html` from `build123d_techdraw.renderers.html`):

```python
from build123d_techdraw import DrawingSheet, SheetSize, Theme, add_standard_multiview

sheet = DrawingSheet(SheetSize.A3_LANDSCAPE)
sheet.set_title_block(
    title="FLANGED BEARING HOUSING ASSEMBLY",
    drawing_number="BRG-400",
    material="SEE BOM",
)
add_standard_multiview(sheet, assembly, scale="auto")

# Export a single self-contained HTML5 file with live WebGL2 3D viewport
sheet.export_html(
    "bearing_housing_blueprint.html",
    theme=Theme.DARK_BLUEPRINT,
    linear_deflection=0.08,  # Mesh tessellation accuracy (mm)
    angular_deflection=0.25,  # Angular tessellation accuracy (rad)
    crease_angle_deg=20.0,  # Dihedral angle threshold for sharp feature edges
    initial_layout="split",  # "split", "2d", or "3d"
)
```

### Programmatic String Rendering

If you are serving blueprints dynamically (e.g., from FastAPI, Jupyter, or a static site generator), render directly to an HTML string:

```python
from build123d_techdraw.renderers.html import HTMLRenderer, render_interactive_html

html_doc: str = render_interactive_html(sheet, theme="dark")
# Equivalent class method:
html_doc = HTMLRenderer.render_sheet(sheet, theme="iso_white", initial_layout="split")
```

Run the full showcase script with `uv run python examples/interactive_3d_blueprint.py`.
