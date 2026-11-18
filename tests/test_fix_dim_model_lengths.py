"""SheetView dimension helpers report model lengths, not paper lengths, at any view scale."""

from __future__ import annotations

import build123d as bd
import pytest

from build123d_techdraw import DrawingSheet, SheetSize, ViewType
from build123d_techdraw.core.view import SheetView


def _half_scale_view() -> SheetView:
    sheet = DrawingSheet(SheetSize.A3_LANDSCAPE)
    return sheet.add_view(
        bd.Box(100.0, 50.0, 20.0), ViewType.FRONT, center=(200.0, 150.0), scale=0.5
    )


def test_helpers_report_model_lengths_at_half_scale() -> None:
    view = _half_scale_view()
    assert view.dim_horizontal(-50.0, 50.0, model_y=10.0).text == "100"
    assert view.dim_vertical(-10.0, 10.0, model_x=50.0, offset_x=10.0).text == "20"
    assert view.dim_aligned((-50.0, -10.0), (50.0, -10.0)).text == "100"
    assert view.add_overall_horizontal().text == "100"
    assert view.add_overall_vertical().text == "20"


def test_explicit_text_is_kept() -> None:
    view = _half_scale_view()
    assert view.dim_horizontal(-50.0, 50.0, model_y=10.0, text="REF").text == "REF"


@pytest.mark.parametrize("scale", [0.5, 2.0])
def test_nominal_is_model_length(scale: float) -> None:
    sheet = DrawingSheet(SheetSize.A3_LANDSCAPE)
    view = sheet.add_view(
        bd.Box(40.0, 30.0, 10.0), ViewType.FRONT, center=(200.0, 150.0), scale=scale
    )
    dim = view.dim_horizontal(-20.0, 20.0, model_y=5.0)
    assert dim.nominal == pytest.approx(40.0)
