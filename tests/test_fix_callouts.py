"""Regression tests: leader callouts must not collide with view geometry.

Covers ChamferCallout inside a circular DetailView (text crossing a silhouette edge, arrow in
empty space) and HoleCallout text crossing the outer flange contour.
"""

from __future__ import annotations

import math

import build123d as bd

from build123d_techdraw import (
    BuildDrawing,
    ChamferCallout,
    DetailView,
    HoleCallout,
    LeaderNote,
    SheetSize,
    TopView,
)
from build123d_techdraw.annotations.callouts import (
    Segment2D,
    leader_text_rect,
    segment_intersects_rect,
    view_geometry_segments,
)
from build123d_techdraw.core.view import SheetView


def _text_rect(note: LeaderNote) -> tuple[float, float, float, float]:
    going_left = (note.elbow_offset[0] < 0) if note.align_right is None else note.align_right
    return leader_text_rect(
        note.target, note.elbow_offset, note.lines, note.font_size, note.shelf_length, going_left
    )


def _assert_text_clear(note: LeaderNote, view: SheetView) -> None:
    segments = view_geometry_segments(view)
    assert segments, "view geometry should be known"
    rect = _text_rect(note)
    hits = [seg for seg in segments if segment_intersects_rect(seg, rect)]
    assert not hits, f"leader text {rect} crosses view edges {hits[:3]}"


def _point_segment_distance(px: float, py: float, seg: Segment2D) -> float:
    x1, y1, x2, y2 = seg
    dx, dy = x2 - x1, y2 - y1
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def test_segment_intersects_rect() -> None:
    rect = (0.0, 0.0, 10.0, 5.0)
    assert segment_intersects_rect((-5.0, 2.0, 15.0, 2.0), rect)  # crosses through
    assert segment_intersects_rect((2.0, 1.0, 3.0, 2.0), rect)  # fully inside
    assert not segment_intersects_rect((-5.0, 6.0, 15.0, 6.0), rect)  # above
    assert not segment_intersects_rect((11.0, -3.0, 20.0, 8.0), rect)  # to the right
    assert segment_intersects_rect((-1.0, -1.0, 11.0, 6.0), rect)  # diagonal


def _flange() -> bd.Compound:
    return bd.Cylinder(radius=50.0, height=20.0) - bd.Cylinder(radius=15.0, height=30.0)


def test_hole_callout_text_routed_outside_outer_contour() -> None:
    """FLG-200: '⌀30 THRU' landing must not cross the ⌀100 contour."""
    with (
        BuildDrawing(sheet_size=SheetSize.A3_LANDSCAPE),
        TopView(_flange(), center=(120.0, 150.0), scale=1.0) as top,
    ):
        callout = HoleCallout(
            target=(105.0, 150.0),
            diameter=30.0,
            depth="THRU",
            elbow_offset=(-22.0, 18.0),
        )

    assert callout.requested_elbow_offset == (-22.0, 18.0)
    assert callout.elbow_offset != (-22.0, 18.0)
    # Same direction (down-left), only pushed outward.
    assert callout.elbow_offset[0] < -22.0
    assert callout.elbow_offset[1] > 18.0
    _assert_text_clear(callout, top)
    # The whole text block lies outside the outer ⌀100 contour.
    x0, y0, x1, y1 = _text_rect(callout)
    for cx, cy in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
        assert math.hypot(cx - 120.0, cy - 150.0) > 50.0


def test_leader_note_keeps_requested_offset_when_clear() -> None:
    """A landing already clear of the geometry (including a clear interior region) is honoured."""
    with (
        BuildDrawing(sheet_size=SheetSize.A3_LANDSCAPE),
        TopView(_flange(), center=(120.0, 150.0), scale=1.0) as top,
    ):
        outside = LeaderNote("NOTE", target=(120.0, 100.0), elbow_offset=(20.0, -15.0))
        # Inside the ⌀30 bore, which has no edges: a clear interior region.
        interior = LeaderNote(
            "X", target=(115.0, 152.0), elbow_offset=(-1.0, -1.0), shelf_length=3.0
        )

    assert outside.elbow_offset == (20.0, -15.0)
    assert interior.elbow_offset == (-1.0, -1.0)
    _assert_text_clear(outside, top)


def _chamfered_housing() -> tuple[bd.Part, bd.Edge]:
    base = bd.Box(90.0, 60.0, 30.0)
    edge = base.edges().filter_by(bd.Axis.Y).sort_by(bd.Axis.Z)[-1]
    housing = bd.chamfer(edge, length=8.0)
    assert isinstance(housing, bd.Part)
    inclined = next(f for f in housing.faces() if 0.1 < abs(f.normal_at().Z) < 0.9)
    chamfer_edge = inclined.edges().filter_by(bd.Axis.Y).sort_by(bd.Axis.Z)[-1]
    return housing, chamfer_edge


def test_chamfer_callout_in_detail_view_clear_of_edges_and_on_chamfer() -> None:
    """ADV-001 Detail B: '8 × 45°' clear of silhouette edges; arrow ends on the chamfer edge."""
    housing, chamfer_edge = _chamfered_housing()
    with BuildDrawing(sheet_size=SheetSize.A2_LANDSCAPE):
        with TopView(housing, center=(110.0, 140.0), scale=1.0) as top:
            pass
        with DetailView(
            parent_view=top,
            region_center=(148.0, 140.0),
            radius=14.0,
            scale=2.0,
            label="B",
            center=(250.0, 140.0),
            shape="circle",
        ) as detail:
            callout = ChamferCallout(target_or_edge=chamfer_edge, width=8.0, angle_deg=45.0)

    assert callout.lines == ["8 × 45°"]
    segments = view_geometry_segments(detail)
    # The arrow tip lies on a drawn edge inside the detail circle.
    tx, ty = callout.target
    assert math.hypot(tx - 250.0, ty - 140.0) < 28.0
    assert min(_point_segment_distance(tx, ty, seg) for seg in segments) < 0.05
    _assert_text_clear(callout, detail)


def test_detail_view_segments_clipped_to_boundary() -> None:
    housing, _ = _chamfered_housing()
    with BuildDrawing(sheet_size=SheetSize.A2_LANDSCAPE):
        with TopView(housing, center=(110.0, 140.0), scale=1.0) as top:
            pass
        with DetailView(
            parent_view=top,
            region_center=(148.0, 140.0),
            radius=14.0,
            scale=2.0,
            label="B",
            center=(250.0, 140.0),
        ) as detail:
            pass
    for x1, y1, x2, y2 in view_geometry_segments(detail):
        assert math.hypot(x1 - 250.0, y1 - 140.0) <= 28.0 + 1e-6
        assert math.hypot(x2 - 250.0, y2 - 140.0) <= 28.0 + 1e-6
