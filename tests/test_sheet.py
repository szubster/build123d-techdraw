"""Unit tests for DrawingSheet, SheetSize, ISO 5457 borders, and title blocks."""

from pathlib import Path

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


def test_set_title_block_and_exports(tmp_path: Path) -> None:
    import build123d as bd

    sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE)
    sheet.set_title_block(
        title="WIDGET BRACKET",
        drawing_number="WB-001",
        sheet_num=1,
        num_sheets=3,
        scale="1:2",
        rev="REV C",
        date="2026-11-06",
        material="AL 6061",
        finish="ANODIZED",
        author="DEV",
        project="PROJECT X",
        status="RELEASED",
        units="INCHES",
        third_angle=True,
    )
    assert sheet.title_info.drawing_title == "WIDGET BRACKET"
    assert sheet.title_info.drawing_no == "WB-001"
    assert sheet.title_info.sheet_no == "1 OF 3"
    assert sheet.title_info.scale == "1:2"
    assert sheet.title_info.rev == "REV C"

    # Test export_dxf without views
    dxf_empty = str(tmp_path / "empty.dxf")
    sheet.export_dxf(dxf_empty)
    assert (tmp_path / "empty.dxf").exists()

    # Add a view with shape and test export_dxf
    box = bd.Box(20.0, 10.0, 5.0)
    sheet.add_view(box, center=(100.0, 100.0))
    dxf_with_view = str(tmp_path / "part.dxf")
    sheet.export_dxf(dxf_with_view)
    assert (tmp_path / "part.dxf").exists()


def test_theme_print_and_switcher() -> None:
    sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE)
    svg_print = sheet.to_svg(theme="print")
    assert 'class="theme-iso-white"' in svg_print
    assert 'id="theme-switcher-btn"' in svg_print

    svg_dark = sheet.to_svg(theme="dark_blueprint")
    assert 'class="theme-dark-blueprint"' in svg_dark


def test_iso_projection_symbols() -> None:
    from build123d_techdraw.core.title_block import render_title_block_svg

    # Third angle (ISO-A): circle on left (cx="-8"), frustum on right
    ti_third = TitleBlockInfo(drawing_title="TITLE", drawing_no="DWG-001", third_angle=True)
    svg_third = render_title_block_svg(ti_third, 100.0, 100.0)
    assert 'cx="-8"' in svg_third
    assert 'points="0,-4 8,-7 8,7 0,4"' in svg_third

    # First angle (ISO-E): frustum on left, circle on right (cx="8")
    ti_first = TitleBlockInfo(drawing_title="TITLE", drawing_no="DWG-001", third_angle=False)
    svg_first = render_title_block_svg(ti_first, 100.0, 100.0)
    assert 'cx="8"' in svg_first
    assert 'points="-8,-4 0,-7 0,7 -8,4"' in svg_first
