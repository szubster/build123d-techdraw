"""Tests for the static annotation / sheet-item types that replaced runtime duck typing."""

import build123d as bd
import pytest

from build123d_techdraw import (
    Annotation,
    BuildDrawing,
    CenterMark,
    DrawingBooklet,
    DrawingSheet,
    FeatureControlFrame,
    GDTCharacteristic,
    GeneralNotes,
    LeaderNote,
    LinearDimension,
    SheetDirective,
    SheetSize,
    Table,
    TitleBlock,
    ViewType,
    add_standard_multiview,
    run_sheet_lint,
)


class _Marker(Annotation):
    """Minimal user-defined annotation."""

    def __init__(self, x: float, y: float) -> None:
        self.x = x
        self.y = y

    def to_svg(self) -> str:
        return f'<circle cx="{self.x}" cy="{self.y}" r="2" class="dim-line" fill="none"/>'

    def bounding_box_2d(self) -> tuple[float, float, float, float]:
        return (self.x - 2.0, self.y - 2.0, self.x + 2.0, self.y + 2.0)


def test_annotation_is_abstract() -> None:
    with pytest.raises(TypeError):
        Annotation()  # type: ignore[abstract]  # asserting the ABC refuses instantiation


def test_custom_annotation_on_view_registers_on_sheet_and_lints() -> None:
    sheet = DrawingSheet(SheetSize.A4_LANDSCAPE)
    view = sheet.add_view(bd.Box(20.0, 10.0, 5.0), ViewType.FRONT, center=(100.0, 100.0))
    marker = _Marker(2.0, 2.0)  # inside the sheet margin -> COLLISION_MARGIN
    view += marker

    assert marker.view is view
    assert marker in view.annotations
    assert marker in sheet.annotations
    assert marker.to_svg() in sheet.to_svg()
    assert any(i.element is marker and i.code == "COLLISION_MARGIN" for i in run_sheet_lint(sheet))


def test_view_add_rejects_non_annotations() -> None:
    sheet = DrawingSheet()
    view = sheet.add_view(bd.Box(20.0, 10.0, 5.0))
    with pytest.raises(TypeError):
        view.add(42)  # type: ignore[arg-type]  # asserting the runtime guard
    with pytest.raises(TypeError):
        sheet.add("not an annotation")  # type: ignore[arg-type]  # asserting the runtime guard


def test_bounding_boxes_match_linter_conventions() -> None:
    h = LinearDimension(start=(10.0, 20.0), end=(50.0, 20.0), offset=8.0, dim_type="horizontal")
    assert h.bounding_box_2d() == (10.0, 24.5, 50.0, 30.0)
    v = LinearDimension(start=(10.0, 20.0), end=(10.0, 60.0), offset=-8.0, dim_type="vertical")
    assert v.bounding_box_2d() == (-1.5, 20.0, 4.0, 60.0)

    note = LeaderNote("NOTE", target=(100.0, 100.0), elbow_offset=(10.0, -5.0), shelf_length=20.0)
    assert note.bounding_box_2d() == (100.0, 91.0, 130.0, 104.0)

    cm = CenterMark(center=(50.0, 50.0), size=10.0)
    assert cm.bounding_box_2d() == (45.0, 45.0, 55.0, 55.0)

    fcf = FeatureControlFrame(GDTCharacteristic.FLATNESS, "0.05", position=(30.0, 40.0))
    assert fcf.bounding_box_2d() == (30.0, 40.0, 65.0, 40.0 + fcf.frame_height)

    table = Table(headers=["A"], rows=[["1"]], position=(5.0, 6.0))
    assert table.bounding_box_2d() == (5.0, 6.0, 5.0 + table.width, 6.0 + table.height)


def test_sheet_directives_apply_to_sheet() -> None:
    sheet = DrawingSheet()
    title = TitleBlock(title="BRACKET", drawing_no="DWG-042")
    notes = GeneralNotes(["1. NOTE"])
    assert isinstance(title, SheetDirective)
    sheet += [title, notes]
    assert sheet.title_info.drawing_title == "BRACKET"
    assert sheet.title_info.drawing_no == "DWG-042"
    assert sheet.notes == ["1. NOTE"]

    layout = add_standard_multiview(DrawingSheet(), bd.Box(30.0, 20.0, 10.0))
    target = DrawingSheet()
    target += layout
    assert set(target.views) == set(layout.views.values())


def test_build_drawing_is_a_drawing_sheet() -> None:
    with BuildDrawing(SheetSize.A4_LANDSCAPE, title="PLATE") as dwg:
        pass
    assert isinstance(dwg, DrawingSheet)
    assert dwg.title_info.drawing_title == "PLATE"
    booklet = DrawingBooklet(sheets=[dwg])
    assert booklet.sheets == [dwg]
