"""Unit tests for advanced dimension types: Angular, Ordinate, Baseline, Chain, and narrow-gap auto-flip."""

import build123d as bd

from build123d_techdraw.annotations.dimensions import (
    AngularDimension,
    BaselineDimensionGroup,
    ChainDimensionGroup,
    DimensionType,
    LinearDimension,
    OrdinateDimensionGroup,
)
from build123d_techdraw.core.sheet import DrawingSheet, SheetSize
from build123d_techdraw.core.view import ViewType


def test_safe_dimension_narrow_gap_autoflip() -> None:
    """Check that linear dimension auto-flips arrowheads outwards when gap is too narrow."""
    # Narrow horizontal gap (4mm < 2 * 3.0 + 2.0 = 8mm)
    dim_narrow = LinearDimension(
        start=(50.0, 50.0),
        end=(54.0, 50.0),
        offset=-10.0,
        dim_type=DimensionType.HORIZONTAL,
        arrow_size=3.0,
    )
    svg_narrow = dim_narrow.to_svg()
    # When flipped outward, arrow dx vectors point inward towards the gap points
    assert 'class="dim-arrow"' in svg_narrow
    assert "4" in svg_narrow

    # Wide horizontal gap (30mm)
    dim_wide = LinearDimension(
        start=(50.0, 50.0),
        end=(80.0, 50.0),
        offset=-10.0,
        dim_type=DimensionType.HORIZONTAL,
        arrow_size=3.0,
    )
    svg_wide = dim_wide.to_svg()
    assert 'class="dim-arrow"' in svg_wide
    assert "30" in svg_wide


def test_angular_dimension_explicit_and_svg() -> None:
    """Test angular dimension with explicit apex, start and end angles."""
    dim = AngularDimension(
        radius=25.0,
        apex=(100.0, 100.0),
        start_angle_deg=0.0,
        end_angle_deg=45.0,
        nominal=45.0,
        suffix="°",
    )
    svg = dim.to_svg()
    assert 'class="angular-dim"' in svg
    assert 'class="dim-line"' in svg
    assert 'class="dim-arrow"' in svg
    assert "45°" in svg
    assert "path" in svg


def test_angular_dimension_with_view_and_edges() -> None:
    """Test angular dimension resolved from CAD edges on a view."""
    sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE)
    box = bd.Box(40, 40, 20)
    view = sheet.add_view(
        box,
        view_type=ViewType.TOP,
        center=(100.0, 100.0),
    )

    edges = list(box.edges().filter_by(bd.Axis.Z))
    e1 = edges[0]
    e2 = edges[1]

    dim = sheet.add_angular_dimension(
        target1=e1,
        target2=e2,
        radius=20.0,
        view=view,
    )
    assert dim.resolved is True
    svg = sheet.to_svg()
    assert 'class="angular-dim"' in svg


def test_ordinate_dimensions() -> None:
    """Test horizontal and vertical ordinate dimensions with and without jogs."""
    sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE)
    box = bd.Box(50, 30, 10)
    view = sheet.add_view(
        box,
        view_type=ViewType.FRONT,
        center=(100.0, 100.0),
    )

    # Horizontal ordinate
    ord_h = sheet.add_ordinate(
        target=(110.0, 100.0),
        datum_coord=100.0,
        direction="horizontal",
        extension_length=15.0,
        jog=3.0,
        view=view,
    )
    svg_h = ord_h.to_svg()
    assert 'class="ordinate-dim"' in svg_h
    assert "polyline" in svg_h
    assert "10" in svg_h

    # Vertical ordinate
    ord_v = sheet.add_ordinate(
        target=(100.0, 120.0),
        datum_coord=100.0,
        direction="vertical",
        extension_length=15.0,
        jog=-2.0,
        view=view,
    )
    svg_v = ord_v.to_svg()
    assert 'class="ordinate-dim"' in svg_v
    assert "20" in svg_v


def test_ordinate_dimension_group() -> None:
    """Test OrdinateDimensionGroup creating datum 0 and coordinates along an axis."""
    sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE)
    view = sheet.add_view(bd.Box(10, 10, 10), center=(100.0, 100.0))

    group = OrdinateDimensionGroup(
        datum_target=(50.0, 80.0),
        direction="horizontal",
        extension_length=12.0,
        view=view,
    )
    group.add_ordinate((65.0, 80.0))
    group.add_ordinate((90.0, 80.0))
    group.add_ordinate((120.0, 80.0))
    assert len(group.ordinates) == 4  # 1 datum + 3 points
    assert group.ordinates[0].datum_coord == 50.0


def test_baseline_and_chain_dimension_groups() -> None:
    """Test stacked baseline and chain dimensions."""
    sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE)
    view = sheet.add_view(bd.Box(10, 10, 10), center=(100.0, 100.0))

    # Baseline group
    base_grp = BaselineDimensionGroup(
        base_target=(20.0, 50.0),
        targets=[(40.0, 50.0), (70.0, 50.0), (100.0, 50.0)],
        direction="horizontal",
        first_offset=-10.0,
        step_offset=8.0,
        view=view,
    )
    b_dims = base_grp.dimensions
    assert len(b_dims) == 3
    assert b_dims[0].offset == -10.0
    assert b_dims[1].offset == -18.0
    assert b_dims[2].offset == -26.0

    # Chain group
    chain_grp = ChainDimensionGroup(
        targets=[(20.0, 50.0), (45.0, 50.0), (75.0, 50.0), (110.0, 50.0)],
        direction="horizontal",
        offset=-10.0,
        view=view,
    )
    c_dims = chain_grp.dimensions
    assert len(c_dims) == 3
    assert c_dims[0].start == (20.0, 50.0)
    assert c_dims[0].end == (45.0, 50.0)
    assert c_dims[1].start == (45.0, 50.0)
    assert c_dims[1].end == (75.0, 50.0)
