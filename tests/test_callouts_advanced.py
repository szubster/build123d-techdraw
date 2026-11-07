"""Unit tests for advanced manufacturing callouts: Chamfer Callout and ISO 2553 Welding Symbols."""

import build123d as bd

from build123d_techdraw.annotations.callouts import ChamferCallout, WeldingSymbol, WeldType
from build123d_techdraw.core.sheet import DrawingSheet, SheetSize
from build123d_techdraw.core.view import ViewType


def test_chamfer_callout_formatting_and_svg() -> None:
    """Test chamfer callout with angle and compact formats."""
    # 2 x 45 deg chamfer
    chamfer_ang = ChamferCallout(
        target_or_edge=(50.0, 50.0),
        width=2.0,
        angle_deg=45.0,
        quantity=2,
    )
    svg_ang = chamfer_ang.to_svg()
    assert "2x 2 × 45°" in svg_ang
    assert 'class="dim-arrow"' in svg_ang
    assert 'class="leader-note"' in svg_ang

    # Compact format: C2.5
    chamfer_c = ChamferCallout(
        target_or_edge=(60.0, 60.0),
        width=2.5,
        style="c",
    )
    svg_c = chamfer_c.to_svg()
    assert "C2.5" in svg_c


def test_chamfer_callout_on_sheet_and_view() -> None:
    """Test chamfer callout attached through sheet and view APIs."""
    sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE)
    box = bd.Box(30, 30, 10)
    view = sheet.add_view(box, view_type=ViewType.TOP, center=(80.0, 80.0))

    callout = sheet.add_chamfer_callout(
        target=(80.0, 65.0),
        width=1.5,
        angle_deg=45.0,
        view=view,
    )
    assert callout in sheet.annotations
    assert callout in view.annotations
    svg = sheet.to_svg()
    assert "1.5 × 45°" in svg


def test_welding_symbol_iso_types_and_decorations() -> None:
    """Test ISO 2553 welding symbols with weld shapes, flags, all-around, and tail process."""
    # Fillet weld with leg length 5, length 50, all around, field weld, and GTAW process
    weld = WeldingSymbol(
        target=(100.0, 100.0),
        weld_type=WeldType.FILLET,
        size="a5",
        length="50",
        all_around=True,
        field_weld=True,
        tail_text="GTAW 141",
    )
    svg = weld.to_svg()
    assert 'class="welding-symbol"' in svg
    assert "weld-flag" in svg  # Field weld flag
    assert "weld-all-around" in svg  # All around circle
    assert "GTAW 141" in svg  # Tail note
    assert "a5" in svg
    assert "50" in svg

    # V-groove weld on other side (dashed reference line)
    weld_v = WeldingSymbol(
        target=(120.0, 120.0),
        weld_type=WeldType.V_BUTT,
        size="s8",
        arrow_side=False,
    )
    svg_v = weld_v.to_svg()
    assert "weld-ref-dashed" in svg_v
    assert "s8" in svg_v


def test_welding_symbol_on_sheet() -> None:
    """Test adding welding symbol via sheet API."""
    sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE)
    box = bd.Box(20, 20, 10)
    view = sheet.add_view(box, view_type=ViewType.TOP, center=(100.0, 100.0))

    weld = sheet.add_welding_symbol(
        target=(90.0, 100.0),
        weld_type=WeldType.SQUARE_BUTT,
        size="s3",
        view=view,
    )
    assert weld in sheet.annotations
    svg = sheet.to_svg()
    assert "s3" in svg
