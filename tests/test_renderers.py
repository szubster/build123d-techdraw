"""Unit tests for SVG, PDF, and DXF renderers."""

import os
import tempfile

import build123d as bd
import matplotlib.pyplot as plt
import pytest

from build123d_techdraw.core.booklet import DrawingBooklet
from build123d_techdraw.core.sheet import DrawingSheet, SheetSize
from build123d_techdraw.core.view import ViewType
from build123d_techdraw.renderers.dxf import DXFRenderer
from build123d_techdraw.renderers.pdf import MM_TO_PT, PDFRenderer
from build123d_techdraw.renderers.svg import SVGRenderer
from build123d_techdraw.renderers.theme import THEME_COLORS, Theme


def test_svg_dual_theming() -> None:
    box = bd.Box(30.0, 20.0, 10.0)
    sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE)
    sheet.add_view(box, view_type=ViewType.FRONT, center=(100.0, 100.0))

    # Dark Blueprint theme
    svg_dark = SVGRenderer.render_sheet(sheet, theme=Theme.DARK_BLUEPRINT)
    assert "theme-dark-blueprint" in svg_dark or "theme-iso-white" in svg_dark
    assert 'class="theme-dark-blueprint"' in svg_dark

    # Clean White ISO theme
    svg_white = SVGRenderer.render_sheet(sheet, theme=Theme.ISO_WHITE)
    assert 'class="theme-iso-white"' in svg_white


def test_pdf_export_and_booklet() -> None:
    box = bd.Box(40.0, 30.0, 20.0)
    sheet1 = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE)
    sheet1.add_view(box, view_type=ViewType.FRONT, center=(120.0, 100.0))

    sheet2 = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE)
    sheet2.add_view(box, view_type=ViewType.TOP, center=(120.0, 100.0))

    booklet = DrawingBooklet(title="TEST BOOKLET")
    booklet.add_sheet(sheet1)
    booklet.add_sheet(sheet2)

    with tempfile.TemporaryDirectory() as tmpdir:
        # Export booklet SVGs
        svg_files = booklet.export_svgs(tmpdir)
        assert len(svg_files) == 2
        for f in svg_files:
            assert os.path.exists(f)

        # Export multi-page PDF
        pdf_path = os.path.join(tmpdir, "drawing_booklet.pdf")
        booklet.export_pdf(pdf_path)
        assert os.path.exists(pdf_path)
        assert os.path.getsize(pdf_path) > 1000

        # Export single sheet PDF
        sheet_pdf = os.path.join(tmpdir, "single_sheet.pdf")
        sheet1.export_pdf(sheet_pdf)
        assert os.path.exists(sheet_pdf)
        assert os.path.getsize(sheet_pdf) > 1000


def test_dxf_export() -> None:
    rect = bd.Rectangle(50.0, 30.0)
    circle = bd.Circle(15.0)
    with tempfile.TemporaryDirectory() as tmpdir:
        dxf_path = os.path.join(tmpdir, "part.dxf")
        DXFRenderer.export_shape_to_dxf(rect, dxf_path)
        assert os.path.exists(dxf_path)
        assert os.path.getsize(dxf_path) > 500

        dxf_circle_path = os.path.join(tmpdir, "circle.dxf")
        DXFRenderer.export_shape_to_dxf(circle, dxf_circle_path)
        assert os.path.exists(dxf_circle_path)
        assert os.path.getsize(dxf_circle_path) > 500


def test_svg_renderer_save() -> None:
    sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE)
    with tempfile.TemporaryDirectory() as tmpdir:
        svg_path = os.path.join(tmpdir, "sheet.svg")
        SVGRenderer.save(sheet, svg_path)
        assert os.path.exists(svg_path)
        assert os.path.getsize(svg_path) > 500


def test_pdf_lineweight_calibration_stroke_guard_and_dim_knockout() -> None:
    """Verify MM_TO_PT lineweight scaling, fill stroke guard, and dimension text knockout."""
    svg_snippet = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100mm" height="100mm">
  <line x1="0" y1="10" x2="50" y2="10" class="line-visible" stroke-width="0.50"/>
  <line x1="0" y1="20" x2="50" y2="20" class="dim-line"/>
  <rect x="2" y="2" width="96" height="96" class="border-main" fill="none"/>
  <polygon points="10,10 15,12 15,8" fill="#38BDF8"/>
  <polygon points="20,20 25,22 25,18" class="dim-arrow"/>
  <line x1="0" y1="30" x2="40" y2="30" stroke="#FF0000" stroke-width="0.40"/>
  <line x1="0" y1="40" x2="40" y2="40"/>
  <text x="50" y="50" class="dim-text" font-size="3.2">50.0</text>
  <text x="50" y="70" class="view-title" font-size="4.0">FRONT</text>
</svg>"""
    fig, ax = plt.subplots()
    colors = THEME_COLORS[Theme.DARK_BLUEPRINT]
    try:
        PDFRenderer._render_svg_to_ax(ax, svg_snippet, 100.0, 100.0, colors)
        # patches[0] is canvas background rect
        user_patches = ax.patches[1:]
        assert len(user_patches) == 7

        # 1. Contours (0.50mm -> 1.42pt)
        assert user_patches[0].get_linewidth() == pytest.approx(0.50 * MM_TO_PT)
        # 2. Dimensions/Leaders (0.25mm -> 0.71pt)
        assert user_patches[1].get_linewidth() == pytest.approx(0.25 * MM_TO_PT)
        # 3. Borders (0.70mm -> 1.98pt)
        assert user_patches[2].get_linewidth() == pytest.approx(0.70 * MM_TO_PT)
        # 4. Filled shape without explicit stroke -> no accidental stroke (alpha == 0.0)
        assert user_patches[3].get_edgecolor()[3] == pytest.approx(0.0)
        # 5. dim-arrow -> solid fill without stroke outline
        assert user_patches[4].get_edgecolor()[3] == pytest.approx(0.0)
        # 6. Explicit inline stroke -> scaled by MM_TO_PT
        assert user_patches[5].get_linewidth() == pytest.approx(0.40 * MM_TO_PT)
        # 7. Unfilled shape without explicit stroke -> fallback 0.35 * MM_TO_PT
        assert user_patches[6].get_linewidth() == pytest.approx(0.35 * MM_TO_PT)

        # Dimension text background knockout vs regular text
        dim_txt = next(t for t in ax.texts if t.get_text() == "50.0")
        title_txt = next(t for t in ax.texts if t.get_text() == "FRONT")
        bbox_patch = dim_txt.get_bbox_patch()
        assert bbox_patch is not None
        assert bbox_patch.get_edgecolor()[3] == pytest.approx(0.0)
        assert title_txt.get_bbox_patch() is None
    finally:
        plt.close(fig)
