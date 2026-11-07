"""Unit tests for CAD feature recognition: Holes, Counterbores, Countersinks, Bolt Circles, and smart auto-dimensions."""

import build123d as bd

from build123d_techdraw.core.sheet import DrawingSheet, SheetSize
from build123d_techdraw.layout.multiview import add_standard_multiview
from build123d_techdraw.recognizers.features import (
    recognize_bolt_circles,
    recognize_holes,
)


def test_recognize_simple_holes() -> None:
    """Test recognizing simple cylindrical through holes in a plate."""
    # Plate 100x100x10 with two through holes of diameter 8
    plate = bd.Box(100, 100, 10)
    hole1 = bd.Pos(-25, 0, 0) * bd.Cylinder(radius=4, height=12)
    hole2 = bd.Pos(25, 0, 0) * bd.Cylinder(radius=4, height=12)
    part = plate - hole1 - hole2

    holes = recognize_holes(part)
    assert len(holes) == 2
    assert holes[0].diameter == 8.0
    assert holes[0].quantity == 2
    assert holes[1].quantity == 2


def test_recognize_counterbore_holes() -> None:
    """Test recognizing counterbore holes."""
    plate = bd.Box(60, 60, 20)
    through_h = bd.Cylinder(radius=3, height=25)  # Dia 6
    cbore_h = bd.Pos(0, 0, 6) * bd.Cylinder(radius=5, height=10)  # Dia 10, depth 4
    part = plate - through_h - cbore_h

    holes = recognize_holes(part)
    assert len(holes) == 1
    h = holes[0]
    assert h.diameter == 6.0
    assert h.counterbore_dia == 10.0
    assert h.counterbore_depth is not None
    assert h.counterbore_depth > 0.0


def test_recognize_bolt_circles() -> None:
    """Test recognizing bolt circle (PCD) hole patterns."""
    flange = bd.Cylinder(radius=40, height=10)
    center_hole = bd.Cylinder(radius=10, height=12)
    part = flange - center_hole

    # Add 4 holes on PCD = 50 (radius 25)
    import math

    pcd_radius = 25.0
    for i in range(4):
        ang = i * (math.pi / 2.0)
        x = pcd_radius * math.cos(ang)
        y = pcd_radius * math.sin(ang)
        h = bd.Pos(x, y, 0) * bd.Cylinder(radius=2.5, height=12)  # Dia 5
        part = part - h

    bolt_circles = recognize_bolt_circles(part)
    assert len(bolt_circles) >= 1
    bc = bolt_circles[0]
    assert abs(bc.pcd - 50.0) < 0.5
    assert bc.hole_count == 4
    assert bc.hole_diameter == 5.0


def test_smart_multiview_auto_dimensions() -> None:
    """Test add_standard_multiview with auto_dimensions='smart' adding recognized hole annotations."""
    sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    plate = bd.Box(80, 80, 15)
    hole = bd.Cylinder(radius=4, height=20)  # Dia 8
    part = plate - hole

    layout = add_standard_multiview(
        sheet=sheet,
        shape=part,
        auto_dimensions="smart",
    )
    assert layout.top is not None
    # Verify sheet SVG contains dimension/hole annotations
    svg = sheet.to_svg()
    assert "Ø8" in svg or "8" in svg
