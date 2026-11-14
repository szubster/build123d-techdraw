"""Tests for 1:1 CAM DXF export directly from View."""

from pathlib import Path

import build123d as bd
import ezdxf

from build123d_techdraw import BuildDrawing, Centerline, TopView


def test_view_export_dxf_1to1_layers(tmp_path: Path) -> None:
    """Verify exported DXF contains 1:1 entities on requested layers regardless of view sheet scale."""
    # Create plate (120x80x10) with through hole (r=6) and countersink (r_inner=4, r_outer=8)
    plate = bd.Box(120.0, 80.0, 10.0)
    through_hole = bd.Cylinder(radius=6.0, height=20.0).locate(bd.Location((-25.0, 0.0, 0.0)))
    csink = (
        bd.Cylinder(radius=4.0, height=20.0)
        + bd.Cone(bottom_radius=4.0, top_radius=8.0, height=4.0).locate(
            bd.Location((0.0, 0.0, 3.0))
        )
    ).locate(bd.Location((25.0, 0.0, 0.0)))
    part = plate - through_hole - csink

    dxf_file = str(tmp_path / "plate_1to1.dxf")

    # Sheet scale is 0.5 (half-size on drawing sheet)
    with BuildDrawing(sheet_size="A3"), TopView(part, center=(120.0, 100.0), scale=0.5) as tv:
        # Add explicit centerline annotation
        Centerline(start=(-25.0, -10.0), end=(-25.0, 10.0))

        tv.export_dxf(
            filepath=dxf_file,
            layers={
                "outer_profile": "CUT_OUTER",
                "inner_holes": "CUT_INNER",
                "centerlines": "MARK",
                "countersinks": "ETCH",
            },
            scale=1.0,  # Strict 1:1 manufacturing scale
        )

    # Inspect exported DXF with ezdxf
    doc = ezdxf.readfile(dxf_file)
    msp = doc.modelspace()

    # 1. Verify requested layers exist
    layer_names = [layer.dxf.name for layer in doc.layers]
    assert "CUT_OUTER" in layer_names
    assert "CUT_INNER" in layer_names
    assert "MARK" in layer_names
    assert "ETCH" in layer_names

    # 2. Verify CUT_OUTER entities
    outer_entities = msp.query('*[layer=="CUT_OUTER"]')
    assert len(outer_entities) > 0

    # Bounding box of outer profile should be 120 x 80 in 1:1 model coordinates
    xs: list[float] = []
    ys: list[float] = []
    for e in outer_entities:
        if e.dxftype() == "LINE":
            xs.extend([e.dxf.start.x, e.dxf.end.x])
            ys.extend([e.dxf.start.y, e.dxf.end.y])
    if xs and ys:
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
        assert abs(width - 120.0) < 1.0
        assert abs(height - 80.0) < 1.0

    # 3. Verify CUT_INNER entities (through holes)
    inner_entities = msp.query('*[layer=="CUT_INNER"]')
    assert len(inner_entities) > 0
    inner_radii = [e.dxf.radius for e in inner_entities if e.dxftype() == "CIRCLE"]
    assert any(abs(r - 6.0) < 0.2 or abs(r - 4.0) < 0.2 for r in inner_radii)

    # 4. Verify ETCH entities (countersinks)
    csink_entities = msp.query('*[layer=="ETCH"]')
    assert len(csink_entities) > 0
    csink_radii = [e.dxf.radius for e in csink_entities if e.dxftype() == "CIRCLE"]
    assert any(abs(r - 8.0) < 0.5 for r in csink_radii)

    # 5. Verify MARK entities (centerlines)
    mark_entities = msp.query('*[layer=="MARK"]')
    assert len(mark_entities) > 0


def test_view_export_dxf_default_layers(tmp_path: Path) -> None:
    """Verify default layer mappings when layers argument is omitted."""
    box = bd.Box(50.0, 30.0, 10.0)
    dxf_file = str(tmp_path / "box_default.dxf")

    with BuildDrawing(sheet_size="A4"), TopView(box, center=(100.0, 100.0), scale=1.0) as tv:
        tv.export_dxf(dxf_file)

    doc = ezdxf.readfile(dxf_file)
    msp = doc.modelspace()
    assert "CUT_OUTER" in [layer.dxf.name for layer in doc.layers]
    assert len(msp.query('*[layer=="CUT_OUTER"]')) > 0
