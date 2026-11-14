"""Tests for OrdinateDimensionSet functionality."""

import build123d as bd

from build123d_techdraw import (
    BuildDrawing,
    FrontView,
    OrdinateDimensionSet,
)


def test_ordinate_dimension_set_x_direction() -> None:
    """Verify OrdinateDimensionSet along X axis renders datum 0 and coordinates with baseline offset."""
    plate = bd.Box(150.0, 80.0, 10.0)

    with (
        BuildDrawing(sheet_size="A3") as sheet,
        FrontView(plate, center=(120.0, 100.0), scale=1.0) as fv,
    ):
        ords = OrdinateDimensionSet(
            datum_origin=(0.0, 0.0),
            direction="X",
            coordinates=[0.0, 15.0, 30.0, 57.5, 85.0, 127.0],
            baseline_offset=-16.0,
            jog=False,
        )

    assert len(fv.annotations) == 1
    assert ords in fv.annotations
    assert ords.direction == "X"

    svg = sheet.to_svg()
    assert "ordinate-dim-set" in svg
    assert "ordinate-dim" in svg
    assert ">0</text>" in svg
    assert ">15</text>" in svg or ">15.00</text>" in svg
    assert ">30</text>" in svg or ">30.00</text>" in svg
    assert ">57.5" in svg
    assert ">85" in svg
    assert ">127" in svg


def test_ordinate_dimension_set_auto_jog() -> None:
    """Verify witness lines auto-jog when coordinates are tightly spaced (<5mm)."""
    plate = bd.Box(60.0, 60.0, 10.0)

    with BuildDrawing(sheet_size="A3"), FrontView(plate, center=(100.0, 100.0), scale=1.0):
        ords = OrdinateDimensionSet(
            datum_origin=(0.0, 0.0),
            direction="X",
            coordinates=[0.0, 2.0, 4.5, 20.0],
            baseline_offset=-15.0,
            jog=True,
        )

    svg = ords.to_svg()
    # Closely spaced ordinates should have 4-point polyline witness lines (jogs)
    lines_and_polys = svg.count("<polyline points=")
    assert lines_and_polys == 4
    # The jog coordinates differ in X between feature and baseline
    assert "ordinate-dim" in svg


def test_ordinate_dimension_set_y_direction() -> None:
    """Verify OrdinateDimensionSet along Y axis."""
    plate = bd.Box(80.0, 120.0, 10.0)

    with BuildDrawing(sheet_size="A3"), FrontView(plate, center=(120.0, 100.0), scale=1.0):
        ords = OrdinateDimensionSet(
            datum_origin=(0.0, 0.0),
            direction="Y",
            coordinates=[0.0, 25.0, 50.0, 100.0],
            baseline_offset=20.0,
            jog=True,
        )

    svg = ords.to_svg()
    assert ords.direction == "Y"
    assert ">0</text>" in svg
    assert ">25</text>" in svg or ">25.00</text>" in svg
    assert ">50</text>" in svg or ">50.00</text>" in svg
    assert ">100</text>" in svg or ">100.00</text>" in svg
