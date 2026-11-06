"""Unit tests for DrawingSheet, SheetSize, ISO 5457 borders, and title blocks."""

from build123d_techdraw.core.sheet import DrawingSheet, SheetSize
from build123d_techdraw.core.title_block import TitleBlockInfo


def test_sheet_sizes() -> None:
    assert SheetSize.A4_LANDSCAPE.width == 297.0
    assert SheetSize.A4_LANDSCAPE.height == 210.0
    assert SheetSize.A4_PORTRAIT.width == 210.0
    assert SheetSize.A4_PORTRAIT.height == 297.0
    assert SheetSize.A3_LANDSCAPE.width == 420.0
    assert SheetSize.A3_LANDSCAPE.height == 297.0
    assert SheetSize.A2_LANDSCAPE.width == 594.0
    assert SheetSize.A1_LANDSCAPE.width == 841.0
    assert SheetSize.A0_LANDSCAPE.width == 1189.0


def test_sheet_initialization_and_borders() -> None:
    sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    assert sheet.width_mm == 420.0
    assert sheet.height_mm == 297.0

    border_svg = sheet.render_border_and_grid()
    assert 'class="sheet-bg"' in border_svg
    assert 'class="border-main"' in border_svg
    assert 'class="grid-ticks"' in border_svg
    assert 'class="zone-labels"' in border_svg


def test_sheet_title_block_and_tables() -> None:
    title_info = TitleBlockInfo(
        drawing_title="CYLINDER BASEPLATE ASSEMBLY",
        drawing_no="DWG-042",
        author="TEST ENG",
        third_angle=True,
    )
    sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE, title_info=title_info)
    sheet.add_bom_item(1, 4, "M5-16", "ISO 4762 Socket Head Screw", "A2 Stainless")
    sheet.add_revision("B", "ADD MOUNTING HOLES", "2026-11-06", "SZ")
    sheet.set_notes(["1. NOTE ONE", "2. NOTE TWO"])

    svg = sheet.to_svg()
    assert "CYLINDER BASEPLATE ASSEMBLY" in svg
    assert "DWG-042" in svg
    assert "TEST ENG" in svg
    assert "M5-16" in svg
    assert "ADD MOUNTING HOLES" in svg
    assert "NOTE ONE" in svg
    assert "PROJECTION" in svg


def test_custom_size_and_first_angle() -> None:
    title_info = TitleBlockInfo(
        drawing_title="A VERY LONG PART TITLE THAT TRIGGERS TITLE WRAPPING LOGIC",
        drawing_no="DWG-099",
        third_angle=False,
    )
    sheet = DrawingSheet(custom_size=(500.0, 350.0), title_info=title_info)
    assert sheet.width_mm == 500.0
    assert sheet.height_mm == 350.0
    svg = sheet.to_svg()
    assert "DWG-099" in svg
