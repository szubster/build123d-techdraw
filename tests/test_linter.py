"""Unit tests for drawing linter: Margins, Title block clearance, Zero-length dims, and Overlaps."""

import build123d as bd

from build123d_techdraw.annotations.dimensions import DimensionType, LinearDimension
from build123d_techdraw.core.sheet import DrawingSheet, SheetSize
from build123d_techdraw.core.view import ViewType
from build123d_techdraw.layout.linter import run_sheet_lint


def test_clean_sheet_lint() -> None:
    """Test that a well-placed view and dimension produce no critical errors."""
    sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE)
    box = bd.Box(40, 40, 10)
    view = sheet.add_view(box, view_type=ViewType.TOP, center=(100.0, 100.0))
    sheet.add_dimension(
        start=(80.0, 80.0),
        end=(120.0, 80.0),
        offset=-10.0,
        view=view,
    )

    issues = sheet.lint()
    errors = [i for i in issues if i.severity == "ERROR"]
    assert len(errors) == 0


def test_margin_violation_lint() -> None:
    """Test detecting views and annotations violating margin bounds."""
    sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE, margin=10.0)
    # Place an annotation very close to edge (< 10mm)
    dim_out = LinearDimension(
        start=(5.0, 50.0),
        end=(8.0, 50.0),
        offset=-2.0,
        dim_type=DimensionType.HORIZONTAL,
    )
    sheet.annotations.append(dim_out)

    issues = run_sheet_lint(sheet)
    margin_issues = [i for i in issues if i.code == "COLLISION_MARGIN"]
    assert len(margin_issues) >= 1


def test_title_block_collision_lint() -> None:
    """Test detecting annotations placed inside the title block exclusion zone."""
    sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE, margin=10.0)
    # Title block is at bottom right: X > W - 180 - 10, Y > H - 55 - 10
    tb_x = sheet.width_mm - 50.0
    tb_y = sheet.height_mm - 25.0

    dim_in_tb = LinearDimension(
        start=(tb_x - 10, tb_y),
        end=(tb_x + 10, tb_y),
        offset=0.0,
        dim_type=DimensionType.HORIZONTAL,
    )
    sheet.annotations.append(dim_in_tb)

    issues = sheet.lint()
    tb_issues = [i for i in issues if i.code == "COLLISION_TITLE_BLOCK"]
    assert len(tb_issues) >= 1
    assert "title block" in tb_issues[0].message.lower()


def test_zero_length_dimension_lint() -> None:
    """Test detecting zero length dimensions."""
    sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE)
    zero_dim = LinearDimension(
        start=(50.0, 50.0),
        end=(50.0, 50.0),
        offset=10.0,
        dim_type=DimensionType.HORIZONTAL,
    )
    sheet.annotations.append(zero_dim)

    issues = sheet.lint()
    zero_issues = [i for i in issues if i.code == "ZERO_LENGTH_DIMENSION"]
    assert len(zero_issues) == 1
    assert zero_issues[0].severity == "ERROR"


def test_annotation_overlap_lint() -> None:
    """Test detecting duplicate/overlapping annotations."""
    sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE)
    dim1 = LinearDimension(
        start=(50.0, 50.0),
        end=(90.0, 50.0),
        offset=-10.0,
        dim_type=DimensionType.HORIZONTAL,
    )
    dim2 = LinearDimension(
        start=(50.0, 50.0),
        end=(90.0, 50.0),
        offset=-10.0,
        dim_type=DimensionType.HORIZONTAL,
    )
    sheet.annotations.extend([dim1, dim2])

    issues = sheet.lint()
    overlap_issues = [i for i in issues if i.code == "OVERLAP_ANNOTATION"]
    assert len(overlap_issues) >= 1
