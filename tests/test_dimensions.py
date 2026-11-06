"""Unit tests for linear, aligned, radial, and diameter dimensions in paper space."""

import build123d as bd

from build123d_techdraw.annotations.dimensions import (
    DiameterDimension,
    DimensionType,
    LinearDimension,
    RadialDimension,
    format_dimension_value,
    render_arrowhead,
)
from build123d_techdraw.core.sheet import DrawingSheet, SheetSize
from build123d_techdraw.core.view import ViewType


def test_format_dimension_value() -> None:
    assert format_dimension_value(50.0) == "50"
    assert format_dimension_value(50.5) == "50.5"
    assert format_dimension_value(50.25) == "50.25"
    assert format_dimension_value(50.001, precision=2) == "50"


def test_render_arrowhead() -> None:
    arrow = render_arrowhead(10.0, 10.0, 1.0, 0.0, length=3.0)
    assert 'class="dim-arrow"' in arrow
    assert "10.00,10.00" in arrow


def test_linear_dimensions_horizontal_and_vertical() -> None:
    # Horizontal
    dim_h = LinearDimension(
        start=(20.0, 50.0),
        end=(80.0, 50.0),
        offset=-10.0,
        dim_type=DimensionType.HORIZONTAL,
        prefix="2x ",
        suffix=" mm",
        tolerance="±0.1",
    )
    svg_h = dim_h.to_svg()
    assert "2x 60 mm ±0.1" in svg_h
    assert 'class="dim-line"' in svg_h
    assert 'class="dim-arrow"' in svg_h
    assert 'class="dim-text"' in svg_h

    # Vertical
    dim_v = LinearDimension(
        start=(50.0, 20.0),
        end=(50.0, 70.0),
        offset=12.0,
        dim_type=DimensionType.VERTICAL,
        tolerance="H7",
    )
    svg_v = dim_v.to_svg()
    assert "50 H7" in svg_v
    assert "rotate(-90" in svg_v


def test_aligned_dimension() -> None:
    dim_a = LinearDimension(
        start=(10.0, 10.0),
        end=(40.0, 50.0),
        offset=8.0,
        dim_type=DimensionType.ALIGNED,
    )
    svg_a = dim_a.to_svg()
    assert "50" in svg_a  # hypot(30, 40) = 50
    assert "rotate(" in svg_a


def test_radial_and_diameter_dimensions() -> None:
    # Radial
    dim_r = RadialDimension(
        center=(100.0, 100.0),
        radius=15.0,
        angle_deg=45.0,
        tolerance="±0.05",
    )
    svg_r = dim_r.to_svg()
    assert "R15 ±0.05" in svg_r
    assert 'class="radial-dim"' in svg_r

    # Diameter
    dim_d = DiameterDimension(
        center=(100.0, 100.0),
        diameter=30.0,
        angle_deg=30.0,
        prefix="",
    )
    svg_d = dim_d.to_svg()
    assert "⌀30" in svg_d
    assert 'class="diameter-dim"' in svg_d


def test_view_model_anchored_dimensions() -> None:
    part = bd.Box(80.0, 40.0, 20.0)
    sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    view = sheet.add_view(part, view_type=ViewType.FRONT, center=(150.0, 150.0), scale=1.0)

    # Model space dimensions
    dim1 = view.dim_horizontal(-40.0, 40.0, -10.0, offset_y=12.0)
    dim2 = view.dim_vertical(-10.0, 10.0, -40.0, offset_x=-15.0)
    dim_ov_h = view.add_overall_horizontal(offset_y=-10.0)
    dim_ov_v = view.add_overall_vertical(offset_x=12.0)

    assert len(sheet.annotations) == 4
    assert dim1.dim_type == DimensionType.HORIZONTAL
    assert dim2.dim_type == DimensionType.VERTICAL
    assert dim_ov_h.dim_type == DimensionType.HORIZONTAL
    assert dim_ov_v.dim_type == DimensionType.VERTICAL

    svg = sheet.to_svg()
    assert 'class="dim-line"' in svg
    assert "80" in svg
    assert "20" in svg
