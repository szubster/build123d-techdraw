"""GPU-accelerated shaded technical drawing views showcase (Gooch, Blueprint, BOM colors, X-Ray)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import build123d as bd
from build123d.topology import Shape

import build123d_techdraw as td
from build123d_techdraw.core.view import VIEW_VECTORS, ViewType
from build123d_techdraw.gpu import ViewStyle, render_shaded_view
from build123d_techdraw.renderers.theme import Theme


def build_pump_flange_assembly() -> bd.Compound:
    """Build a multi-part CNC-machined bearing/pump flange housing assembly."""
    # 1. Cast/CNC aluminium housing with mounting flange, neck, stiffening ribs, and stepped bore
    flange = bd.Cylinder(42.0, 10.0, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    neck = bd.Pos(0, 0, 10.0) * bd.Cylinder(
        24.0, 34.0, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
    )
    ribs = [
        bd.Rot(0, 0, angle)
        * bd.Pos(27.0, 0, 10.0)
        * bd.Box(
            20.0,
            6.0,
            24.0,
            align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN),
        )
        for angle in (45.0, 135.0, 225.0, 315.0)
    ]
    housing = flange + neck
    for rib in ribs:
        housing = housing + rib

    # Stepped internal through-bore + counterbore seat for the bushing
    housing = housing - bd.Cylinder(
        13.0, 60.0, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
    )
    housing = housing - bd.Pos(0, 0, 24.0) * bd.Cylinder(
        17.5, 25.0, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
    )

    # 4 mounting holes on Ø66 bolt circle
    for angle in (0.0, 90.0, 180.0, 270.0):
        rad = math.radians(angle)
        hx, hy = 33.0 * math.cos(rad), 33.0 * math.sin(rad)
        housing = housing - bd.Pos(hx, hy, -1.0) * bd.Cylinder(
            4.5, 14.0, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
        )
    housing.label = "Flange Housing"

    # 2. Bronze flanged bearing bushing pressed into the upper counterbore
    bushing_barrel = bd.Pos(0, 0, 24.0) * bd.Cylinder(
        17.4, 24.0, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
    )
    bushing_lip = bd.Pos(0, 0, 44.0) * bd.Cylinder(
        21.0, 4.0, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
    )
    bushing = (bushing_barrel + bushing_lip) - bd.Pos(0, 0, 22.0) * bd.Cylinder(
        12.5, 30.0, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
    )
    bushing.label = "Bronze Bushing"

    # 3. Machined steel retainer collar seated at the lower bore
    collar = bd.Pos(0, 0, -4.0) * (
        bd.Cylinder(19.0, 4.0, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
        - bd.Cylinder(13.0, 6.0, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    )
    collar.label = "Retainer Collar"

    # 4. 4 socket-head mounting bolts seated on the flange
    bolts: list[Shape[Any]] = []
    for idx, angle in enumerate((0.0, 90.0, 180.0, 270.0), start=1):
        rad = math.radians(angle)
        bx, by = 33.0 * math.cos(rad), 33.0 * math.sin(rad)
        head = bd.Pos(bx, by, 10.0) * bd.Cylinder(
            7.0, 6.0, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
        )
        socket = bd.Pos(bx, by, 13.0) * bd.Cylinder(
            3.5, 4.0, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
        )
        bolt = head - socket
        bolt.label = f"M8 Cap Screw {idx}"
        bolts.append(bolt)

    return bd.Compound(children=[housing, bushing, collar, *bolts])


def generate_shaded_technical_views(output_dir: Path) -> None:
    """Generate an A3 technical drawing and standalone PNGs showcasing GPU shaded views."""
    output_dir.mkdir(parents=True, exist_ok=True)
    assembly = build_pump_flange_assembly()

    sheet = td.DrawingSheet(td.SheetSize.A3_LANDSCAPE)
    sheet.set_title_block(
        title="PUMP FLANGE HOUSING ASSEMBLY — SHADED VIEWS",
        drawing_no="TD-GPU-001",
        rev="A",
        author="build123d-techdraw",
        material="AL 6061-T6 / C93200 BRONZE / 304 SS",
        scale="1:1",
    )
    sheet.set_notes(
        [
            "1. ALL DIMENSIONS IN MILLIMETRES UNLESS OTHERWISE SPECIFIED.",
            "2. GENERAL TOLERANCES PER ISO 2768-mK.",
            "3. SHADED VIEWS RENDERED VIA WGPU WGSL SHADERS UNDER EXACT VECTOR EDGES.",
        ]
    )

    # First-angle ISO orthographic wireframe views on the left
    front = sheet.add_view(
        assembly,
        ViewType.FRONT,
        center=(78.0, 72.0),
        scale=1.0,
        show_hidden=True,
        view_label="FRONT",
    )
    top = sheet.add_view(
        assembly,
        ViewType.TOP,
        center=(78.0, 178.0),
        scale=1.0,
        show_hidden=False,
        view_label="TOP",
    )
    front.add_overall_horizontal(offset_y=12.0)
    front.add_overall_vertical(offset_x=-14.0)
    top.add_overall_horizontal(offset_y=12.0)
    top.centermark(0.0, 0.0, size=44.0)

    # 2x2 gallery of GPU-shaded isometric views on the right
    sheet.add_view(
        assembly,
        ViewType.ISOMETRIC,
        center=(198.0, 84.0),
        scale=1.0,
        show_hidden=False,
        view_label="GOOCH + SSAO",
        style=ViewStyle.GOOCH,
        shading_dpi=220,
        ssao=True,
    )
    sheet.add_view(
        assembly,
        ViewType.ISOMETRIC,
        center=(322.0, 84.0),
        scale=1.0,
        show_hidden=False,
        view_label="BLUEPRINT SHADED",
        style=ViewStyle.BLUEPRINT_SHADED,
        shading_dpi=220,
        ssao=True,
    )
    sheet.add_view(
        assembly,
        ViewType.ISOMETRIC,
        center=(198.0, 192.0),
        scale=1.0,
        show_hidden=False,
        view_label="BOM PART COLORS",
        style=ViewStyle.SHADED_WITH_EDGES,
        shading_dpi=220,
        color_by_part=True,
        ssao=True,
    )
    sheet.add_view(
        assembly,
        ViewType.ISOMETRIC,
        center=(322.0, 192.0),
        scale=1.0,
        show_hidden=False,
        view_label="X-RAY INSPECTION",
        style=ViewStyle.XRAY,
        shading_dpi=220,
        ssao=False,
    )

    sheet.export_svg(str(output_dir / "shaded_technical_views.svg"), theme=Theme.DARK_BLUEPRINT)
    sheet.export_svg(str(output_dir / "shaded_technical_views_white.svg"), theme=Theme.ISO_WHITE)
    sheet.export_pdf(str(output_dir / "shaded_technical_views.pdf"), theme=Theme.DARK_BLUEPRINT)

    # Standalone high-resolution PNG renders of each shader style
    look_from, look_up = VIEW_VECTORS[ViewType.ISOMETRIC]
    standalone_specs: list[tuple[str, ViewStyle, bool, Theme]] = [
        ("shaded_gooch.png", ViewStyle.GOOCH, False, Theme.DARK_BLUEPRINT),
        ("shaded_blueprint.png", ViewStyle.BLUEPRINT_SHADED, False, Theme.DARK_BLUEPRINT),
        ("shaded_bom_colors.png", ViewStyle.SHADED_WITH_EDGES, True, Theme.ISO_WHITE),
        ("shaded_xray.png", ViewStyle.XRAY, False, Theme.DARK_BLUEPRINT),
    ]
    for filename, style, by_part, theme in standalone_specs:
        rendered = render_shaded_view(
            assembly,
            look_from,
            look_up,
            style=style,
            shading_dpi=300,
            color_by_part=by_part,
            ssao=style is not ViewStyle.XRAY,
            shading_theme=theme,
        )
        rendered.images[theme].save(output_dir / filename, format="PNG")


if __name__ == "__main__":
    generate_shaded_technical_views(Path(__file__).resolve().parent / "output")
