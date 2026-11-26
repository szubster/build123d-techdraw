"""Tests for 2D Jump Flooding SDF clearance field, GPU/CPU parity, and smart callout layout."""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

import build123d as bd
import numpy as np
import pytest
from PIL import Image
from scipy.ndimage import distance_transform_edt

from build123d_techdraw import (
    DrawingSheet,
    SheetSize,
    Theme,
    ViewType,
    run_sheet_lint,
)
from build123d_techdraw.gpu import (
    GPU_ENV_VAR,
    SheetDistanceField,
    compute_distance_field_from_mask,
    compute_sheet_sdf,
    is_gpu_available,
    reset_gpu_cache,
)
from build123d_techdraw.layout import (
    auto_dimension_sheet,
    auto_dimension_view,
    auto_dimension_views,
    place_balloons_sdf,
    place_leaders_sdf,
)
from build123d_techdraw.layout.auto_dimension import SheetOccupancy
from tests.raster_diff import assert_images_match

GOLDEN_HEATMAP_PATH = Path(__file__).parent / "golden_rasters" / "sdf_clearance_heatmap.png"


@pytest.fixture(autouse=True)
def _clean_gpu_env(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Ensure GPU env var and adapter cache stay isolated between tests."""
    reset_gpu_cache()
    monkeypatch.delenv(GPU_ENV_VAR, raising=False)
    yield
    reset_gpu_cache()


def _make_concave_l_bracket() -> bd.Solid:
    """Build a bold concave L-bracket with holes where coarse AABB wastes a 50x42 mm pocket."""
    full = bd.Pos(0.0, 0.0, 8.0) * bd.Box(90.0, 74.0, 16.0)
    notch = bd.Pos(20.0, 16.0, 8.0) * bd.Box(50.0, 42.0, 24.0)
    part = full - notch
    h1 = bd.Pos(-26.0, 18.0, 8.0) * bd.Cylinder(radius=4.5, height=24.0)
    h2 = bd.Pos(-26.0, -18.0, 8.0) * bd.Cylinder(radius=6.0, height=24.0)
    h3 = bd.Pos(22.0, -18.0, 8.0) * bd.Cylinder(radius=5.0, height=24.0)
    result = part - h1 - h2 - h3
    solids = result.solids()
    return solids[0] if solids else result  # type: ignore[return-value]


def _build_golden_sdf_sheet(*, gpu: bool | None) -> tuple[DrawingSheet, SheetDistanceField]:
    """Build a deterministic A4 concave L-bracket sheet + SDF placements for golden raster diff."""
    part = _make_concave_l_bracket()
    sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE)
    sheet.set_title_block(
        title="CONCAVE L-BRACKET",
        drawing_number="SDF-GOLD-001",
        rev="A",
        date="2026-11-21",
        author="CI",
        material="AL 6061-T6",
        finish="ANODIZED",
    )
    sheet.revision_rows[0].date = "2026-11-21"
    view = sheet.add_view(
        part,
        view_type=ViewType.TOP,
        center=(135.0, 102.0),
        scale=1.0,
        view_label="TOP",
    )

    sdf = compute_sheet_sdf(sheet, resolution_px_per_mm=2.0, gpu=gpu)

    targets: list[tuple[int, tuple[float, float]]] = [
        (1, view.to_sheet_3d(-26.0, 18.0, 16.0)),
        (2, view.to_sheet_3d(-26.0, -18.0, 16.0)),
        (3, view.to_sheet_3d(22.0, -18.0, 16.0)),
        (4, view.to_sheet_3d(-5.0, 2.0, 16.0)),  # Inner concave elbow facing the pocket!
        (5, view.to_sheet_3d(5.0, -8.0, 16.0)),  # Horizontal arm inner edge facing pocket!
        (6, view.to_sheet_3d(-10.0, 20.0, 16.0)),  # Vertical arm inner edge facing pocket!
    ]
    place_balloons_sdf(sheet, view, targets, gpu=gpu, radius_mm=4.0, sdf=sdf)

    leaders: list[tuple[tuple[float, float], str]] = [
        (view.to_sheet_3d(38.0, -18.0, 16.0), "WING END FACE"),
        (view.to_sheet_3d(-38.0, 28.0, 16.0), "DATUM CORNER"),
    ]
    place_leaders_sdf(sheet, view, leaders, gpu=gpu, sdf=sdf)
    return sheet, sdf


# ---------------------------------------------------------------------------
# 1. GPU JFA+1 Compute Shader vs Exact CPU SciPy EDT Parity
# ---------------------------------------------------------------------------


@pytest.mark.gpu
def test_gpu_jfa_vs_cpu_edt_synthetic_masks() -> None:
    """GPU JFA+1 compute shader must match exact SciPy EDT across diverse synthetic masks."""
    if not is_gpu_available():
        pytest.skip("No wgpu adapter available in this environment.")

    res = 2.0
    # Non-power-of-two grid with border, concave L-shape, isolated point seed, and disjoint bars.
    h, w = 211, 317
    mask = np.zeros((h, w), dtype=np.bool_)
    mask[:12, :] = True
    mask[-12:, :] = True
    mask[:, :18] = True
    mask[:, -12:] = True
    # Concave L-shape obstacle in the center
    mask[45:165, 65:115] = True
    mask[125:165, 65:210] = True
    # Isolated single-pixel seed and small box in the upper-right
    mask[50, 250] = True
    mask[145:180, 240:290] = True

    dist_gpu, backend_gpu = compute_distance_field_from_mask(
        mask, resolution_px_per_mm=res, gpu=True
    )
    dist_cpu, backend_cpu = compute_distance_field_from_mask(
        mask, resolution_px_per_mm=res, gpu=False
    )

    assert backend_gpu == "gpu"
    assert backend_cpu == "cpu"
    assert dist_gpu.shape == (h, w)
    assert dist_gpu.dtype == np.float32

    err_mm = np.abs(dist_gpu - dist_cpu)
    max_err_mm = float(np.max(err_mm))
    mean_err_mm = float(np.mean(err_mm))

    assert max_err_mm < 0.5, f"GPU JFA max error {max_err_mm:.4f} mm exceeds 0.5 mm (1 px)"
    assert mean_err_mm < 0.1, f"GPU JFA mean error {mean_err_mm:.4f} mm exceeds 0.1 mm"
    # Obstacle pixels must report exact 0.0 clearance.
    assert float(np.max(dist_gpu[mask])) == 0.0

    # Empty mask edge case (no obstacles anywhere)
    empty = np.zeros((32, 48), dtype=np.bool_)
    empty_gpu, _ = compute_distance_field_from_mask(empty, resolution_px_per_mm=res, gpu=True)
    empty_cpu, _ = compute_distance_field_from_mask(empty, resolution_px_per_mm=res, gpu=False)
    assert np.allclose(empty_gpu, empty_cpu, atol=1e-4)


@pytest.mark.gpu
def test_gpu_jfa_vs_cpu_edt_full_a3_drawing_sheet() -> None:
    """GPU JFA+1 vs CPU SciPy EDT parity on a full A3 sheet (840x594 px) with L-bracket."""
    if not is_gpu_available():
        pytest.skip("No wgpu adapter available in this environment.")

    part = _make_concave_l_bracket()
    sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    sheet.add_bom_item(1, 1, "BRK-01", "L-BRACKET", "AL 6061-T6")
    sheet.add_view(part, view_type=ViewType.TOP, center=(150.0, 120.0), scale=1.0, view_label="TOP")
    sheet.add_view(
        part, view_type=ViewType.FRONT, center=(280.0, 120.0), scale=1.0, view_label="FRONT"
    )

    sdf_gpu = compute_sheet_sdf(sheet, resolution_px_per_mm=2.0, gpu=True)
    sdf_cpu = compute_sheet_sdf(sheet, resolution_px_per_mm=2.0, gpu=False)

    assert sdf_gpu.backend == "gpu"
    assert sdf_cpu.backend == "cpu"
    assert sdf_gpu.distance_mm.shape == (594, 840)
    assert np.array_equal(sdf_gpu.obstacle_mask, sdf_cpu.obstacle_mask)

    err_mm = np.abs(sdf_gpu.distance_mm - sdf_cpu.distance_mm)
    assert float(np.max(err_mm)) < 0.5
    assert float(np.mean(err_mm)) < 0.1


# ---------------------------------------------------------------------------
# 2. CPU Fallback, Env Var Control, and Incremental Box Updates
# ---------------------------------------------------------------------------


def test_cpu_fallback_when_gpu_false_or_env_disabled_or_wgpu_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CPU SciPy EDT fallback works cleanly with gpu=False, env=0, or missing wgpu."""
    part = _make_concave_l_bracket()
    sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE)
    view = sheet.add_view(part, view_type=ViewType.TOP, center=(125.0, 95.0), scale=1.0)

    # Explicit gpu=False
    sdf_explicit = compute_sheet_sdf(sheet, gpu=False)
    assert sdf_explicit.backend == "cpu"
    assert sdf_explicit.distance_mm.shape == (420, 594)

    # Environment variable BUILD123D_TECHDRAW_GPU=0
    monkeypatch.setenv(GPU_ENV_VAR, "0")
    sdf_env = compute_sheet_sdf(sheet)
    assert sdf_env.backend == "cpu"
    assert np.array_equal(sdf_explicit.distance_mm, sdf_env.distance_mm)

    # Missing wgpu optional dependency
    monkeypatch.delenv(GPU_ENV_VAR, raising=False)
    monkeypatch.setitem(sys.modules, "wgpu", None)
    reset_gpu_cache()
    sdf_no_wgpu = compute_sheet_sdf(sheet)
    assert sdf_no_wgpu.backend == "cpu"

    # End-to-end SDF auto-dimensioning + balloons run cleanly on CPU fallback
    anns = auto_dimension_view(view, use_sdf=True, gpu=False, sdf=sdf_no_wgpu)
    assert len(anns) >= 4
    balloons = place_balloons_sdf(
        sheet,
        view,
        [(1, view.to_sheet_3d(-26.0, 18.0, 16.0)), (2, view.to_sheet_3d(22.0, -18.0, 16.0))],
        gpu=False,
        sdf=sdf_no_wgpu,
    )
    assert len(balloons) == 2
    assert run_sheet_lint(sheet) == []


def test_incremental_mark_occupied_box_matches_exact_edt() -> None:
    """mark_occupied_box must update distance_mm identically to a full SciPy EDT recompute."""
    part = _make_concave_l_bracket()
    sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE)
    sheet.add_view(part, view_type=ViewType.TOP, center=(125.0, 95.0), scale=1.0)

    sdf = compute_sheet_sdf(sheet, resolution_px_per_mm=2.0, gpu=False)
    # Mark three boxes incrementally
    boxes = [
        (40.0, 40.0, 62.0, 52.0),
        (145.0, 62.0, 165.0, 74.0),
        (210.0, 95.0, 224.0, 112.0),
    ]
    for bb in boxes:
        sdf.mark_occupied_box(bb, pad_mm=1.0)
        assert sdf.box_min_clearance(*bb) == 0.0

    # Compare against exact fresh EDT on the updated obstacle_mask
    exact_px = np.asarray(distance_transform_edt(~sdf.obstacle_mask), dtype=np.float32)
    exact_edt = np.asarray(exact_px / np.float32(sdf.resolution_px_per_mm), dtype=np.float32)
    max_diff = float(np.max(np.abs(sdf.distance_mm - exact_edt)))
    assert max_diff < 1e-4, f"Incremental update drifted by {max_diff:.6f} mm from exact EDT"

    # Out-of-bounds queries must return 0.0 clearance
    assert sdf.query_clearance(-5.0, 50.0) == 0.0
    assert sdf.query_clearance(50.0, 999.0) == 0.0
    assert sdf.box_min_clearance(-2.0, 20.0, 10.0, 30.0) == 0.0


# ---------------------------------------------------------------------------
# 3. Concave Pocket Discovery & Crowded Zero-Lint Placement
# ---------------------------------------------------------------------------


def test_concave_l_bracket_pocket_discovered_where_naive_aabb_rejects() -> None:
    """SDF exposes open concave pockets inside a view's AABB that naive AABB treats as solid."""
    part = _make_concave_l_bracket()
    sheet = DrawingSheet(sheet_size=SheetSize.A4_LANDSCAPE)
    view = sheet.add_view(part, view_type=ViewType.TOP, center=(140.0, 100.0), scale=1.0)

    sdf = compute_sheet_sdf(sheet, resolution_px_per_mm=2.0, gpu=False)
    occupancy = SheetOccupancy(sheet)

    # Point strictly inside the concave L-notch (inside view AABB, outside solid metal)
    pocket_xy = view.to_sheet_3d(20.0, 16.0, 16.0)
    solid_xy = view.to_sheet_3d(-25.0, -18.0, 16.0)

    assert view.min_x < pocket_xy[0] < view.max_x
    assert view.min_y < pocket_xy[1] < view.max_y
    assert sdf.query_clearance(*pocket_xy) > 10.0
    assert sdf.query_clearance(*solid_xy) == 0.0

    # Target at the inner concave corner facing the 50x42 mm open L-pocket
    inner_corner = view.to_sheet_3d(-5.0, -5.0, 16.0)
    best_xy = sdf.find_best_callout_position(
        inner_corner,
        box_size_mm=(8.0, 8.0),
        min_dist_mm=10.0,
        max_dist_mm=30.0,
        avoid_leader_crossings=True,
    )
    placed_box = (best_xy[0] - 4.0, best_xy[1] - 4.0, best_xy[0] + 4.0, best_xy[1] + 4.0)

    # The SDF-placed balloon sits cleanly inside the view's AABB concave pocket!
    assert sdf.box_min_clearance(*placed_box) > 1.0
    assert view.min_x <= best_xy[0] <= view.max_x
    assert view.min_y <= best_xy[1] <= view.max_y
    # Naive coarse AABB occupancy check rejects this exact valid pocket position!
    assert occupancy.collides(placed_box, ignore_view=view, check_own_view_geom=True) is True


def test_crowded_balloons_leaders_and_auto_dims_zero_lint_issues() -> None:
    """Crowded BOM balloons + leader notes + SDF auto-dims yield 0 lint issues and 0 overlaps."""
    part = _make_concave_l_bracket()
    sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    sheet.add_bom_item(1, 1, "LBR-01", "L-BRACKET BODY", "AL 6061-T6")
    sheet.add_bom_item(2, 2, "BSH-02", "DOWEL BUSHING", "STEEL")
    sheet.add_bom_item(3, 1, "PRT-03", "FLUID PORT", "BRASS")

    top_v = sheet.add_view(
        part, view_type=ViewType.TOP, center=(160.0, 125.0), scale=1.0, view_label="TOP"
    )
    front_v = sheet.add_view(
        part, view_type=ViewType.FRONT, center=(290.0, 125.0), scale=1.0, view_label="FRONT"
    )

    sdf = compute_sheet_sdf(sheet, resolution_px_per_mm=2.0, gpu=False)
    created_dims = auto_dimension_sheet(sheet, part, use_sdf=True, gpu=False, sdf=sdf)
    assert len(created_dims) >= 5

    # Place 8 crowded balloons around the concave Top view
    balloon_targets: list[tuple[int, tuple[float, float]]] = [
        (1, top_v.to_sheet_3d(-26.0, 18.0, 16.0)),
        (2, top_v.to_sheet_3d(-26.0, -18.0, 16.0)),
        (3, top_v.to_sheet_3d(22.0, -18.0, 16.0)),
        (4, top_v.to_sheet_3d(-5.0, 0.0, 16.0)),
        (5, top_v.to_sheet_3d(8.0, -6.0, 16.0)),
        (6, top_v.to_sheet_3d(-12.0, 26.0, 16.0)),
        (7, top_v.to_sheet_3d(38.0, -28.0, 16.0)),
        (8, top_v.to_sheet_3d(-38.0, -28.0, 16.0)),
    ]
    balloons = place_balloons_sdf(sheet, top_v, balloon_targets, gpu=False, radius_mm=4.0, sdf=sdf)
    assert [b.item_no for b in balloons] == [1, 2, 3, 4, 5, 6, 7, 8]

    # Verify zero pairwise balloon overlaps
    for i in range(len(balloons)):
        for j in range(i + 1, len(balloons)):
            dist = math.hypot(
                balloons[i].balloon_center[0] - balloons[j].balloon_center[0],
                balloons[i].balloon_center[1] - balloons[j].balloon_center[1],
            )
            assert dist >= 8.0, f"Balloons {i + 1} and {j + 1} overlap (dist={dist:.2f} mm)"

    # Also test model-space coordinates on Front view for place_leaders_sdf
    leaders = place_leaders_sdf(
        sheet,
        front_v,
        [
            ((-30.0, 10.0), "MACHINED DATUM A"),
            ((25.0, 10.0), ["BREAK EDGES", "0.3 x 45°"]),
        ],
        gpu=False,
        coord_space="model",
        sdf=sdf,
    )
    assert len(leaders) == 2

    issues = run_sheet_lint(sheet)
    assert issues == [], f"Expected 0 sheet lint issues, got: {issues}"


def test_default_use_sdf_false_preserves_legacy_behavior() -> None:
    """Calling auto_dimension_views with default use_sdf=False never invokes SDF."""
    part = _make_concave_l_bracket()
    sheet1 = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    v1 = sheet1.add_view(part, view_type=ViewType.TOP, center=(150.0, 120.0), scale=1.0)
    anns1 = auto_dimension_views(sheet1, part, {ViewType.TOP: v1})

    sheet2 = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    v2 = sheet2.add_view(part, view_type=ViewType.TOP, center=(150.0, 120.0), scale=1.0)
    anns2 = auto_dimension_views(sheet2, part, {ViewType.TOP: v2}, use_sdf=False)

    assert len(anns1) == len(anns2)
    assert [a.bounding_box_2d() for a in anns1] == [a.bounding_box_2d() for a in anns2]


# ---------------------------------------------------------------------------
# 4. Golden Heatmap Visual Regression (CPU + GPU / Lavapipe) & Example Test
# ---------------------------------------------------------------------------


def test_sdf_heatmap_matches_golden_cpu(tmp_path: Path) -> None:
    """CPU SDF clearance heatmap must match committed golden reference raster."""
    assert GOLDEN_HEATMAP_PATH.exists(), f"Missing golden raster: {GOLDEN_HEATMAP_PATH}"
    sheet, sdf = _build_golden_sdf_sheet(gpu=False)
    assert sdf.backend == "cpu"
    assert run_sheet_lint(sheet) == []

    actual = sdf.render_heatmap_image(theme=Theme.DARK_BLUEPRINT)
    assert actual.size == (594, 420)
    assert_images_match(
        actual,
        GOLDEN_HEATMAP_PATH,
        min_ssim=0.985,
        max_diff_pixel_ratio=0.015,
        pixel_tolerance=4,
        save_diff_to=tmp_path / "diff_cpu.png",
        label="sdf_clearance_heatmap_cpu",
    )

    # Also verify ISO_WHITE theme rendering produces a bright light-background RGBA heatmap.
    white_img = sdf.render_heatmap_image(theme=Theme.ISO_WHITE)
    assert isinstance(white_img, Image.Image)
    assert white_img.size == (594, 420)
    arr_white = np.asarray(white_img, dtype=np.uint8)
    assert float(arr_white[:, :, :3].mean()) > 150.0


@pytest.mark.gpu
def test_sdf_heatmap_matches_golden_gpu(tmp_path: Path) -> None:
    """GPU JFA+1 clearance heatmap must match golden reference on RTX 3070 and Lavapipe."""
    if not is_gpu_available():
        pytest.skip("No wgpu adapter available in this environment.")

    sheet, sdf = _build_golden_sdf_sheet(gpu=True)
    assert sdf.backend == "gpu"
    assert run_sheet_lint(sheet) == []

    actual = sdf.render_heatmap_image(theme=Theme.DARK_BLUEPRINT)
    assert actual.size == (594, 420)
    assert_images_match(
        actual,
        GOLDEN_HEATMAP_PATH,
        min_ssim=0.985,
        max_diff_pixel_ratio=0.015,
        pixel_tolerance=4,
        save_diff_to=tmp_path / "diff_gpu.png",
        label="sdf_clearance_heatmap_gpu",
    )


def test_example_sdf_smart_layout_generates_zero_lint_outputs(tmp_path: Path) -> None:
    """examples/sdf_smart_layout.py generates all drawing artifacts + heatmap with 0 lint issues."""
    from examples.sdf_smart_layout import generate_sdf_smart_layout

    generate_sdf_smart_layout(tmp_path, gpu=False)

    expected_files = [
        "sdf_smart_layout.svg",
        "sdf_smart_layout_white.svg",
        "sdf_smart_layout.pdf",
        "sdf_smart_layout.dxf",
        "sdf_smart_layout_heatmap.png",
    ]
    for name in expected_files:
        fpath = tmp_path / name
        assert fpath.exists() and fpath.stat().st_size > 1000, f"Missing or tiny output: {name}"

    with Image.open(tmp_path / "sdf_smart_layout_heatmap.png") as img:
        assert img.size == (840, 594)
