"""Tests for ISO 6410-1 cosmetic threads across SVG, PDF, DXF, Builder/Algebraic, and recognizers."""

from __future__ import annotations

from pathlib import Path

import build123d as bd
import pytest
from ezdxf.filemanagement import readfile as dxf_readfile

from build123d_techdraw import (
    BuildDrawing,
    CosmeticThread,
    DrawingSheet,
    DXFRenderer,
    FrontView,
    IsometricView,
    PDFRenderer,
    SectionView,
    SheetSize,
    SVGRenderer,
    TopView,
    auto_cosmetic_threads,
    recognize_holes,
)


@pytest.fixture
def tapped_plate() -> bd.Compound:
    """A 40x30x12 mm plate with a central Ø5.0 mm (M6 tap drill) through hole."""
    with bd.BuildPart() as bp:
        bd.Box(40, 30, 12)
        with bd.Locations((0, 0, 0)):
            bd.Hole(radius=2.5)  # 5.0 mm tap drill for M6
    assert bp.part is not None
    return bp.part


def test_cosmetic_thread_builder_end_and_side_views(tapped_plate: bd.Compound) -> None:
    """Verify Builder mode auto-detection of End View (3/4 arc) vs Side View (dashed lines)."""
    cyl_face = tapped_plate.faces().filter_by(bd.GeomType.CYLINDER)[0]

    with BuildDrawing(SheetSize.A4_LANDSCAPE) as sheet:
        with TopView(tapped_plate, center=(90.0, 110.0), scale=2.0):
            ct_end = CosmeticThread(cyl_face, thread="M6")
        with FrontView(tapped_plate, center=(190.0, 110.0), scale=2.0):
            ct_side = CosmeticThread(cyl_face, thread="M6")
        with IsometricView(tapped_plate, center=(240.0, 160.0), scale=1.5):
            ct_iso = CosmeticThread(cyl_face, thread="M6")

    assert ct_end.view_mode == "end"
    assert ct_end.paper_radius == pytest.approx(6.0, abs=1e-3)  # (6.0 / 2) * 2.0
    assert ct_end.bounding_box_2d() is not None
    end_svg = ct_end.to_svg()
    assert "<path" in end_svg
    assert 'class="line-thread"' in end_svg
    assert 'stroke-width="0.25"' in end_svg
    assert "A 6.00 6.00 0 1 0" in end_svg

    assert ct_side.view_mode == "side"
    assert ct_side.bounding_box_2d() is not None
    side_svg = ct_side.to_svg()
    assert side_svg.count("<line") == 2
    assert 'class="line-thread line-thread-dashed"' in side_svg
    assert 'stroke-dasharray="2.0,1.2"' in side_svg

    assert ct_iso._skip is True
    assert ct_iso.to_svg() == ""
    assert ct_iso.bounding_box_2d() is None

    full_svg = sheet.to_svg()
    assert "line-thread" in full_svg


def test_cosmetic_thread_section_view_and_external_thread(tapped_plate: bd.Compound) -> None:
    """Verify solid thin lines in SectionView with depth cap, plus external thread minor-dia arc."""
    cyl_face = tapped_plate.faces().filter_by(bd.GeomType.CYLINDER)[0]

    with (
        BuildDrawing(SheetSize.A4_LANDSCAPE) as sheet,
        SectionView(
            tapped_plate,
            cutting_plane=bd.Plane.XZ,
            center=(140.0, 105.0),
            scale=2.0,
        ),
    ):
        ct_sec = CosmeticThread(cyl_face, thread="M6", depth=8.0)

    assert ct_sec.view_mode == "section"
    assert ct_sec.has_end_cap is True
    sec_svg = ct_sec.to_svg()
    assert sec_svg.count("<line") == 3  # 2 parallel + 1 end cap
    assert "line-thread-dashed" not in sec_svg
    assert 'class="line-thread"' in sec_svg

    # External thread on a shaft: 3/4 arc drawn at minor diameter
    ct_ext = CosmeticThread(
        center=(100.0, 100.0),
        thread="M10",
        thread_type="external",
        view_mode="end",
    )
    assert ct_ext.paper_radius == pytest.approx(8.5 / 2.0, abs=1e-3)
    assert "A 4.25 4.25 0 1 0" in ct_ext.to_svg()
    assert sheet is not None


def test_algebraic_mode_and_view_sheet_helpers(tapped_plate: bd.Compound) -> None:
    """Verify Algebraic mode (+=), view.cosmetic_thread(), and sheet.add_cosmetic_thread()."""
    cyl_face = tapped_plate.faces().filter_by(bd.GeomType.CYLINDER)[0]

    sheet = DrawingSheet(SheetSize.A4_LANDSCAPE)
    top = TopView(tapped_plate, scale=1.0).locate((100.0, 100.0))
    ct1 = CosmeticThread(cyl_face, thread="M6")
    top += ct1
    ct2 = top.cosmetic_thread(model_x=10.0, model_y=0.0, nominal_diameter=6.0, minor_diameter=5.0)
    ct3 = sheet.add_cosmetic_thread(center=(50.0, 50.0), thread="M8")
    sheet += top

    assert ct1 in top.annotations
    assert ct2 in top.annotations
    assert ct3 in sheet.annotations
    assert ct1.view_mode == "end"
    assert ct2.view_mode == "end"
    assert ct3.paper_radius == pytest.approx(4.0, abs=1e-3)


def test_recognize_holes_and_auto_cosmetic_threads(tapped_plate: bd.Compound) -> None:
    """Verify recognize_holes(threaded_diameters=...) and auto_cosmetic_threads()."""
    holes = recognize_holes(tapped_plate, threaded_diameters={5.0: "M6"})
    assert len(holes) == 1
    assert holes[0].is_threaded is True
    assert holes[0].thread_spec == "M6x1.0"
    assert holes[0].thread_source == "explicit"

    sheet = DrawingSheet(SheetSize.A4_LANDSCAPE)
    top = TopView(tapped_plate, scale=2.0).locate((100.0, 100.0))
    front = FrontView(tapped_plate, scale=2.0).locate((200.0, 100.0))
    sheet += [top, front]

    end_threads = auto_cosmetic_threads(top, threaded_diameters={5.0: "M6"})
    side_threads = front.auto_cosmetic_threads(infer_threads=True)

    assert len(end_threads) == 1
    assert end_threads[0].view_mode == "end"
    assert end_threads[0].paper_radius == pytest.approx(3.0 * 2.0, abs=1e-3)

    assert len(side_threads) == 1
    assert side_threads[0].view_mode == "side"


def test_cosmetic_thread_svg_pdf_dxf_export(tapped_plate: bd.Compound, tmp_path: Path) -> None:
    """Verify CosmeticThread exports cleanly to SVG, PDF, and DXF THREADS layer."""
    cyl_face = tapped_plate.faces().filter_by(bd.GeomType.CYLINDER)[0]

    with BuildDrawing(SheetSize.A4_LANDSCAPE) as sheet:
        with TopView(tapped_plate, center=(90.0, 110.0), scale=2.0):
            CosmeticThread(cyl_face, thread="M6")
        with FrontView(tapped_plate, center=(190.0, 110.0), scale=2.0):
            CosmeticThread(cyl_face, thread="M6")

    svg_path = tmp_path / "threads.svg"
    pdf_path = tmp_path / "threads.pdf"
    dxf_path = tmp_path / "threads.dxf"

    SVGRenderer.save(sheet, str(svg_path))
    PDFRenderer.render_sheets_to_pdf([sheet], str(pdf_path))
    DXFRenderer.export_sheet_to_dxf(sheet, str(dxf_path))

    assert svg_path.exists() and "line-thread" in svg_path.read_text(encoding="utf-8")
    assert pdf_path.exists() and pdf_path.stat().st_size > 500
    assert dxf_path.exists()

    doc = dxf_readfile(str(dxf_path))
    assert "THREADS" in doc.layers
    thread_entities = [e for e in doc.modelspace() if e.dxf.layer == "THREADS"]
    assert len(thread_entities) >= 3  # 1 end arc + 2 side lines
