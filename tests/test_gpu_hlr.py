"""Tests for GPU Visibility-Buffer Hidden Line Removal (GPU HLR).

Covers:
- Visual & geometric parity against exact OCCT ``HLRBRep_Algo`` across canonical CAD shapes
- Cross-backend golden raster stability (hardware RTX 3070 + software Vulkan Lavapipe)
- Visibility classification correctness (visible, fully occluded, and split partial occlusion)
- Opt-in / ``auto`` / fallback / error-handling behaviour (CPU-safe, runs with ``GPU=0``)
- Builder / SheetView / ``update_shape()`` / multi-format export integration
- Complex multi-hundred-face assembly performance & stats reporting
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import build123d as bd
import pytest
import svgelements
from build123d.topology import Shape
from PIL import Image, ImageDraw

import build123d_techdraw as td
from build123d_techdraw.core.view import (
    VIEW_VECTORS,
    ViewType,
    _compute_view_projection,
    _ProjectedViewPayload,
    project_shape_to_view,
)
from build123d_techdraw.gpu import (
    GPU_ENV_VAR,
    GPUHLRResult,
    GPUHLRStats,
    count_shape_faces,
    is_gpu_available,
    project_shape_gpu_hlr,
    reset_gpu_cache,
    resolve_hlr_backend,
)
from tests.raster_diff import assert_images_match, compare_images

GOLDEN_DIR = Path(__file__).parent / "golden_rasters"


@pytest.fixture(autouse=True)
def _clean_gpu_runtime(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Keep GPU cache and env var isolated between tests."""
    reset_gpu_cache()
    yield
    reset_gpu_cache()
    monkeypatch.undo()


# ---------------------------------------------------------------------------
# Helpers: canonical test shapes & deterministic vector-to-raster renderer
# ---------------------------------------------------------------------------


def _make_box_with_hole_and_pocket() -> Shape[Any]:
    return (
        bd.Box(40.0, 24.0, 16.0)
        - bd.Cylinder(5.0, 16.0)
        - bd.Pos(10.0, 0.0, 4.0) * bd.Box(10.0, 10.0, 8.0)
    )


def _make_cylinder_with_transverse_bore() -> Shape[Any]:
    return bd.Cylinder(12.0, 36.0) - bd.Rot(0.0, 90.0, 0.0) * bd.Cylinder(5.0, 30.0)


def _make_filleted_l_bracket() -> Shape[Any]:
    raw = bd.Box(40.0, 20.0, 8.0) + bd.Pos(-16.0, 0.0, 14.0) * bd.Box(8.0, 20.0, 20.0)
    return bd.fillet(raw.edges().filter_by(bd.Axis.Y), 2.5)


def _make_multi_solid_assembly() -> bd.Compound:
    return bd.Compound(
        children=[
            bd.Box(40.0, 30.0, 8.0),
            bd.Pos(0.0, 0.0, 12.0) * bd.Cylinder(8.0, 16.0),
            bd.Pos(12.0, 8.0, 9.0) * bd.Cylinder(3.0, 10.0),
            bd.Pos(-12.0, -8.0, 9.0) * bd.Cylinder(3.0, 10.0),
        ]
    )


def _rasterize_view_payload(
    payload: _ProjectedViewPayload,
    *,
    size: int = 384,
    pad: int = 20,
) -> Image.Image:
    """Rasterize a projected SVG payload to RGBA with distinct visible vs hidden styles."""
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{payload.vb_w}" height="{payload.vb_h}">'
        f"{payload.inner_content}</svg>"
    )
    img = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)
    scale = (size - 2 * pad) / max(payload.vb_w, payload.vb_h, 1e-6)
    ox = (size - payload.vb_w * scale) * 0.5 - payload.vb_min_x * scale
    oy = (size - payload.vb_h * scale) * 0.5 - payload.vb_min_y * scale

    parsed = svgelements.SVG.parse(io.StringIO(svg))
    for el in parsed.elements():
        if not isinstance(el, svgelements.Shape):
            continue
        path = (el if isinstance(el, svgelements.Path) else svgelements.Path(el)).reify()
        stroke_val = str(el.values.get("stroke", ""))
        is_hidden = "hidden" in stroke_val
        color = (180, 195, 215, 255) if is_hidden else (10, 20, 40, 255)
        width = 1 if is_hidden else 2
        for seg in path:
            n_steps = 2 if isinstance(seg, svgelements.Line) else 24
            pts: list[tuple[float, float]] = []
            for k in range(n_steps + 1):
                pt = seg.point(k / n_steps)
                if pt is not None:
                    pts.append((float(pt.x) * scale + ox, float(pt.y) * scale + oy))
            if len(pts) >= 2:
                draw.line(pts, fill=color, width=width)
    return img


# ---------------------------------------------------------------------------
# 1. Opt-in, Auto, Fallback & Error Handling (CPU-safe, runs without GPU)
# ---------------------------------------------------------------------------


def test_top_level_hlr_exports() -> None:
    assert td.project_shape_gpu_hlr is project_shape_gpu_hlr
    assert td.GPUHLRResult is GPUHLRResult
    assert td.GPUHLRStats is GPUHLRStats


def test_count_shape_faces_and_invalid_args() -> None:
    box = bd.Box(20.0, 20.0, 20.0)
    assert count_shape_faces(box) == 6
    assert count_shape_faces(box.wrapped) == 6

    with pytest.raises(ValueError, match="Invalid hlr mode"):
        resolve_hlr_backend(box, "invalid")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="resolution"):
        project_shape_gpu_hlr(box, (0.0, -1.0, 0.0), (0.0, 0.0, 1.0), resolution=16)


def test_exact_mode_works_with_gpu_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GPU_ENV_VAR, "0")
    box = bd.Box(30.0, 20.0, 10.0)

    default_payload = _compute_view_projection(box, ViewType.FRONT, show_hidden=True)
    exact_payload = _compute_view_projection(box, ViewType.FRONT, show_hidden=True, hlr="exact")
    assert exact_payload.inner_content == default_payload.inner_content
    assert exact_payload.exact_w == pytest.approx(30.0, abs=1e-3)
    assert exact_payload.exact_h == pytest.approx(10.0, abs=1e-3)


def test_auto_mode_falls_back_when_gpu_disabled_or_small_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    box = bd.Box(30.0, 20.0, 10.0)

    # Disabled via environment variable
    monkeypatch.setenv(GPU_ENV_VAR, "0")
    assert resolve_hlr_backend(box, "auto", face_threshold=1) is False
    view_env = project_shape_to_view(
        box, ViewType.FRONT, 100.0, 100.0, hlr="auto", hlr_gpu_face_threshold=1
    )
    assert view_env.width == pytest.approx(30.0, abs=1e-2)

    # Disabled via explicit gpu=False
    monkeypatch.delenv(GPU_ENV_VAR, raising=False)
    assert resolve_hlr_backend(box, "auto", gpu=False, face_threshold=1) is False

    # Small shape below threshold falls back even if GPU is enabled
    assert resolve_hlr_backend(box, "auto", face_threshold=250) is False


def test_gpu_mode_errors_when_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    box = bd.Box(20.0, 20.0, 20.0)

    # Explicit contradiction: hlr="gpu" + gpu=False
    with pytest.raises(ValueError, match="gpu=False"):
        project_shape_to_view(box, ViewType.FRONT, 100.0, 100.0, hlr="gpu", gpu=False)

    # Missing wgpu package -> actionable ImportError
    monkeypatch.setattr("build123d_techdraw.gpu.runtime._import_wgpu", lambda: None)
    reset_gpu_cache()
    with pytest.raises(ImportError, match="build123d-techdraw\\[gpu\\]"):
        project_shape_gpu_hlr(box, (0.0, -1.0, 0.0), (0.0, 0.0, 1.0))

    # wgpu installed but no adapter -> actionable RuntimeError
    monkeypatch.undo()
    monkeypatch.setattr("build123d_techdraw.gpu.runtime.is_gpu_installed", lambda: True)
    monkeypatch.setattr("build123d_techdraw.gpu.runtime.is_gpu_available", lambda: False)
    with pytest.raises(RuntimeError, match="wgpu adapter"):
        project_shape_gpu_hlr(box, (0.0, -1.0, 0.0), (0.0, 0.0, 1.0))


# ---------------------------------------------------------------------------
# 2. Accuracy Parity vs Exact OCCT HLR + Cross-Backend Golden Rasters (GPU)
# ---------------------------------------------------------------------------


@pytest.mark.gpu
@pytest.mark.parametrize(
    ("name", "factory"),
    [
        ("box_hole", _make_box_with_hole_and_pocket),
        ("cyl_bore", _make_cylinder_with_transverse_bore),
        ("l_bracket", _make_filleted_l_bracket),
        ("assembly", _make_multi_solid_assembly),
    ],
)
def test_gpu_hlr_matches_exact_occt_and_golden_raster(
    name: str,
    factory: Any,
    tmp_path: Path,
) -> None:
    if not is_gpu_available():
        pytest.skip("No wgpu adapter available in this environment.")

    shape = factory()
    exact_payload = _compute_view_projection(
        shape, ViewType.ISOMETRIC, show_hidden=True, hlr="exact"
    )
    gpu_payload = _compute_view_projection(shape, ViewType.ISOMETRIC, show_hidden=True, hlr="gpu")

    # Tight geometric bounding-box parity
    assert gpu_payload.exact_w == pytest.approx(exact_payload.exact_w, abs=0.25)
    assert gpu_payload.exact_h == pytest.approx(exact_payload.exact_h, abs=0.25)

    # Visual parity against exact OCCT HLR (both visible & hidden layers)
    exact_img = _rasterize_view_payload(exact_payload)
    gpu_img = _rasterize_view_payload(gpu_payload)
    res = compare_images(
        gpu_img,
        exact_img,
        min_ssim=0.97,
        max_diff_pixel_ratio=0.015,
        save_diff_to=tmp_path / f"diff_exact_vs_gpu_{name}.png",
    )
    assert res.passed, (
        f"GPU HLR diverged from exact OCCT on {name}: "
        f"ssim={res.ssim:.4f}, diff_ratio={res.diff_pixel_ratio:.4f}"
    )

    # Cross-backend golden raster check (RTX 3070 + Vulkan Lavapipe)
    golden_path = GOLDEN_DIR / f"gpu_hlr_{name}.png"
    assert golden_path.exists(), f"Missing golden raster {golden_path}"
    assert_images_match(
        gpu_img,
        golden_path,
        min_ssim=0.985,
        max_diff_pixel_ratio=0.01,
        save_diff_to=tmp_path / f"diff_golden_{name}.png",
        label=f"gpu_hlr_{name}",
    )


# ---------------------------------------------------------------------------
# 3. Hidden-Line Visibility Correctness (Fully & Partially Occluded Edges)
# ---------------------------------------------------------------------------


@pytest.mark.gpu
def test_hidden_and_partially_occluded_edge_classification() -> None:
    if not is_gpu_available():
        pytest.skip("No wgpu adapter available in this environment.")

    # Front wall (x in [-20, 20], y in [-10, -6], z in [-15, 15])
    # Fully occluded rear block behind wall (x in [-8, 8], y in [4, 8], z in [-6, 6])
    # Partially occluded horizontal bar behind wall extending out both sides (x in [-35, 35], y in [2, 5], z in [-2, 2])
    front_wall = bd.Pos(0.0, -8.0, 0.0) * bd.Box(40.0, 4.0, 30.0)
    rear_block = bd.Pos(0.0, 6.0, 0.0) * bd.Box(16.0, 4.0, 12.0)
    spanning_bar = bd.Pos(0.0, 3.5, 0.0) * bd.Box(70.0, 3.0, 4.0)
    scene = bd.Compound(children=[front_wall, rear_block, spanning_bar])

    lf, lu = VIEW_VECTORS[ViewType.FRONT]
    res = project_shape_gpu_hlr(scene, lf, lu, show_hidden=True, resolution=2048)

    assert 'id="layer_hidden"' in res.inner_svg
    assert 'id="layer_visible"' in res.inner_svg
    assert res.inner_svg.index("layer_hidden") < res.inner_svg.index("layer_visible")
    assert len(res.visible_polylines_2d) > 0
    assert len(res.hidden_polylines_2d) > 0

    # Rear block edges (|x| <= 8, |z| <= 6) must be hidden, NOT visible.
    rear_hidden = [
        p
        for p in res.hidden_polylines_2d
        if abs(float(p[:, 0].min())) <= 8.5
        and abs(float(p[:, 0].max())) <= 8.5
        and abs(float(p[:, 1].min())) <= 6.5
        and abs(float(p[:, 1].max())) <= 6.5
    ]
    assert len(rear_hidden) >= 4, "Expected fully occluded rear block edges in layer_hidden."

    # Spanning bar's top/bottom edges (z = ±2) must split into BOTH:
    # - visible outer wings (|x| > 20, reaching ±35)
    # - hidden middle spans behind the front wall (|x| < 20)
    bar_vis_wings = [
        p
        for p in res.visible_polylines_2d
        if abs(float(p[:, 1].max()) - float(p[:, 1].min())) < 0.2
        and abs(abs(float(p[0, 1])) - 2.0) < 0.3
        and float(abs(p[:, 0]).max()) > 30.0
    ]
    bar_hid_mid = [
        p
        for p in res.hidden_polylines_2d
        if abs(float(p[:, 1].max()) - float(p[:, 1].min())) < 0.2
        and abs(abs(float(p[0, 1])) - 2.0) < 0.3
        and float(abs(p[:, 0]).max()) <= 21.0
    ]
    assert len(bar_vis_wings) >= 4, "Expected visible protruding wings of partially occluded bar."
    assert len(bar_hid_mid) >= 2, "Expected hidden occluded center of partially occluded bar."

    # show_hidden=False omits hidden polylines and layer_hidden completely
    res_no_hid = project_shape_gpu_hlr(scene, lf, lu, show_hidden=False, resolution=1024)
    assert len(res_no_hid.hidden_polylines_2d) == 0
    assert "layer_hidden" not in res_no_hid.inner_svg


# ---------------------------------------------------------------------------
# 4. Builder, SheetView, update_shape() & Export Integration (GPU)
# ---------------------------------------------------------------------------


@pytest.mark.gpu
def test_builder_sheet_and_update_shape_preserve_gpu_hlr(tmp_path: Path) -> None:
    if not is_gpu_available():
        pytest.skip("No wgpu adapter available in this environment.")

    part_a = bd.Box(40.0, 20.0, 15.0) - bd.Cylinder(4.0, 15.0)
    part_b = bd.Box(60.0, 20.0, 15.0) - bd.Cylinder(4.0, 15.0)

    with td.BuildDrawing(sheet_size=td.SheetSize.A4_LANDSCAPE) as sheet:
        front = td.FrontView(part_a, center=(90.0, 95.0), scale=1.0, hlr="gpu", hlr_resolution=1024)
        td.TopView(part_a, scale=1.0, align_to=(front, "bottom", 20.0), hlr="gpu")
        td.IsometricView(part_a, center=(210.0, 95.0), scale=1.0, show_hidden=False, hlr="gpu")

    assert front._regen_kwargs["hlr"] == "gpu"
    assert front._regen_kwargs["hlr_resolution"] == 1024
    assert front.width == pytest.approx(40.0, abs=0.2)

    # Parametric update_shape() preserves hlr="gpu" and updates view dimensions
    sheet.update_shape(part_b)
    assert front.width == pytest.approx(60.0, abs=0.2)

    # Full downstream renderer compatibility (SVG, PDF, DXF)
    svg_path = tmp_path / "gpu_hlr.svg"
    pdf_path = tmp_path / "gpu_hlr.pdf"
    dxf_path = tmp_path / "gpu_hlr.dxf"
    sheet.export_svg(str(svg_path), theme=td.Theme.ISO_WHITE)
    sheet.export_pdf(str(pdf_path))
    sheet.export_dxf(str(dxf_path))
    assert svg_path.stat().st_size > 1000
    assert pdf_path.stat().st_size > 1000
    assert dxf_path.stat().st_size > 1000


# ---------------------------------------------------------------------------
# 5. Heavy Multi-Hundred-Face Assembly Performance & Auto Trigger (GPU)
# ---------------------------------------------------------------------------


@pytest.mark.gpu
def test_heavy_assembly_gpu_hlr_performance_and_auto_trigger() -> None:
    if not is_gpu_available():
        pytest.skip("No wgpu adapter available in this environment.")

    from examples.gpu_hlr_assembly import build_gearbox_assembly

    assembly = build_gearbox_assembly()
    n_faces = count_shape_faces(assembly)
    assert n_faces >= 250
    assert resolve_hlr_backend(assembly, "auto", face_threshold=250) is True

    lf, lu = VIEW_VECTORS[ViewType.ISOMETRIC]
    res = project_shape_gpu_hlr(assembly, lf, lu, show_hidden=True, resolution=2048)

    assert res.stats.num_faces == n_faces
    assert res.stats.num_triangles > 5000
    assert res.stats.num_edges > 200
    assert res.stats.num_samples > 10000
    assert res.stats.num_visible_polylines > 50
    assert res.stats.num_hidden_polylines > 20
    assert res.stats.gpu_s > 0.0
    assert res.stats.total_s > 0.0
    assert res.stats.total_s < 15.0  # Generous bound even on software Lavapipe CI
