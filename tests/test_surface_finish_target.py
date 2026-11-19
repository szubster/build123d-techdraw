"""Regression tests for surface texture symbol placement (ISO 1302 / ISO 21920-1, BUG-07)."""

from __future__ import annotations

import build123d as bd
import pytest

from build123d_techdraw import (
    DrawingSheet,
    MaterialRemoval,
    SheetSize,
    SurfaceFinishSymbol,
    ViewType,
)
from build123d_techdraw.core.view import SheetView


def _flange() -> bd.Part:
    part = bd.Cylinder(50.0, 12.0) + bd.Pos(0.0, 0.0, 10.0) * bd.Cylinder(24.0, 12.0)
    result = part - bd.Cylinder(15.0, 40.0)
    assert isinstance(result, bd.Part)
    return result


def _front(part: bd.Part) -> SheetView:
    sheet = DrawingSheet(SheetSize.A3_LANDSCAPE)
    return sheet.add_view(part, ViewType.FRONT, center=(290.0, 150.0), scale=1.0)


def test_surface_finish_targeted_face_vertex_touches_edge() -> None:
    part = _flange()
    front = _front(part)
    sf = SurfaceFinishSymbol(
        target=part.faces().sort_by(bd.Axis.Z)[-1],
        ra_value="Ra 1.6",
        material_removal=MaterialRemoval.REQUIRED,
        view=front,
    )
    x, y = sf.position
    assert y == pytest.approx(front.min_y, abs=1e-3)  # hub top edge in the front view
    assert x == pytest.approx(front.center_x, abs=1e-3)


def test_surface_finish_bbox_sits_on_vertex() -> None:
    sf = SurfaceFinishSymbol(position=(100.0, 80.0), ra_value="Ra 1.6", process="GROUND")
    min_x, min_y, max_x, max_y = sf.bounding_box_2d()
    assert max_y == pytest.approx(80.0)
    assert min_x < 100.0 < max_x
    assert min_y < 80.0 - 8.66 - 2.8  # covers the Ra text and the process line
    plain = SurfaceFinishSymbol(position=(100.0, 80.0), ra_value="Ra 1.6")
    assert plain.bounding_box_2d()[1] > min_y
