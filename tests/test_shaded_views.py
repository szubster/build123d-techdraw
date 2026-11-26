"""Tests for GPU-accelerated WGSL shaded technical views, SVG/PDF embedding, and golden rasters."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import build123d as bd
import numpy as np
import pytest
from build123d.exporters import Drawing
from build123d.topology import Shape

import build123d_techdraw as td
from build123d_techdraw.core.view import VIEW_VECTORS, ViewType, project_shape_to_view
from build123d_techdraw.gpu import (
    GPU_ENV_VAR,
    ShadedRender,
    ViewStyle,
    render_shaded_view,
    reset_gpu_cache,
)
from build123d_techdraw.renderers.theme import Theme
from tests.raster_diff import assert_images_match, extract_embedded_pngs_from_svg

GOLDEN_DIR = Path(__file__).resolve().parent / "golden_rasters"


@pytest.fixture(autouse=True)
def _clean_gpu_runtime(monkeypatch: pytest.MonkeyPatch) -> Any:
    reset_gpu_cache()
    monkeypatch.delenv(GPU_ENV_VAR, raising=False)
    yield
    reset_gpu_cache()


def _golden_test_part() -> Shape[Any]:
    """Deterministic mechanical test part with a central bore and stepped boss."""
    base = bd.Box(36.0, 26.0, 12.0)
    boss = bd.Pos(0.0, 0.0, 6.0) * bd.Cylinder(
        9.0, 8.0, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
    )
    bore = bd.Cylinder(5.0, 40.0)
    return base + boss - bore


def _two_part_assembly() -> bd.Compound:
    """Two-solid labeled assembly for BOM per-part shading tests."""
    plate = bd.Box(32.0, 24.0, 6.0)
    plate.label = "Base Plate"
    peg = bd.Pos(0.0, 0.0, 3.0) * bd.Cylinder(
        6.0, 14.0, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
    )
    peg.label = "Center Peg"
    return bd.Compound(children=[plate, peg])


# ---------------------------------------------------------------------------
# 1. CPU-only & error-handling tests (run even with BUILD123D_TECHDRAW_GPU=0)
# ---------------------------------------------------------------------------


def test_view_style_exports_and_coercion() -> None:
    assert td.ViewStyle is ViewStyle
    assert ViewStyle.coerce("GOOCH") is ViewStyle.GOOCH
    assert ViewStyle.coerce("  blueprint_shaded ") is ViewStyle.BLUEPRINT_SHADED
    assert ViewStyle.coerce(ViewStyle.XRAY) is ViewStyle.XRAY
    with pytest.raises(ValueError, match="Unknown view style"):
        ViewStyle.coerce("not_a_style")


def test_wireframe_runs_without_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GPU_ENV_VAR, "0")
    monkeypatch.setattr("build123d_techdraw.gpu.runtime._import_wgpu", lambda: None)

    sheet = td.DrawingSheet(td.SheetSize.A4_LANDSCAPE)
    view = sheet.add_view(
        bd.Box(20.0, 15.0, 10.0),
        ViewType.ISOMETRIC,
        center=(148.0, 105.0),
        style=ViewStyle.WIREFRAME,
    )
    assert view.style is ViewStyle.WIREFRAME
    svg = sheet.to_svg()
    assert "<image" not in svg
    assert "shaded-layer" not in svg


def test_shaded_style_with_gpu_false_raises_actionable_error() -> None:
    box = bd.Box(20.0, 15.0, 10.0)
    with pytest.raises(RuntimeError, match="ViewStyle.WIREFRAME"):
        project_shape_to_view(
            box, ViewType.ISOMETRIC, 100.0, 100.0, style=ViewStyle.GOOCH, gpu=False
        )


def test_shaded_style_with_env_disabled_raises_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(GPU_ENV_VAR, "0")
    box = bd.Box(20.0, 15.0, 10.0)
    with pytest.raises(RuntimeError, match="BUILD123D_TECHDRAW_GPU=0"):
        project_shape_to_view(box, ViewType.ISOMETRIC, 100.0, 100.0, style=ViewStyle.GOOCH)


def test_shaded_style_with_missing_wgpu_raises_import_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("build123d_techdraw.gpu.runtime._import_wgpu", lambda: None)
    box = bd.Box(20.0, 15.0, 10.0)
    with pytest.raises(ImportError, match=r"build123d-techdraw\[gpu\]"):
        project_shape_to_view(box, ViewType.ISOMETRIC, 100.0, 100.0, style=ViewStyle.GOOCH)


def test_render_shaded_view_rejects_wireframe_and_bad_args() -> None:
    box = bd.Box(20.0, 15.0, 10.0)
    with pytest.raises(ValueError, match="WIREFRAME"):
        render_shaded_view(box, (1.0, 1.0, 1.0), (0.0, 0.0, 1.0), style=ViewStyle.WIREFRAME)
    with pytest.raises(ValueError, match="positive"):
        render_shaded_view(box, (1.0, 1.0, 1.0), (0.0, 0.0, 1.0), shading_dpi=0)


# ---------------------------------------------------------------------------
# 2. GPU shader modes, SSAO, BOM per-part colors, and API integration
# ---------------------------------------------------------------------------


@pytest.mark.gpu
@pytest.mark.parametrize(
    "style",
    [
        ViewStyle.SHADED,
        ViewStyle.SHADED_WITH_EDGES,
        ViewStyle.GOOCH,
        ViewStyle.TOON,
        ViewStyle.BLUEPRINT_SHADED,
        ViewStyle.XRAY,
    ],
)
def test_all_shaded_view_styles_render_cleanly(style: ViewStyle) -> None:
    part = _golden_test_part()
    look_from, look_up = VIEW_VECTORS[ViewType.ISOMETRIC]
    rendered = render_shaded_view(part, look_from, look_up, style=style, shading_dpi=150)

    assert isinstance(rendered, ShadedRender)
    assert rendered.style is style
    assert set(rendered.images.keys()) == {Theme.DARK_BLUEPRINT, Theme.ISO_WHITE}

    for theme, img in rendered.images.items():
        arr = np.asarray(img)
        assert arr.ndim == 3 and arr.shape[2] == 4
        # Corner pixel must stay transparent; interior must have visible coverage.
        assert arr[0, 0, 3] == 0
        coverage = float(np.mean(arr[:, :, 3] > 16))
        assert 0.15 < coverage < 0.95, f"Unexpected coverage {coverage:.3f} for {style} / {theme}"


@pytest.mark.gpu
def test_ssao_darkens_crevices_and_color_by_part_distinguishes_solids() -> None:
    assy = _two_part_assembly()
    look_from, look_up = VIEW_VECTORS[ViewType.ISOMETRIC]

    with_ssao = render_shaded_view(
        assy, look_from, look_up, style=ViewStyle.GOOCH, shading_dpi=150, ssao=True
    )
    without_ssao = render_shaded_view(
        assy, look_from, look_up, style=ViewStyle.GOOCH, shading_dpi=150, ssao=False
    )
    a_on = np.asarray(with_ssao.images[Theme.DARK_BLUEPRINT], dtype=np.float32)
    a_off = np.asarray(without_ssao.images[Theme.DARK_BLUEPRINT], dtype=np.float32)
    mask = a_on[:, :, 3] > 200
    assert float(a_on[mask, :3].mean()) < float(a_off[mask, :3].mean())

    mono = render_shaded_view(
        assy, look_from, look_up, style=ViewStyle.SHADED_WITH_EDGES, shading_dpi=150
    )
    bom = render_shaded_view(
        assy,
        look_from,
        look_up,
        style=ViewStyle.SHADED_WITH_EDGES,
        shading_dpi=150,
        color_by_part=True,
    )
    custom = render_shaded_view(
        assy,
        look_from,
        look_up,
        style=ViewStyle.SHADED_WITH_EDGES,
        shading_dpi=150,
        part_colors={"Base Plate": "#D946EF", "Center Peg": (0.1, 0.85, 0.25)},
    )
    assert not np.array_equal(
        np.asarray(mono.images[Theme.ISO_WHITE]),
        np.asarray(bom.images[Theme.ISO_WHITE]),
    )
    assert not np.array_equal(
        np.asarray(bom.images[Theme.ISO_WHITE]),
        np.asarray(custom.images[Theme.ISO_WHITE]),
    )


@pytest.mark.gpu
def test_shaded_layer_precedes_vector_edges_and_aligns_pixel_for_pixel() -> None:
    part = _golden_test_part()
    sheet = td.DrawingSheet(td.SheetSize.A4_LANDSCAPE)
    view = sheet.add_view(
        part,
        ViewType.ISOMETRIC,
        center=(148.0, 105.0),
        scale=1.0,
        show_hidden=False,
        style=ViewStyle.GOOCH,
        shading_dpi=200,
    )

    svg = sheet.to_svg(theme=Theme.DARK_BLUEPRINT)
    assert ".theme-dark-blueprint .shaded-layer-white" in svg
    assert ".theme-iso-white .shaded-layer-dark" in svg

    # Shaded <image> elements MUST appear BEFORE #layer_visible in the SVG DOM.
    assert svg.index("<image") < svg.index('id="layer_visible"')

    # Pure SHADED omits vector edges; SHADED_WITH_EDGES keeps them.
    pure_view = project_shape_to_view(
        part, ViewType.ISOMETRIC, 148.0, 105.0, style=ViewStyle.SHADED, shading_dpi=120
    )
    assert "<image" in pure_view.svg_content
    assert 'id="layer_visible"' not in pure_view.svg_content

    # Pixel-for-pixel alignment: map opaque raster bounds back to Drawing (u, v) and
    # compare against build123d.Drawing.visible_lines.bounding_box().
    root = ET.fromstring(svg)
    img_el = next(el for el in root.iter() if el.tag.endswith("image"))
    x0 = float(img_el.attrib["x"])
    y0 = float(img_el.attrib["y"])
    w = float(img_el.attrib["width"])
    h = float(img_el.attrib["height"])

    embedded = extract_embedded_pngs_from_svg(svg)
    assert len(embedded) == 2  # dual-theme dark + white
    arr = np.asarray(embedded[0])
    ys, xs = np.nonzero(arr[:, :, 3] > 128)
    px_h, px_w = arr.shape[:2]

    raster_u_min = x0 + (float(xs.min()) / px_w) * w
    raster_u_max = x0 + (float(xs.max() + 1) / px_w) * w
    raster_v_max = -(y0 + (float(ys.min()) / px_h) * h)
    raster_v_min = -(y0 + (float(ys.max() + 1) / px_h) * h)

    look_from, look_up = VIEW_VECTORS[ViewType.ISOMETRIC]
    dwg = Drawing(part, look_from=look_from, look_up=look_up, with_hidden=False)
    bb = dwg.visible_lines.bounding_box()

    # Sub-millimetre (< 0.4 mm) agreement between raster silhouette and exact OCCT vector bbox.
    assert abs(raster_u_min - float(bb.min.X)) < 0.4
    assert abs(raster_u_max - float(bb.max.X)) < 0.4
    assert abs(raster_v_min - float(bb.min.Y)) < 0.4
    assert abs(raster_v_max - float(bb.max.Y)) < 0.4
    assert view.style is ViewStyle.GOOCH


@pytest.mark.gpu
def test_builder_multiview_and_update_shape_preserve_shading() -> None:
    part = _golden_test_part()
    with td.BuildDrawing(td.SheetSize.A3_LANDSCAPE):
        iso = td.IsometricView(
            part,
            center=(300.0, 120.0),
            scale=1.0,
            style=ViewStyle.TOON,
            shading_dpi=120,
        )
    assert iso.style is ViewStyle.TOON
    assert "<image" in iso.svg_content

    bigger = bd.Box(50.0, 35.0, 20.0) - bd.Cylinder(6.0, 20.0)
    old_svg = iso.svg_content
    iso.update_shape(bigger)
    assert iso.style is ViewStyle.TOON
    assert "<image" in iso.svg_content
    assert iso.svg_content != old_svg

    mv_sheet = td.DrawingSheet(td.SheetSize.A3_LANDSCAPE)
    mv = td.add_standard_multiview(
        mv_sheet,
        part,
        views=[ViewType.FRONT, ViewType.ISOMETRIC],
        iso_style=ViewStyle.BLUEPRINT_SHADED,
        shading_dpi=120,
        parallel=False,
    )
    assert mv.front is not None and mv.front.style is ViewStyle.WIREFRAME
    assert mv.isometric is not None and mv.isometric.style is ViewStyle.BLUEPRINT_SHADED


@pytest.mark.gpu
def test_pdf_export_embeds_only_active_theme_shaded_image(tmp_path: Path) -> None:
    part = _golden_test_part()
    sheet = td.DrawingSheet(td.SheetSize.A4_LANDSCAPE)
    sheet.add_view(
        part,
        ViewType.ISOMETRIC,
        center=(148.0, 105.0),
        scale=1.0,
        show_hidden=False,
        style=ViewStyle.GOOCH,
        shading_dpi=150,
    )

    dark_pdf = tmp_path / "shaded_dark.pdf"
    white_pdf = tmp_path / "shaded_white.pdf"
    sheet.export_pdf(str(dark_pdf), theme=Theme.DARK_BLUEPRINT)
    sheet.export_pdf(str(white_pdf), theme=Theme.ISO_WHITE)

    dark_bytes = dark_pdf.read_bytes()
    white_bytes = white_pdf.read_bytes()
    assert dark_bytes.startswith(b"%PDF-")
    assert white_bytes.startswith(b"%PDF-")

    # Each RGBA image emits 2 /Subtype /Image XObjects (RGB image + /SMask alpha) — never 4!
    dark_img_count = len(re.findall(rb"/Subtype\s*/Image\b", dark_bytes))
    white_img_count = len(re.findall(rb"/Subtype\s*/Image\b", white_bytes))
    assert dark_img_count == 2
    assert white_img_count == 2
    assert dark_bytes != white_bytes


# ---------------------------------------------------------------------------
# 3. Golden raster visual regression tests (RTX 3070 & Vulkan Lavapipe)
# ---------------------------------------------------------------------------


@pytest.mark.gpu
@pytest.mark.parametrize(
    ("style", "theme", "golden_name"),
    [
        (ViewStyle.GOOCH, Theme.DARK_BLUEPRINT, "shaded_gooch_dark.png"),
        (ViewStyle.GOOCH, Theme.ISO_WHITE, "shaded_gooch_white.png"),
        (ViewStyle.BLUEPRINT_SHADED, Theme.DARK_BLUEPRINT, "shaded_blueprint_dark.png"),
        (ViewStyle.XRAY, Theme.DARK_BLUEPRINT, "shaded_xray_dark.png"),
    ],
)
def test_golden_shaded_rasters_match(
    style: ViewStyle,
    theme: Theme,
    golden_name: str,
    tmp_path: Path,
) -> None:
    part = _golden_test_part()
    sheet = td.DrawingSheet(td.SheetSize.A4_LANDSCAPE)
    sheet.add_view(
        part,
        ViewType.ISOMETRIC,
        center=(148.0, 105.0),
        scale=1.0,
        show_hidden=False,
        style=style,
        shading_dpi=180,
        shading_theme=theme,
        ssao=style is not ViewStyle.XRAY,
    )
    svg = sheet.to_svg(theme=theme)
    embedded = extract_embedded_pngs_from_svg(svg)
    assert len(embedded) == 1

    golden_path = GOLDEN_DIR / golden_name
    assert golden_path.exists(), f"Missing golden raster: {golden_path}"
    assert_images_match(
        embedded[0],
        golden_path,
        save_diff_to=tmp_path / f"diff_{golden_name}",
        label=golden_name,
    )
