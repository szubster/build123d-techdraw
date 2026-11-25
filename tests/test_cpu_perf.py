"""Unit tests and performance checks for CPU optimizations (Stage 1C):
- 4D spatial-hash hidden-edge deduplication equivalence & speedup,
- Process-isolated parallel multi-view projection determinism & stress safety,
- Direct native PDF 1.4 export with embedded raster <image> & <clipPath> support.
"""

from __future__ import annotations

import base64
import io
import math
import re
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import build123d as bd
import matplotlib.pyplot as plt
import pytest
from build123d.exporters import Drawing
from build123d.topology import Shape
from PIL import Image, ImageDraw

from build123d_techdraw import (
    BuildDrawing,
    DrawingSheet,
    PDFRenderer,
    SheetSize,
    StandardMultiview,
    Theme,
    add_standard_multiview,
)
from build123d_techdraw.core.view import _filter_coincident_hidden_edges
from build123d_techdraw.layout.multiview import shutdown_projection_pool
from build123d_techdraw.renderers.theme import THEME_COLORS


@dataclass(frozen=True)
class _Vec2:
    X: float
    Y: float


@dataclass(frozen=True)
class _BBox2D:
    min: _Vec2
    max: _Vec2


class _FakeEdge:
    """Lightweight stand-in with the exact ``bounding_box()`` surface used by dedup."""

    def __init__(self, x0: float, x1: float, y0: float, y1: float) -> None:
        self._bb = _BBox2D(_Vec2(x0, y0), _Vec2(x1, y1))

    def bounding_box(self) -> _BBox2D:
        return self._bb


def _brute_force_filter(
    hidden_edges: list[Any],
    visible_edges: list[Any],
    tol: float = 1e-2,
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


def _make_dense_ribbed_plate() -> Shape[Any]:
    """Build a multi-hole, ribbed plate producing many projected visible & hidden edges."""
    plate: Shape[Any] = bd.Box(160.0, 100.0, 12.0)
    for rx in (-48.0, 0.0, 48.0):
        plate = plate + (bd.Pos(rx, 0.0, 14.0) * bd.Box(6.0, 82.0, 16.0))
    for ix in range(-3, 4):
        for iy in (-2, -1, 1, 2):
            plate = plate - (
                bd.Pos(ix * 20.0, iy * 18.0, 0.0) * bd.Cylinder(radius=3.0, height=40.0)
            )
    return plate


def _make_png_data_uri(
    width: int = 32,
    height: int = 24,
    *,
    transparent: bool = True,
) -> str:
    mode = "RGBA" if transparent else "RGB"
    bg = (0, 0, 0, 0) if transparent else (15, 23, 42)
    img = Image.new(mode, (width, height), bg)
    draw = ImageDraw.Draw(img)
    fg = (56, 189, 248, 220) if transparent else (56, 189, 248)
    draw.rectangle([4, 4, width - 5, height - 5], fill=fg)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _decompress_pdf_streams(pdf_bytes: bytes) -> list[bytes]:
    """Extract and inflate all FlateDecode streams from raw PDF bytes."""
    streams: list[bytes] = []
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", pdf_bytes, flags=re.DOTALL):
        raw = match.group(1)
        try:
            streams.append(zlib.decompress(raw))
        except zlib.error:
            streams.append(raw)
    return streams


# ---------------------------------------------------------------------------
# 1. Hidden-Edge Spatial-Hash Deduplication Tests
# ---------------------------------------------------------------------------


def test_hidden_edge_dedup_boundary_straddling_equivalence() -> None:
    """Spatial-hash dedup matches brute force exactly across bucket boundaries."""
    tol = 1e-2
    # Cell boundaries occur at multiples of 0.25; place pairs straddling x=0.25 and y=0.50.
    visible = [
        _FakeEdge(0.246, 10.246, 0.496, 20.496),  # straddles 0.25/0.50 from below
        _FakeEdge(-0.254, 5.000, -0.504, 8.000),  # negative bucket boundary
        _FakeEdge(100.0, 120.0, 50.0, 75.0),
    ]
    hidden = [
        # Just inside tol=0.01 across the 0.25/0.50 cell boundary -> MUST be filtered out
        _FakeEdge(0.254, 10.254, 0.504, 20.504),
        # Just outside tol=0.01 on one coordinate -> MUST be kept
        _FakeEdge(0.257, 10.254, 0.504, 20.504),
        # Negative boundary match within tol -> MUST be filtered out
        _FakeEdge(-0.247, 5.004, -0.497, 8.004),
        # Exact match -> MUST be filtered out
        _FakeEdge(100.0, 120.0, 50.0, 75.0),
        # Distinct edge -> MUST be kept
        _FakeEdge(10.0, 20.0, 30.0, 40.0),
    ]

    expected = _brute_force_filter(hidden, visible, tol=tol)
    actual = _filter_coincident_hidden_edges(hidden, visible, tol=tol)

    assert actual == expected
    assert len(actual) == 2
    assert _filter_coincident_hidden_edges([], visible) == []
    assert _filter_coincident_hidden_edges(hidden, []) == hidden
    assert _filter_coincident_hidden_edges(hidden, visible, tol=0.0) == hidden


def test_hidden_edge_dedup_dense_cad_shape_equivalence_and_speedup() -> None:
    """Verify exact equivalence and significant speedup on a dense ribbed multi-hole CAD shape."""
    part = _make_dense_ribbed_plate()
    dwg = Drawing(part, look_from=(0.0, -1.0, 0.0), look_up=(0.0, 0.0, 1.0), with_hidden=True)
    v_edges = list(dwg.visible_lines.edges())
    h_edges = list(dwg.hidden_lines.edges())
    assert len(v_edges) >= 10
    assert len(h_edges) >= 80

    t0 = time.perf_counter()
    expected = _brute_force_filter(h_edges, v_edges, tol=1e-2)
    t_brute = time.perf_counter() - t0

    t1 = time.perf_counter()
    actual = _filter_coincident_hidden_edges(h_edges, v_edges, tol=1e-2)
    t_spatial = time.perf_counter() - t1

    # Exact same surviving edge objects in identical order
    assert len(actual) == len(expected)
    for a, e in zip(actual, expected, strict=True):
        assert a is e

    # Spatial-hash precomputes bounding boxes once (O(H + V)) vs brute-force O(H * V)
    assert t_spatial < t_brute


# ---------------------------------------------------------------------------
# 2. Parallel Multi-View Projection Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("layout_name", ["first_angle", "third_angle"])
def test_parallel_multiview_byte_identical_svg_and_order(layout_name: str) -> None:
    """Parallel multiview produces byte-for-byte identical SVG and view ordering to sequential."""
    part = _make_dense_ribbed_plate()

    sheet_seq = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    mv_seq = add_standard_multiview(
        sheet_seq,
        part,
        layout=layout_name,
        scale="auto",
        show_hidden=True,
        auto_centerlines=True,
        parallel=False,
    )

    sheet_par = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    mv_par = add_standard_multiview(
        sheet_par,
        part,
        layout=layout_name,
        scale="auto",
        show_hidden=True,
        auto_centerlines=True,
        parallel=True,
        max_workers=4,
    )

    assert [v.view_type for v in sheet_par.views] == [v.view_type for v in sheet_seq.views]
    assert list(mv_par.views.keys()) == list(mv_seq.views.keys())
    assert mv_par.scale == pytest.approx(mv_seq.scale)
    assert sheet_par.to_svg() == sheet_seq.to_svg()


def _make_medium_ribbed_plate() -> Shape[Any]:
    """Build an 8-hole ribbed plate for fast, thorough multi-view stress tests."""
    plate: Shape[Any] = bd.Box(120.0, 80.0, 12.0)
    plate = plate + (bd.Pos(0.0, 0.0, 14.0) * bd.Box(8.0, 64.0, 14.0))
    for ix in (-40.0, -18.0, 18.0, 40.0):
        for iy in (-24.0, 24.0):
            plate = plate - (bd.Pos(ix, iy, 0.0) * bd.Cylinder(radius=3.5, height=30.0))
    return plate


def test_parallel_multiview_stress_and_pool_lifecycle() -> None:
    """Repeated parallel projections across complex shapes stay deterministic and crash-free."""
    part = _make_medium_ribbed_plate()
    ref_sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    add_standard_multiview(ref_sheet, part, show_hidden=True, parallel=False)
    ref_svg = ref_sheet.to_svg()

    # Stress-test 6 consecutive parallel runs on the warm process-isolated pool
    for _ in range(6):
        s = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
        add_standard_multiview(s, part, show_hidden=True, parallel=True, max_workers=4)
        assert s.to_svg() == ref_svg

    # Verify StandardMultiview builder mode forwards parallel=True cleanly
    with BuildDrawing(sheet_size=SheetSize.A3_LANDSCAPE) as bs:
        StandardMultiview(part, show_hidden=True, parallel=True, max_workers=2)
    assert bs.to_svg() == ref_svg

    # Invalid parallel mode raises ValueError
    with pytest.raises(ValueError, match="parallel"):
        add_standard_multiview(
            DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE),
            part,
            parallel="invalid",  # type: ignore[arg-type]
        )

    # Clean pool shutdown and transparent restart
    shutdown_projection_pool()
    s_after = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    add_standard_multiview(s_after, part, show_hidden=True, parallel=True, max_workers=2)
    assert s_after.to_svg() == ref_svg
    shutdown_projection_pool()


def test_auto_centerlines_reuses_projected_circles_without_duplicate_drawing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """auto_centerlines reuses cached visible circles instead of invoking Drawing() twice per view."""
    import build123d_techdraw.layout.multiview as mv_mod

    def _forbid_extra_drawing(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError(
            "auto_centerlines should reuse _visible_circles without re-running Drawing"
        )

    monkeypatch.setattr(mv_mod, "Drawing", _forbid_extra_drawing)

    part = bd.Box(60.0, 40.0, 15.0) - bd.Cylinder(radius=8.0, height=30.0)
    sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    add_standard_multiview(sheet, part, auto_centerlines=True, parallel=False)
    assert 'class="centermark"' in sheet.to_svg()


# ---------------------------------------------------------------------------
# 3. Fast Native Vector PDF Renderer & Embedded Raster <image> Tests
# ---------------------------------------------------------------------------


def test_pdf_embedded_raster_image_href_xlink_transform_and_clip(tmp_path: Path) -> None:
    """Verify SVG <image> with data:image/png;base64 (href & xlink:href), transforms, SMask & clipPath."""
    rgba_uri = _make_png_data_uri(32, 24, transparent=True)
    rgb_uri = _make_png_data_uri(20, 20, transparent=False)

    svg_code = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
     viewBox="0 0 200 150" width="200mm" height="150mm">
  <defs>
    <clipPath id="imgClip">
      <circle cx="150" cy="75" r="25"/>
    </clipPath>
  </defs>
  <g transform="translate(50, 40) scale(1.5, 1.5)">
    <image x="0" y="0" width="40" height="30" href="{rgba_uri}" preserveAspectRatio="none"/>
  </g>
  <g clip-path="url(#imgClip)">
    <image x="125" y="50" width="50" height="50" xlink:href="{rgb_uri}" opacity="0.85"/>
  </g>
</svg>"""

    pdf_path = tmp_path / "embedded_images.pdf"
    PDFRenderer.render_sheets_to_pdf([svg_code], str(pdf_path), theme=Theme.DARK_BLUEPRINT)

    pdf_bytes = pdf_path.read_bytes()
    assert pdf_bytes.startswith(b"%PDF-1.4")
    # Two distinct Image XObjects (32x24 RGBA + its /SMask, and 20x20 RGB)
    assert b"/Subtype /Image" in pdf_bytes
    assert b"/Width 32 /Height 24" in pdf_bytes
    assert b"/Width 20 /Height 20" in pdf_bytes
    assert b"/SMask " in pdf_bytes
    assert b"/ColorSpace /DeviceGray" in pdf_bytes
    assert b"/ExtGState" in pdf_bytes

    # Decompress content stream and verify exact transformed CTM & clip operator
    streams = _decompress_pdf_streams(pdf_bytes)
    joined = b"\n".join(streams).decode("latin1", errors="ignore")
    assert "/Im1 Do" in joined
    assert "/Im2 Do" in joined
    assert "W n" in joined

    # Image 1: local (0..40, 0..30) transformed by translate(50, 40) scale(1.5) ->
    # SVG rect x=[50, 110], y=[40, 85] on 150mm tall sheet ->
    # PDF points: width = 60 * MM_TO_PT, height = 45 * MM_TO_PT,
    # bottom-left = (50 * MM_TO_PT, (150 - 85) * MM_TO_PT)
    mm_to_pt = 72.0 / 25.4
    m_cm = re.search(
        r"([-+]?\d+\.\d+)\s+([-+]?\d+\.\d+)\s+([-+]?\d+\.\d+)\s+([-+]?\d+\.\d+)\s+"
        r"([-+]?\d+\.\d+)\s+([-+]?\d+\.\d+)\s+cm\s+/Im1\s+Do",
        joined,
    )
    assert m_cm is not None
    ma, mb, mc, md, me, mf = (float(m_cm.group(i)) for i in range(1, 7))
    assert ma == pytest.approx(60.0 * mm_to_pt, abs=0.05)
    assert mb == pytest.approx(0.0, abs=0.05)
    assert mc == pytest.approx(0.0, abs=0.05)
    assert md == pytest.approx(45.0 * mm_to_pt, abs=0.05)
    assert me == pytest.approx(50.0 * mm_to_pt, abs=0.05)
    assert mf == pytest.approx(65.0 * mm_to_pt, abs=0.05)

    # Also verify _render_svg_to_ax renders <image> artists cleanly
    fig, ax = plt.subplots()
    try:
        PDFRenderer._render_svg_to_ax(
            ax, svg_code, 200.0, 150.0, THEME_COLORS[Theme.DARK_BLUEPRINT]
        )
        assert len(ax.images) == 2
    finally:
        plt.close(fig)


def test_pdf_unicode_type3_symbols_hatches_and_speed(tmp_path: Path) -> None:
    """Verify native PDF renders Unicode engineering symbols, vector hatches, and runs quickly."""
    svg_code = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 297 210" width="297mm" height="210mm">
  <g fill="url(#hatch_aluminum_4_45)" class="section-hatch-fill" stroke="var(--line-visible)" stroke-width="0.5">
    <path d="M 30 30 L 90 30 L 90 80 L 30 80 Z"/>
  </g>
  <path d="M 120 60 A 20 15 0 1 0 160 60" class="line-visible" fill="none"/>
  <text x="140" y="120" class="dim-text" text-anchor="middle" font-size="3.5">⌀30 H7 ±0.05</text>
  <text x="140" y="140" class="view-title" font-size="4.0" font-weight="bold">⏥ 0.02 △</text>
  <text x="200" y="100" transform="rotate(-90 200 100)" class="dim-text" text-anchor="middle" font-size="3.2">47.5</text>
</svg>"""

    pdf_path = tmp_path / "features.pdf"
    t0 = time.perf_counter()
    PDFRenderer.render_sheets_to_pdf([svg_code], str(pdf_path), theme=Theme.ISO_WHITE)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    pdf_bytes = pdf_path.read_bytes()
    assert pdf_bytes.startswith(b"%PDF-1.4")
    # Vector tiling hatch pattern embedded
    assert b"/PatternType 1" in pdf_bytes
    # Dynamic Type 3 Unicode fallback font with ToUnicode CMap for ⌀ (2300), ⏥ (23E5), △ (25B3)
    assert b"/Subtype /Type3" in pdf_bytes
    assert b"/ToUnicode" in pdf_bytes
    streams = _decompress_pdf_streams(pdf_bytes)
    joined = b"\n".join(streams)
    assert b"<2300>" in joined
    assert b"<23E5>" in joined
    assert b"<25B3>" in joined

    # Also verify multi-sheet export speed on real golden examples if present
    examples_dir = Path(__file__).resolve().parent.parent / "examples" / "output"
    golden_svgs = sorted(examples_dir.glob("*.svg"))[:4]
    if golden_svgs:
        # Warm font metric cache
        multi_pdf = tmp_path / "multi_golden.pdf"
        t1 = time.perf_counter()
        PDFRenderer.render_sheets_to_pdf(
            [str(p) for p in golden_svgs],
            str(multi_pdf),
            theme=Theme.DARK_BLUEPRINT,
            bookmarks=True,
            booklet_title="BENCHMARK BOOKLET",
            sheet_titles=[p.stem for p in golden_svgs],
        )
        multi_ms = (time.perf_counter() - t1) * 1000.0
        assert multi_pdf.exists() and multi_pdf.stat().st_size > 2000
        # 4 complex A3/A2 engineering sheets should export well under 400 ms (typically ~15-35 ms)
        assert multi_ms < 400.0, f"Expected fast native PDF export (<400ms), got {multi_ms:.1f}ms"
    assert elapsed_ms < 500.0
    assert math.isfinite(elapsed_ms)
