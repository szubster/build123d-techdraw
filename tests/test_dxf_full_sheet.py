"""Unit tests for full multi-layer DXF sheet export."""

from pathlib import Path

import build123d as bd
import ezdxf

from build123d_techdraw.core.sheet import DrawingSheet, SheetSize
from build123d_techdraw.core.view import ViewType
from build123d_techdraw.renderers.dxf import DXFRenderer


def test_export_sheet_to_dxf(tmp_path: Path) -> None:
    """Test full drawing sheet export to multi-layer DXF."""
    sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE)
    box = bd.Box(50, 30, 15)

    view = sheet.add_view(
        box,
        view_type=ViewType.FRONT,
        center=(100.0, 100.0),
    )
    sheet.add_dimension(
        start=(75.0, 85.0),
        end=(125.0, 85.0),
        offset=-10.0,
        view=view,
    )

    out_file = tmp_path / "drawing_sheet.dxf"
    sheet.export_dxf(str(out_file), flat_pattern=False)

    assert out_file.exists()
    assert out_file.stat().st_size > 0

    # Read back with ezdxf to inspect layers and entities
    doc = ezdxf.readfile(str(out_file))  # type: ignore[attr-defined]
    layer_names = [layer.dxf.name for layer in doc.layers]

    # Standard CAD layers should be created
    assert "BORDER" in layer_names
    assert "TITLE_BLOCK" in layer_names
    assert "VISIBLE" in layer_names
    assert "DIMENSIONS" in layer_names

    # Check modelspace entities
    msp = doc.modelspace()
    entities = list(msp)
    assert len(entities) > 0


def test_export_shape_flat_pattern(tmp_path: Path) -> None:
    """Test 1:1 DXF flat pattern export for CNC manufacturing."""
    sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE)
    plate = bd.Box(80, 40, 5)
    sheet.add_view(plate, view_type=ViewType.TOP, center=(100.0, 100.0))

    out_file = tmp_path / "flat_pattern.dxf"
    # Flat pattern export using the shape
    DXFRenderer.export_shape_to_dxf(plate, str(out_file))

    assert out_file.exists()
    doc = ezdxf.readfile(str(out_file))  # type: ignore[attr-defined]
    msp = doc.modelspace()
    assert len(list(msp)) > 0
