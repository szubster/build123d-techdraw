"""Regression tests for broken views (BUG-02): geometry, coordinate mapping and dimension jogs."""

from __future__ import annotations

import re

import build123d as bd
import pytest

from build123d_techdraw import DrawingSheet, LinearDimension, SheetSize, ViewType
from build123d_techdraw.annotations.dimensions import _estimate_text_width
from build123d_techdraw.core.view import SheetView


def _rod_view(scale: float = 0.5) -> tuple[DrawingSheet, SheetView]:
    sheet = DrawingSheet(SheetSize.A2_LANDSCAPE)
    rod = bd.Box(400.0, 14.0, 14.0)
    view = sheet.add_view(rod, ViewType.FRONT, center=(430.0, 180.0), scale=scale)
    return sheet, view


def _clip_rects(svg: str) -> list[tuple[float, float, float, float]]:
    pattern = r'<clipPath id="[^"]+"><rect x="([-\d.]+)" y="([-\d.]+)" width="([-\d.]+)" height="([-\d.]+)"/>'
    return [(float(x), float(y), float(w), float(h)) for x, y, w, h in re.findall(pattern, svg)]


def test_x_break_keeps_both_segments_with_gap() -> None:
    _, view = _rod_view()
    unbroken_min = view.min_x
    view.add_break(interval=(-130.0, 130.0), axis="X", style="zigzag", gap=10.0)

    # 400 long at 1:2 = 200 mm; removed (330 - 70) * 0.5 - 10 = 120 mm.
    assert view.width == pytest.approx(80.0)
    assert view.min_x == pytest.approx(430.0 - 40.0)
    assert unbroken_min == pytest.approx(330.0)

    # Rod ends land on the broken view edges; the interval is in model coordinates.
    assert view.to_sheet_x(-200.0) == pytest.approx(view.min_x)
    assert view.to_sheet_x(200.0) == pytest.approx(view.max_x)
    left_cut = view.to_sheet_x(-200.0 + 70.0)
    right_cut = view.to_sheet_x(-200.0 + 330.0)
    assert left_cut - view.min_x == pytest.approx(70.0 * 0.5)
    assert view.max_x - right_cut == pytest.approx(70.0 * 0.5)
    assert right_cut - left_cut == pytest.approx(10.0)
    assert view.break_gap_centers("X") == [pytest.approx((left_cut + right_cut) / 2.0)]

    # One clipped copy per kept segment; each clip covers exactly its segment on the cut side.
    svg = view.svg_content
    rects = _clip_rects(svg)
    assert len(rects) == 2
    (x1, _, w1, _), (x2, _, w2, _) = rects
    assert w1 > 0.0 and w2 > 0.0
    assert x1 + w1 == pytest.approx(left_cut, abs=0.01)
    assert x2 == pytest.approx(right_cut, abs=0.01)
    assert x1 < view.min_x and x2 + w2 > view.max_x
    assert svg.count('class="break-line') == 2


def test_x_break_svg_translations_match_coordinate_mapping() -> None:
    _, view = _rod_view()
    view.add_break(interval=(-130.0, 130.0), axis="X", gap=10.0)
    shifts = [
        float(m) - view.center_x
        for m in re.findall(
            r'<g transform="translate\(([-\d.]+), [-\d.]+\) scale', view.svg_content
        )
    ]
    assert len(shifts) == 2
    # Left segment moves right, right segment moves left, each by half the removed length.
    assert shifts[0] == pytest.approx(60.0, abs=0.01)
    assert shifts[1] == pytest.approx(-60.0, abs=0.01)
    assert view.to_sheet_x(-200.0) - (view.center_x - 100.0) == pytest.approx(shifts[0])


def test_y_break_shortens_height_and_maps_both_ends() -> None:
    sheet = DrawingSheet(SheetSize.A3_LANDSCAPE)
    column = bd.Box(20.0, 20.0, 200.0)
    view = sheet.add_view(column, ViewType.FRONT, center=(200.0, 150.0), scale=1.0)
    view.add_break(interval=(-50.0, 50.0), axis="Y", style="wavy", gap=10.0)

    assert view.height == pytest.approx(200.0 - 90.0)
    assert view.to_sheet_y(-100.0) == pytest.approx(view.max_y)  # bottom end
    assert view.to_sheet_y(100.0) == pytest.approx(view.min_y)  # top end
    lower_cut = view.to_sheet_y(-50.0)
    upper_cut = view.to_sheet_y(50.0)
    assert lower_cut - upper_cut == pytest.approx(10.0)
    assert view.max_y - lower_cut == pytest.approx(50.0)
    assert len(_clip_rects(view.svg_content)) == 2


def test_break_survives_locate_and_scale_to() -> None:
    _, view = _rod_view()
    view.add_break(interval=(-130.0, 130.0), axis="X", gap=10.0)
    view.locate((300.0, 200.0))
    assert view.width == pytest.approx(80.0)
    assert view.to_sheet_x(-200.0) == pytest.approx(view.min_x)
    view.scale_to(1.0)
    assert view.unbroken_width == pytest.approx(400.0)
    assert view.width == pytest.approx(400.0 - (260.0 - 10.0))
    assert view.to_sheet_x(200.0) == pytest.approx(view.max_x)


@pytest.mark.parametrize(
    ("interval", "message"),
    [
        ((-130.0, 250.0), "strictly inside"),
        ((-210.0, 0.0), "strictly inside"),
        ((0.0, 10.0), "shorter on paper than the gap"),
    ],
)
def test_add_break_rejects_invalid_intervals(interval: tuple[float, float], message: str) -> None:
    _, view = _rod_view()
    with pytest.raises(ValueError, match=message):
        view.add_break(interval=interval, axis="X", gap=10.0)


def test_add_break_rejects_overlap_and_mixed_axes() -> None:
    _, view = _rod_view(scale=1.0)
    view.add_break(interval=(-100.0, 0.0), axis="X", gap=10.0)
    with pytest.raises(ValueError, match="overlaps"):
        view.add_break(interval=(-50.0, 50.0), axis="X", gap=10.0)
    with pytest.raises(ValueError, match="same axis"):
        view.add_break(interval=(-5.0, 5.0), axis="Y", gap=5.0)


def test_two_breaks_on_one_axis() -> None:
    _, view = _rod_view(scale=1.0)
    view.add_break(interval=(50.0, 150.0), axis="X", gap=10.0)
    view.add_break(interval=(-150.0, -50.0), axis="X", gap=10.0)
    assert view.width == pytest.approx(400.0 - 2 * 90.0)
    assert len(_clip_rects(view.svg_content)) == 3
    assert view.to_sheet_x(-200.0) == pytest.approx(view.min_x)
    assert view.to_sheet_x(200.0) == pytest.approx(view.max_x)
    assert len(view.break_gap_centers("X")) == 2


def test_full_view_dimension_spans_broken_geometry_and_text_avoids_jog() -> None:
    _, view = _rod_view()
    view.add_break(interval=(-130.0, 130.0), axis="X", style="zigzag", gap=10.0)
    dim = LinearDimension(offset=-18.0, view=view)

    assert dim.text == "400"
    assert dim.has_break
    # Extension lines at the drawn rod ends.
    assert dim.start[0] == pytest.approx(view.to_sheet_x(-200.0))
    assert dim.end[0] == pytest.approx(view.to_sheet_x(200.0))

    svg = dim.to_svg()
    jog = re.search(r'<polyline points="([-\d.]+),[-\d.]+ .*?break-jog', svg)
    assert jog is not None
    jog_left = float(jog.group(1))
    gap_centre = view.break_gap_centers("X")[0]
    assert jog_left == pytest.approx(gap_centre - 3.5, abs=0.01)

    text = re.search(r'<text x="([-\d.]+)"[^>]*>400</text>', svg)
    assert text is not None
    text_x = float(text.group(1))
    half_text = _estimate_text_width("400", dim.font_size) / 2.0
    assert abs(text_x - gap_centre) >= 3.5 + half_text


def test_dim_horizontal_across_break_gets_jog() -> None:
    _, view = _rod_view()
    view.add_break(interval=(-130.0, 130.0), axis="X", gap=10.0)
    assert view.dim_horizontal(-200.0, 200.0, model_y=7.0, offset_y=-12.0).has_break
    # A dimension entirely on one side of the break has no jog.
    assert not view.dim_horizontal(-200.0, -150.0, model_y=7.0, offset_y=-12.0).has_break
