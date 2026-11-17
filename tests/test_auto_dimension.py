"""Tests for smart collision-avoiding auto-dimension placement and canonical view de-duplication."""

from __future__ import annotations

import build123d as bd

from build123d_techdraw import (
    BuildDrawing,
    DrawingSheet,
    FrontView,
    SheetSize,
    StandardMultiview,
    add_standard_multiview,
)
from build123d_techdraw.annotations.callouts import HoleCallout
from build123d_techdraw.annotations.dimensions import DimensionType, LinearDimension
from build123d_techdraw.layout.linter import run_sheet_lint
from build123d_techdraw.recognizers.features import (
    recognize_bosses,
    recognize_holes,
    recognize_steps,
)


def _make_stepped_part_with_holes() -> bd.Solid:
    """Create a stepped bracket with two distinct Z-axis holes and a cylindrical boss."""
    base = bd.Box(80.0, 50.0, 12.0)
    step = bd.Pos(-20.0, 0.0, 10.0) * bd.Box(40.0, 50.0, 8.0)
    part = base + step
    # Two distinct holes along Z
    h1 = bd.Pos(-25.0, 10.0, 0.0) * bd.Cylinder(radius=3.0, height=30.0)  # Ø6
    h2 = bd.Pos(20.0, -10.0, 0.0) * bd.Cylinder(radius=5.0, height=30.0)  # Ø10
    part = part - h1 - h2
    return part  # type: ignore[return-value]


def test_recognizers_distinguish_holes_bosses_and_steps() -> None:
    """Verify recognize_holes excludes external bosses and recognize_bosses/steps work."""
    plate = bd.Box(80.0, 60.0, 10.0)
    boss = bd.Pos(15.0, 0.0, 10.0) * bd.Cylinder(radius=8.0, height=10.0)
    hole = bd.Pos(-20.0, 0.0, 0.0) * bd.Cylinder(radius=4.0, height=30.0)
    step = bd.Pos(-20.0, 0.0, 7.5) * bd.Box(40.0, 60.0, 5.0)
    part = plate + boss + step - hole

    holes = recognize_holes(part)
    assert len(holes) == 1
    assert holes[0].diameter == 8.0

    bosses = recognize_bosses(part)
    assert len(bosses) == 1
    assert bosses[0].diameter == 16.0

    steps = recognize_steps(part)
    assert any(s.axis == "X" for s in steps)
    assert any(s.axis == "Z" for s in steps)


def test_iso_stacking_10mm_7mm_and_inner_to_outer() -> None:
    """Verify parallel dimensions stack at 10 mm first offset + 7 mm step, shortest innermost."""
    part = _make_stepped_part_with_holes()
    sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    layout = add_standard_multiview(
        sheet=sheet,
        shape=part,
        scale=1.0,
        show_labels=False,
        auto_dimensions=True,
    )

    assert layout.front is not None
    front_h_dims = [
        a
        for a in layout.front.annotations
        if isinstance(a, LinearDimension) and a.dim_type == DimensionType.HORIZONTAL
    ]
    assert len(front_h_dims) >= 2  # step X (40) + overall X (80)

    offsets = sorted(abs(d.offset) for d in front_h_dims)
    assert offsets[0] == 10.0
    for i in range(1, len(offsets)):
        assert abs((offsets[i] - offsets[i - 1]) - 7.0) < 1e-6

    # Shortest span must be closest (10 mm), longest span outermost
    by_offset = sorted(front_h_dims, key=lambda d: abs(d.offset))
    spans = [abs(d.end[0] - d.start[0]) for d in by_offset]
    assert spans == sorted(spans)

    # With view labels enabled, bottom dimension avoids the FRONT label at max_y + 7mm.
    # Third-angle puts the Top view above the Front view, so Front dims go below it.
    sheet_lbl = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    layout_lbl = add_standard_multiview(
        sheet=sheet_lbl,
        shape=part,
        layout="third_angle",
        scale=1.0,
        show_labels=True,
        auto_dimensions=True,
    )
    assert layout_lbl.front is not None
    lbl_offsets = sorted(
        abs(a.offset)
        for a in layout_lbl.front.annotations
        if isinstance(a, LinearDimension) and a.dim_type == DimensionType.HORIZONTAL
    )
    assert lbl_offsets[0] >= 17.0
    for i in range(1, len(lbl_offsets)):
        assert abs((lbl_offsets[i] - lbl_offsets[i - 1]) - 7.0) < 1e-6


def test_canonical_view_deduplication_and_zero_lint_overlaps() -> None:
    """Verify no duplicate overall/hole dimensions across views and zero lint collisions."""
    part = _make_stepped_part_with_holes()
    sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    layout = add_standard_multiview(
        sheet=sheet,
        shape=part,
        scale=1.0,
        auto_dimensions=True,
    )

    # Hole callouts should appear ONLY on Top view (circular view for Z-axis holes)
    assert layout.top is not None
    assert layout.front is not None
    assert layout.right is not None
    top_callouts = [a for a in layout.top.annotations if isinstance(a, HoleCallout)]
    front_callouts = [a for a in layout.front.annotations if isinstance(a, HoleCallout)]
    right_callouts = [a for a in layout.right.annotations if isinstance(a, HoleCallout)]
    assert len(top_callouts) == 2
    assert len(front_callouts) == 0
    assert len(right_callouts) == 0

    # Overall X (80mm) should appear only once (on Front), not repeated on Top
    all_dims = [a for a in sheet.annotations if isinstance(a, LinearDimension)]
    overall_80 = [d for d in all_dims if abs(abs(d.end[0] - d.start[0]) - 80.0) < 0.1]
    assert len(overall_80) == 1

    # Linter must find zero annotation overlaps, zero margin/title-block collisions
    issues = run_sheet_lint(sheet)
    bad = [
        i
        for i in issues
        if i.code in ("OVERLAP_ANNOTATION", "COLLISION_TITLE_BLOCK", "COLLISION_MARGIN")
    ]
    assert bad == [], f"Unexpected lint issues: {bad}"


def test_builder_mode_and_single_view_parity() -> None:
    """Verify StandardMultiview (Builder Mode) and SheetView.auto_dimension() work identically."""
    part = _make_stepped_part_with_holes()

    with BuildDrawing(sheet_size=SheetSize.A3_LANDSCAPE) as s:
        mv = StandardMultiview(part, scale=1.0, auto_dimensions=True)

    assert mv.front is not None
    assert len(s.annotations) >= 6
    issues = s.lint()
    assert not [i for i in issues if i.code == "OVERLAP_ANNOTATION"]

    # Single-view auto_dimension() in Builder Mode
    with BuildDrawing(sheet_size=SheetSize.A4_LANDSCAPE) as s2:
        v = FrontView(part, center=(120.0, 100.0), scale=1.0)
        dims = v.auto_dimension()

    assert len(dims) >= 2
    assert not [i for i in s2.lint() if i.code == "OVERLAP_ANNOTATION"]
