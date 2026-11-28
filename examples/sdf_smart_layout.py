"""Distance-Field (SDF / Jump Flooding) smart annotation and callout layout example.

Demonstrates an L-shaped stepped CNC hydraulic manifold block with bores,
counterbores, and a stiffening rib where coarse view AABBs waste large concave
whitespace pockets. Uses WebGPU JFA+1 (or exact CPU SciPy EDT fallback) to compute
a 2D Euclidean sheet clearance field and place auto-dimensions, hole callouts,
BOM balloons, and leader notes with zero ``run_sheet_lint`` warnings.
"""

from __future__ import annotations

from pathlib import Path

import build123d as bd

from build123d_techdraw import (
    DrawingSheet,
    SheetSize,
    Theme,
    add_standard_multiview,
    run_sheet_lint,
)
from build123d_techdraw.gpu.sdf import compute_sheet_sdf
from build123d_techdraw.layout.auto_dimension import (
    auto_dimension_sheet,
    place_balloons_sdf,
    place_leaders_sdf,
)


def build_cnc_manifold_block() -> bd.Solid:
    """Build an L-shaped stepped CNC manifold block with bores, counterbores, and rib."""
    # Lower L-shaped manifold deck: 84 x 62 x 16 mm with a bold 48 x 32 mm concave notch
    deck_full = bd.Pos(0.0, 0.0, 8.0) * bd.Box(84.0, 62.0, 16.0)
    concave_cut = bd.Pos(18.0, 15.0, 8.0) * bd.Box(48.0, 32.0, 24.0)
    body = deck_full - concave_cut

    # Tall vertical valve tower on the -X / -Y corner (bold L-profile in Front view too)
    tower = bd.Pos(-26.0, -15.0, 31.0) * bd.Box(32.0, 32.0, 30.0)
    body = body + tower

    # Mid-height stepped mounting flange on the -X / +Y wing
    mid_step = bd.Pos(-26.0, 15.0, 20.0) * bd.Box(32.0, 28.0, 8.0)
    body = body + mid_step

    # Stiffening gusset rib bracing the vertical valve tower onto the lower +X deck
    rib_sketch = bd.Polygon(
        [(-10.0, 16.0), (14.0, 16.0), (-10.0, 38.0)],
        align=None,
    )
    rib_face = bd.Plane.XZ.offset(15.0) * rib_sketch
    rib = bd.extrude(rib_face, amount=3.5, both=True)
    body = body + rib

    # Machined bores and counterbores:
    # 1. Primary supply port with counterbore on the +X lower wing
    cbore_through = bd.Pos(26.0, -16.0, 8.0) * bd.Cylinder(radius=5.0, height=28.0)
    cbore_pocket = bd.Pos(26.0, -16.0, 14.0) * bd.Cylinder(radius=8.5, height=4.0)
    body = body - cbore_through - cbore_pocket

    # 2. Secondary gallery bore near the inner concave elbow
    elbow_bore = bd.Pos(2.0, -16.0, 8.0) * bd.Cylinder(radius=4.0, height=28.0)
    body = body - elbow_bore

    # 3. Pair of Ø6 mounting bores on the -X / +Y stepped flange
    mnt_1 = bd.Pos(-32.0, 19.0, 12.0) * bd.Cylinder(radius=3.0, height=34.0)
    mnt_2 = bd.Pos(-19.0, 19.0, 12.0) * bd.Cylinder(radius=3.0, height=34.0)
    body = body - mnt_1 - mnt_2

    # 4. Horizontal valve spool cross-bore along X through the tall tower
    spool_bore = (
        bd.Pos(-26.0, -15.0, 36.0) * bd.Rot(0.0, 90.0, 0.0) * bd.Cylinder(radius=4.5, height=44.0)
    )
    body = body - spool_bore

    solids = body.solids()
    assert solids, "Expected solid geometry for CNC manifold block."
    return solids[0]


def generate_sdf_smart_layout(output_dir: Path, *, gpu: bool | None = None) -> None:
    """Generate the SDF smart-layout CNC manifold drawing and clearance heatmap."""
    output_dir.mkdir(parents=True, exist_ok=True)

    part = build_cnc_manifold_block()

    sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    sheet.set_title_block(
        title="CNC HYDRAULIC MANIFOLD",
        subtitle="SDF / JFA SMART ANNOTATION LAYOUT",
        drawing_number="MNF-204-SDF",
        rev="REV A",
        date="2026-11-21",
        author="TECHDRAW SDF",
        material="AL 7075-T6",
        finish="HARD ANODIZE TYPE III",
    )

    # Populate BOM table (the SDF automatically reserves its exact screen footprint)
    sheet.add_bom_item(1, 1, "MNF-204-01", "L-STEPPED MANIFOLD BODY", "AL 7075-T6")
    sheet.add_bom_item(2, 1, "PRT-G10-02", "PRIMARY SUPPLY PORT (CBORE)", "ISO 228-1")
    sheet.add_bom_item(3, 1, "PRT-GAL-03", "INNER GALLERY TRANSFER BORE", "REAM H7")
    sheet.add_bom_item(4, 2, "MNT-M06-04", "MOUNTING FLANGE CLEARANCE", "ISO 273")
    sheet.add_bom_item(5, 1, "SPL-D09-05", "CROSS-DRILLED SPOOL BORE", "HONED Ra 0.8")
    sheet.add_bom_item(6, 1, "RIB-GUS-06", "TOWER STIFFENING GUSSET RIB", "AS MACHINED")

    # Standard ISO first-angle multiview at 1:1 with generous spacing for clean ISO stacks
    layout = add_standard_multiview(
        sheet=sheet,
        shape=part,
        scale=1.0,
        spacing=34.0,
        show_hidden=True,
        auto_dimensions=False,
        auto_centerlines=True,
    )
    assert layout.front is not None
    assert layout.top is not None
    assert layout.right is not None
    assert layout.isometric is not None

    # Compute the 2D sheet clearance field (GPU JFA+1 when available, else exact SciPy EDT)
    sdf = compute_sheet_sdf(sheet, resolution_px_per_mm=2.0, gpu=gpu)

    # 1. Opt-in SDF-guided auto-dimensioning (stacked ISO dims + SDF hole callouts)
    auto_dimension_sheet(sheet, part, use_sdf=True, gpu=gpu, sdf=sdf)

    # 2. SDF-placed BOM balloons around the concave Front and Top views
    front_targets: list[tuple[int, tuple[float, float]]] = [
        (1, layout.front.to_sheet_3d(-26.0, -15.0, 41.0)),  # Valve tower body
        (5, layout.front.to_sheet_3d(-26.0, -15.0, 36.0)),  # Cross-drilled spool bore
        (6, layout.front.to_sheet_3d(1.0, -15.0, 23.0)),  # Gusset rib facing concave pocket
    ]
    top_targets: list[tuple[int, tuple[float, float]]] = [
        (2, layout.top.to_sheet_3d(26.0, -16.0, 16.0)),  # Counterbored supply port
        (3, layout.top.to_sheet_3d(2.0, -16.0, 16.0)),  # Inner gallery bore near L-notch
        (4, layout.top.to_sheet_3d(-32.0, 19.0, 24.0)),  # Mounting flange hole
    ]
    place_balloons_sdf(sheet, layout.front, front_targets, gpu=gpu, radius_mm=4.0, sdf=sdf)
    place_balloons_sdf(sheet, layout.top, top_targets, gpu=gpu, radius_mm=4.0, sdf=sdf)

    # 3. SDF-placed engineering leader notes on the isometric view
    leader_targets: list[tuple[tuple[float, float], str | list[str]]] = [
        (layout.isometric.to_sheet_3d(-26.0, -15.0, 46.0), "VALVE TOWER DATUM"),
        (layout.isometric.to_sheet_3d(26.0, -16.0, 16.0), "PRIMARY SUPPLY PORT"),
        (layout.isometric.to_sheet_3d(2.0, -15.0, 24.0), "7 mm GUSSET RIB"),
    ]
    place_leaders_sdf(sheet, layout.isometric, leader_targets, gpu=gpu, sdf=sdf)

    # Verify zero drafting linter defects
    lint_issues = run_sheet_lint(sheet)
    if lint_issues:
        details = "; ".join(f"{i.code}: {i.message}" for i in lint_issues)
        raise RuntimeError(f"Expected 0 lint issues on SDF smart layout, got: {details}")

    # Export blueprint outputs (dark & white SVG, PDF, DXF) + clearance heatmap PNG
    sheet.export_svg(str(output_dir / "sdf_smart_layout.svg"), theme=Theme.DARK_BLUEPRINT)
    sheet.export_svg(str(output_dir / "sdf_smart_layout_white.svg"), theme=Theme.ISO_WHITE)
    sheet.export_pdf(str(output_dir / "sdf_smart_layout.pdf"))
    sheet.export_dxf(str(output_dir / "sdf_smart_layout.dxf"))

    heatmap = sdf.render_heatmap_image(theme=Theme.DARK_BLUEPRINT)
    heatmap.save(output_dir / "sdf_smart_layout_heatmap.png", format="PNG")
    print(
        f"Generated SDF smart layout outputs (backend={sdf.backend}, "
        f"annotations={len(sheet.annotations)}, lint_issues=0)."
    )


if __name__ == "__main__":
    generate_sdf_smart_layout(Path(__file__).parent / "output")
