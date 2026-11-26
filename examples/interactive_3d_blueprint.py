"""Interactive 3D-in-2D Blueprint Showcase: Flanged Bearing Housing Assembly.

Builds a multi-part mechanical assembly (housing, bronze bushings, stepped drive shaft,
retaining end cap, and 4x M6 socket head cap screws) and exports both traditional ISO 2D
drawings (SVG/PDF/DXF) and a self-contained WebGL2 interactive HTML blueprint.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import build123d as bd
from build123d.topology import Shape

from build123d_techdraw import (
    DrawingSheet,
    PartInfo,
    SheetSize,
    Theme,
    ViewType,
    add_assembly_bom_and_balloons,
    run_sheet_lint,
    set_part_info,
)


def build_bearing_housing_assembly() -> bd.Compound:
    """Construct a 5-component flanged bearing housing assembly (axis along Y, Z-up).

    The bolted retaining end cap, 4x M6 socket head cap screws, and stepped drive stub sit
    on the ``+Y`` face so standard ISO isometric views ``(1.2, 1.8, 1.2)`` showcase the full
    fastener & flange detail directly.
    """
    axis_z = 42.0
    pcd_r = 25.0
    bolt_angles = [math.radians(45.0 + i * 90.0) for i in range(4)]

    # 1. Flanged Bearing Housing (AL 6061-T6)
    base = bd.Pos(0.0, 0.0, 7.0) * bd.Box(112.0, 52.0, 14.0)
    base = bd.chamfer(base.edges().filter_by(bd.Axis.Z), length=5.0)
    web = bd.Pos(0.0, 0.0, 27.0) * bd.Box(66.0, 48.0, 30.0)
    boss = bd.Pos(0.0, 0.0, axis_z) * bd.Rot(90.0, 0.0, 0.0) * bd.Cylinder(radius=33.0, height=48.0)
    lube_boss = bd.Pos(0.0, 0.0, 74.5) * bd.Cylinder(radius=8.5, height=9.0)
    housing = base + web + boss + lube_boss

    # Internal stepped bore along Y: Ø28 central clearance + Ø36 front/rear bushing seats
    thru_bore = (
        bd.Pos(0.0, 0.0, axis_z) * bd.Rot(90.0, 0.0, 0.0) * bd.Cylinder(radius=14.0, height=60.0)
    )
    front_seat = (
        bd.Pos(0.0, 18.0, axis_z) * bd.Rot(90.0, 0.0, 0.0) * bd.Cylinder(radius=18.0, height=14.0)
    )
    rear_seat = (
        bd.Pos(0.0, -18.0, axis_z) * bd.Rot(90.0, 0.0, 0.0) * bd.Cylinder(radius=18.0, height=14.0)
    )
    lube_port = bd.Pos(0.0, 0.0, 66.0) * bd.Cylinder(radius=3.5, height=32.0)
    housing = housing - thru_bore - front_seat - rear_seat - lube_port

    # 4 base mounting holes (Ø9 THRU)
    for sx in (-42.0, 42.0):
        for sy in (-15.0, 15.0):
            housing = housing - (bd.Pos(sx, sy, 7.0) * bd.Cylinder(radius=4.5, height=20.0))

    # 4 M6 tapped blind holes on the +Y flange face (Y = +12 .. +24)
    for ang in bolt_angles:
        bx = pcd_r * math.cos(ang)
        bz = axis_z + pcd_r * math.sin(ang)
        tap_hole = (
            bd.Pos(bx, 18.0, bz) * bd.Rot(90.0, 0.0, 0.0) * bd.Cylinder(radius=3.1, height=14.0)
        )
        housing = housing - tap_hole

    housing.label = "Flanged Bearing Housing"
    housing.material = "alu_g6061_t6"
    set_part_info(housing, PartInfo(part_no="HSG-401", material_spec="AL 6061-T6"))

    # 2. Bronze Sleeve Bushings (Qty 2, front +Y & rear -Y)
    def _make_bushing(y_barrel: float, y_flange: float) -> Shape[Any]:
        barrel = (
            bd.Pos(0.0, y_barrel, axis_z)
            * bd.Rot(90.0, 0.0, 0.0)
            * bd.Cylinder(radius=17.9, height=11.5)
        )
        flange = (
            bd.Pos(0.0, y_flange, axis_z)
            * bd.Rot(90.0, 0.0, 0.0)
            * bd.Cylinder(radius=21.0, height=3.0)
        )
        bore = (
            bd.Pos(0.0, 0.5 * (y_barrel + y_flange), axis_z)
            * bd.Rot(90.0, 0.0, 0.0)
            * bd.Cylinder(radius=10.0, height=24.0)
        )
        bushing = (barrel + flange) - bore
        bushing.label = "Bronze Sleeve Bushing"
        bushing.material = "brass"
        set_part_info(bushing, PartInfo(part_no="BSH-402", material_spec="C93200 BRONZE"))
        return bushing

    bushing_front = _make_bushing(18.25, 25.5)
    bushing_rear = _make_bushing(-18.25, -25.5)

    # 3. Stepped Hardened Drive Shaft (4140 Alloy Steel)
    journal = (
        bd.Pos(0.0, 1.5, axis_z) * bd.Rot(90.0, 0.0, 0.0) * bd.Cylinder(radius=9.9, height=87.0)
    )
    collar = (
        bd.Pos(0.0, 0.0, axis_z) * bd.Rot(90.0, 0.0, 0.0) * bd.Cylinder(radius=12.8, height=20.0)
    )
    drive_stub = (
        bd.Pos(0.0, 51.5, axis_z) * bd.Rot(90.0, 0.0, 0.0) * bd.Cylinder(radius=7.5, height=15.0)
    )
    shaft = journal + collar + drive_stub
    shaft.label = "Stepped Drive Shaft"
    shaft.material = "alloy_steel"
    set_part_info(shaft, PartInfo(part_no="SHF-403", material_spec="4140 STEEL"))

    # 4. Retaining End Cap (SS 304) on +Y flange face
    cap_body = (
        bd.Pos(0.0, 30.0, axis_z) * bd.Rot(90.0, 0.0, 0.0) * bd.Cylinder(radius=31.0, height=6.0)
    )
    cap_recess = (
        bd.Pos(0.0, 28.0, axis_z) * bd.Rot(90.0, 0.0, 0.0) * bd.Cylinder(radius=21.3, height=3.2)
    )
    cap_bore = (
        bd.Pos(0.0, 30.0, axis_z) * bd.Rot(90.0, 0.0, 0.0) * bd.Cylinder(radius=12.0, height=12.0)
    )
    end_cap = cap_body - cap_recess - cap_bore
    for ang in bolt_angles:
        bx = pcd_r * math.cos(ang)
        bz = axis_z + pcd_r * math.sin(ang)
        bolt_hole = (
            bd.Pos(bx, 30.0, bz) * bd.Rot(90.0, 0.0, 0.0) * bd.Cylinder(radius=3.3, height=12.0)
        )
        end_cap = end_cap - bolt_hole
    end_cap.label = "Retaining End Cap"
    end_cap.material = "stainless"
    set_part_info(end_cap, PartInfo(part_no="CAP-404", material_spec="SS 304"))

    # 5. M6x16 Socket Head Cap Screws (Qty 4, A2 Stainless) on +Y flange PCD
    screws: list[Shape[Any]] = []
    for ang in bolt_angles:
        bx = pcd_r * math.cos(ang)
        bz = axis_z + pcd_r * math.sin(ang)
        shank = bd.Pos(bx, 25.0, bz) * bd.Rot(90.0, 0.0, 0.0) * bd.Cylinder(radius=3.0, height=16.0)
        head = bd.Pos(bx, 36.0, bz) * bd.Rot(90.0, 0.0, 0.0) * bd.Cylinder(radius=5.0, height=6.0)
        socket = bd.Pos(bx, 37.5, bz) * bd.Rot(90.0, 0.0, 0.0) * bd.Cylinder(radius=2.5, height=3.5)
        screw = (shank + head) - socket
        screw.label = "M6x16 Socket Cap Screw"
        screw.material = "stainless"
        set_part_info(screw, PartInfo(part_no="SCR-M6-16", material_spec="A2 STAINLESS"))
        screws.append(screw)

    return bd.Compound(children=[housing, bushing_front, bushing_rear, shaft, end_cap, *screws])


def build_interactive_3d_blueprint_sheet() -> DrawingSheet:
    """Create the complete A3 ISO first-angle technical drawing sheet with BOM and section view."""
    assembly = build_bearing_housing_assembly()

    sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    sheet.set_title_block(
        title="FLANGED BEARING HOUSING ASSEMBLY",
        drawing_number="BRG-400",
        rev="REV A",
        scale="1:1",
        material="SEE BOM",
        finish="ANODIZED / AS MACHINED",
        author="BUILD123D-TECHDRAW",
        project="INTERACTIVE 3D BLUEPRINT",
        third_angle=False,
    )
    sheet.set_notes(
        [
            "1. ALL DIMENSIONS IN MILLIMETERS (mm); ISO 128 FIRST-ANGLE PROJECTION.",
            "2. PRESS-FIT BRONZE BUSHINGS (ITEM 2) FLUSH TO HOUSING SHOULDERS.",
            "3. TORQUE M6 CAP SCREWS (ITEM 5) TO 9.5 N·m IN CROSS PATTERN.",
            "4. OPEN .HTML OUTPUT FOR LIVE WEBGL2 3D SECTION CUT AND BOM EXPLOSION.",
        ]
    )

    scale = 1.0
    # First-angle orthographic layout: Front top-left, Top directly below Front,
    # Section A-A (YZ axial cut through stepped bore & shaft) to the right of Front,
    # and Isometric (ISO 5455 1:2) + BOM balloons on the upper right above the BOM table.
    front = sheet.add_view(
        assembly,
        view_type=ViewType.FRONT,
        center=(90.0, 80.0),
        scale=scale,
        show_hidden=True,
        view_label="FRONT VIEW",
    )
    sheet.add_view(
        assembly,
        view_type=ViewType.TOP,
        center=(90.0, 200.0),
        scale=scale,
        show_hidden=True,
        view_label="TOP VIEW",
    )
    section = sheet.add_section_view(
        assembly,
        cutting_plane=bd.Plane.YZ,
        center=(212.0, 80.0),
        scale=scale,
        view_label="SECTION A-A",
        show_background=True,
        hatch="steel",
    )
    iso = sheet.add_view(
        assembly,
        view_type=ViewType.ISOMETRIC,
        center=(332.0, 108.0),
        scale=0.5,
        show_hidden=False,
        view_label="ISOMETRIC ASSEMBLY",
    )

    # Cutting plane A-A vertically along X=0 through Front view
    cut_x = front.to_sheet_x(0.0)
    sheet.add_section_cutting_line(
        points=[(cut_x, front.min_y - 7.0), (cut_x, front.max_y + 7.0)],
        label="A",
        arrow_dir="left",
    )

    # Key engineering dimensions positioned cleanly with view label clearance
    sheet.add_dimension(
        start=(front.min_x, front.max_y),
        end=(front.max_x, front.max_y),
        offset=10.0,
        text="112.0",
        view=front,
    )
    front.bottom_annotation_y = front.max_y + 13.0

    sheet.add_dimension(
        start=(section.min_x, section.max_y),
        end=(section.max_x, section.max_y),
        offset=10.0,
        text="100.0",
        view=section,
    )
    section.bottom_annotation_y = section.max_y + 13.0

    # Automated assembly BOM table + radiating balloons on the isometric view
    add_assembly_bom_and_balloons(
        sheet=sheet,
        assembly=assembly,
        target_view=iso,
        balloon_distance=30.0,
        balloon_radius=4.2,
    )

    return sheet


def generate_interactive_3d_blueprint(output_dir: Path) -> None:
    """Generate the interactive HTML blueprint alongside standard SVG, PDF, and DXF outputs."""
    output_dir.mkdir(parents=True, exist_ok=True)
    sheet = build_interactive_3d_blueprint_sheet()

    issues = run_sheet_lint(sheet)
    errors = [i for i in issues if i.severity == "ERROR"]
    assert not errors, f"Unexpected lint errors: {errors}"

    sheet.export_html(
        str(output_dir / "interactive_3d_blueprint.html"),
        theme=Theme.DARK_BLUEPRINT,
    )
    sheet.export_svg(str(output_dir / "interactive_3d_blueprint.svg"), theme=Theme.DARK_BLUEPRINT)
    sheet.export_svg(str(output_dir / "interactive_3d_blueprint_white.svg"), theme=Theme.ISO_WHITE)
    sheet.export_pdf(str(output_dir / "interactive_3d_blueprint.pdf"))
    sheet.export_dxf(str(output_dir / "interactive_3d_blueprint.dxf"))
    print("Generated interactive 3D blueprint outputs (HTML, SVG, PDF, DXF).")


if __name__ == "__main__":
    generate_interactive_3d_blueprint(Path(__file__).parent / "output")
