"""Regression tests for BUG-04: the DiameterDimension leader lands its text in clear space."""

from __future__ import annotations

import math
import re

import build123d as bd
import pytest

from build123d_techdraw import BoltCircle, DiameterDimension, DrawingSheet, SheetSize, ViewType
from build123d_techdraw.annotations.base import BBox2D
from build123d_techdraw.annotations.dimensions import (
    _estimate_text_width,
    _segment_hits_box,
    _view_contour_segments,
)
from build123d_techdraw.core.view import SheetView


def _text_box(svg: str, font_size: float) -> BBox2D:
    m = re.search(
        r'<text x="([-\d.]+)" y="([-\d.]+)" class="dim-text" text-anchor="(\w+)"[^>]*>([^<]+)<', svg
    )
    assert m is not None
    x, y, anchor, label = float(m.group(1)), float(m.group(2)), m.group(3), m.group(4)
    w = _estimate_text_width(label, font_size)
    x0 = x - w if anchor == "end" else x
    return (x0, y - font_size, x0 + w, y + 1.2)


def _flange_top() -> SheetView:
    flange = bd.Cylinder(radius=50.0, height=12.0)
    hub = bd.Pos(0.0, 0.0, 10.0) * bd.Cylinder(radius=24.0, height=12.0)
    part = flange + hub - bd.Cylinder(radius=15.0, height=40.0)
    for i in range(4):
        ang = math.radians(45.0 + 90.0 * i)
        part = part - (
            bd.Pos(35.0 * math.cos(ang), 35.0 * math.sin(ang), 0.0)
            * bd.Cylinder(radius=3.0, height=20.0)
        )
    sheet = DrawingSheet(SheetSize.A3_LANDSCAPE)
    view = sheet.add_view(part, ViewType.TOP, center=(120.0, 150.0), scale=1.0)
    return view


def test_diameter_landing_clear_of_geometry_and_pcd() -> None:
    view = _flange_top()
    BoltCircle(center=(120.0, 150.0), diameter=70.0, num_holes=4, start_angle_deg=45.0, view=view)
    dim = DiameterDimension(
        center=(120.0, 150.0), diameter=30.0, angle_deg=135.0, tolerance="H7", view=view
    )

    box = _text_box(dim.to_svg(), dim.font_size)
    segments = _view_contour_segments(view)
    assert segments
    assert not any(_segment_hits_box(s, box) for s in segments)
    # Clear of the dashed PCD circle (r = 35) as well: every corner is outside it.
    cx, cy = 120.0, 150.0
    nearest = min(
        math.hypot(px - cx, py - cy) for px in (box[0], box[2]) for py in (box[1], box[3])
    )
    assert nearest > 35.0
    # And outside the outer contour (r = 50) since no clear interior region exists there.
    assert nearest > 50.0
    bbox = dim.bounding_box_2d()
    assert bbox[0] <= box[0] + 0.5 and bbox[1] <= box[1] + 0.5


def test_diameter_keeps_short_leader_in_clear_interior() -> None:
    sheet = DrawingSheet(SheetSize.A3_LANDSCAPE)
    plate = bd.Box(120.0, 120.0, 5.0) - bd.Cylinder(radius=5.0, height=10.0)
    view = sheet.add_view(plate, ViewType.TOP, center=(200.0, 150.0), scale=1.0)
    dim = DiameterDimension(center=(200.0, 150.0), diameter=10.0, angle_deg=45.0, view=view)
    box = _text_box(dim.to_svg(), dim.font_size)
    # Default 10 mm leader: elbow at r = 5 + 10 from the centre, text inside the plate outline.
    elbow_x = 200.0 + 15.0 * math.cos(math.radians(45.0))
    assert box[0] == pytest.approx(elbow_x + 2.0, abs=0.01)
    assert view.min_x < box[0] < box[2] < view.max_x
