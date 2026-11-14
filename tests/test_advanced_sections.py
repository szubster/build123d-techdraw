"""Tests for Stepped/Offset Section Views, Aligned/Revolved Section Views, and multi-segment SectionCuttingLine."""

from pathlib import Path

import build123d as bd
import pytest

from build123d_techdraw import (
    AlignedSectionView,
    BuildDrawing,
    DrawingSheet,
    FrontView,
    OffsetSectionView,
    SectionCuttingLine,
    SheetSize,
    TopView,
    ViewType,
)


def _make_offset_block() -> bd.Solid:
    """Block (80 x 50 x 20) with a center bore at (0, 0) and an offset hole at (20, 15)."""
    with bd.BuildPart() as bp:
        bd.Box(80.0, 50.0, 20.0)
        with bd.Locations((0.0, 0.0, 0.0)):
            bd.Hole(radius=6.0)
        with bd.Locations((20.0, 15.0, 0.0)):
            bd.Hole(radius=4.0)
    return bp.part


def _make_bolt_flange() -> bd.Solid:
    """Circular flange (R=35, H=12) with center bore (R=8) and 3 bolt holes at 0, 120, 240 deg."""
    with bd.BuildPart() as bp:
        bd.Cylinder(radius=35.0, height=12.0)
        bd.Hole(radius=8.0)
        with bd.PolarLocations(radius=24.0, count=3, start_angle=180.0):
            bd.Hole(radius=3.5)
    return bp.part


def test_section_cutting_line_stepped_and_aligned() -> None:
    # Stepped/offset 4-point cutting line
    stepped = SectionCuttingLine(
        points=[(20.0, 100.0), (60.0, 100.0), (60.0, 130.0), (120.0, 130.0)],
        label="A",
        arrow_dir="up",
    )
    svg_step = stepped.to_svg()
    assert 'class="section-cutting-plane"' in svg_step
    assert ">A<" in svg_step
    # 3 segments -> 3 chain-thin centerlines and 6 thick segments (ends + corner bends)
    assert svg_step.count('class="line-center"') == 3
    assert svg_step.count('class="line-section-thick"') == 8  # 6 line segments + 2 arrow stems

    # Angled/aligned 3-point cutting line
    aligned = SectionCuttingLine(
        points=[(30.0, 100.0), (80.0, 100.0), (110.0, 60.0)],
        label="B",
        arrow_dir="up",
    )
    svg_aligned = aligned.to_svg()
    assert svg_aligned.count('class="line-center"') == 2
    assert ">B<" in svg_aligned

    # Validation
    with pytest.raises(ValueError, match="at least 2 points"):
        SectionCuttingLine(points=[(10.0, 10.0)])


def test_section_cutting_line_3d_model_points() -> None:
    part = _make_offset_block()
    with (
        BuildDrawing(SheetSize.A3_LANDSCAPE) as sheet,
        TopView(part, center=(120.0, 120.0), scale=1.0),
    ):
        cut = SectionCuttingLine(
            points=[(-40.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 15.0, 0.0), (40.0, 15.0, 0.0)],
            label="C",
        )
    assert len(cut.points) == 4
    # Jog in Y on TopView shifts sheet Y coordinate
    assert abs(cut.points[0][1] - cut.points[2][1]) == pytest.approx(15.0, abs=0.5)
    svg = sheet.to_svg()
    assert ">C<" in svg


def test_offset_section_view_builder_and_algebraic() -> None:
    part = _make_offset_block()

    # Builder mode
    with (
        BuildDrawing(SheetSize.A3_LANDSCAPE) as sheet,
        OffsetSectionView(
            part,
            base_plane=bd.Plane.XZ,
            points=[(-40.0, 0.0), (0.0, 0.0), (0.0, 15.0), (40.0, 15.0)],
            center=(150.0, 150.0),
            scale=1.0,
            view_label="SECTION A-A",
            hatch="aluminum",
        ) as sec_view,
    ):
        pass

    assert sec_view.view_type == ViewType.SECTION
    assert sec_view.width == pytest.approx(80.0, abs=1.0)
    assert sec_view.height == pytest.approx(20.0, abs=1.0)
    svg = sheet.to_svg()
    assert "SECTION A-A" in svg
    assert 'class="section-hatch-fill"' in svg

    # Algebraic mode + show_jog_lines=True
    alg_sheet = DrawingSheet(SheetSize.A3_LANDSCAPE)
    v_alg = OffsetSectionView(
        part,
        base_plane=bd.Plane.XZ,
        points=[(-40.0, 0.0), (0.0, 0.0), (0.0, 15.0), (40.0, 15.0)],
        center=(150.0, 150.0),
        scale=1.0,
        show_jog_lines=True,
        show_background=False,
    )
    alg_sheet += v_alg
    assert len(alg_sheet.views) == 1

    # Direct sheet helper
    alg_sheet.add_offset_section_view(
        part,
        base_plane=bd.Plane.XZ,
        points=[(-40.0, 0.0), (0.0, 0.0), (0.0, 15.0), (40.0, 15.0)],
        center=(250.0, 150.0),
    )
    assert len(alg_sheet.views) == 2

    with pytest.raises(ValueError, match="at least 2 path points"):
        OffsetSectionView(part, base_plane=bd.Plane.XZ, points=[(0.0, 0.0)])


def test_aligned_section_view_unfolds_bolt_pattern(tmp_path: Path) -> None:
    flange = _make_bolt_flange()

    # Unfold left leg (-X at 180 deg) and angled leg (at 60 deg: cos 60=0.5, sin 60=0.866)
    with BuildDrawing(SheetSize.A3_LANDSCAPE) as sheet:
        FrontView(flange, center=(120.0, 120.0), scale=1.0)
        with AlignedSectionView(
            flange,
            base_plane=bd.Plane.XZ,
            points=[(-35.0, 0.0), (0.0, 0.0), (17.5, -30.31)],
            center=(260.0, 120.0),
            scale=1.0,
            view_label="SECTION B-B",
        ) as aligned_view:
            pass

    assert aligned_view.view_type == ViewType.SECTION
    # Unfolded width should match full diameter (70mm)
    assert aligned_view.width == pytest.approx(70.0, abs=1.5)
    assert aligned_view.height == pytest.approx(12.0, abs=1.0)

    svg_path = tmp_path / "aligned.svg"
    pdf_path = tmp_path / "aligned.pdf"
    dxf_path = tmp_path / "aligned.dxf"
    sheet.export_svg(str(svg_path))
    sheet.export_pdf(str(pdf_path))
    sheet.export_dxf(str(dxf_path))

    assert svg_path.exists() and "SECTION B-B" in svg_path.read_text(encoding="utf-8")
    assert pdf_path.exists() and pdf_path.stat().st_size > 0
    assert dxf_path.exists() and dxf_path.stat().st_size > 0

    # Algebraic helper
    alg_sheet = DrawingSheet(SheetSize.A3_LANDSCAPE)
    alg_sheet.add_aligned_section_view(
        flange,
        base_plane=bd.Plane.XZ,
        points=[(-35.0, 0.0), (0.0, 0.0), (17.5, -30.31)],
        center=(150.0, 150.0),
    )
    assert len(alg_sheet.views) == 1
