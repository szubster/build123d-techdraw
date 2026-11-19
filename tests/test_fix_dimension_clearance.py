"""Regression tests for BUG-03: linear dimensions are kept clear of the view outline."""

from __future__ import annotations

import re

import build123d as bd
import pytest

from build123d_techdraw import DrawingSheet, SheetSize, ViewType
from build123d_techdraw.annotations.dimensions import _estimate_text_width
from build123d_techdraw.core.view import SheetView
from build123d_techdraw.layout.auto_dimension import DIM_STACK_STEP_MM, FIRST_DIM_OFFSET_MM


def _bracket_front() -> SheetView:
    base = bd.Box(80.0, 50.0, 15.0)
    flange = bd.Box(30.0, 50.0, 40.0).move(bd.Location((-25.0, 0.0, 20.0)))
    hole = bd.Cylinder(radius=5.5, height=60.0).move(bd.Location((-25.0, 0.0, 20.0)))
    part = base + flange - hole
    sheet = DrawingSheet(SheetSize.A3_LANDSCAPE)
    return sheet.add_view(part, ViewType.FRONT, center=(200.0, 150.0), scale=1.0)


def test_vertical_dim_on_outline_is_pushed_outside_with_text_clearance() -> None:
    view = _bracket_front()
    # Requested line lands exactly on the left outline (x = -40): the reported bug.
    dim = view.dim_vertical(-5.5, 5.5, model_x=-25.0, prefix="⌀", tolerance="H11", offset_x=-15.0)
    dim_x = view.to_sheet_x(-25.0) + dim.offset
    assert dim_x == pytest.approx(view.min_x - FIRST_DIM_OFFSET_MM)
    bbox = dim.bounding_box_2d()
    assert bbox[2] < view.min_x
    # Rotated text (incl. tolerance) sits left of the line, outside the outline.
    assert dim_x - 1.4 - dim.font_size < view.min_x
    assert dim.requested_offset == -15.0


def test_dim_already_outside_keeps_requested_offset() -> None:
    view = _bracket_front()
    dim = view.dim_vertical(-7.5, 40.0, model_x=40.0, offset_x=12.0)
    assert dim.offset == 12.0
    hdim = view.dim_horizontal(-40.0, 40.0, model_y=40.0, offset_y=-8.0)
    assert hdim.offset == -8.0


def test_pushed_dim_stacks_past_existing_row() -> None:
    view = _bracket_front()
    overall = view.add_overall_vertical(offset_x=-FIRST_DIM_OFFSET_MM)
    inner = view.dim_vertical(0.0, 20.0, model_x=-25.0, offset_x=-5.0)
    overall_x = view.min_x + overall.offset
    inner_x = view.to_sheet_x(-25.0) + inner.offset
    assert overall_x == pytest.approx(view.min_x - FIRST_DIM_OFFSET_MM)
    assert inner_x == pytest.approx(overall_x - DIM_STACK_STEP_MM)


def test_horizontal_dim_inside_view_pushed_below() -> None:
    view = _bracket_front()
    dim = view.dim_horizontal(-30.5, -19.5, model_y=0.0, offset_y=5.0)
    dim_y = view.to_sheet_y(0.0) + dim.offset
    assert dim_y == pytest.approx(view.max_y + FIRST_DIM_OFFSET_MM)
    assert dim_y - 1.4 - dim.font_size > view.max_y


def test_text_wider_than_span_goes_on_dimension_line_extension() -> None:
    view = _bracket_front()
    dim = view.dim_horizontal(
        -30.5, -19.5, model_y=40.0, prefix="⌀", tolerance="H11", offset_y=-10.0
    )
    right = view.to_sheet_x(-19.5)
    svg = dim.to_svg()
    text = re.search(r'<text x="([-\d.]+)"', svg)
    assert text is not None
    text_w = _estimate_text_width("⌀11 H11", dim.font_size)
    assert float(text.group(1)) - text_w / 2.0 > right
    assert dim.bounding_box_2d()[2] >= right + text_w
