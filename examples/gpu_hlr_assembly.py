"""GPU Visibility-Buffer HLR Assembly Drawing & Benchmark example.

Builds a multi-component finned gearbox housing assembly (>300 B-Rep faces),
projects a full A3 ISO first-angle multi-view engineering drawing using the
GPU visibility-buffer HLR engine, and prints a wall-clock benchmark comparing
exact OCCT ``HLRBRep_Algo`` against GPU Visibility-Buffer HLR.
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

import build123d as bd
from build123d.topology import Shape

from build123d_techdraw import (
    AssemblyBOM,
    BuildDrawing,
    FrontView,
    IsometricView,
    PartInfo,
    RightView,
    SheetSize,
    Theme,
    TitleBlock,
    TopView,
    ViewType,
    is_gpu_enabled,
    run_sheet_lint,
    set_part_info,
)
from build123d_techdraw.core.view import VIEW_VECTORS, _compute_view_projection
from build123d_techdraw.gpu.hlr import HLRMode, count_shape_faces, project_shape_gpu_hlr


def _make_shcs_m6(length: float = 16.0) -> Shape[Any]:
    """Create a simplified ISO 4762 M6 socket head cap screw with hex socket."""
    shank = bd.Cylinder(
        radius=3.0, height=length, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MAX)
    )
    head = bd.Cylinder(
        radius=5.0, height=6.0, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
    )
    hex_socket = bd.Pos(0, 0, 2.5) * bd.extrude(bd.RegularPolygon(radius=2.8, side_count=6), 4.0)
    bolt = bd.Part() + shank + head - hex_socket
    return bolt


def build_gearbox_assembly() -> bd.Compound:
    """Build a multi-component finned gearbox drive housing assembly (>300 faces)."""
    # 1. Finned Aluminum Gearbox Housing
    base_plate = bd.Box(110.0, 96.0, 12.0, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    core_body = bd.Pos(0, 0, 12.0) * bd.Cylinder(
        radius=38.0, height=66.0, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
    )
    housing = bd.Part() + base_plate + core_body

    # 10 radial cooling fins along the housing barrel
    for k in range(10):
        z_fin = 16.0 + k * 6.0
        fin = bd.Pos(0, 0, z_fin) * bd.Cylinder(
            radius=46.0, height=2.2, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
        )
        housing = housing + fin

    # Side boss for inspection cover (+X face)
    side_boss = bd.Pos(36.0, 0, 44.0) * bd.Box(18.0, 44.0, 36.0)
    housing = housing + side_boss

    # Central through bore + 4 base mounting holes
    housing = housing - bd.Pos(0, 0, 39.0) * bd.Cylinder(radius=22.0, height=82.0)
    for sx in (-44.0, 44.0):
        for sy in (-36.0, 36.0):
            housing = housing - bd.Pos(sx, sy, 6.0) * bd.Cylinder(radius=4.5, height=16.0)

    housing.label = "Finned Gearbox Housing"
    housing.material = "alu_g6061_t6"
    set_part_info(housing, PartInfo(part_no="GBX-101", material_spec="AL 6061-T6"))

    # 2. Top Bearing Cover Flange
    cover_base = bd.Pos(0, 0, 78.0) * bd.Cylinder(
        radius=44.0, height=10.0, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
    )
    cover_hub = bd.Pos(0, 0, 88.0) * bd.Cylinder(
        radius=26.0, height=8.0, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
    )
    cover = bd.Part() + cover_base + cover_hub
    cover = cover - bd.Pos(0, 0, 87.0) * bd.Cylinder(radius=16.0, height=24.0)
    for i in range(8):
        ang = math.radians(i * 45.0)
        bx, by = 35.0 * math.cos(ang), 35.0 * math.sin(ang)
        cover = cover - bd.Pos(bx, by, 83.0) * bd.Cylinder(radius=3.3, height=14.0)
    cover.label = "Top Bearing Cover"
    cover.material = "alu_g6061_t6"
    set_part_info(cover, PartInfo(part_no="GBX-102", material_spec="AL 6061-T6"))

    # 3. Stepped Output Shaft + Coupling Flange
    shaft_lower = bd.Pos(0, 0, 10.0) * bd.Cylinder(
        radius=15.0, height=88.0, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
    )
    shaft_upper = bd.Pos(0, 0, 98.0) * bd.Cylinder(
        radius=12.0, height=28.0, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
    )
    coupling = bd.Pos(0, 0, 118.0) * bd.Cylinder(
        radius=24.0, height=8.0, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
    )
    shaft = bd.Part() + shaft_lower + shaft_upper + coupling
    for i in range(6):
        ang = math.radians(i * 60.0)
        shaft = shaft - bd.Pos(17.5 * math.cos(ang), 17.5 * math.sin(ang), 122.0) * bd.Cylinder(
            radius=2.6, height=12.0
        )
    shaft.label = "Stepped Output Shaft"
    shaft.material = "alloy_steel"
    set_part_info(shaft, PartInfo(part_no="GBX-103", material_spec="4140 STEEL"))

    # 4. Bronze Bearing Retainer Collar
    collar = bd.Pos(0, 0, 96.0) * bd.Cylinder(
        radius=21.0, height=10.0, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
    ) - bd.Pos(0, 0, 101.0) * bd.Cylinder(radius=12.2, height=14.0)
    collar.label = "Bearing Retainer Collar"
    collar.material = "alloy_steel"
    set_part_info(collar, PartInfo(part_no="GBX-104", material_spec="BRONZE C93200"))

    # 5. Side Inspection Plate (+X face)
    plate = bd.Pos(47.0, 0, 44.0) * bd.Box(4.0, 40.0, 32.0)
    for dy in (-14.0, 14.0):
        for dz in (-10.0, 10.0):
            plate = plate - bd.Pos(47.0, dy, 44.0 + dz) * bd.Rot(0, 90, 0) * bd.Cylinder(
                radius=3.2, height=8.0
            )
    plate.label = "Side Inspection Plate"
    plate.material = "alloy_steel"
    set_part_info(plate, PartInfo(part_no="GBX-105", material_spec="304 STAINLESS"))

    # 6. Socket Head Cap Screws (8 top + 4 base + 4 side = 16 fasteners)
    parts: list[Shape[Any]] = [housing, cover, shaft, collar, plate]
    bolt_proto = _make_shcs_m6(length=14.0)
    for i in range(8):
        ang = math.radians(i * 45.0)
        bx, by = 35.0 * math.cos(ang), 35.0 * math.sin(ang)
        bolt = bd.Pos(bx, by, 88.0) * bolt_proto
        bolt.label = "M6x14 SHCS"
        bolt.material = "alloy_steel"
        set_part_info(bolt, PartInfo(part_no="ISO-4762-M6", material_spec="12.9 ALLOY"))
        parts.append(bolt)

    for sx in (-44.0, 44.0):
        for sy in (-36.0, 36.0):
            bolt = bd.Pos(sx, sy, 12.0) * bolt_proto
            bolt.label = "M6x14 SHCS"
            bolt.material = "alloy_steel"
            set_part_info(bolt, PartInfo(part_no="ISO-4762-M6", material_spec="12.9 ALLOY"))
            parts.append(bolt)

    for dy in (-14.0, 14.0):
        for dz in (-10.0, 10.0):
            bolt = bd.Pos(49.0, dy, 44.0 + dz) * bd.Rot(0, 90, 0) * bolt_proto
            bolt.label = "M6x14 SHCS"
            bolt.material = "alloy_steel"
            set_part_info(bolt, PartInfo(part_no="ISO-4762-M6", material_spec="12.9 ALLOY"))
            parts.append(bolt)

    return bd.Compound(children=parts)


def generate_gpu_hlr_assembly(output_dir: Path) -> None:
    """Generate the A3 multi-view GPU HLR drawing and print Exact vs GPU timings."""
    output_dir.mkdir(parents=True, exist_ok=True)
    assembly = build_gearbox_assembly()
    n_faces = count_shape_faces(assembly)

    use_gpu = is_gpu_enabled()
    hlr_mode: HLRMode = "gpu" if use_gpu else "exact"

    # Benchmark Exact OCCT HLR vs GPU Visibility-Buffer HLR on the isometric view.
    t0 = time.perf_counter()
    _compute_view_projection(assembly, ViewType.ISOMETRIC, show_hidden=True, hlr="exact")
    exact_s = time.perf_counter() - t0

    if use_gpu:
        lf, lu = VIEW_VECTORS[ViewType.ISOMETRIC]
        # Warm up cached WGSL pipelines once, then measure steady-state projection.
        project_shape_gpu_hlr(bd.Box(10, 10, 10), lf, lu, show_hidden=False, resolution=128)
        gpu_res = project_shape_gpu_hlr(assembly, lf, lu, show_hidden=True, resolution=2048)
        speedup = exact_s / max(gpu_res.stats.total_s, 1e-6)
        print(
            f"[GPU HLR Benchmark] {n_faces} faces, {gpu_res.stats.num_triangles} tris, "
            f"{gpu_res.stats.num_edges} edges | "
            f"Exact OCCT: {exact_s:.3f}s | GPU HLR: {gpu_res.stats.total_s:.3f}s "
            f"(tess={gpu_res.stats.tessellate_s:.3f}s, edges={gpu_res.stats.edges_s:.3f}s, "
            f"gpu={gpu_res.stats.gpu_s:.3f}s, vec={gpu_res.stats.vectorize_s:.3f}s) | "
            f"Speedup: {speedup:.2f}x"
        )

    # Full A3 ISO first-angle multi-view engineering drawing projected with GPU HLR.
    with BuildDrawing(sheet_size=SheetSize.A3_LANDSCAPE) as sheet:
        TitleBlock(
            title="FINNED GEARBOX DRIVE ASSEMBLY",
            drawing_number="GBX-ASM-001",
            rev="A",
            scale="1:2",
            author="GPU HLR ENGINE",
            material="SEE BOM",
        )

        # ISO 128-3 first-angle layout: Front upper-center, Top below Front, Right to the left
        front = FrontView(
            assembly,
            center=(175.0, 95.0),
            scale=0.5,
            show_hidden=True,
            view_label="FRONT",
            hlr=hlr_mode,
        )
        TopView(
            assembly,
            scale=0.5,
            show_hidden=True,
            view_label="TOP",
            align_to=(front, "bottom", 24.0),
            hlr=hlr_mode,
        )
        RightView(
            assembly,
            scale=0.5,
            show_hidden=True,
            view_label="RIGHT",
            align_to=(front, "left", 28.0),
            hlr=hlr_mode,
        )
        iso = IsometricView(
            assembly,
            center=(322.0, 95.0),
            scale=0.5,
            show_hidden=False,
            view_label="ISOMETRIC",
            hlr=hlr_mode,
        )

        # Key overall dimensions on Front view
        front.add_overall_horizontal(offset_y=-10.0)
        front.add_overall_vertical(offset_x=12.0)

        # Automated assembly BOM table and radiating balloons on the isometric view
        AssemblyBOM(assembly, target_view=iso, balloon_distance=42.0)

    issues = run_sheet_lint(sheet)
    errors = [i for i in issues if i.severity.upper() == "ERROR"]
    if errors:
        for err in errors:
            print(f"[LINT ERROR] {err.code}: {err.message}")
        raise RuntimeError(f"gpu_hlr_assembly drawing has {len(errors)} lint error(s).")

    sheet.export_svg(str(output_dir / "gpu_hlr_assembly.svg"), theme=Theme.DARK_BLUEPRINT)
    sheet.export_svg(str(output_dir / "gpu_hlr_assembly_white.svg"), theme=Theme.ISO_WHITE)
    sheet.export_pdf(str(output_dir / "gpu_hlr_assembly.pdf"))
    sheet.export_dxf(str(output_dir / "gpu_hlr_assembly.dxf"))
    print(f"Generated GPU HLR assembly drawing ({n_faces} faces, 0 lint errors) in {output_dir}.")


if __name__ == "__main__":
    generate_gpu_hlr_assembly(Path(__file__).parent / "output")
