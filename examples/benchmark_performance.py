"""CPU performance benchmark for build123d-techdraw Stage 1C optimizations.

Measures wall-clock speedups for:
1. 4D spatial-hash hidden-edge deduplication vs O(H * V) brute-force reference,
2. Process-isolated parallel multi-view projection vs sequential projection,
3. Direct native PDF 1.4 vector+raster export across golden example sheets and
   an embedded RGBA shaded raster ``<image>`` sheet.
"""

from __future__ import annotations

import base64
import contextlib
import io
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import build123d as bd
from build123d.exporters import Drawing
from build123d.topology import Shape
from PIL import Image, ImageDraw

from build123d_techdraw import (
    DrawingSheet,
    PDFRenderer,
    SheetSize,
    Theme,
    add_standard_multiview,
)
from build123d_techdraw.core.view import _filter_coincident_hidden_edges
from build123d_techdraw.layout.multiview import shutdown_projection_pool


def _brute_force_hidden_dedup(
    hidden_edges: list[Any], visible_edges: list[Any], tol: float = 1e-2
) -> list[Any]:
    clean: list[Any] = []
    for h_edge in hidden_edges:
        h_bb = h_edge.bounding_box()
        coincides = False
        for v_edge in visible_edges:
            v_bb = v_edge.bounding_box()
            if (
                abs(h_bb.min.X - v_bb.min.X) < tol
                and abs(h_bb.max.X - v_bb.max.X) < tol
                and abs(h_bb.min.Y - v_bb.min.Y) < tol
                and abs(h_bb.max.Y - v_bb.max.Y) < tol
            ):
                coincides = True
                break
        if not coincides:
            clean.append(h_edge)
    return clean


def _build_dense_manifold() -> Shape[Any]:
    """Build a ribbed, filleted multi-hole mechanical manifold for benchmarking."""
    part: Shape[Any] = bd.Box(160.0, 100.0, 14.0)
    for rx in (-48.0, 0.0, 48.0):
        part = part + (bd.Pos(rx, 0.0, 15.0) * bd.Box(8.0, 80.0, 16.0))
    for bx in (-32.0, 32.0):
        part = part + (bd.Pos(bx, 0.0, 18.0) * bd.Cylinder(radius=16.0, height=22.0))
    for bx in (-32.0, 32.0):
        part = part - (bd.Pos(bx, 0.0, 0.0) * bd.Cylinder(radius=8.0, height=60.0))
    for ix in range(-3, 4):
        for iy in (-2, -1, 1, 2):
            if abs(ix) == 1 and abs(iy) == 1:
                continue
            part = part - (bd.Pos(ix * 20.0, iy * 18.0, 0.0) * bd.Cylinder(radius=3.0, height=40.0))
    circ_edges = [
        e for e in part.edges() if e.geom_type == bd.GeomType.CIRCLE and float(e.radius) > 10.0
    ]
    if circ_edges and isinstance(part, (bd.Solid, bd.Compound)):
        with contextlib.suppress(Exception):
            part = part.fillet(1.5, circ_edges[:4])
    return part


def benchmark_hidden_dedup(shape: Shape[Any]) -> None:
    print("\n1. Hidden-Edge Deduplication (O(H*V) brute force vs O(H+V) 4D spatial hash)")
    dwg = Drawing(shape, look_from=(0.0, -1.0, 0.0), look_up=(0.0, 0.0, 1.0), with_hidden=True)
    v_edges = list(dwg.visible_lines.edges())
    h_edges = list(dwg.hidden_lines.edges())

    t0 = time.perf_counter()
    ref = _brute_force_hidden_dedup(h_edges, v_edges)
    t_brute_ms = (time.perf_counter() - t0) * 1000.0

    t1 = time.perf_counter()
    opt = _filter_coincident_hidden_edges(h_edges, v_edges)
    t_spatial_ms = (time.perf_counter() - t1) * 1000.0

    assert len(ref) == len(opt) and all(a is b for a, b in zip(ref, opt, strict=True))
    speedup = t_brute_ms / max(t_spatial_ms, 1e-6)
    print(f"   Edges: {len(v_edges)} visible, {len(h_edges)} hidden -> {len(opt)} kept")
    print(f"   Brute-force : {t_brute_ms:7.2f} ms")
    print(f"   Spatial-hash: {t_spatial_ms:7.2f} ms  ({speedup:.1f}x speedup, exact match)")


def benchmark_multiview(shape: Shape[Any]) -> None:
    print("\n2. Multi-View Projection (Sequential vs Process-Isolated Parallel Pool)")
    t0 = time.perf_counter()
    s_seq = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    add_standard_multiview(s_seq, shape, show_hidden=True, auto_centerlines=True, parallel=False)
    t_seq_ms = (time.perf_counter() - t0) * 1000.0
    svg_seq = s_seq.to_svg()

    # Cold parallel (includes one-time forkserver/spawn worker boot)
    t1 = time.perf_counter()
    s_cold = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    add_standard_multiview(s_cold, shape, show_hidden=True, auto_centerlines=True, parallel=True)
    t_cold_ms = (time.perf_counter() - t1) * 1000.0
    assert s_cold.to_svg() == svg_seq

    # Warm parallel (reuses persistent worker pool, typical for booklets / multi-sheet runs)
    t2 = time.perf_counter()
    s_warm = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    add_standard_multiview(s_warm, shape, show_hidden=True, auto_centerlines=True, parallel=True)
    t_warm_ms = (time.perf_counter() - t2) * 1000.0
    assert s_warm.to_svg() == svg_seq

    shutdown_projection_pool()
    print(f"   Shape faces : {len(shape.faces())}")
    print(f"   Sequential  : {t_seq_ms:7.1f} ms")
    print(f"   Parallel (cold pool boot): {t_cold_ms:7.1f} ms")
    print(
        f"   Parallel (warm pool)     : {t_warm_ms:7.1f} ms  "
        f"({t_seq_ms / max(t_warm_ms, 1e-6):.2f}x speedup, byte-identical SVG)"
    )


def benchmark_pdf_export() -> None:
    print("\n3. Direct Native PDF 1.4 Export (Vector + Embedded RGBA Raster <image>)")
    examples_dir = Path(__file__).resolve().parent / "output"
    svgs = sorted(examples_dir.glob("*.svg"))
    if not svgs:
        print("   No SVGs in examples/output/; skipping golden SVG PDF benchmark.")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        # Warm font metric caches
        PDFRenderer.render_sheets_to_pdf(
            [str(svgs[0])], os.path.join(tmpdir, "warmup.pdf"), theme=Theme.DARK_BLUEPRINT
        )

        t0 = time.perf_counter()
        for svg_path in svgs:
            theme = Theme.ISO_WHITE if svg_path.stem.endswith("_white") else Theme.DARK_BLUEPRINT
            PDFRenderer.render_sheets_to_pdf(
                [str(svg_path)],
                os.path.join(tmpdir, f"{svg_path.stem}.pdf"),
                theme=theme,
            )
        t_all_ms = (time.perf_counter() - t0) * 1000.0

        # Also benchmark an embedded 1024x768 RGBA shaded view <image>
        img = Image.new("RGBA", (1024, 768), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([80, 60, 944, 708], radius=40, fill=(56, 189, 248, 220))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        img_svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 420 297" width="420mm" height="297mm">'
            '<g transform="translate(210, 148) scale(1.0)">'
            f'<image x="-100" y="-75" width="200" height="150" href="data:image/png;base64,{b64}"/>'
            "</g></svg>"
        )
        t1 = time.perf_counter()
        PDFRenderer.render_sheets_to_pdf(
            [img_svg], os.path.join(tmpdir, "shaded_raster.pdf"), theme=Theme.DARK_BLUEPRINT
        )
        t_img_ms = (time.perf_counter() - t1) * 1000.0

    print(
        f"   {len(svgs)} example SVGs -> PDF: {t_all_ms:6.1f} ms total "
        f"({t_all_ms / len(svgs):.2f} ms/sheet)"
    )
    print(f"   1024x768 RGBA shaded <image> -> PDF: {t_img_ms:6.1f} ms")


def main() -> None:
    print("=== build123d-techdraw CPU Performance Benchmark ===")
    t_build = time.perf_counter()
    manifold = _build_dense_manifold()
    print(f"Built benchmark manifold in {(time.perf_counter() - t_build) * 1000.0:.1f} ms")

    benchmark_hidden_dedup(manifold)
    benchmark_multiview(manifold)
    benchmark_pdf_export()


if __name__ == "__main__":
    main()
