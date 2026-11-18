"""Regression tests: ISO 5459 datum feature indicator sits outside the material (BUG-08)."""

from __future__ import annotations

import build123d as bd
import pytest

from build123d_techdraw import (
    DatumFeature,
    DatumFeatureSymbol,
    DrawingSheet,
    SheetSize,
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


def test_datum_on_bottom_face_points_down_out_of_material() -> None:
    part = _flange()
    front = _front(part)
    datum = DatumFeature(label="A", target=part.faces().sort_by(bd.Axis.Z)[0], view=front)
    assert datum.direction == "down"
    tx, ty = datum.target
    assert ty == pytest.approx(front.max_y, abs=1e-3)  # triangle base on the bottom outline
    min_x, min_y, max_x, max_y = datum.bounding_box_2d()
    assert min_y == pytest.approx(ty, abs=1e-6)  # nothing above the edge, i.e. inside the part
    assert max_y > front.max_y + datum.stem_length
    assert min_x < tx < max_x


def test_datum_on_top_face_points_up() -> None:
    part = _flange()
    front = _front(part)
    datum = DatumFeature(label="B", target=part.faces().sort_by(bd.Axis.Z)[-1], view=front)
    assert datum.direction == "up"
    _min_x, _min_y, _max_x, max_y = datum.bounding_box_2d()
    assert max_y == pytest.approx(front.min_y, abs=1e-3)


@pytest.mark.parametrize(
    ("axis", "index", "expected"),
    [
        (bd.Axis.X, 0, "left"),
        (bd.Axis.X, -1, "right"),
        (bd.Axis.Z, 0, "down"),
        (bd.Axis.Z, -1, "up"),
    ],
)
def test_datum_direction_follows_projected_outward_normal(
    axis: bd.Axis, index: int, expected: str
) -> None:
    box = bd.Box(40.0, 20.0, 10.0)
    sheet = DrawingSheet(SheetSize.A3_LANDSCAPE)
    view = sheet.add_view(box, ViewType.FRONT, center=(100.0, 100.0), scale=1.0)
    datum = DatumFeatureSymbol(label="C", target=box.faces().sort_by(axis)[index], view=view)
    assert datum.direction == expected


def test_datum_triangle_base_lies_on_outline() -> None:
    sym = DatumFeatureSymbol(label="A", target=(50.0, 50.0), direction="down")
    tri, _stem_end, _box = sym._geometry()
    base = [p for p in tri if p[1] == pytest.approx(50.0)]
    assert len(base) == 2  # base on the feature line
    apex = next(p for p in tri if p not in base)
    assert apex[1] > 50.0  # apex points away from the material, towards the frame


def test_datum_explicit_direction_is_kept_and_validated() -> None:
    part = _flange()
    front = _front(part)
    sym = DatumFeature(
        label="A", target=part.faces().sort_by(bd.Axis.Z)[0], direction="left", view=front
    )
    assert sym.direction == "left"
    with pytest.raises(ValueError, match="direction"):
        DatumFeatureSymbol(label="A", target=(0.0, 0.0), direction="sideways")
