"""Unit tests verifying 3D CAD projections of build123d parts onto 2D DrawingSheet."""

import build123d as bd

from build123d_techdraw.core.sheet import DrawingSheet, SheetSize
from build123d_techdraw.core.view import ViewType


def test_box_projections() -> None:
    box = bd.Box(60.0, 40.0, 30.0)
    sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)

    v_front = sheet.add_view(box, view_type=ViewType.FRONT, center=(120.0, 160.0), scale=1.0)
    v_top = sheet.add_view(box, view_type=ViewType.TOP, center=(120.0, 90.0), scale=1.0)
    v_right = sheet.add_view(box, view_type=ViewType.RIGHT, center=(200.0, 160.0), scale=1.0)
    v_iso = sheet.add_view(box, view_type=ViewType.ISOMETRIC, center=(280.0, 100.0), scale=0.8)

    assert len(sheet.views) == 4
    assert v_front.width > 0
    assert v_front.height > 0
    assert v_top.width > 0
    assert v_right.width > 0
    assert v_iso.width > 0

    # Test coordinate transformations
    sx, sy = v_front.to_sheet_coords(0.0, 0.0)
    assert abs(sx - 120.0) < 5.0
    assert abs(sy - 160.0) < 5.0

    # Test 3D coordinate transformations
    s3x, s3y = v_front.to_sheet_3d(0.0, 0.0, 0.0)
    assert isinstance(s3x, float)
    assert isinstance(s3y, float)

    # Test dictionary compatibility
    assert v_front["scale"] == 1.0
    assert v_front["center_x"] == 120.0


def test_cylinder_with_hidden_lines_and_section() -> None:
    cyl = bd.Cylinder(radius=15.0, height=50.0)
    sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE)

    # Front view with hidden lines
    v_front = sheet.add_view(
        cyl,
        view_type=ViewType.FRONT,
        center=(100.0, 100.0),
        scale=1.0,
        show_hidden=True,
        view_label="CYLINDER ELEVATION",
    )
    assert v_front.view_label == "CYLINDER ELEVATION"

    # Section view cut through XZ plane
    v_sec = sheet.add_section_view(
        cyl,
        cutting_plane=bd.Plane.XZ,
        center=(180.0, 100.0),
        scale=1.0,
        view_label="SECTION A-A",
    )
    assert v_sec.view_type == ViewType.SECTION

    svg = sheet.to_svg()
    assert "CYLINDER ELEVATION" in svg
    assert "SECTION A-A" in svg
    assert "sectionHatch" in svg
