"""Unit tests for the interactive 3D-in-2D HTML5 WebGL2 blueprint renderer."""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import build123d as bd
import numpy as np
import pytest

from build123d_techdraw import (
    DrawingSheet,
    HTMLRenderer,
    PartInfo,
    SheetSize,
    Theme,
    ViewType,
    add_assembly_bom_and_balloons,
    add_standard_multiview,
    export_interactive_html,
    render_interactive_html,
    set_part_info,
)


def _extract_scene_json(html_doc: str) -> dict[str, Any]:
    match = re.search(
        r'<script id="techdraw-scene-data" type="application/json">(.*?)</script>',
        html_doc,
        re.DOTALL,
    )
    assert match is not None, "Missing embedded techdraw-scene-data JSON script"
    payload = json.loads(match.group(1).replace("<\\/", "</"))
    assert isinstance(payload, dict)
    return payload


def _extract_viewer_js(html_doc: str) -> str:
    scripts = re.findall(r"<script>(.*?)</script>", html_doc, re.DOTALL)
    assert len(scripts) == 1, f"Expected 1 inline viewer <script>, found {len(scripts)}"
    return scripts[0]


def _decode_f32(b64: str) -> np.ndarray[Any, np.dtype[np.float32]]:
    raw = base64.b64decode(b64)
    return np.frombuffer(raw, dtype="<f4")


def _decode_u32(b64: str) -> np.ndarray[Any, np.dtype[np.uint32]]:
    raw = base64.b64decode(b64)
    return np.frombuffer(raw, dtype="<u4")


def _make_multi_part_assembly() -> bd.Compound:
    housing = bd.Box(80.0, 50.0, 24.0) - bd.Cylinder(radius=10.0, height=40.0)
    housing.label = "Main Housing"
    housing.material = "alu_g6061_t6"
    set_part_info(housing, PartInfo(part_no="HSG-001", material_spec="AL 6061-T6"))

    bushing = bd.Pos(0.0, 0.0, 14.0) * (
        bd.Cylinder(radius=9.8, height=16.0) - bd.Cylinder(radius=6.0, height=20.0)
    )
    bushing.label = "Bronze Bushing"
    bushing.material = "brass"
    set_part_info(bushing, PartInfo(part_no="BSH-002", material_spec="C93200 BRONZE"))

    pin_a = bd.Pos(26.0, 14.0, 16.0) * bd.Cylinder(radius=4.0, height=20.0)
    pin_a.label = "Dowel Pin"
    pin_a.material = "alloy_steel"
    set_part_info(pin_a, PartInfo(part_no="PIN-003", material_spec="4140 STEEL"))

    pin_b = bd.Pos(-26.0, -14.0, 16.0) * bd.Cylinder(radius=4.0, height=20.0)
    pin_b.label = "Dowel Pin"
    pin_b.material = "alloy_steel"
    set_part_info(pin_b, PartInfo(part_no="PIN-003", material_spec="4140 STEEL"))

    return bd.Compound(children=[housing, bushing, pin_a, pin_b])


def test_html_self_contained_single_part_and_buffers(tmp_path: Path) -> None:
    part = bd.Box(40.0, 30.0, 16.0) - bd.Cylinder(radius=6.0, height=30.0)
    sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE)
    sheet.set_title_block(title="BEARING BLOCK", drawing_number="BLK-100")
    sheet.add_view(part, view_type=ViewType.FRONT, center=(100.0, 100.0))
    sheet.add_view(part, view_type=ViewType.ISOMETRIC, center=(210.0, 100.0))

    html_doc = HTMLRenderer.render_sheet(sheet, theme=Theme.DARK_BLUEPRINT)
    assert html_doc.lstrip().startswith("<!DOCTYPE html>")
    assert "</html>" in html_doc
    assert 'data-theme="dark-blueprint"' in html_doc
    assert 'class="theme-dark-blueprint"' in html_doc
    assert "BEARING BLOCK" in html_doc
    assert "BLK-100" in html_doc

    # Strictly zero external script/CSS/CDN dependencies
    assert re.search(r"<script[^>]+src=", html_doc, re.IGNORECASE) is None
    assert re.search(r"<link[^>]+href=", html_doc, re.IGNORECASE) is None
    assert "@import" not in html_doc
    # No http:// or https:// URLs anywhere outside XML namespace declarations
    stripped_xmlns = re.sub(r'xmlns(?::\w+)?="http://www\.w3\.org/[^"]+"', "", html_doc)
    assert "http://" not in stripped_xmlns
    assert "https://" not in stripped_xmlns

    # Decode base64 binary mesh buffers and validate geometry invariants
    scene = _extract_scene_json(html_doc)
    assert scene["initial_theme"] == "dark_blueprint"
    assert scene["iso_view_bounds"] is not None
    assert len(scene["parts"]) == 1

    p0 = scene["parts"][0]
    assert p0["bom_item_no"] is None
    assert p0["explode_vector"] == [0.0, 0.0, 0.0]
    assert p0["triangle_count"] > 0
    assert p0["edge_segment_count"] > 0

    positions = _decode_f32(p0["positions_b64"])
    normals = _decode_f32(p0["normals_b64"])
    indices = _decode_u32(p0["indices_b64"])
    edges = _decode_f32(p0["edges_b64"])

    assert positions.size == p0["vertex_count"] * 3
    assert normals.size == p0["vertex_count"] * 3
    assert indices.size == p0["triangle_count"] * 3
    assert edges.size == p0["edge_segment_count"] * 6
    assert np.all(np.isfinite(positions))
    assert np.all(np.isfinite(normals))
    assert np.all(np.isfinite(edges))
    assert int(indices.max()) < p0["vertex_count"]

    norm_lengths = np.linalg.norm(normals.reshape(-1, 3), axis=1)
    assert np.allclose(norm_lengths, 1.0, atol=1e-3)

    # Verify export_html and export_interactive_html write valid files
    out_sheet = tmp_path / "via_sheet.html"
    sheet.export_html(str(out_sheet), theme=Theme.DARK_BLUEPRINT)
    assert out_sheet.exists()
    assert out_sheet.read_text(encoding="utf-8") == html_doc

    out_fn = tmp_path / "via_fn.html"
    export_interactive_html(sheet, out_fn, theme=Theme.DARK_BLUEPRINT)
    assert out_fn.read_text(encoding="utf-8") == html_doc


def test_html_assembly_bom_metadata_and_explode_vectors() -> None:
    assembly = _make_multi_part_assembly()
    sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    sheet.set_title_block(title="HOUSING ASSEMBLY", drawing_number="ASM-200")
    layout = add_standard_multiview(sheet, assembly, scale="auto")
    assert layout.isometric is not None
    add_assembly_bom_and_balloons(sheet, assembly, target_view=layout.isometric)

    html_doc = render_interactive_html(sheet, theme=Theme.DARK_BLUEPRINT)
    scene = _extract_scene_json(html_doc)

    assert len(scene["bom_items"]) == 3
    assert len(scene["parts"]) == 4

    by_name: dict[str, list[dict[str, Any]]] = {}
    for part in scene["parts"]:
        by_name.setdefault(part["name"], []).append(part)
        assert part["bom_item_no"] is not None
        assert part["triangle_count"] > 0
        assert part["edge_segment_count"] > 0

    assert set(by_name.keys()) == {"Main Housing", "Bronze Bushing", "Dowel Pin"}
    assert len(by_name["Dowel Pin"]) == 2
    assert all(p["part_no"] == "PIN-003" for p in by_name["Dowel Pin"])
    assert all(p["qty"] == 2 for p in by_name["Dowel Pin"])

    # Anchor housing stays centered; pins & bushing get non-zero explode vectors
    housing_part = by_name["Main Housing"][0]
    assert housing_part["explode_vector"] == [0.0, 0.0, 0.0]

    non_zero_explode = [
        p for p in scene["parts"] if float(np.linalg.norm(p["explode_vector"])) > 1.0
    ]
    assert len(non_zero_explode) == 3

    # Opposite pins explode in opposite directions along X/Y
    v_a = np.asarray(by_name["Dowel Pin"][0]["explode_vector"], dtype=float)
    v_b = np.asarray(by_name["Dowel Pin"][1]["explode_vector"], dtype=float)
    assert float(v_a[0] * v_b[0]) < 0.0


def test_html_themes_layout_modes_and_empty_sheet() -> None:
    box = bd.Box(25.0, 20.0, 12.0)
    sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE)
    sheet.add_view(box, view_type=ViewType.FRONT, center=(120.0, 100.0))

    html_white = HTMLRenderer.render_sheet(sheet, theme=Theme.ISO_WHITE, initial_layout="inspector")
    assert 'data-theme="iso-white"' in html_white
    assert 'class="theme-iso-white"' in html_white
    assert 'class="workspace layout-inspector"' in html_white
    scene_white = _extract_scene_json(html_white)
    assert scene_white["initial_theme"] == "iso_white"
    assert scene_white["initial_layout"] == "inspector"

    # String alias "print" resolves to ISO_WHITE
    html_print = render_interactive_html(sheet, theme="print")
    assert 'data-theme="iso-white"' in html_print

    # Sheet without any 3D views degrades cleanly
    empty_sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE)
    html_empty = render_interactive_html(empty_sheet)
    scene_empty = _extract_scene_json(html_empty)
    assert scene_empty["parts"] == []
    assert "<svg" in html_empty


def test_sharp_crease_edges_exclude_cylinder_seam() -> None:
    """Plain cylinder should emit circular rim edges only, excluding its vertical seam edge."""
    cyl = bd.Cylinder(radius=10.0, height=30.0)
    sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE)
    sheet.add_view(cyl, view_type=ViewType.ISOMETRIC, center=(120.0, 100.0))

    scene = HTMLRenderer.serialize_sheet_scene(sheet)
    edges = _decode_f32(scene["parts"][0]["edges_b64"]).reshape(-1, 2, 3)
    assert edges.shape[0] > 8
    # Every segment must lie on either the top (z=+15) or bottom (z=-15) rim, never spanning Z
    dz = np.abs(edges[:, 1, 2] - edges[:, 0, 2])
    assert float(dz.max()) < 1e-3


def test_html_shaders_ui_controls_and_js_syntax(tmp_path: Path) -> None:
    box = bd.Box(30.0, 20.0, 10.0)
    sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE)
    sheet.add_view(box, view_type=ViewType.ISOMETRIC, center=(120.0, 100.0))
    html_doc = HTMLRenderer.render_sheet(sheet)
    js_code = _extract_viewer_js(html_doc)

    # Shaders, uniforms, shading modes, camera presets, and theme sync present
    for token in (
        "#version 300 es",
        "uShadingMode",
        "uClipEnabled",
        "uClipNormal",
        "uClipOffset",
        "uPartOffset",
        "uHatchPitch",
        "!gl_FrontFacing",
        "fwidth(",
        "discard;",
        "applyTheme",
        "theme-iso-white",
        "theme-dark-blueprint",
        'data-shading="blueprint"',
        'data-shading="iso"',
        'data-shading="xray"',
        'data-shading="wireframe"',
        'data-cam="iso"',
        'data-cam="front"',
        'data-cam="top"',
        'data-cam="right"',
        'data-cam="reset"',
        "slider-section",
        "slider-explode",
        "webgl-fallback",
    ):
        assert token in html_doc, f"Expected token missing from HTML output: {token}"

    node_bin = shutil.which("node")
    if node_bin is not None:
        js_file = tmp_path / "viewer.js"
        js_file.write_text(js_code, encoding="utf-8")
        proc = subprocess.run(
            [node_bin, "--check", str(js_file)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, f"JS syntax check failed:\n{proc.stderr}"


@pytest.mark.parametrize("theme", [Theme.DARK_BLUEPRINT, Theme.ISO_WHITE])
def test_interactive_3d_blueprint_example_module(theme: Theme) -> None:
    import importlib.util

    example_path = (
        Path(__file__).resolve().parent.parent / "examples" / "interactive_3d_blueprint.py"
    )
    spec = importlib.util.spec_from_file_location("interactive_3d_blueprint", example_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    sheet = mod.build_interactive_3d_blueprint_sheet()
    assert isinstance(sheet, DrawingSheet)
    errors = [i for i in sheet.lint() if i.severity == "ERROR"]
    assert not errors, f"Lint errors in example sheet: {errors}"

    html_doc = render_interactive_html(sheet, theme=theme)
    scene = _extract_scene_json(html_doc)
    assert len(scene["bom_items"]) == 5
    assert len(scene["parts"]) == 9
