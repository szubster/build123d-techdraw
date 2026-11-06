"""Unit tests for SVG, PDF, and DXF renderers."""

import os
import tempfile

import build123d as bd

from build123d_techdraw.core.booklet import DrawingBooklet
from build123d_techdraw.core.sheet import DrawingSheet, SheetSize
from build123d_techdraw.core.view import ViewType
from build123d_techdraw.renderers.dxf import DXFRenderer
from build123d_techdraw.renderers.svg import SVGRenderer
from build123d_techdraw.renderers.theme import Theme


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
