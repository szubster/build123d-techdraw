"""Tests verifying that helper methods do not register duplicate annotations."""

import build123d as bd

from build123d_techdraw import BuildDrawing, FrontView


def test_duplicate_annotation_registration_fix() -> None:
    """Validate that helper dimension calls inside BuildDrawing do not produce duplicates or false lint errors."""
    box = bd.Box(100.0, 50.0, 10.0)

    with BuildDrawing(sheet_size="A4_LANDSCAPE") as s:
        view = FrontView(box, center=(100.0, 100.0))
        dim = view.add_overall_horizontal()

    sheet = s
    assert len(sheet.annotations) == 1
    assert dim in sheet.annotations
    assert dim in view.annotations

    issues = sheet.lint()
    overlap_issues = [i for i in issues if i.code == "OVERLAP_ANNOTATION"]
    assert len(overlap_issues) == 0

    # Also test dim_horizontal, dim_vertical, and add_overall_vertical
    with BuildDrawing(sheet_size="A4_LANDSCAPE") as s2:
        v2 = FrontView(box, center=(120.0, 120.0))
        v2.dim_horizontal(0.0, 50.0, model_y=0.0, offset_y=-5.0)
        v2.dim_vertical(0.0, 25.0, model_x=0.0, offset_x=-5.0)
        # The inner dim is pushed out of the outline to the first row (10 mm); the overall
        # dimension goes on the next row (ISO 129-1: larger dimensions outside smaller ones).
        v2.add_overall_vertical(offset_x=-17.0)

    sheet2 = s2
    assert len(sheet2.annotations) == 3
    issues2 = sheet2.lint()
    assert len([i for i in issues2 if i.code == "OVERLAP_ANNOTATION"]) == 0
