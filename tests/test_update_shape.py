"""Tests for ``SheetView.update_shape`` / ``DrawingSheet.update_shape`` regeneration."""

from __future__ import annotations

import build123d as bd
import pytest

from build123d_techdraw import DrawingSheet, SheetSize, ViewType
from build123d_techdraw.core.view import SheetView


def _block(length: float, height: float = 20.0, hole_r: float = 4.0) -> bd.Part:
    box = bd.Box(length, 30.0, height)
    hole = bd.Pos(length / 4.0, 0.0, 0.0) * bd.Cylinder(hole_r, height * 2.0)
    return box - hole


def _bounds(view: SheetView) -> tuple[float, float, float, float]:
    return (view.min_x, view.min_y, view.max_x, view.max_y)


def test_update_shape_regenerates_section_detail_and_auxiliary_views() -> None:
    part_v1 = _block(60.0)
    part_v2 = _block(100.0, height=40.0, hole_r=7.0)

    sheet = DrawingSheet(SheetSize.A3_LANDSCAPE)
    front = sheet.add_view(part_v1, ViewType.FRONT, center=(100.0, 100.0))
    section = sheet.add_section_view(part_v1, bd.Plane.XZ, center=(250.0, 100.0))
    offset_section = sheet.add_offset_section_view(
        part_v1, bd.Plane.XZ, points=[(-40.0, 0.0, 0.0), (40.0, 0.0, 0.0)], center=(250.0, 200.0)
    )
    detail = sheet.add_detail_view(
        front, focus_target=(100.0, 100.0), radius=8.0, scale=2.0, center=(100.0, 220.0)
    )
    aux = sheet.add_auxiliary_view(part_v1, face=(1.0, -1.0, 0.0), center=(330.0, 100.0))

    before = {
        name: (v.svg_content, _bounds(v))
        for name, v in {
            "section": section,
            "offset": offset_section,
            "detail": detail,
            "aux": aux,
        }.items()
    }
    front_svg_v1_inner = front.inner_svg_content

    sheet.update_shape(part_v2)

    for name, v in {
        "section": section,
        "offset": offset_section,
        "aux": aux,
    }.items():
        assert v.shape is part_v2, name
        assert v.svg_content != before[name][0], name
        assert _bounds(v) != before[name][1], name

    # Section width grows from 60 to 100 model units at scale 1.
    assert section.width == pytest.approx(100.0, abs=0.5)
    assert section.height == pytest.approx(40.0, abs=0.5)
    assert section.center_x == 250.0
    assert section.hatch_id is not None

    # Detail view follows its (already updated) parent and re-cuts its geometry.
    assert front.inner_svg_content != front_svg_v1_inner
    assert detail.parent_view is front
    assert detail.shape is part_v2
    assert detail.inner_svg_content == front.inner_svg_content
    assert detail.svg_content != before["detail"][0]
    # Exactly one detail callout on the parent after regeneration.
    assert front.svg_content.count('class="detail-callout"') == 1


def test_detail_update_shape_updates_parent_first() -> None:
    part_v1 = _block(60.0)
    part_v2 = _block(100.0)
    sheet = DrawingSheet(SheetSize.A3_LANDSCAPE)
    front = sheet.add_view(part_v1, ViewType.FRONT, center=(100.0, 100.0))
    detail = sheet.add_detail_view(front, focus_target=(100.0, 100.0), center=(100.0, 220.0))

    detail.update_shape(part_v2)

    assert front.shape is part_v2
    assert detail.shape is part_v2
    assert detail.inner_svg_content == front.inner_svg_content
    assert front.svg_content.count('class="detail-callout"') == 1


def test_custom_model_origin_survives_update_shape() -> None:
    sheet = DrawingSheet(SheetSize.A3_LANDSCAPE)
    front = sheet.add_view(
        _block(60.0), ViewType.FRONT, center=(150.0, 150.0), model_origin=(-30.0, -10.0)
    )
    section = sheet.add_section_view(
        _block(60.0), bd.Plane.XZ, center=(300.0, 150.0), model_origin=(5.0, 7.0)
    )

    sheet.update_shape(_block(100.0))

    assert front.model_origin == (-30.0, -10.0)
    assert (front.origin_x, front.origin_y) == (-30.0, -10.0)
    assert section.model_origin == (5.0, 7.0)


def test_update_shape_preserves_view_settings() -> None:
    sheet = DrawingSheet(SheetSize.A3_LANDSCAPE)
    front = sheet.add_view(
        _block(60.0), ViewType.FRONT, center=(150.0, 150.0), view_label="FRONT", view_label_y=200.0
    )
    front.enforce_iso_scale = False
    front.scale_label = "CUSTOM"

    # Dimensions reserve space below the view.
    front.bottom_annotation_y = front.max_y + 25.0
    reserved = front.bottom_annotation_y - front.max_y

    sheet.update_shape(_block(100.0, height=40.0))

    assert front.view_label_pos == (150.0, 200.0)
    assert front.enforce_iso_scale is False
    assert front.scale_label == "CUSTOM"
    assert front.max_y == pytest.approx(170.0, abs=0.5)
    assert front.bottom_annotation_y - front.max_y == pytest.approx(reserved)


def test_update_shape_auto_label_offset_follows_new_geometry() -> None:
    sheet = DrawingSheet(SheetSize.A3_LANDSCAPE)
    front = sheet.add_view(_block(60.0), ViewType.FRONT, center=(150.0, 150.0), view_label="F")
    old_label_y = front.view_label_pos[1] if front.view_label_pos else 0.0

    sheet.update_shape(_block(60.0, height=40.0))

    assert front.view_label_pos is not None
    assert front.view_label_pos[1] == pytest.approx(old_label_y + 10.0, abs=0.5)


def test_update_shape_honours_moved_view() -> None:
    sheet = DrawingSheet(SheetSize.A3_LANDSCAPE)
    section = sheet.add_section_view(_block(60.0), bd.Plane.XZ, center=(100.0, 100.0))
    section.locate((220.0, 140.0))

    sheet.update_shape(_block(80.0))

    assert (section.center_x, section.center_y) == (220.0, 140.0)
    assert section.min_x == pytest.approx(180.0, abs=0.5)


def test_update_shape_raises_for_view_without_build_parameters() -> None:
    sheet = DrawingSheet(SheetSize.A3_LANDSCAPE)
    front = sheet.add_view(_block(60.0), ViewType.FRONT, center=(150.0, 150.0))
    manual = SheetView(ViewType.SECTION, 300.0, 150.0, 1.0, 50.0, 20.0)
    sheet.add_view(manual)
    front_svg = front.svg_content

    with pytest.raises(ValueError, match="no recorded build parameters"):
        sheet.update_shape(_block(100.0))
    # Nothing was changed: the check runs before any view is regenerated.
    assert front.svg_content == front_svg

    with pytest.raises(ValueError, match="Cannot update shape"):
        manual.update_shape(_block(100.0))
