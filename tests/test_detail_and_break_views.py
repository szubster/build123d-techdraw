"""Tests for DetailView and BreakView functionality."""

from pathlib import Path

import build123d as bd
import pytest

from build123d_techdraw import (
    BreakView,
    BuildSheet,
    DetailView,
    FrontView,
    LinearDimension,
    SectionView,
)
from build123d_techdraw.core.view import ViewType


def test_detail_view_circular_and_nested_annotations() -> None:
    """Verify DetailView clips geometry, scales up, renders callout circle on parent view, and accepts nested annotations."""
    shaft = bd.Cylinder(radius=10.0, height=120.0)

    with BuildSheet(sheet_size="A3") as sheet:
        with FrontView(shaft, center=(120.0, 100.0), scale=0.5) as front:
            pass

        with DetailView(
            parent_view=front,
            region_center=(160.0, 100.0),
            radius=12.0,
            scale=2.0,
            label="A",
            center=(280.0, 100.0),
            shape="circle",
        ) as detail:
            dim = LinearDimension(offset=-8.0, text="1.50 PITCH")

    assert detail.view_type == ViewType.DETAIL
    assert detail.scale == 2.0
    assert detail.center_x == 280.0
    assert detail.center_y == 100.0
    assert detail.width == pytest.approx(2.0 * 12.0 * 2.0, rel=1e-2)
    assert len(detail.annotations) == 1
    assert detail.annotations[0] is dim

    svg = sheet.to_svg()

    # 1. Parent view callout
    assert "detail-callout" in svg
    assert "detail-callout-circle" in svg
    assert ">A</text>" in svg

    # 2. Detail view bounding circle and clipPath
    assert "detail_A_280_100" in svg
    assert "clip_detail_A_280_100" in svg
    assert '<circle cx="280.00" cy="100.00"' in svg

    # 3. View labels
    assert "DETAIL A" in svg
    assert "SCALE 2:1" in svg

    # 4. Context-aware nested annotation inside DetailView
    assert "1.50 PITCH" in svg


def test_detail_view_rectangular() -> None:
    """Verify DetailView with rectangular shape clips and renders rectangular callout and boundary."""
    plate = bd.Box(80.0, 50.0, 10.0)

    with BuildSheet(sheet_size="A3") as sheet:
        with FrontView(plate, center=(100.0, 100.0), scale=1.0) as front:
            pass

        with DetailView(
            parent_view=front,
            region_center=(120.0, 100.0),
            radius=10.0,
            scale=4.0,
            label="B",
            center=(240.0, 100.0),
            shape="rect",
        ) as detail:
            pass

    assert detail.detail_shape == "rect"
    svg = sheet.to_svg()

    # Rectangular callout on parent
    assert '<rect x="' in svg and 'class="dim-line detail-callout-circle"' in svg
    # Rectangular boundary on detail view
    assert 'class="border-main"' in svg
    assert "DETAIL B" in svg


def test_break_view_via_add_break() -> None:
    """Verify add_break compresses visual span while maintaining true dimensional measurement."""
    long_beam = bd.Box(1000.0, 20.0, 20.0)

    with (
        BuildSheet(sheet_size="A3") as sheet,
        FrontView(long_beam, center=(200.0, 100.0), scale=1.0) as fv,
    ):
        fv.add_break(
            interval=(80.0, 920.0),
            axis="X",
            style="zigzag",
            gap=10.0,
        )
        dim = LinearDimension(offset=-15.0)

    # Original unbroken width was 1000 mm.
    # Excluded span: 920 - 80 = 840 mm.
    # Shift = 840 - gap(10) = 830 mm.
    # Broken visual width = 1000 - 830 = 170 mm.
    assert fv.unbroken_width == pytest.approx(1000.0, rel=1e-2)
    assert fv.width == pytest.approx(170.0, rel=1e-2)

    # True nominal measurement must be 1000.00
    assert dim.nominal == pytest.approx(1000.0, rel=1e-2)
    assert dim.text in ("1000", "1000.00")
    assert dim.has_break is True

    svg = sheet.to_svg()
    assert "break-line" in svg
    assert "break-jog" in svg
    assert "1000" in svg
    assert "broken-view" in svg


def test_break_view_class_wavy_style() -> None:
    """Verify BreakView builder class with wavy break line style."""
    long_rail = bd.Box(800.0, 30.0, 15.0)

    with (
        BuildSheet(sheet_size="A3") as sheet,
        BreakView(
            long_rail,
            interval=(100.0, 700.0),
            axis="X",
            style="wavy",
            gap=12.0,
            center=(220.0, 120.0),
            scale=0.5,
        ) as bv,
    ):
        dim = LinearDimension(offset=20.0)

    assert bv.unbroken_width == pytest.approx(400.0, rel=1e-2)
    # Excluded span in model: 600 mm. In paper (scale 0.5): 300 mm.
    # Shift = 300 - 12 = 288 mm.
    # New paper width = 400 - 288 = 112 mm.
    assert bv.width == pytest.approx(112.0, rel=1e-2)

    # True model nominal length
    assert dim.nominal == pytest.approx(800.0, rel=1e-2)
    assert dim.text in ("800", "800.00")

    svg = sheet.to_svg()
    assert "break-line" in svg
    assert "break-jog" in svg
    assert "800" in svg


def test_section_view_iso_hatch_patterns(tmp_path) -> None:
    """Verify SectionView supports standard ISO/ANSI hatch patterns and custom pitch/angle."""
    part = bd.Box(60.0, 40.0, 20.0)
    cutting_plane = bd.Plane(origin=(0.0, 0.0, 0.0), x_dir=(0, 1, 0))

    with BuildSheet(sheet_size="A3") as sheet:
        with SectionView(
            part,
            cutting_plane=cutting_plane,
            center=(100.0, 100.0),
            hatch="steel",
            hatch_pitch=3.0,
            hatch_angle=45.0,
        ):
            pass

        with SectionView(
            part,
            cutting_plane=cutting_plane,
            center=(220.0, 100.0),
            hatch="aluminum",
        ):
            pass

        with SectionView(
            part,
            cutting_plane=cutting_plane,
            center=(100.0, 200.0),
            hatch="brass",
        ):
            pass

        with SectionView(
            part,
            cutting_plane=cutting_plane,
            center=(220.0, 200.0),
            hatch="plastic",
        ):
            pass

    svg = sheet.to_svg()
    assert "hatch_steel" in svg
    assert "hatch_aluminum" in svg
    assert "hatch_brass" in svg
    assert "hatch_plastic" in svg
    assert 'fill="url(#hatch_steel_3_45)"' in svg
    assert 'fill="url(#hatch_aluminum_4_45)"' in svg
    assert 'fill="url(#hatch_brass_4_45)"' in svg
    assert 'fill="url(#hatch_plastic_4_45)"' in svg

    # Also test PDF export with hatch patterns
    pdf_path = str(tmp_path / "section_hatches.pdf")
    sheet.sheet.export_pdf(pdf_path)
    assert Path(pdf_path).exists()
    assert Path(pdf_path).stat().st_size > 0
