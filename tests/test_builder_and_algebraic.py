"""Comprehensive test suite for build123d-style Builder and Algebraic modes."""

import tempfile
from pathlib import Path

import build123d as bd
import pytest

from build123d_techdraw import (
    AssemblyBOM,
    BuildDrawing,
    BuildSheet,
    Centerline,
    CenterMark,
    DatumFeature,
    DiameterDimension,
    DrawingSheet,
    FeatureControlFrame,
    FrontView,
    GDTCharacteristic,
    GeneralNotes,
    IsometricView,
    LeaderNote,
    LinearDimension,
    RadialDimension,
    Revision,
    RightView,
    SectionView,
    SheetSize,
    StandardMultiview,
    SurfaceFinish,
    TitleBlock,
    TopView,
)


@pytest.fixture
def bracket_part() -> bd.Compound:
    """Create a stepped mechanical bracket with a cylindrical hole."""
    base = bd.Box(80.0, 50.0, 15.0)
    flange = bd.Box(30.0, 50.0, 40.0).move(bd.Location((-25.0, 0.0, 20.0)))
    part = base + flange
    hole = bd.Cylinder(radius=5.5, height=60.0).move(bd.Location((-25.0, 0.0, 20.0)))
    return part - hole


@pytest.fixture
def simple_assembly() -> bd.Compound:
    """Create a two-component assembly with labels."""
    b1 = bd.Box(50, 40, 10)
    b1.label = "Baseplate"
    p1 = bd.Cylinder(4, 20).move(bd.Location((10, 10, 15)))
    p1.label = "Pin"
    return bd.Compound(children=[b1, p1])


def test_builder_mode_complete_flow(bracket_part: bd.Compound) -> None:
    """Test full Builder mode context manager workflow with topological annotations."""
    with BuildSheet(
        sheet_size=SheetSize.A3_LANDSCAPE,
        title="BRACKET BLUEPRINT",
        drawing_no="DWG-BRK-001",
        rev="A",
        author="ENGINEER",
        material="AL 6061-T6",
    ) as builder:
        GeneralNotes(
            [
                "1. ALL DIMENSIONS IN MILLIMETERS.",
                "2. GENERAL TOLERANCES ISO 2768-mK.",
            ]
        )
        Revision(rev="B", description="PRODUCTION RELEASE", date="2026-11-07")

        # Front View with direct topological annotations
        with FrontView(bracket_part, center=(130.0, 150.0), scale=1.0) as front:
            # Dimension a 3D edge directly
            edge_x = bracket_part.edges().filter_by(bd.Axis.X).sort_by(bd.Axis.Y)[0]
            d1 = LinearDimension(edge_x, offset=-15.0, tolerance="h9")
            assert d1 in front.annotations
            assert d1 in builder.annotations

            # Centermark on cylinder face
            cyl_face = [f for f in bracket_part.faces() if f.geom_type == bd.GeomType.CYLINDER][0]
            cm = CenterMark(cyl_face)
            assert cm in front.annotations

            # GD&T Datum and Feature Control Frame
            df = DatumFeature(label="A", target=bracket_part.faces().sort_by(bd.Axis.Z)[0])
            fcf = FeatureControlFrame(
                characteristic=GDTCharacteristic.FLATNESS,
                tolerance_value="0.05",
                datum_refs=["A"],
                target=bracket_part.faces().sort_by(bd.Axis.Z)[-1],
                leader_offset=(15.0, -10.0),
            )
            assert df in front.annotations
            assert fcf in front.annotations

            # Surface finish and Leader note
            sf = SurfaceFinish(target=bracket_part.faces().sort_by(bd.Axis.Y)[0], ra_value="Ra 1.6")
            note = LeaderNote("M11 CLEARANCE HOLE", target=cyl_face, leader_offset=(20.0, 15.0))
            assert sf in front.annotations
            assert note in front.annotations

        # Top View with two-vertex dimension
        with TopView(bracket_part, center=(130.0, 230.0), scale=1.0) as top:
            v_min = bracket_part.vertices().sort_by(bd.Axis.X)[0]
            v_max = bracket_part.vertices().sort_by(bd.Axis.X)[-1]
            d2 = LinearDimension(v_min, v_max, offset=12.0, direction="horizontal")
            assert d2 in top.annotations

        # Isometric View
        with IsometricView(bracket_part, center=(320.0, 200.0), scale=0.8):
            pass

        # Section View
        cutting_plane = bd.Plane(origin=(-25.0, 0.0, 0.0), x_dir=(0, 1, 0))
        with SectionView(
            bracket_part, cutting_plane=cutting_plane, center=(320.0, 100.0), scale=0.8
        ):
            pass

    # Verify sheet state
    sheet = builder.sheet
    assert len(sheet.views) == 4
    assert sheet.title_info.drawing_title == "BRACKET BLUEPRINT"
    assert sheet.title_info.drawing_no == "DWG-BRK-001"
    assert len(sheet.notes) == 2
    assert len(sheet.revision_rows) == 2  # initial + Rev B

    # Render SVG and verify elements are present
    svg = builder.to_svg()
    assert "BRACKET BLUEPRINT" in svg
    assert "DWG-BRK-001" in svg
    assert "PRODUCTION RELEASE" in svg
    assert "h9" in svg
    assert "Ra 1.6" in svg
    assert "M11 CLEARANCE HOLE" in svg


def test_build_drawing_alias(bracket_part: bd.Compound) -> None:
    """Test BuildDrawing alias works identically to BuildSheet."""
    with BuildDrawing() as dwg:
        TitleBlock(title="ALIAS TEST", drawing_no="DWG-ALT-001")
        with FrontView(bracket_part, center=(120, 120)):
            pass
    assert dwg.title_info.drawing_title == "ALIAS TEST"
    assert len(dwg.views) == 1


def test_algebraic_mode_composition(bracket_part: bd.Compound) -> None:
    """Test full Algebraic mode workflow with operator overloading and chaining."""
    sheet = DrawingSheet(SheetSize.A3_LANDSCAPE)

    # Title block and notes composition via operators
    sheet += TitleBlock(title="ALGEBRAIC BRACKET", drawing_no="ALG-100", author="SZUBA")
    sheet += GeneralNotes(["NOTE A", "NOTE B"])
    sheet += Revision(rev="C", description="DESIGN CHANGE")

    assert sheet.title_info.drawing_title == "ALGEBRAIC BRACKET"
    assert sheet.title_info.author == "SZUBA"
    assert len(sheet.notes) == 2

    # Create Views explicitly
    front = FrontView(bracket_part, scale=1.0).locate((120.0, 140.0))
    top = TopView(bracket_part, scale=1.0).align_to(front, direction="above", spacing=25.0)
    right = RightView(bracket_part, scale=1.0).align_to(front, direction="right", spacing=30.0)
    iso = IsometricView(bracket_part, scale=0.8).align_to(right, direction="above", spacing=20.0)

    # Verify align_to math
    assert top.center_x == pytest.approx(front.center_x)
    assert top.min_y == pytest.approx(front.min_y - 25.0 - top.height, abs=1.0)
    assert right.center_y == pytest.approx(front.center_y)
    assert right.min_x == pytest.approx(front.max_x + 30.0, abs=1.0)

    # Test scale_to chaining
    iso.scale_to(0.75)
    assert iso.scale == 0.75

    # Annotations via operators
    edge_z = bracket_part.edges().filter_by(bd.Axis.Z).sort_by(bd.Axis.X)[0]
    dim1 = LinearDimension(edge_z, offset=-15.0)
    cyl_face = [f for f in bracket_part.faces() if f.geom_type == bd.GeomType.CYLINDER][0]
    cm = CenterMark(cyl_face)

    # Add single annotation and list of annotations
    front += dim1
    front += [cm, DatumFeature("A", bracket_part.faces().sort_by(bd.Axis.Z)[0])]
    assert len(front.annotations) == 3

    # Compose into sheet via list
    sheet += [front, top, right, iso]
    assert len(sheet.views) == 4
    # Annotations on front view should now be in sheet.annotations
    assert dim1 in sheet.annotations
    assert cm in sheet.annotations

    # Export to temp file
    with tempfile.TemporaryDirectory() as td:
        svg_path = str(Path(td) / "test.svg")
        pdf_path = str(Path(td) / "test.pdf")
        sheet.export_svg(svg_path)
        sheet.export_pdf(pdf_path)
        assert Path(svg_path).exists()
        assert Path(pdf_path).exists()


def test_standard_multiview_builder_mode(bracket_part: bd.Compound) -> None:
    """Test StandardMultiview inside BuildSheet context."""
    with (
        BuildSheet(SheetSize.A3_LANDSCAPE) as sheet,
        StandardMultiview(bracket_part, scale="auto") as mv,
    ):
        assert mv.front is not None
        assert mv.top is not None
        assert mv.isometric is not None
        with mv.front:
            d = LinearDimension(
                bracket_part.edges().filter_by(bd.Axis.X)[0],
                offset=15.0,
            )
            assert d in mv.front.annotations
            assert d in sheet.annotations

    assert len(sheet.views) >= 3


def test_standard_multiview_algebraic_mode(bracket_part: bd.Compound) -> None:
    """Test StandardMultiview used algebraically with sheet += mv."""
    sheet = DrawingSheet(SheetSize.A3_LANDSCAPE)
    mv = StandardMultiview(bracket_part, scale="auto")
    assert mv.front is not None

    d = LinearDimension(bracket_part.edges().filter_by(bd.Axis.X)[0], offset=12.0)
    mv.front += d

    sheet += mv
    assert len(sheet.views) >= 3
    assert d in sheet.annotations


def test_assembly_bom_both_modes(simple_assembly: bd.Compound) -> None:
    """Test AssemblyBOM in both Builder and Algebraic modes."""
    # 1. Builder mode
    with (
        BuildSheet(SheetSize.A3_LANDSCAPE),
        StandardMultiview(simple_assembly, scale="auto") as mv,
    ):
        bom = AssemblyBOM(simple_assembly, target_view=mv.isometric, balloon_distance=20.0)
        assert len(bom.bom_items) == 2
        assert len(bom.balloons) == 2

    # 2. Algebraic mode
    sheet_a = DrawingSheet(SheetSize.A3_LANDSCAPE)
    mv_a = StandardMultiview(simple_assembly, scale="auto")
    bom_a = AssemblyBOM(simple_assembly, target_view=mv_a.isometric, balloon_distance=20.0)
    sheet_a += mv_a
    sheet_a += bom_a
    assert len(sheet_a.bom_items) == 2


def test_radial_and_diameter_dimensions_topological(bracket_part: bd.Compound) -> None:
    """Test RadialDimension and DiameterDimension with circular edges and cylindrical faces."""
    circ_edge = [e for e in bracket_part.edges() if e.geom_type == bd.GeomType.CIRCLE][0]
    cyl_face = [f for f in bracket_part.faces() if f.geom_type == bd.GeomType.CYLINDER][0]

    sheet = DrawingSheet()
    top = TopView(bracket_part, scale=1.0)
    sheet += top

    # Diameter Dimension on 3D circle
    dim_dia = DiameterDimension(circ_edge)
    top += dim_dia
    assert "⌀11" in str(dim_dia.text)

    # Radial Dimension on cylindrical face
    dim_rad = RadialDimension(cyl_face)
    top += dim_rad
    assert "R5.5" in str(dim_rad.text)

    # Centerline on cylinder
    cl = Centerline(cyl_face)
    front = FrontView(bracket_part, scale=1.0)
    front += cl
    sheet += front
    assert cl.resolved
