"""Unit tests for GD&T frames, datum tags, surface finish, centerlines, and callouts."""

import build123d as bd

from build123d_techdraw.annotations.callouts import (
    BOMBalloon,
    LeaderNote,
    SectionCuttingLine,
)
from build123d_techdraw.annotations.centerlines import (
    BoltCircle,
    Centerline,
    CenterMark,
)
from build123d_techdraw.annotations.gdt import (
    DatumFeatureSymbol,
    FeatureControlFrame,
    GDTCharacteristic,
)
from build123d_techdraw.annotations.surface import (
    MaterialRemoval,
    SurfaceFinishSymbol,
)
from build123d_techdraw.core.sheet import DrawingSheet, SheetSize
from build123d_techdraw.core.view import ViewType


def test_gdt_feature_control_frames() -> None:
    # Position with 3 datums
    fcf = FeatureControlFrame(
        position=(50.0, 50.0),
        characteristic=GDTCharacteristic.POSITION,
        tolerance_value="⌀0.05 Ⓜ",
        datum_a="A",
        datum_b="B",
        datum_c="C",
    )
    svg = fcf.to_svg()
    assert "⌖" in svg
    assert "⌀0.05 Ⓜ" in svg
    assert "A" in svg
    assert "B" in svg
    assert "C" in svg

    # Perpendicularity
    fcf_perp = FeatureControlFrame(
        position=(100.0, 50.0),
        characteristic=GDTCharacteristic.PERPENDICULARITY,
        tolerance_value="0.02",
        datum_a="A",
    )
    svg_perp = fcf_perp.to_svg()
    assert "⟂" in svg_perp
    assert "0.02" in svg_perp


def test_datum_feature_symbols() -> None:
    for direction in ["up", "down", "left", "right"]:
        sym = DatumFeatureSymbol(target=(60.0, 60.0), label="B", direction=direction)
        svg = sym.to_svg()
        assert 'class="datum-symbol"' in svg
        assert "B" in svg


def test_surface_finish_symbols() -> None:
    sym_req = SurfaceFinishSymbol(
        position=(80.0, 80.0),
        ra_value="Ra 1.6",
        material_removal=MaterialRemoval.REQUIRED,
        process="GROUND",
    )
    svg_req = sym_req.to_svg()
    assert "Ra 1.6" in svg_req
    assert "GROUND" in svg_req
    assert 'class="surface-finish-symbol"' in svg_req

    sym_proh = SurfaceFinishSymbol(
        position=(120.0, 80.0),
        ra_value="Ra 3.2",
        material_removal=MaterialRemoval.PROHIBITED,
    )
    svg_proh = sym_proh.to_svg()
    assert "Ra 3.2" in svg_proh
    assert "<circle" in svg_proh


def test_centerlines_and_bolt_circles() -> None:
    cl = Centerline(start=(10.0, 10.0), end=(50.0, 10.0))
    assert 'class="line-center"' in cl.to_svg()

    cm = CenterMark(center=(40.0, 40.0), size=8.0)
    assert 'class="centermark"' in cm.to_svg()

    bc = BoltCircle(center=(100.0, 100.0), diameter=60.0, num_holes=6)
    svg_bc = bc.to_svg()
    assert 'class="bolt-circle"' in svg_bc
    assert svg_bc.count("<line") == 12  # 6 holes * 2 lines per hole cross


def test_callouts_and_balloons() -> None:
    # Leader note with dot terminator
    note_dot = LeaderNote(
        target=(50.0, 50.0),
        lines=["2x M6x1.0 THRU", "C'BORE ⌀10.5 x 6 DEEP"],
        terminator="dot",
    )
    svg_dot = note_dot.to_svg()
    assert "2x M6x1.0 THRU" in svg_dot
    assert "leader-dot" in svg_dot

    # Leader note with arrow terminator
    note_arr = LeaderNote(
        target=(80.0, 80.0),
        lines=["DATUM TARGET"],
        terminator="arrow",
    )
    svg_arr = note_arr.to_svg()
    assert 'class="dim-arrow"' in svg_arr

    # BOM balloon
    balloon = BOMBalloon(item_no=3, target=(40.0, 40.0), balloon_center=(60.0, 25.0))
    svg_b = balloon.to_svg()
    assert 'class="bom-balloon"' in svg_b
    assert ">3<" in svg_b

    # Section cutting line
    cut = SectionCuttingLine(start=(30.0, 100.0), end=(120.0, 100.0), label="B", arrow_dir="up")
    svg_cut = cut.to_svg()
    assert 'class="section-cutting-plane"' in svg_cut
    assert ">B<" in svg_cut


def test_view_anchored_annotations_integration() -> None:
    part = bd.Cylinder(radius=25.0, height=30.0)
    sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    view = sheet.add_view(part, view_type=ViewType.FRONT, center=(150.0, 150.0), scale=1.0)

    # Attach annotations via view methods
    view.centermark(0.0, 0.0)
    view.centerline_horizontal(0.0)
    view.centerline_vertical(0.0)
    view.bolt_circle((0.0, 0.0), diameter_model=35.0, num_holes=4)
    view.leader(12.0, 10.0, "CHAMFER 1.0 x 45°")
    view.balloon(1, -20.0, 0.0, 100.0, 120.0)
    view.add_gdt_frame(0.0, -20.0, GDTCharacteristic.POSITION, "⌀0.02", datum_a="A")
    view.add_datum_symbol(0.0, -15.0, label="A")
    view.add_surface_finish(15.0, 15.0, ra_value="Ra 0.8")

    assert len(sheet.annotations) == 9

    svg = sheet.to_svg()
    assert "CHAMFER 1.0 x 45°" in svg
    assert "Ra 0.8" in svg
    assert "⌀0.02" in svg
    assert 'class="centermark"' in svg
    assert 'class="bolt-circle"' in svg


def test_projection_cone_symbols() -> None:
    from build123d_techdraw.annotations.symbols import render_projection_cone_svg

    svg_third = render_projection_cone_svg((100.0, 100.0), third_angle=True)
    assert 'class="projection-symbol"' in svg_third
    assert "<polygon" in svg_third

    svg_first = render_projection_cone_svg((100.0, 100.0), third_angle=False)
    assert 'class="projection-symbol"' in svg_first
    assert "<polygon" in svg_first


def test_sheet_direct_annotation_methods() -> None:
    sheet = DrawingSheet(sheet_size=SheetSize.A4_PORTRAIT)
    sheet.add_dimension((20.0, 30.0), (80.0, 30.0), offset=10.0)
    sheet.add_radial_dimension((50.0, 50.0), radius=15.0)
    sheet.add_diameter_dimension((50.0, 50.0), diameter=30.0)
    sheet.add_centerline((10.0, 50.0), (90.0, 50.0))
    sheet.add_centermark((50.0, 50.0))
    sheet.add_bolt_circle((50.0, 50.0), diameter=40.0, num_holes=4)
    sheet.add_leader((50.0, 50.0), ["NOTE 1"])
    sheet.add_balloon(1, target=(50.0, 50.0), balloon_center=(70.0, 40.0))
    sheet.add_gdt_frame((50.0, 60.0), GDTCharacteristic.FLATNESS, "0.01")
    sheet.add_datum_symbol((50.0, 70.0), label="A")
    sheet.add_surface_finish((50.0, 80.0), ra_value="Ra 1.6")
    sheet.add_section_cut((20.0, 100.0), (80.0, 100.0), label="A")

    assert len(sheet.annotations) == 12
    svg = sheet.to_svg()
    assert "NOTE 1" in svg
    assert "Ra 1.6" in svg
    assert "0.01" in svg
