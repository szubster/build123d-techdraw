"""Unit and integration tests for multi-view declarative layouts and BOM callouts."""

from typing import Any

import build123d as bd
import pytest

from build123d_techdraw import (
    DrawingSheet,
    PartInfo,
    SheetSize,
    ViewType,
    add_assembly_bom_and_balloons,
    add_standard_multiview,
    generate_assembly_bom,
    get_part_info,
    set_part_info,
)
from build123d_techdraw.layout.multiview import projected_view_extent


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


def test_auto_centerlines_and_exact_dimensions() -> None:
    part = bd.Box(100.0, 50.0, 30.0)
    pin = bd.Cylinder(radius=5.0, height=30.0).move(bd.Location((20.0, 0.0, 0.0)))
    assy = bd.Compound(children=[part, pin])

    sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    _layout = add_standard_multiview(
        sheet=sheet,
        shape=assy,
        scale="auto",
        auto_dimensions=True,
        auto_centerlines=True,
    )

    svg = sheet.to_svg()
    # Check exact dimension values (no stroke-inflation .35 leak)
    assert ">100<" in svg
    assert ">100.35<" not in svg
    assert ">30<" in svg
    assert ">30.35<" not in svg
    assert ">50<" in svg
    assert ">50.35<" not in svg

    # Check centerlines and centermarks
    assert 'class="centermark"' in svg
    assert 'stroke-dasharray="10,2,2,2"' in svg


def _stepped_block() -> bd.Compound:
    return bd.Box(80.0, 40.0, 20.0) - bd.Pos(20.0, 0.0, 5.0) * bd.Box(40.0, 40.0, 10.0)


@pytest.mark.parametrize("auto_dims", [False, True], ids=["plain", "dims"])
@pytest.mark.parametrize("layout_name", ["third_angle", "first_angle"])
@pytest.mark.parametrize(
    "make_shape",
    [
        _stepped_block,
        lambda: bd.Box(100.0, 50.0, 30.0),
        lambda: bd.Cylinder(radius=30.0, height=50.0),
        lambda: bd.Box(120.0, 120.0, 8.0),
        lambda: bd.Box(200.0, 20.0, 20.0),
        lambda: bd.Box(30.0, 30.0, 150.0),
    ],
    ids=["stepped_block", "box", "cylinder", "flat_plate", "long_bar", "tall_post"],
)
def test_multiview_auto_scale_views_inside_frame(
    make_shape: Any, layout_name: str, auto_dims: bool
) -> None:
    """Every view (with its dimensions and label) stays inside the frame and off the title block."""
    sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    layout = add_standard_multiview(
        sheet=sheet, shape=make_shape(), layout=layout_name, auto_dimensions=auto_dims
    )
    info = sheet.inspect_layout()
    fx0, fy0, fx1, fy1 = info["drawable_bounds"]
    tx0, ty0, tx1, ty1 = info["title_block_bounds"]

    geom = {}
    for v_type, view in layout.views.items():
        name = v_type.value
        x0, y0, x1, y1 = view.get_visual_bounds(include_annotations=True, include_label=True)
        assert x0 >= fx0, f"{name} crosses left frame ({x0:.1f} < {fx0})"
        assert y0 >= fy0, f"{name} crosses top frame ({y0:.1f} < {fy0})"
        assert x1 <= fx1, f"{name} crosses right frame ({x1:.1f} > {fx1})"
        assert y1 <= fy1, f"{name} crosses bottom frame ({y1:.1f} > {fy1})"
        hits_tb = x0 < tx1 and tx0 < x1 and y0 < ty1 and ty0 < y1
        assert not hits_tb, f"{name} overlaps the title block"
        geom[name] = (view.min_x, view.min_y, view.max_x, view.max_y)

    items = list(geom.items())
    for i, (na, a) in enumerate(items):
        for nb, b in items[i + 1 :]:
            overlap = a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]
            assert not overlap, f"{na} overlaps {nb}"


def test_projected_isometric_extent_matches_drawn_view() -> None:
    part = _stepped_block()
    w, h = projected_view_extent(part, ViewType.ISOMETRIC)
    sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    view = sheet.add_view(part, view_type=ViewType.ISOMETRIC, center=(200.0, 150.0), scale=1.0)
    assert abs(view.width - w) < 0.05
    assert abs(view.height - h) < 0.05
    # The old diagonal heuristic (|bbox| * 0.75 ≈ 69 mm) underestimated the width.
    assert w > 85.0


def test_assembly_bom_uses_part_info_and_build123d_material() -> None:
    base = bd.Box(60.0, 40.0, 10.0)
    base.label = "Base Plate"
    base.material = "alu_g6061_t6"
    set_part_info(base, PartInfo(part_no="BP-001", material_spec="AL 6061-T6"))

    pin = bd.Cylinder(radius=5.0, height=25.0)
    pin.label = "Guide Pin"
    pin.material = "alu_g6061_t6"
    set_part_info(pin, PartInfo(part_no="GP-002"))
    pin2 = pin.moved(bd.Location((20.0, 0.0, 0.0)))  # copies keep their PartInfo
    assert get_part_info(pin2) == PartInfo(part_no="GP-002")

    washer = bd.Cylinder(radius=8.0, height=2.0)
    washer.label = "Washer"

    bom = {b.description: b for b in generate_assembly_bom([base, pin, pin2, washer])}

    assert (bom["Base Plate"].part_no, bom["Base Plate"].material) == ("BP-001", "AL 6061-T6")
    assert bom["Guide Pin"].qty == 2
    assert bom["Guide Pin"].part_no == "GP-002"
    assert bom["Guide Pin"].material == "ALU G6061 T6"
    assert bom["Washer"].part_no == f"PART-{bom['Washer'].item_no:03d}"
    assert bom["Washer"].material == "STANDARD"
