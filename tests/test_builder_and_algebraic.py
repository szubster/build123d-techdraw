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


def test_context_aware_annotations_and_string_sheet_size(bracket_part: bd.Compound) -> None:
    """Test exact ergonomic workflow: string sheet_size and context-aware annotations."""
    from build123d_techdraw import BOMBalloon

    hole_center = (100.0, 100.0)
    with BuildSheet(sheet_size="A3") as sheet:  # noqa: SIM117
        with FrontView(bracket_part, center=(100.0, 100.0)) as fv:
            dim = LinearDimension(offset=-10.0, text="127.00")
            cm = CenterMark(hole_center)
            balloon = BOMBalloon(balloon_no=1, position=(20.0, 20.0))

    assert sheet.width_mm == 420.0
    assert sheet.height_mm == 297.0
    assert dim in fv.annotations and dim in sheet.annotations
    assert cm in fv.annotations and cm in sheet.annotations
    assert balloon in fv.annotations and balloon in sheet.annotations
    assert balloon.balloon_no == 1
    assert balloon.position == (20.0, 20.0)

    svg = sheet.to_svg()
    assert "127.00" in svg
    assert 'class="centermark"' in svg
    assert 'class="bom-balloon"' in svg


def test_flexible_sheet_size_coercion() -> None:
    """Test case-insensitive string and enum coercion in DrawingSheet and BuildSheet."""
    s_a3 = DrawingSheet(sheet_size="a3")
    assert (s_a3.width_mm, s_a3.height_mm) == (420.0, 297.0)

    s_a4_l = DrawingSheet(sheet_size="A4_LANDSCAPE")
    assert (s_a4_l.width_mm, s_a4_l.height_mm) == (297.0, 210.0)

    s_a3_p = DrawingSheet(sheet_size="a3-portrait")
    assert (s_a3_p.width_mm, s_a3_p.height_mm) == (297.0, 420.0)

    s_letter = BuildSheet(sheet_size="LETTER")
    assert (s_letter.width_mm, s_letter.height_mm) == (279.4, 215.9)

    s_enum_short = BuildSheet(sheet_size=SheetSize.A3)
    assert (s_enum_short.width_mm, s_enum_short.height_mm) == (420.0, 297.0)

    with pytest.raises(ValueError, match="Unknown sheet_size"):
        DrawingSheet(sheet_size="INVALID_PAPER")


def test_raw_svg_and_table_components() -> None:
    """Test first-class RawSVG and Table / Schedule in both Builder and Algebraic modes."""
    from build123d_techdraw import RawSVG, Schedule, Table, Theme

    # Builder mode
    with BuildSheet(sheet_size="A3") as sheet:
        raw = RawSVG(
            '<circle cx="10" cy="10" r="5" class="schematic-node"/>', position=(40.0, 50.0)
        )
        tbl = Table(
            headers=["PIN", "SIGNAL", "AWG"],
            rows=[
                [1, "24V_DC", "18"],
                [2, "GND", "18"],
                [3, "CAN_H", "22"],
            ],
            position=(40.0, 80.0),
            col_widths=[15.0, 35.0, 15.0],
            alignments=["center", "left", "right"],
            title="J1 TERMINAL PINOUT",
        )

    assert raw in sheet.annotations
    assert tbl in sheet.annotations
    assert tbl.width == pytest.approx(65.0)
    assert tbl.height > 0.0

    svg_dark = sheet.to_svg(theme=Theme.DARK_BLUEPRINT)
    assert "schematic-node" in svg_dark
    assert "translate(40.00, 50.00)" in svg_dark
    assert "J1 TERMINAL PINOUT" in svg_dark
    assert "24V_DC" in svg_dark
    assert 'class="table-row-alt"' in svg_dark

    # Algebraic mode with Schedule alias and auto-calculated column widths
    alg_sheet = DrawingSheet("A4")
    alg_sheet += RawSVG("<rect width='10' height='10'/>", position=(20.0, 20.0))
    alg_sheet += Schedule(
        headers=["PART", "QTY"],
        rows=[["M5x12 SHCS", 8]],
        position=(20.0, 40.0),
    )
    svg_white = alg_sheet.to_svg(theme=Theme.ISO_WHITE)
    assert "M5x12 SHCS" in svg_white
    assert 'class="theme-iso-white"' in svg_white


def test_title_block_explicit_line_breaks_subtitle_and_custom_fields() -> None:
    """Test explicit \n line breaks, consistent font size, subtitle, and custom fields."""
    with BuildSheet(sheet_size="A3") as sheet:
        TitleBlock(
            title="DUAL-AXIS ACTUATOR\nPRIMARY HOUSING",
            subtitle="MACHINED SUB-ASSEMBLY",
            drawing_no="ACT-100",
            fields={"TOLERANCES": "ISO 2768-mK", "PROJECT": "MONITOR ARM"},
        )

    assert sheet.title_info.project == "MONITOR ARM"
    assert sheet.title_info.subtitle == "MACHINED SUB-ASSEMBLY"
    assert sheet.title_info.fields["TOLERANCES"] == "ISO 2768-mK"

    svg = sheet.to_svg()
    assert "DUAL-AXIS ACTUATOR" in svg
    assert "PRIMARY HOUSING" in svg
    assert "MACHINED SUB-ASSEMBLY" in svg
    assert "MONITOR ARM" in svg
    assert "TOLERANCES" in svg
    assert "ISO 2768-mK" in svg
    # Both title lines must share identical font-size="2.4" (not 2.9 then 2.7)
    assert svg.count('class="tb-title" font-size="2.4"') == 2


def test_builder_mode_align_to_in_view_constructors(bracket_part: bd.Compound) -> None:
    """Test seamless align_to=(other, direction, spacing) directly in Builder view constructors."""
    with BuildSheet(sheet_size="A3") as sheet:
        with FrontView(bracket_part, center=(150.0, 160.0)) as front:
            pass
        with TopView(bracket_part, align_to=(front, "above", 25.0)) as top:
            # Inside context block, top is already aligned!
            LinearDimension(offset=-8.0)
        with IsometricView(bracket_part, align_to=(front, "right", 40.0)) as iso:
            pass

    assert len(sheet.views) == 3
    assert top.center_x == pytest.approx(front.center_x)
    assert top.max_y == pytest.approx(front.min_y - 25.0, abs=1e-2)
    assert iso.center_y == pytest.approx(front.center_y)
    assert iso.min_x == pytest.approx(front.max_x + 40.0, abs=1e-2)


def test_hole_thread_callouts_and_tolerance_helpers(bracket_part: bd.Compound) -> None:
    """Test HoleCallout, ThreadCallout, and Tolerance formatting helpers."""
    from build123d_techdraw import HoleCallout, ThreadCallout, Tolerance

    with BuildSheet(sheet_size="A3") as sheet:  # noqa: SIM117
        with FrontView(bracket_part, center=(150.0, 150.0)) as fv:
            hc = HoleCallout(
                center=(140.0, 140.0),
                diameter=5.5,
                depth=None,
                countersink_dia=10.0,
                countersink_angle=90.0,
            )
            tc = ThreadCallout(
                center=(160.0, 140.0),
                thread="M5x0.8",
                depth="THRU",
                class_fit="6H",
            )
            dim_bilat = LinearDimension(
                offset=-12.0,
                nominal=127.0,
                tolerance=Tolerance.bilateral(+0.2, -0.1),
            )
            dim_fit = LinearDimension(
                offset=12.0,
                nominal=50.0,
                tolerance=Tolerance.fit("H7"),
            )

    assert hc.text == "⌀5.5 THRU ⌵ ⌀10 × 90°"
    assert tc.text == "M5x0.8 - 6H THRU"
    assert dim_bilat.text == "127"
    assert dim_bilat.tolerance == "+0.2 / -0.1"
    assert dim_fit.tolerance == "H7"
    assert hc in fv.annotations and tc in fv.annotations

    svg = sheet.to_svg()
    assert "⌀5.5 THRU ⌵ ⌀10 × 90°" in svg
    assert "M5x0.8 - 6H THRU" in svg
    assert "127 +0.2 / -0.1" in svg
    assert "50 H7" in svg


def test_booklet_auto_pagination(bracket_part: bd.Compound, tmp_path: Path) -> None:
    """Test DrawingBooklet automatic sheet_no and total_sheets pagination."""
    from build123d_techdraw import DrawingBooklet

    sheets = []
    for i in range(3):
        with BuildSheet(sheet_size="A4", title=f"PART {i + 1}", drawing_no=f"DWG-{i + 1:03d}") as s:
            FrontView(bracket_part, center=(120.0, 100.0), scale=0.8)
        sheets.append(s.sheet)

    booklet = DrawingBooklet(title="AUTO PAGINATED PACKAGE", sheets=sheets)
    svg_paths = booklet.export_svgs(str(tmp_path / "svgs"))
    pdf_path = str(tmp_path / "booklet.pdf")
    booklet.export_pdf(pdf_path)

    for idx, sheet_obj in enumerate(sheets):
        assert sheet_obj.title_info.sheet_no == f"{idx + 1}"
        assert sheet_obj.title_info.total_sheets == "3"
        svg_text = Path(svg_paths[idx]).read_text(encoding="utf-8")
        assert f">{idx + 1} OF 3<" in svg_text

    assert Path(pdf_path).exists()
