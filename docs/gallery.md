# Examples Gallery

Explore all **12 runnable examples** in `build123d-techdraw`—from WebGPU-shaded views, GPU Visibility-Buffer HLR, SDF smart layout, and live interactive 3D HTML5 blueprints to production ISO mechanical drawings and multi-page booklets.

Regenerate every example locally with:

```bash
uv run python examples/generate_all.py
```

---

## GPU Shaders & 3D Interactive

<div class="td-gallery-grid" markdown>

<div class="td-gallery-card">
  <div class="td-gallery-media">
    <img src="../assets/examples/shaded_technical_views.svg" alt="Shaded Technical Views">
  </div>
  <div class="td-gallery-body">
    <h3>Shaded Technical Views</h3>
    <p>Pump flange housing assembly pairing ISO first-angle orthographic wireframes with a 2×2 WebGPU shader gallery: Gooch + SSAO, Blueprint Shaded, BOM Part Colors, and X-Ray Inspection.</p>
    <div class="td-gallery-meta">
      <span class="td-badge">WebGPU WGSL</span>
      <span class="td-badge">ViewStyle</span>
      <span class="td-badge">SSAO</span>
      <span class="td-badge">SVG + PDF</span>
    </div>
    <div class="td-gallery-links">
      <a href="https://github.com/szubster/build123d-techdraw/blob/main/examples/shaded_technical_views.py" target="_blank" rel="noopener">Source Script &#8599;</a>
      <a href="../user-guide/gpu-acceleration/#shaded-vector-edge-technical-views">User Guide &rarr;</a>
    </div>
  </div>
</div>

<div class="td-gallery-card">
  <div class="td-gallery-media">
    <img src="../assets/examples/interactive_3d_blueprint.svg" alt="Interactive 3D-in-2D Blueprint">
  </div>
  <div class="td-gallery-body">
    <h3>Interactive 3D-in-2D HTML5 Blueprint</h3>
    <p>Flanged bearing housing assembly with ISO first-angle views, Section A-A, and BOM—exported to a zero-CDN self-contained HTML5 app with live WebGL2 orbit, section cut, and explode.</p>
    <div class="td-gallery-meta">
      <span class="td-badge">WebGL2 HTML5</span>
      <span class="td-badge">Live Section Cut</span>
      <span class="td-badge">BOM Linking</span>
    </div>
    <div class="td-gallery-links">
      <a href="../assets/examples/interactive_3d_blueprint.html" target="_blank" rel="noopener">Launch Live 3D &#8599;</a>
      <a href="../user-guide/interactive-3d-html/">Embedded Demo &rarr;</a>
      <a href="https://github.com/szubster/build123d-techdraw/blob/main/examples/interactive_3d_blueprint.py" target="_blank" rel="noopener">Source &#8599;</a>
    </div>
  </div>
</div>

<div class="td-gallery-card">
  <div class="td-gallery-media">
    <img src="../assets/examples/gpu_hlr_assembly.svg" alt="GPU Visibility-Buffer HLR Assembly">
  </div>
  <div class="td-gallery-body">
    <h3>GPU Visibility-Buffer HLR Assembly</h3>
    <p>Finned gearbox drive housing assembly (&gt;300 B-Rep faces) projected with the WebGPU depth + Face-ID visibility-buffer HLR engine into crisp vector SVG, PDF, and DXF with automated BOM.</p>
    <div class="td-gallery-meta">
      <span class="td-badge">GPU HLR</span>
      <span class="td-badge">Vector Output</span>
      <span class="td-badge">Assembly BOM</span>
    </div>
    <div class="td-gallery-links">
      <a href="https://github.com/szubster/build123d-techdraw/blob/main/examples/gpu_hlr_assembly.py" target="_blank" rel="noopener">Source Script &#8599;</a>
      <a href="../user-guide/gpu-acceleration/#gpu-visibility-buffer-hlr">User Guide &rarr;</a>
    </div>
  </div>
</div>

<div class="td-gallery-card">
  <div class="td-gallery-media">
    <img src="../assets/examples/sdf_smart_layout.svg" alt="SDF Smart Layout CNC Hydraulic Manifold">
  </div>
  <div class="td-gallery-body">
    <h3>SDF / Jump-Flooding Smart Layout</h3>
    <p>L-shaped CNC hydraulic manifold annotated via 2D Euclidean Distance-Field (WebGPU JFA+1 / SciPy EDT) clearance placement for stacked dimensions, hole callouts, balloons, and leaders with 0 lint warnings.</p>
    <div class="td-gallery-meta">
      <span class="td-badge">WGSL JFA+1</span>
      <span class="td-badge">SDF Placement</span>
      <span class="td-badge">0 Lint Issues</span>
    </div>
    <div class="td-gallery-links">
      <a href="https://github.com/szubster/build123d-techdraw/blob/main/examples/sdf_smart_layout.py" target="_blank" rel="noopener">Source Script &#8599;</a>
      <a href="../assets/examples/sdf_smart_layout_heatmap.png" target="_blank" rel="noopener">Clearance Heatmap &#8599;</a>
    </div>
  </div>
</div>

</div>

---

## Production Mechanical Drawings

<div class="td-gallery-grid" markdown>

<div class="td-gallery-card">
  <div class="td-gallery-media">
    <img src="../assets/examples/precision_flange.svg" alt="Precision Flange Drawing">
  </div>
  <div class="td-gallery-body">
    <h3>Precision Flange</h3>
    <p>Automated CAD feature recognition (<code>recognize_holes</code>, <code>recognize_bolt_circles</code>), PCD bolt circles, diameter/radial/angular/ordinate dimensions, thread &amp; hole callouts, and ISO 1302 surface finish.</p>
    <div class="td-gallery-meta">
      <span class="td-badge">Feature Recognition</span>
      <span class="td-badge">Ordinate Dims</span>
      <span class="td-badge">ISO 1302</span>
    </div>
    <div class="td-gallery-links">
      <a href="https://github.com/szubster/build123d-techdraw/blob/main/examples/precision_flange.py" target="_blank" rel="noopener">Source Script &#8599;</a>
    </div>
  </div>
</div>

<div class="td-gallery-card">
  <div class="td-gallery-media">
    <img src="../assets/examples/advanced_views.svg" alt="Advanced Views Drawing">
  </div>
  <div class="td-gallery-body">
    <h3>Advanced Views (Section, Detail, Auxiliary, Break)</h3>
    <p>Full ISO 128-3 specialized views: <code>SectionView</code> with cutting line, magnified <code>DetailView</code> with chamfer callout, <code>AuxiliaryView</code> on an inclined face, and zigzag broken view.</p>
    <div class="td-gallery-meta">
      <span class="td-badge">Section View</span>
      <span class="td-badge">Detail View</span>
      <span class="td-badge">Auxiliary &amp; Break</span>
    </div>
    <div class="td-gallery-links">
      <a href="https://github.com/szubster/build123d-techdraw/blob/main/examples/advanced_views.py" target="_blank" rel="noopener">Source Script &#8599;</a>
    </div>
  </div>
</div>

<div class="td-gallery-card">
  <div class="td-gallery-media">
    <img src="../assets/examples/stepped_bracket.svg" alt="Stepped Bracket Blueprint">
  </div>
  <div class="td-gallery-body">
    <h3>Stepped Bracket (GD&amp;T &amp; Tolerances)</h3>
    <p>Detailed mechanical blueprint featuring ISO 1101 GD&amp;T Feature Control Frames, datum symbols, hole tolerance callouts, centermarks, and ISO 5457 borders.</p>
    <div class="td-gallery-meta">
      <span class="td-badge">ISO 1101 GD&amp;T</span>
      <span class="td-badge">Tolerances</span>
      <span class="td-badge">ISO 5457</span>
    </div>
    <div class="td-gallery-links">
      <a href="https://github.com/szubster/build123d-techdraw/blob/main/examples/stepped_bracket.py" target="_blank" rel="noopener">Source Script &#8599;</a>
    </div>
  </div>
</div>

<div class="td-gallery-card">
  <div class="td-gallery-media">
    <img src="../assets/examples/assembly_drawing.svg" alt="Assembly BOM Drawing">
  </div>
  <div class="td-gallery-body">
    <h3>Assembly BOM &amp; Balloons</h3>
    <p>Recursive multi-component assembly discovery generating a structured ISO Bill of Materials table and radiating balloon callouts on the isometric view.</p>
    <div class="td-gallery-meta">
      <span class="td-badge">Auto BOM</span>
      <span class="td-badge">Balloons</span>
      <span class="td-badge">Multi-View</span>
    </div>
    <div class="td-gallery-links">
      <a href="https://github.com/szubster/build123d-techdraw/blob/main/examples/assembly_bom.py" target="_blank" rel="noopener">Source Script &#8599;</a>
    </div>
  </div>
</div>

<div class="td-gallery-card">
  <div class="td-gallery-media">
    <img src="../assets/examples/welded_frame_booklet.svg" alt="Welded Frame Booklet">
  </div>
  <div class="td-gallery-body">
    <h3>Welded Frame Booklet</h3>
    <p>Multi-page <code>DrawingBooklet</code> with PDF bookmarks, ISO 2553 welding symbols, process step badges, custom tables, cards, and chain/baseline dimension groups.</p>
    <div class="td-gallery-meta">
      <span class="td-badge">DrawingBooklet</span>
      <span class="td-badge">ISO 2553 Welds</span>
      <span class="td-badge">Chain / Baseline</span>
    </div>
    <div class="td-gallery-links">
      <a href="https://github.com/szubster/build123d-techdraw/blob/main/examples/welded_frame_booklet.py" target="_blank" rel="noopener">Source Script &#8599;</a>
    </div>
  </div>
</div>

<div class="td-gallery-card">
  <div class="td-gallery-media">
    <img src="../assets/examples/quickstart_enclosure.svg" alt="Quickstart Enclosure Base">
  </div>
  <div class="td-gallery-body">
    <h3>Quickstart Enclosure Base</h3>
    <p>Minimal 30-second example demonstrating <code>add_standard_multiview</code> with automatic ISO 5455 scale selection and overall envelope dimensions.</p>
    <div class="td-gallery-meta">
      <span class="td-badge">Quickstart</span>
      <span class="td-badge">Auto-Scale</span>
      <span class="td-badge">First-Angle ISO</span>
    </div>
    <div class="td-gallery-links">
      <a href="https://github.com/szubster/build123d-techdraw/blob/main/examples/quickstart_enclosure.py" target="_blank" rel="noopener">Source Script &#8599;</a>
    </div>
  </div>
</div>

<div class="td-gallery-card">
  <div class="td-gallery-media">
    <img src="../assets/examples/builder_mode_bracket.svg" alt="Builder Mode Bracket">
  </div>
  <div class="td-gallery-body">
    <h3>Builder Mode Bracket</h3>
    <p>Declarative context-manager API (<code>with BuildDrawing(...)</code>, <code>with FrontView(...)</code>) with direct 3D topological selection for GD&amp;T and linear dimensions.</p>
    <div class="td-gallery-meta">
      <span class="td-badge">Builder Mode</span>
      <span class="td-badge">3D Anchors</span>
    </div>
    <div class="td-gallery-links">
      <a href="https://github.com/szubster/build123d-techdraw/blob/main/examples/builder_mode_bracket.py" target="_blank" rel="noopener">Source Script &#8599;</a>
    </div>
  </div>
</div>

<div class="td-gallery-card">
  <div class="td-gallery-media">
    <img src="../assets/examples/algebraic_mode_bracket.svg" alt="Algebraic Mode Bracket">
  </div>
  <div class="td-gallery-body">
    <h3>Algebraic Mode Bracket</h3>
    <p>Functional operator composition (<code>sheet += [front, top, iso]</code>, <code>align_to</code> view chaining) with <code>TitleBlock</code>, <code>GeneralNotes</code>, <code>Revision</code>, and topological annotations.</p>
    <div class="td-gallery-meta">
      <span class="td-badge">Algebraic Mode</span>
      <span class="td-badge">align_to</span>
    </div>
    <div class="td-gallery-links">
      <a href="https://github.com/szubster/build123d-techdraw/blob/main/examples/algebraic_mode_bracket.py" target="_blank" rel="noopener">Source Script &#8599;</a>
    </div>
  </div>
</div>

</div>
