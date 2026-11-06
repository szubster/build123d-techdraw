"""Unit and integration tests for multi-view declarative layouts and BOM callouts."""

import build123d as bd

from build123d_techdraw import (
    DrawingSheet,
    SheetSize,
    ViewType,
    add_assembly_bom_and_balloons,
    add_standard_multiview,
    generate_assembly_bom,
)


def test_standard_multiview_auto_scale_third_angle() -> None:
    part = bd.Box(100.0, 50.0, 30.0)
    sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)

    layout = add_standard_multiview(
        sheet=sheet,
        shape=part,
        scale="auto",
        spacing=20.0,
        show_hidden=True,
    )

    assert layout.front is not None
    assert layout.top is not None
    assert layout.right is not None
    assert layout.isometric is not None
    assert layout.scale > 0

    # In Third Angle projection:
    # Front and Top must align vertically (same X)
    assert abs(layout.front.center[0] - layout.top.center[0]) < 1e-4
    # Front and Right must align horizontally (same Y)
    assert abs(layout.front.center[1] - layout.right.center[1]) < 1e-4
    # Top view must be above Front view (lower Y in SVG)
    assert layout.top.center[1] < layout.front.center[1]
    # Right view must be to the right of Front view (greater X)
    assert layout.right.center[0] > layout.front.center[0]

    svg = sheet.to_svg()
    assert 'class="sheet-view"' in svg
    assert "FRONT" in svg
    assert "TOP" in svg
    assert "RIGHT" in svg
    assert "ISOMETRIC" in svg


def test_standard_multiview_first_angle_and_auto_dims() -> None:
    part = bd.Box(80.0, 40.0, 20.0)
    sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)

    layout = add_standard_multiview(
        sheet=sheet,
        shape=part,
        layout="first_angle",
        scale=1.0,
        auto_dimensions=True,
    )

    assert layout.front is not None
    assert layout.top is not None
    assert layout.scale == 1.0

    # In First Angle:
    # Top view is below Front view (greater Y in SVG)
    assert layout.top.center[1] > layout.front.center[1]
    # Overall dimensions were created
    assert len(sheet.annotations) >= 3

    svg = sheet.to_svg()
    assert 'class="dim-line"' in svg
    assert "80" in svg
    assert "20" in svg


def test_multiview_custom_views_subset() -> None:
    part = bd.Cylinder(radius=20.0, height=50.0)
    sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE)

    layout = add_standard_multiview(
        sheet=sheet,
        shape=part,
        views=[ViewType.FRONT, ViewType.TOP],
        scale=0.5,
    )

    assert layout.front is not None
    assert layout.top is not None
    assert layout.right is None
    assert layout.isometric is None
    assert len(layout.views) == 2


def test_assembly_bom_generation_and_balloons() -> None:
    b1 = bd.Box(60.0, 40.0, 10.0)
    b1.label = "Base Plate"

    p1 = bd.Cylinder(radius=5.0, height=25.0).move(bd.Location((15.0, 10.0, 15.0)))
    p1.label = "Guide Pin"

    p2 = bd.Cylinder(radius=5.0, height=25.0).move(bd.Location((-15.0, -10.0, 15.0)))
    p2.label = "Guide Pin"

    assembly = bd.Compound(children=[b1, p1, p2])

    bom = generate_assembly_bom(assembly)
    assert len(bom) == 2

    base_item = next(b for b in bom if b.description == "Base Plate")
    pin_item = next(b for b in bom if b.description == "Guide Pin")

    assert base_item.qty == 1
    assert pin_item.qty == 2

    # Add assembly to sheet with multiview and balloons
    sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    layout = add_standard_multiview(sheet=sheet, shape=assembly, scale="auto")
    assert layout.isometric is not None

    bom_res, balloons = add_assembly_bom_and_balloons(
        sheet=sheet,
        assembly=assembly,
        target_view=layout.isometric,
        balloon_distance=25.0,
    )

    assert len(bom_res) == 2
    assert len(balloons) == 2
    assert len(sheet.bom_items) == 2

    svg = sheet.to_svg()
    assert 'class="bom-table"' in svg
    assert "Base Plate" in svg
    assert "Guide Pin" in svg
    assert 'class="bom-balloon"' in svg
