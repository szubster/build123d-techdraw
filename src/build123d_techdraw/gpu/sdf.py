"""2D Euclidean Distance-Field (SDF) sheet occupancy and Jump Flooding Algorithm (JFA).

Rasterizes a :class:`~build123d_techdraw.core.sheet.DrawingSheet` into a high-resolution
binary obstacle mask — including exact projected 2D view geometry contours (so concave
L/C/T-shaped parts expose true whitespace pockets instead of coarse AABBs), margins,
ISO 7200 title block, revision/BOM tables, view labels, and existing annotations — and
computes an exact millimeter clearance field via a WebGPU (WGSL) Jump Flooding Algorithm
(JFA+1) compute shader, with automatic exact CPU fallback via
:func:`scipy.ndimage.distance_transform_edt`.
"""

from __future__ import annotations

import io
import math
import re
import struct
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import svgelements as se
from numpy.typing import NDArray
from PIL import Image, ImageDraw
from scipy.ndimage import (  # type: ignore[import-untyped]  # scipy ships no type stubs in env
    binary_dilation,
    binary_erosion,
    binary_fill_holes,
    distance_transform_edt,
)

from build123d_techdraw.annotations.base import BBox2D
from build123d_techdraw.annotations.centerlines import CenterMark
from build123d_techdraw.gpu.runtime import get_gpu_device, is_gpu_enabled

if TYPE_CHECKING:
    from build123d_techdraw.core.sheet import DrawingSheet
    from build123d_techdraw.core.view import SheetView
    from build123d_techdraw.renderers.theme import Theme

BackendName = Literal["gpu", "cpu"]

# ---------------------------------------------------------------------------
# WGSL Jump Flooding Algorithm (JFA + 1) compute shaders
# ---------------------------------------------------------------------------

_SEED_INIT_WGSL = """
struct Params {
    width: u32,
    height: u32,
    step: i32,
    inv_res: f32,
};

@group(0) @binding(0) var<uniform> params: Params;
@group(0) @binding(1) var<storage, read> mask: array<u32>;
@group(0) @binding(2) var<storage, read_write> seeds_out: array<vec2<i32>>;

@compute @workgroup_size(16, 16, 1)
fn main(@builtin(global_invocation_id) gid: vec3<u32>) {
    if (gid.x >= params.width || gid.y >= params.height) {
        return;
    }
    let idx = gid.y * params.width + gid.x;
    if (mask[idx] != 0u) {
        seeds_out[idx] = vec2<i32>(i32(gid.x), i32(gid.y));
    } else {
        seeds_out[idx] = vec2<i32>(-1, -1);
    }
}
"""

_JFA_STEP_WGSL = """
struct Params {
    width: u32,
    height: u32,
    step: i32,
    inv_res: f32,
};

@group(0) @binding(0) var<uniform> params: Params;
@group(0) @binding(1) var<storage, read> seeds_in: array<vec2<i32>>;
@group(0) @binding(2) var<storage, read_write> seeds_out: array<vec2<i32>>;

@compute @workgroup_size(16, 16, 1)
fn main(@builtin(global_invocation_id) gid: vec3<u32>) {
    let x = i32(gid.x);
    let y = i32(gid.y);
    let w = i32(params.width);
    let h = i32(params.height);
    if (x >= w || y >= h) {
        return;
    }

    let idx = u32(y * w + x);
    var best_seed = seeds_in[idx];
    var best_dist_sq: i32 = 2147483647;
    if (best_seed.x >= 0) {
        let dx0 = best_seed.x - x;
        let dy0 = best_seed.y - y;
        best_dist_sq = dx0 * dx0 + dy0 * dy0;
        if (best_dist_sq == 0) {
            seeds_out[idx] = best_seed;
            return;
        }
    }

    let st = params.step;
    for (var oy: i32 = -1; oy <= 1; oy = oy + 1) {
        let ny = y + oy * st;
        if (ny < 0 || ny >= h) {
            continue;
        }
        let row_off = ny * w;
        for (var ox: i32 = -1; ox <= 1; ox = ox + 1) {
            if (ox == 0 && oy == 0) {
                continue;
            }
            let nx = x + ox * st;
            if (nx < 0 || nx >= w) {
                continue;
            }
            let cand = seeds_in[u32(row_off + nx)];
            if (cand.x >= 0) {
                let dx = cand.x - x;
                let dy = cand.y - y;
                let d2 = dx * dx + dy * dy;
                if (d2 < best_dist_sq) {
                    best_dist_sq = d2;
                    best_seed = cand;
                }
            }
        }
    }
    seeds_out[idx] = best_seed;
}
"""

_DIST_RESOLVE_WGSL = """
struct Params {
    width: u32,
    height: u32,
    step: i32,
    inv_res: f32,
};

@group(0) @binding(0) var<uniform> params: Params;
@group(0) @binding(1) var<storage, read> seeds_in: array<vec2<i32>>;
@group(0) @binding(2) var<storage, read_write> dist_mm: array<f32>;

@compute @workgroup_size(16, 16, 1)
fn main(@builtin(global_invocation_id) gid: vec3<u32>) {
    let x = i32(gid.x);
    let y = i32(gid.y);
    let w = i32(params.width);
    let h = i32(params.height);
    if (x >= w || y >= h) {
        return;
    }

    let idx = u32(y * w + x);
    let seed = seeds_in[idx];
    if (seed.x < 0) {
        let diag = sqrt(f32(w * w + h * h));
        dist_mm[idx] = diag * params.inv_res;
    } else if (seed.x == x && seed.y == y) {
        dist_mm[idx] = 0.0;
    } else {
        let dx = f32(seed.x - x);
        let dy = f32(seed.y - y);
        dist_mm[idx] = sqrt(dx * dx + dy * dy) * params.inv_res;
    }
}
"""

# Cached compiled JFA compute pipelines keyed by the active wgpu device object.
_PIPELINE_CACHE: tuple[Any, Any, Any, Any] | None = None


def _get_jfa_pipelines(device: Any) -> tuple[Any, Any, Any]:
    """Compile (or reuse cached) JFA compute pipelines for ``device``."""
    global _PIPELINE_CACHE
    if _PIPELINE_CACHE is not None and _PIPELINE_CACHE[0] is device:
        return _PIPELINE_CACHE[1], _PIPELINE_CACHE[2], _PIPELINE_CACHE[3]

    seed_mod = device.create_shader_module(code=_SEED_INIT_WGSL)
    step_mod = device.create_shader_module(code=_JFA_STEP_WGSL)
    dist_mod = device.create_shader_module(code=_DIST_RESOLVE_WGSL)

    seed_pipe = device.create_compute_pipeline(
        layout="auto",
        compute={"module": seed_mod, "entry_point": "main"},
    )
    step_pipe = device.create_compute_pipeline(
        layout="auto",
        compute={"module": step_mod, "entry_point": "main"},
    )
    dist_pipe = device.create_compute_pipeline(
        layout="auto",
        compute={"module": dist_mod, "entry_point": "main"},
    )
    _PIPELINE_CACHE = (device, seed_pipe, step_pipe, dist_pipe)
    return seed_pipe, step_pipe, dist_pipe


def _jfa_steps(width: int, height: int) -> list[int]:
    """Return the Jump Flooding step sequence ``[2^(k-1), ..., 2, 1]`` plus final ``JFA+1``."""
    max_dim = max(width, height)
    if max_dim <= 1:
        return [1]
    step = 1
    while step * 2 < max_dim:
        step *= 2
    steps: list[int] = []
    while step >= 1:
        steps.append(step)
        step //= 2
    # Final JFA+1 refinement pass eliminates residual Voronoi boundary artifacts.
    steps.append(1)
    return steps


def _compute_sdf_gpu(
    obstacle_mask: NDArray[np.bool_],
    resolution_px_per_mm: float,
) -> NDArray[np.float32]:
    """Execute the 2D JFA+1 Euclidean distance transform on WebGPU, returning ``R32F`` mm."""
    import wgpu

    height, width = int(obstacle_mask.shape[0]), int(obstacle_mask.shape[1])
    if not bool(np.any(obstacle_mask)):
        diag_mm = math.hypot(width, height) / resolution_px_per_mm
        return np.full((height, width), diag_mm, dtype=np.float32)

    _adapter, device = get_gpu_device()
    seed_pipe, step_pipe, dist_pipe = _get_jfa_pipelines(device)

    n_pixels = width * height
    inv_res = 1.0 / resolution_px_per_mm

    mask_u32 = np.ascontiguousarray(obstacle_mask.reshape(-1), dtype=np.uint32)
    mask_buf = device.create_buffer_with_data(
        data=mask_u32,
        usage=wgpu.BufferUsage.STORAGE,
    )
    seed_byte_size = n_pixels * 8  # vec2<i32>
    seeds_a = device.create_buffer(
        size=seed_byte_size,
        usage=wgpu.BufferUsage.STORAGE,
    )
    seeds_b = device.create_buffer(
        size=seed_byte_size,
        usage=wgpu.BufferUsage.STORAGE,
    )
    dist_buf = device.create_buffer(
        size=n_pixels * 4,  # R32F (f32)
        usage=wgpu.BufferUsage.STORAGE | wgpu.BufferUsage.COPY_SRC,
    )

    def _make_params_buf(step_val: int) -> Any:
        raw = struct.pack("<IIif", width, height, step_val, inv_res)
        return device.create_buffer_with_data(data=raw, usage=wgpu.BufferUsage.UNIFORM)

    gx = (width + 15) // 16
    gy = (height + 15) // 16

    init_params = _make_params_buf(0)
    init_bg = device.create_bind_group(
        layout=seed_pipe.get_bind_group_layout(0),
        entries=[
            {"binding": 0, "resource": {"buffer": init_params}},
            {"binding": 1, "resource": {"buffer": mask_buf}},
            {"binding": 2, "resource": {"buffer": seeds_a}},
        ],
    )

    encoder = device.create_command_encoder()

    # Pass 1: seed initialization from binary obstacle mask
    cpass = encoder.begin_compute_pass()
    cpass.set_pipeline(seed_pipe)
    cpass.set_bind_group(0, init_bg)
    cpass.dispatch_workgroups(gx, gy, 1)
    cpass.end()

    # Pass 2: ping-pong JFA passes + final JFA+1 pass
    src_buf = seeds_a
    dst_buf = seeds_b
    step_layout = step_pipe.get_bind_group_layout(0)
    # Keep uniform buffer references alive until queue submission completes.
    uniform_refs: list[Any] = [init_params]

    for step_val in _jfa_steps(width, height):
        p_buf = _make_params_buf(step_val)
        uniform_refs.append(p_buf)
        step_bg = device.create_bind_group(
            layout=step_layout,
            entries=[
                {"binding": 0, "resource": {"buffer": p_buf}},
                {"binding": 1, "resource": {"buffer": src_buf}},
                {"binding": 2, "resource": {"buffer": dst_buf}},
            ],
        )
        spass = encoder.begin_compute_pass()
        spass.set_pipeline(step_pipe)
        spass.set_bind_group(0, step_bg)
        spass.dispatch_workgroups(gx, gy, 1)
        spass.end()
        src_buf, dst_buf = dst_buf, src_buf

    # Pass 3: Euclidean distance conversion to R32F millimetres
    resolve_params = _make_params_buf(0)
    uniform_refs.append(resolve_params)
    dist_bg = device.create_bind_group(
        layout=dist_pipe.get_bind_group_layout(0),
        entries=[
            {"binding": 0, "resource": {"buffer": resolve_params}},
            {"binding": 1, "resource": {"buffer": src_buf}},
            {"binding": 2, "resource": {"buffer": dist_buf}},
        ],
    )
    dpass = encoder.begin_compute_pass()
    dpass.set_pipeline(dist_pipe)
    dpass.set_bind_group(0, dist_bg)
    dpass.dispatch_workgroups(gx, gy, 1)
    dpass.end()

    device.queue.submit([encoder.finish()])
    raw_dist = device.queue.read_buffer(dist_buf)
    del uniform_refs
    return np.frombuffer(raw_dist, dtype=np.float32).reshape((height, width)).copy()


def _compute_sdf_cpu(
    obstacle_mask: NDArray[np.bool_],
    resolution_px_per_mm: float,
) -> NDArray[np.float32]:
    """Compute exact 2D Euclidean distance transform in millimetres on CPU via SciPy."""
    height, width = int(obstacle_mask.shape[0]), int(obstacle_mask.shape[1])
    if not bool(np.any(obstacle_mask)):
        diag_mm = math.hypot(width, height) / resolution_px_per_mm
        return np.full((height, width), diag_mm, dtype=np.float32)

    dist_px = np.asarray(distance_transform_edt(~obstacle_mask), dtype=np.float32)
    return np.asarray(dist_px / np.float32(resolution_px_per_mm), dtype=np.float32)


def compute_distance_field_from_mask(
    obstacle_mask: NDArray[np.bool_],
    *,
    resolution_px_per_mm: float = 2.0,
    gpu: bool | None = None,
) -> tuple[NDArray[np.float32], BackendName]:
    """Compute a 2D millimeter distance field from a boolean ``(H, W)`` obstacle mask.

    Uses the WebGPU JFA+1 compute shader when :func:`is_gpu_enabled` returns ``True``
    and falls back to exact CPU :func:`scipy.ndimage.distance_transform_edt` otherwise.
    """
    if resolution_px_per_mm <= 0.0:
        raise ValueError(f"resolution_px_per_mm must be positive, got {resolution_px_per_mm}.")
    mask_bool = np.ascontiguousarray(obstacle_mask, dtype=np.bool_)
    if mask_bool.ndim != 2 or mask_bool.shape[0] == 0 or mask_bool.shape[1] == 0:
        raise ValueError(f"Expected non-empty 2D obstacle_mask, got shape {mask_bool.shape}.")

    if is_gpu_enabled(gpu):
        return _compute_sdf_gpu(mask_bool, resolution_px_per_mm), "gpu"
    return _compute_sdf_cpu(mask_bool, resolution_px_per_mm), "cpu"


# ---------------------------------------------------------------------------
# 2D Sheet Occupancy Rasterizer (exact projected contours + UI exclusion zones)
# ---------------------------------------------------------------------------


def _bbox_to_px_rect(
    bbox: BBox2D,
    res: float,
    width_px: int,
    height_px: int,
    pad_mm: float = 0.0,
) -> tuple[int, int, int, int]:
    """Convert a sheet-mm bounding box into clamped pixel slice bounds ``(x0, y0, x1, y1)``."""
    min_x = min(bbox[0], bbox[2]) - pad_mm
    max_x = max(bbox[0], bbox[2]) + pad_mm
    min_y = min(bbox[1], bbox[3]) - pad_mm
    max_y = max(bbox[1], bbox[3]) + pad_mm
    x0 = max(0, min(width_px, int(math.floor(min_x * res))))
    x1 = max(0, min(width_px, int(math.ceil(max_x * res))))
    y0 = max(0, min(height_px, int(math.floor(min_y * res))))
    y1 = max(0, min(height_px, int(math.ceil(max_y * res))))
    return x0, y0, x1, y1


def _mark_mask_bbox(
    mask: NDArray[np.bool_],
    bbox: BBox2D,
    res: float,
    pad_mm: float = 0.0,
) -> tuple[int, int, int, int]:
    """Set pixels covering ``bbox`` (expanded by ``pad_mm``) to ``True`` in ``mask``."""
    height_px, width_px = int(mask.shape[0]), int(mask.shape[1])
    x0, y0, x1, y1 = _bbox_to_px_rect(bbox, res, width_px, height_px, pad_mm=pad_mm)
    if x1 > x0 and y1 > y0:
        mask[y0:y1, x0:x1] = True
    return x0, y0, x1, y1


def _sheet_title_block_bbox(sheet: DrawingSheet) -> BBox2D | None:
    """Return the conservative union of linter and rendered ISO 7200 title block bounds."""
    if not sheet.show_title_block:
        return None
    from build123d_techdraw.layout.linter import get_title_block_bbox

    lint_bb = get_title_block_bbox(sheet)
    extra = sheet.title_info.extra_fields
    if extra:
        cols = min(3, len(extra))
        extra_rows = (len(extra) + cols - 1) // cols
    else:
        extra_rows = 0
    render_h = 36.0 + extra_rows * 7.0
    render_bb = (
        sheet.width_mm - sheet.right_margin - 145.0,
        sheet.height_mm - sheet.bottom_margin - render_h,
        sheet.width_mm - sheet.right_margin,
        sheet.height_mm - sheet.bottom_margin,
    )
    return (
        min(lint_bb[0], render_bb[0]),
        min(lint_bb[1], render_bb[1]),
        max(lint_bb[2], render_bb[2]),
        max(lint_bb[3], render_bb[3]),
    )


def _sheet_revision_table_bbox(sheet: DrawingSheet) -> BBox2D | None:
    """Return the top-right revision table bounding box when enabled."""
    if not (sheet.show_title_block and sheet.revision_rows):
        return None
    width = 110.0
    total_h = 5.0 + len(sheet.revision_rows) * 5.0
    x0 = sheet.width_mm - sheet.right_margin - width
    y0 = sheet.top_margin
    return (x0, y0, x0 + width, y0 + total_h)


def _sheet_bom_table_bbox(sheet: DrawingSheet) -> BBox2D | None:
    """Return the BOM table bounding box matching :meth:`DrawingSheet.to_svg`."""
    if not sheet.bom_items:
        return None
    width = 175.0
    bom_h = 5.5 + len(sheet.bom_items) * 5.2
    x0 = sheet.width_mm - sheet.right_margin - width
    if sheet.bom_position == "top_right":
        rev_h = 10.0 if sheet.revision_rows else 0.0
        top_y = sheet.top_margin + rev_h + 2.0
    else:
        extra = sheet.title_info.extra_fields
        extra_rows = (len(extra) + min(3, len(extra)) - 1) // min(3, len(extra)) if extra else 0
        tb_y = sheet.height_mm - sheet.bottom_margin - 36.0 - extra_rows * 7.0
        top_y = tb_y - bom_h - 1.0
    return (x0, top_y, x0 + width, top_y + bom_h)


def _sheet_notes_bbox(sheet: DrawingSheet) -> BBox2D | None:
    """Return the bottom-left general notes exclusion box when enabled."""
    if not (sheet.show_notes and sheet.notes):
        return None
    bx = sheet.left_margin + 5.0
    by = sheet.height_mm - sheet.bottom_margin - 30.0
    max_chars = max(len(line) for line in sheet.notes)
    est_w = max(45.0, min(145.0, max_chars * 1.48 + 4.0))
    est_bottom = by + 4.5 + max(0, len(sheet.notes) - 1) * 4.2 + 2.5
    return (bx - 1.5, by - 3.5, bx + est_w, est_bottom)


def _view_label_bbox(view: SheetView) -> BBox2D | None:
    """Return the view title and scale label reservation box below ``view``."""
    if not view.view_label:
        return None
    vx = view.center_x
    vy = max(
        view.bottom_annotation_y + 8.0,
        view.view_label_pos[1] if view.view_label_pos else view.max_y + 8.0,
    )
    half_w = max(len(view.view_label) * 1.6, 20.0)
    min_y = min(view.max_y + 1.5, vy - 4.5)
    max_y = max(view.max_y + 12.0, vy + 4.5)
    return (vx - half_w, min_y, vx + half_w, max_y)


def _parse_svg_length_mm(raw: str | None) -> float | None:
    """Parse an SVG numeric length attribute (e.g. '420mm', '420') to float."""
    if not raw:
        return None
    cleaned = raw.strip().lower().replace("mm", "").replace("px", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _prepare_view_svg_document(svg_fragment: str, width_mm: float, height_mm: float) -> str | None:
    """Wrap a view SVG fragment in a clean root ``<svg>``, stripping ``<defs>`` and flattening nested ``<svg>``."""
    if not svg_fragment or not svg_fragment.strip():
        return None
    wrapped = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width_mm} {height_mm}" '
        f'width="{width_mm}" height="{height_mm}">'
        f"{svg_fragment}</svg>"
    )
    try:
        ET.register_namespace("", "http://www.w3.org/2000/svg")
        root = ET.fromstring(wrapped)
    except ET.ParseError:
        return wrapped

    def _local_name(tag: str) -> str:
        return tag.split("}", 1)[1] if "}" in tag else tag

    def _ns_prefix(tag: str) -> str:
        return f"{tag.split('}', 1)[0]}}}" if "}" in tag else ""

    def _clean(parent: ET.Element) -> None:
        for child in list(parent):
            lname = _local_name(child.tag).lower()
            if lname in ("defs", "clippath", "pattern", "marker", "style", "image"):
                parent.remove(child)
                continue
            if lname == "svg":
                x = float(child.attrib.pop("x", "0") or 0)
                y = float(child.attrib.pop("y", "0") or 0)
                w_raw = child.attrib.pop("width", None)
                h_raw = child.attrib.pop("height", None)
                vb_raw = child.attrib.pop("viewBox", None)
                transforms: list[str] = []
                if x != 0.0 or y != 0.0:
                    transforms.append(f"translate({x:g}, {y:g})")
                if vb_raw and w_raw and h_raw:
                    parts = [float(p) for p in re.split(r"[\s,]+", vb_raw.strip()) if p]
                    w_val = _parse_svg_length_mm(w_raw)
                    h_val = _parse_svg_length_mm(h_raw)
                    if len(parts) == 4 and w_val and h_val and parts[2] > 0 and parts[3] > 0:
                        sx = w_val / parts[2]
                        sy = h_val / parts[3]
                        transforms.append(f"scale({sx:g}, {sy:g})")
                        if parts[0] != 0.0 or parts[1] != 0.0:
                            transforms.append(f"translate({-parts[0]:g}, {-parts[1]:g})")
                existing = child.attrib.get("transform", "").strip()
                if existing:
                    transforms.append(existing)
                if transforms:
                    child.attrib["transform"] = " ".join(transforms)
                child.tag = f"{_ns_prefix(child.tag)}g"
            _clean(child)

    _clean(root)
    return ET.tostring(root, encoding="unicode")


def _rasterize_view_geometry(
    view: SheetView,
    width_mm: float,
    height_mm: float,
    res: float,
    stroke_mm: float,
) -> tuple[NDArray[np.bool_], NDArray[np.bool_]]:
    """Rasterize a single ``SheetView`` into ``(solid_mask, contour_mask)`` boolean arrays.

    Parses the view's projected SVG geometry via ``svgelements`` and rasterizes exact
    polylines/curves via Pillow ``ImageDraw``, then fills enclosed solid interiors via
    morphological hole-filling so concave pockets (L/C/T-brackets, stepped manifolds)
    remain completely open while solid part material is marked occupied.
    """
    width_px = max(1, int(round(width_mm * res)))
    height_px = max(1, int(round(height_mm * res)))
    stroke_px = max(2, int(round(stroke_mm * res)))

    img = Image.new("L", (width_px, height_px), 0)
    draw = ImageDraw.Draw(img)

    doc_str = _prepare_view_svg_document(view.svg_content, width_mm, height_mm)
    drew_any = False

    if doc_str is not None:
        try:
            doc = se.SVG.parse(
                io.BytesIO(doc_str.encode("utf-8")),
                ppi=25.4,
                width=width_mm,
                height=height_mm,
            )
        except Exception:
            doc = None

        if doc is not None:
            from build123d_techdraw.renderers.svg_elements import svg_property

            for el in doc.elements():
                if not isinstance(el, se.Shape):
                    continue
                path = se.Path(el)
                path.reify()
                poly: list[tuple[float, float]] = []
                sub_start: tuple[float, float] | None = None

                for seg in path:
                    if isinstance(seg, se.Move):
                        if len(poly) >= 2:
                            draw.line(poly, fill=255, width=stroke_px, joint="curve")
                            drew_any = True
                        end_pt = svg_property(seg, "end")
                        if end_pt is not None:
                            pt = (float(end_pt.x) * res, float(end_pt.y) * res)
                            poly = [pt]
                            sub_start = pt
                        else:
                            poly = []
                            sub_start = None
                    elif isinstance(seg, se.Line):
                        start_pt = svg_property(seg, "start")
                        end_pt = svg_property(seg, "end")
                        if not poly and start_pt is not None:
                            pt0 = (float(start_pt.x) * res, float(start_pt.y) * res)
                            poly = [pt0]
                            sub_start = pt0
                        if end_pt is not None:
                            poly.append((float(end_pt.x) * res, float(end_pt.y) * res))
                    elif isinstance(seg, se.Close):
                        if sub_start is not None and poly:
                            poly.append(sub_start)
                        if len(poly) >= 2:
                            draw.line(poly, fill=255, width=stroke_px, joint="curve")
                            drew_any = True
                        poly = []
                    elif isinstance(seg, (se.CubicBezier, se.QuadraticBezier, se.Arc)):
                        start_pt = svg_property(seg, "start")
                        if not poly and start_pt is not None:
                            pt0 = (float(start_pt.x) * res, float(start_pt.y) * res)
                            poly = [pt0]
                            sub_start = pt0
                        try:
                            seg_len_px = float(seg.length()) * res
                        except Exception:
                            seg_len_px = 16.0
                        n_steps = max(4, min(64, int(math.ceil(seg_len_px / 1.5))))
                        for k in range(1, n_steps + 1):
                            p = seg.point(k / n_steps)
                            if p is not None:
                                poly.append((float(p.x) * res, float(p.y) * res))

                if len(poly) >= 2:
                    draw.line(poly, fill=255, width=stroke_px, joint="curve")
                    drew_any = True

    from build123d_techdraw.core.view import ViewType

    if view.view_type == ViewType.DETAIL and view.detail_shape != "rect":
        # Clip enlarged detail geometry to its circular detail boundary and draw the rim.
        cx_px = view.center_x * res
        cy_px = view.center_y * res
        r_px = max(view.width, view.height) * 0.5 * res
        yy, xx = np.ogrid[:height_px, :width_px]
        in_disk = (xx - cx_px) ** 2 + (yy - cy_px) ** 2 <= (r_px + 1.0) ** 2
        contours = (np.asarray(img, dtype=np.uint8) > 0) & in_disk
        rim = Image.new("L", (width_px, height_px), 0)
        ImageDraw.Draw(rim).ellipse(
            [cx_px - r_px, cy_px - r_px, cx_px + r_px, cy_px + r_px],
            outline=255,
            width=stroke_px,
        )
        contours |= np.asarray(rim, dtype=np.uint8) > 0
        solid = in_disk | contours
        return solid, contours

    contours = np.asarray(img, dtype=np.uint8) > 0

    if not drew_any or not bool(np.any(contours)):
        # Synthetic view without SVG geometry: fall back to view AABB.
        solid = np.zeros((height_px, width_px), dtype=np.bool_)
        _mark_mask_bbox(solid, (view.min_x, view.min_y, view.max_x, view.max_y), res, pad_mm=0.0)
        return solid, solid.copy()

    # Clip to a padded window around the view bounds to reject any stray SVG artifact.
    pad_clip = 3.5
    vx0, vy0, vx1, vy1 = _bbox_to_px_rect(
        (view.min_x, view.min_y, view.max_x, view.max_y),
        res,
        width_px,
        height_px,
        pad_mm=pad_clip,
    )
    if vx1 > vx0 and vy1 > vy0:
        clipped = np.zeros_like(contours)
        clipped[vy0:vy1, vx0:vx1] = contours[vy0:vy1, vx0:vx1]
        contours = clipped

    # Seal tiny 1-pixel tangent gaps before hole-filling, then erode back so concave
    # whitespace pockets remain completely open while enclosed solid interiors are filled.
    if vx1 > vx0 and vy1 > vy0:
        # Operate on the tight ROI padded by 2 px of False so exterior flood-fill reaches
        # every exterior concave pocket cleanly and runs in < 1 ms.
        roi = contours[vy0:vy1, vx0:vx1]
        padded_roi = np.pad(roi, pad_width=2, mode="constant", constant_values=False)
        sealed = binary_dilation(padded_roi, iterations=1)
        filled = binary_fill_holes(sealed)
        eroded = np.asarray(binary_erosion(filled, iterations=1), dtype=np.bool_)
        solid_roi = roi | eroded[2:-2, 2:-2]
        solid = np.zeros_like(contours)
        solid[vy0:vy1, vx0:vx1] = solid_roi
    else:
        solid = contours.copy()

    return solid, contours


# ---------------------------------------------------------------------------
# Geometric helpers (segment intersection, leader obstacle sampling, heatmaps)
# ---------------------------------------------------------------------------


def _orient2d(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _segments_intersect(
    a0: tuple[float, float],
    a1: tuple[float, float],
    b0: tuple[float, float],
    b1: tuple[float, float],
    *,
    shared_endpoint_tol: float = 0.25,
) -> bool:
    """Return True if two 2D line segments properly intersect (ignoring shared endpoints)."""
    if (
        math.hypot(a0[0] - b0[0], a0[1] - b0[1]) <= shared_endpoint_tol
        or math.hypot(a0[0] - b1[0], a0[1] - b1[1]) <= shared_endpoint_tol
        or math.hypot(a1[0] - b0[0], a1[1] - b0[1]) <= shared_endpoint_tol
        or math.hypot(a1[0] - b1[0], a1[1] - b1[1]) <= shared_endpoint_tol
    ):
        return False

    # Fast AABB rejection
    if (
        max(a0[0], a1[0]) < min(b0[0], b1[0])
        or max(b0[0], b1[0]) < min(a0[0], a1[0])
        or max(a0[1], a1[1]) < min(b0[1], b1[1])
        or max(b0[1], b1[1]) < min(a0[1], a1[1])
    ):
        return False

    o1 = _orient2d(a0, a1, b0)
    o2 = _orient2d(a0, a1, b1)
    o3 = _orient2d(b0, b1, a0)
    o4 = _orient2d(b0, b1, a1)
    return (o1 * o2 < 0.0) and (o3 * o4 < 0.0)


def _closest_point_on_box(
    pt: tuple[float, float],
    bbox: BBox2D,
) -> tuple[float, float]:
    """Return the point on/in ``bbox`` closest to ``pt``."""
    cx = min(max(pt[0], bbox[0]), bbox[2])
    cy = min(max(pt[1], bbox[1]), bbox[3])
    return (cx, cy)


def _angle_diff_deg(a: float, b: float) -> float:
    """Smallest absolute angular difference between two angles in degrees."""
    diff = (a - b + 180.0) % 360.0 - 180.0
    return abs(diff)


def _build_candidate_angles(
    step_deg: float,
    preferred_angles_deg: Sequence[float] | None,
) -> list[float]:
    """Return uniform 0..360 deg sweep angles augmented with distinct preferred angles."""
    n_steps = max(1, int(round(360.0 / step_deg)))
    angles: list[float] = [i * step_deg for i in range(n_steps)]
    if preferred_angles_deg:
        for ang in preferred_angles_deg:
            norm_ang = float(ang) % 360.0
            if all(_angle_diff_deg(norm_ang, existing) > 1.0 for existing in angles):
                angles.append(norm_ang)
    return angles


def _band_contour_mask(
    distance_mm: NDArray[np.float32],
    free_mask: NDArray[np.bool_],
    step_mm: float,
) -> NDArray[np.bool_]:
    """Return a boolean pixel mask marking iso-distance band transitions in free space."""
    band = np.floor(distance_mm / float(step_mm)).astype(np.int32)
    edges = np.zeros_like(free_mask)
    edges[:, 1:] |= band[:, 1:] != band[:, :-1]
    edges[1:, :] |= band[1:, :] != band[:-1, :]
    return free_mask & edges


def _build_color_lut(stops: Sequence[tuple[float, tuple[int, int, int]]]) -> NDArray[np.uint8]:
    """Build a 256-entry RGB lookup table by linear interpolation across normalized stops."""
    xs = np.linspace(0.0, 1.0, 256)
    stop_pos = np.array([s[0] for s in stops], dtype=np.float64)
    lut = np.empty((256, 3), dtype=np.uint8)
    for ch in range(3):
        vals = np.array([s[1][ch] for s in stops], dtype=np.float64)
        lut[:, ch] = np.clip(np.rint(np.interp(xs, stop_pos, vals)), 0, 255).astype(np.uint8)
    return lut


_DARK_BLUEPRINT_LUT = _build_color_lut(
    [
        (0.00, (9, 22, 42)),
        (0.15, (14, 56, 98)),
        (0.35, (18, 112, 168)),
        (0.60, (38, 178, 218)),
        (0.82, (110, 226, 232)),
        (1.00, (224, 248, 255)),
    ]
)

_ISO_WHITE_LUT = _build_color_lut(
    [
        (0.00, (30, 58, 118)),
        (0.18, (64, 116, 184)),
        (0.42, (124, 178, 224)),
        (0.70, (194, 224, 244)),
        (1.00, (248, 252, 255)),
    ]
)


# ---------------------------------------------------------------------------
# SheetDistanceField
# ---------------------------------------------------------------------------


class SheetDistanceField:
    """2D Euclidean Signed/Unsigned Distance Field (clearance in mm) over a ``DrawingSheet``.

    Attributes:
        distance_mm: ``(H, W)`` ``float32`` array giving Euclidean clearance in millimetres
            from each pixel to the nearest occupied obstacle.
        obstacle_mask: ``(H, W)`` ``bool`` array (``True`` = occupied obstacle).
        resolution_px_per_mm: Raster resolution in pixels per sheet millimetre.
        backend: Execution backend that computed the field (``"gpu"`` or ``"cpu"``).
        width_mm: Sheet width in millimetres.
        height_mm: Sheet height in millimetres.
    """

    def __init__(
        self,
        distance_mm: NDArray[np.float32],
        obstacle_mask: NDArray[np.bool_],
        *,
        resolution_px_per_mm: float,
        backend: BackendName,
        width_mm: float,
        height_mm: float,
        geometry_mask: NDArray[np.bool_] | None = None,
        contour_mask: NDArray[np.bool_] | None = None,
        frame_mask: NDArray[np.bool_] | None = None,
        label_mask: NDArray[np.bool_] | None = None,
        drawable_bounds: BBox2D | None = None,
    ) -> None:
        self.distance_mm: NDArray[np.float32] = np.ascontiguousarray(distance_mm, dtype=np.float32)
        self.obstacle_mask: NDArray[np.bool_] = np.ascontiguousarray(obstacle_mask, dtype=np.bool_)
        self.resolution_px_per_mm: float = float(resolution_px_per_mm)
        self.backend: BackendName = backend
        self.width_mm: float = float(width_mm)
        self.height_mm: float = float(height_mm)

        shape_hw = self.obstacle_mask.shape
        self._geometry_mask: NDArray[np.bool_] = (
            np.ascontiguousarray(geometry_mask, dtype=np.bool_)
            if geometry_mask is not None
            else np.zeros(shape_hw, dtype=np.bool_)
        )
        self._contour_mask: NDArray[np.bool_] = (
            np.ascontiguousarray(contour_mask, dtype=np.bool_)
            if contour_mask is not None
            else np.zeros(shape_hw, dtype=np.bool_)
        )
        self._frame_mask: NDArray[np.bool_] = (
            np.ascontiguousarray(frame_mask, dtype=np.bool_)
            if frame_mask is not None
            else np.zeros(shape_hw, dtype=np.bool_)
        )
        self._label_mask: NDArray[np.bool_] = (
            np.ascontiguousarray(label_mask, dtype=np.bool_)
            if label_mask is not None
            else np.zeros(shape_hw, dtype=np.bool_)
        )
        self.drawable_bounds: BBox2D = (
            drawable_bounds
            if drawable_bounds is not None
            else (0.0, 0.0, self.width_mm, self.height_mm)
        )
        self.placed_boxes: list[BBox2D] = []
        self._leader_segments: list[tuple[tuple[float, float], tuple[float, float]]] = []

        # Precompute pixel index coordinate vectors for fast exact incremental box updates.
        h, w = int(shape_hw[0]), int(shape_hw[1])
        self._xs_px: NDArray[np.int32] = np.arange(w, dtype=np.int32)
        self._ys_px: NDArray[np.int32] = np.arange(h, dtype=np.int32)

    @property
    def height_px(self) -> int:
        """Raster height in pixels."""
        return int(self.distance_mm.shape[0])

    @property
    def width_px(self) -> int:
        """Raster width in pixels."""
        return int(self.distance_mm.shape[1])

    def query_clearance(self, x_mm: float, y_mm: float) -> float:
        """Return the Euclidean clearance in millimetres at sheet coordinate ``(x_mm, y_mm)``."""
        if x_mm < 0.0 or y_mm < 0.0 or x_mm >= self.width_mm or y_mm >= self.height_mm:
            return 0.0
        ix = min(self.width_px - 1, max(0, int(math.floor(x_mm * self.resolution_px_per_mm))))
        iy = min(self.height_px - 1, max(0, int(math.floor(y_mm * self.resolution_px_per_mm))))
        return float(self.distance_mm[iy, ix])

    def box_min_clearance(
        self,
        min_x: float,
        min_y: float,
        max_x: float,
        max_y: float,
    ) -> float:
        """Return the minimum SDF clearance (mm) anywhere inside the given sheet-mm rectangle."""
        x0, x1 = (min_x, max_x) if min_x <= max_x else (max_x, min_x)
        y0, y1 = (min_y, max_y) if min_y <= max_y else (max_y, min_y)
        if x0 < 0.0 or y0 < 0.0 or x1 > self.width_mm or y1 > self.height_mm:
            return 0.0
        res = self.resolution_px_per_mm
        ix0 = max(0, min(self.width_px - 1, int(math.floor(x0 * res))))
        ix1 = max(ix0 + 1, min(self.width_px, int(math.ceil(x1 * res))))
        iy0 = max(0, min(self.height_px - 1, int(math.floor(y0 * res))))
        iy1 = max(iy0 + 1, min(self.height_px, int(math.ceil(y1 * res))))
        return float(np.min(self.distance_mm[iy0:iy1, ix0:ix1]))

    def mark_occupied_box(self, bbox: BBox2D, pad_mm: float = 1.0) -> None:
        """Mark ``bbox`` (expanded by ``pad_mm``) occupied and update ``distance_mm`` exactly.

        Uses the exact analytical Euclidean distance to an axis-aligned pixel rectangle
        ``D_new = min(D_old, d_rect)``, which is mathematically identical to re-running a
        full EDT/JFA transform over the updated mask while completing in sub-millisecond time.
        """
        norm_bbox = (
            min(bbox[0], bbox[2]),
            min(bbox[1], bbox[3]),
            max(bbox[0], bbox[2]),
            max(bbox[1], bbox[3]),
        )
        self.placed_boxes.append(norm_bbox)

        x0, y0, x1, y1 = _mark_mask_bbox(
            self.obstacle_mask,
            norm_bbox,
            self.resolution_px_per_mm,
            pad_mm=pad_mm,
        )
        if x1 <= x0 or y1 <= y0:
            return

        # Exact pixel distance to the occupied pixel block [x0, x1 - 1] x [y0, y1 - 1].
        dx_px = np.maximum(0, np.maximum(x0 - self._xs_px, self._xs_px - (x1 - 1)))
        dy_px = np.maximum(0, np.maximum(y0 - self._ys_px, self._ys_px - (y1 - 1)))
        dx_sq = dx_px.astype(np.float32) ** 2
        dy_sq = dy_px.astype(np.float32) ** 2
        dist_rect_mm = np.sqrt(dy_sq[:, None] + dx_sq[None, :]) / np.float32(
            self.resolution_px_per_mm
        )
        np.minimum(self.distance_mm, dist_rect_mm, out=self.distance_mm)

    def register_leader_segment(
        self,
        start_xy: tuple[float, float],
        end_xy: tuple[float, float],
    ) -> None:
        """Record a placed leader line segment so future callouts avoid crossing it."""
        self._leader_segments.append((start_xy, end_xy))

    def _leader_obstacle_length_mm(
        self,
        start_xy: tuple[float, float],
        end_xy: tuple[float, float],
        *,
        start_skip_mm: float = 1.5,
        end_skip_mm: float = 0.8,
    ) -> tuple[float, float, float]:
        """Measure ``(total_obstacle_mm, geometry_obstacle_mm, label_obstacle_mm)`` along a leader."""
        dx = end_xy[0] - start_xy[0]
        dy = end_xy[1] - start_xy[1]
        seg_len = math.hypot(dx, dy)
        if seg_len <= start_skip_mm + end_skip_mm + 1e-6:
            return 0.0, 0.0, 0.0

        t0 = start_skip_mm / seg_len
        t1 = 1.0 - (end_skip_mm / seg_len)
        active_len = seg_len * (t1 - t0)
        n_samples = max(6, int(math.ceil(active_len * self.resolution_px_per_mm)))
        ts = np.linspace(t0, t1, n_samples, dtype=np.float64)
        xs = start_xy[0] + ts * dx
        ys = start_xy[1] + ts * dy

        res = self.resolution_px_per_mm
        ixs = np.clip(np.floor(xs * res).astype(np.int32), 0, self.width_px - 1)
        iys = np.clip(np.floor(ys * res).astype(np.int32), 0, self.height_px - 1)

        obs_frac = float(np.mean(self.obstacle_mask[iys, ixs]))
        geom_frac = float(np.mean(self._geometry_mask[iys, ixs]))
        label_frac = float(np.mean(self._label_mask[iys, ixs]))
        return obs_frac * active_len, geom_frac * active_len, label_frac * active_len

    def _count_leader_crossings(
        self,
        start_xy: tuple[float, float],
        end_xy: tuple[float, float],
    ) -> int:
        """Count how many previously registered leader segments intersect ``(start_xy, end_xy)``."""
        return sum(
            1
            for seg_a, seg_b in self._leader_segments
            if _segments_intersect(start_xy, end_xy, seg_a, seg_b)
        )

    def _score_candidate_box(
        self,
        target_xy: tuple[float, float],
        box: BBox2D,
        *,
        leader_end: tuple[float, float],
        dist_mm: float,
        angle_deg: float,
        min_dist_mm: float,
        preferred_angles_deg: Sequence[float] | None,
        avoid_leader_crossings: bool,
    ) -> tuple[bool, float]:
        """Score a candidate annotation box; returns ``(is_clear, score)``."""
        # Reject out-of-drawable-bounds boxes immediately.
        db = self.drawable_bounds
        if box[0] < db[0] or box[1] < db[1] or box[2] > db[2] or box[3] > db[3]:
            return False, -1e9

        min_clr = self.box_min_clearance(box[0], box[1], box[2], box[3])
        cx = 0.5 * (box[0] + box[2])
        cy = 0.5 * (box[1] + box[3])
        center_clr = self.query_clearance(cx, cy)

        is_clear = min_clr > 0.0
        if is_clear:
            # Strong reward for safe clearance up to 6 mm without flinging far into empty space.
            score = 1000.0 + min(min_clr, 6.0) * 14.0 + min(center_clr, 12.0) * 1.5
        else:
            # Fallback tie-breaker when no fully clear spot exists: prefer higher center clearance.
            score = -500.0 + center_clr * 8.0

        # Distance preference: keep leaders compact and crisp around the feature.
        ideal_dist = min_dist_mm + 4.0
        score -= abs(dist_mm - ideal_dist) * 0.55
        if dist_mm > ideal_dist:
            score -= (dist_mm - ideal_dist) * 0.25

        # Leader obstacle & crossing penalties
        if avoid_leader_crossings:
            obs_mm, geom_mm, label_mm = self._leader_obstacle_length_mm(target_xy, leader_end)
            score -= obs_mm * 3.0 + geom_mm * 2.5 + label_mm * 12.0
            crossings = self._count_leader_crossings(target_xy, leader_end)
            if crossings > 0:
                score -= crossings * 120.0

        # Angular preference (user-specified or gentle ISO oblique-leader preference)
        if preferred_angles_deg:
            best_ang = min(_angle_diff_deg(angle_deg, p) for p in preferred_angles_deg)
            score += max(0.0, 1.0 - best_ang / 60.0) * 12.0
        else:
            # Slight preference for crisp oblique angles (~45/135/225/315 deg) per ISO drafting.
            score += abs(math.sin(2.0 * math.radians(angle_deg))) * 2.0

        return is_clear, score

    def find_best_callout_position(
        self,
        target_xy: tuple[float, float],
        box_size_mm: tuple[float, float],
        *,
        min_dist_mm: float = 10.0,
        max_dist_mm: float = 55.0,
        preferred_angles_deg: Sequence[float] | None = None,
        avoid_leader_crossings: bool = True,
        validator: Callable[[tuple[float, float]], bool] | None = None,
    ) -> tuple[float, float]:
        """Find the highest-clearance collision-free center ``(cx, cy)`` for a callout box.

        Evaluates candidate rings around ``target_xy`` using exact SDF box clearance,
        leader-line obstacle sampling, leader-crossing detection, and distance/angle
        preferences so callouts naturally discover concave part pockets while keeping
        leaders short and unobstructed.
        """
        tx, ty = float(target_xy[0]), float(target_xy[1])
        bw = max(1.0, float(box_size_mm[0]))
        bh = max(1.0, float(box_size_mm[1]))
        half_w = 0.5 * bw
        half_h = 0.5 * bh

        lo_dist = max(2.0, float(min_dist_mm))
        hi_dist = max(lo_dist, float(max_dist_mm))

        angles = _build_candidate_angles(7.5, preferred_angles_deg)

        def _search_range(
            r_lo: float, r_hi: float, step_mm: float
        ) -> tuple[tuple[float, float] | None, int, float]:
            n_rings = max(1, int(math.ceil((r_hi - r_lo) / step_mm)) + 1)
            radii = np.linspace(r_lo, r_hi, n_rings)
            best_pos: tuple[float, float] | None = None
            # Priority tier: 2 = SDF-clear & validator-valid, 1 = validator-valid, 0 = fallback
            best_tier = -1
            best_score = -1e18

            for r_val in radii:
                r = float(r_val)
                for ang in angles:
                    rad = math.radians(ang)
                    cx = tx + r * math.cos(rad)
                    cy = ty + r * math.sin(rad)
                    box = (cx - half_w, cy - half_h, cx + half_w, cy + half_h)
                    leader_end = _closest_point_on_box((tx, ty), box)
                    is_clear, score = self._score_candidate_box(
                        (tx, ty),
                        box,
                        leader_end=leader_end,
                        dist_mm=r,
                        angle_deg=ang,
                        min_dist_mm=lo_dist,
                        preferred_angles_deg=preferred_angles_deg,
                        avoid_leader_crossings=avoid_leader_crossings,
                    )
                    is_valid = validator((cx, cy)) if validator is not None else True
                    if is_clear and is_valid:
                        tier = 2
                    elif is_valid:
                        tier = 1
                    else:
                        tier = 0

                    if tier > best_tier or (tier == best_tier and score > best_score):
                        best_tier = tier
                        best_score = score
                        best_pos = (cx, cy)
            return best_pos, best_tier, best_score

        best_pos, best_tier, best_score = _search_range(lo_dist, hi_dist, 3.0)

        # If the initial search ring was completely blocked (very crowded sheet), widen once.
        if best_tier < 2:
            wider_hi = max(hi_dist * 1.75, 95.0)
            alt_pos, alt_tier, alt_score = _search_range(hi_dist + 3.0, wider_hi, 4.0)
            if alt_pos is not None and (
                alt_tier > best_tier or (alt_tier == best_tier and alt_score > best_score)
            ):
                best_pos = alt_pos

        if best_pos is None:
            # Extreme fallback: clamp slightly offset inside drawable bounds.
            db = self.drawable_bounds
            cx = min(max(tx + lo_dist, db[0] + half_w), db[2] - half_w)
            cy = min(max(ty - lo_dist, db[1] + half_h), db[3] - half_h)
            return (cx, cy)

        return best_pos

    def find_best_leader_elbow(
        self,
        target_xy: tuple[float, float],
        shelf_length_mm: float,
        text_height_mm: float = 8.0,
        *,
        min_dist_mm: float = 12.0,
        max_dist_mm: float = 60.0,
        preferred_angles_deg: Sequence[float] | None = None,
        avoid_leader_crossings: bool = True,
        validator: Callable[[tuple[float, float]], bool] | None = None,
    ) -> tuple[float, float]:
        """Find an optimal ``(elbow_dx, elbow_dy)`` offset for a ``LeaderNote`` / ``HoleCallout``.

        Scores each candidate elbow by the SDF clearance of its horizontal text shelf,
        leader obstacle/crossing avoidance, and optional linter-compatibility ``validator``.
        """
        tx, ty = float(target_xy[0]), float(target_xy[1])
        shelf_len = max(10.0, float(shelf_length_mm))
        text_h = max(6.0, float(text_height_mm))
        lo_dist = max(4.0, float(min_dist_mm))
        hi_dist = max(lo_dist, float(max_dist_mm))

        angles = _build_candidate_angles(5.0, preferred_angles_deg)

        def _search(
            r_lo: float, r_hi: float, step_mm: float
        ) -> tuple[tuple[float, float] | None, int, float]:
            n_rings = max(1, int(math.ceil((r_hi - r_lo) / step_mm)) + 1)
            radii = np.linspace(r_lo, r_hi, n_rings)
            best_off: tuple[float, float] | None = None
            # Priority tier: 2 = SDF-clear & validator-valid, 1 = validator-valid, 0 = fallback
            best_tier = -1
            best_score = -1e18

            for r_val in radii:
                r = float(r_val)
                for ang in angles:
                    rad = math.radians(ang)
                    dx = r * math.cos(rad)
                    dy = r * math.sin(rad)
                    # Avoid near-zero horizontal elbow so shelf direction is unambiguous.
                    if abs(dx) < 1.2:
                        continue
                    ex = tx + dx
                    ey = ty + dy
                    sx = ex + (shelf_len if dx >= 0.0 else -shelf_len)
                    # Visual shelf box covers the text seated above ey and a small margin below.
                    shelf_box = (
                        min(ex, sx),
                        ey - text_h + 1.5,
                        max(ex, sx),
                        ey + 2.0,
                    )
                    is_clear, score = self._score_candidate_box(
                        (tx, ty),
                        shelf_box,
                        leader_end=(ex, ey),
                        dist_mm=r,
                        angle_deg=ang,
                        min_dist_mm=lo_dist,
                        preferred_angles_deg=preferred_angles_deg,
                        avoid_leader_crossings=avoid_leader_crossings,
                    )
                    is_valid = validator((dx, dy)) if validator is not None else True
                    if is_clear and is_valid:
                        tier = 2
                    elif is_valid:
                        tier = 1
                    else:
                        tier = 0

                    if tier > best_tier or (tier == best_tier and score > best_score):
                        best_tier = tier
                        best_score = score
                        best_off = (dx, dy)
            return best_off, best_tier, best_score

        best_off, best_tier, best_score = _search(lo_dist, hi_dist, 3.5)
        if best_tier < 2:
            wider_hi = max(hi_dist * 1.8, 125.0)
            alt_off, alt_tier, alt_score = _search(hi_dist + 3.5, wider_hi, 4.5)
            if alt_off is not None and (
                alt_tier > best_tier or (alt_tier == best_tier and alt_score > best_score)
            ):
                best_off = alt_off
                best_tier = alt_tier
                best_score = alt_score

        # Also check shorter leaders (down to 6 mm) for tight concave pockets if still unplaced.
        if best_tier < 2 and lo_dist > 6.5:
            tight_off, tight_tier, tight_score = _search(6.0, lo_dist - 1.5, 2.0)
            if tight_off is not None and (
                tight_tier > best_tier or (tight_tier == best_tier and tight_score > best_score)
            ):
                best_off = tight_off

        if best_off is None:
            return (lo_dist * 0.8, -lo_dist * 0.6)
        return best_off

    def render_heatmap_image(
        self,
        *,
        theme: Theme | None = None,
        placed_boxes: Sequence[BBox2D] | None = None,
        max_clearance_mm: float = 40.0,
        contour_step_mm: float = 5.0,
    ) -> Image.Image:
        """Render a blueprint-styled 2D clearance heatmap PNG for visualization and CI diffs.

        Displays the continuous Euclidean clearance field with iso-distance contour rings,
        occupied margins/title-block/tables, crisp projected part geometry silhouettes, and
        highlighted placed annotation boxes.
        """
        from build123d_techdraw.renderers.theme import Theme as _Theme

        resolved_theme = _Theme.DARK_BLUEPRINT if theme is None else theme
        is_dark = resolved_theme == _Theme.DARK_BLUEPRINT
        lut = _DARK_BLUEPRINT_LUT if is_dark else _ISO_WHITE_LUT

        norm = np.clip(self.distance_mm / max(1.0, float(max_clearance_mm)), 0.0, 1.0)
        idx = np.clip(np.rint(norm * 255.0), 0, 255).astype(np.uint8)
        rgb = lut[idx].copy()

        # Draw crisp iso-distance clearance contour bands in free space.
        free = ~self.obstacle_mask
        if contour_step_mm > 0.0:
            contour_px = _band_contour_mask(self.distance_mm, free, contour_step_mm)
            major_px = _band_contour_mask(self.distance_mm, free, contour_step_mm * 2.0)

            if is_dark:
                rgb[contour_px] = np.clip(
                    rgb[contour_px].astype(np.int16) + np.array([18, 28, 34]), 0, 255
                ).astype(np.uint8)
                rgb[major_px] = np.clip(
                    rgb[major_px].astype(np.int16) + np.array([34, 48, 56]), 0, 255
                ).astype(np.uint8)
            else:
                rgb[contour_px] = np.clip(
                    rgb[contour_px].astype(np.int16) - np.array([22, 20, 16]), 0, 255
                ).astype(np.uint8)
                rgb[major_px] = np.clip(
                    rgb[major_px].astype(np.int16) - np.array([42, 38, 32]), 0, 255
                ).astype(np.uint8)

        # Style occupied zones distinctly: UI frames, solid part geometry, and crisp contours.
        if is_dark:
            rgb[self.obstacle_mask] = np.array([15, 27, 46], dtype=np.uint8)
            rgb[self._frame_mask] = np.array([11, 21, 36], dtype=np.uint8)
            rgb[self._geometry_mask] = np.array([24, 42, 68], dtype=np.uint8)
            rgb[self._contour_mask] = np.array([248, 252, 255], dtype=np.uint8)
        else:
            rgb[self.obstacle_mask] = np.array([226, 232, 240], dtype=np.uint8)
            rgb[self._frame_mask] = np.array([241, 245, 249], dtype=np.uint8)
            rgb[self._geometry_mask] = np.array([203, 213, 225], dtype=np.uint8)
            rgb[self._contour_mask] = np.array([15, 23, 42], dtype=np.uint8)

        rgba = np.empty((self.height_px, self.width_px, 4), dtype=np.uint8)
        rgba[:, :, :3] = rgb
        rgba[:, :, 3] = 255
        img = Image.fromarray(rgba, mode="RGBA")
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        res = self.resolution_px_per_mm

        # Sheet drawable border frame line
        db = self.drawable_bounds
        frame_col = (56, 189, 248, 220) if is_dark else (15, 23, 42, 220)
        draw.rectangle(
            [db[0] * res, db[1] * res, db[2] * res, db[3] * res],
            outline=frame_col,
            width=max(1, int(round(0.7 * res))),
        )

        # Draw registered leader segments
        leader_col = (244, 63, 94, 235) if is_dark else (220, 38, 38, 235)
        for p0, p1 in self._leader_segments:
            draw.line(
                [p0[0] * res, p0[1] * res, p1[0] * res, p1[1] * res],
                fill=leader_col,
                width=max(1, int(round(0.45 * res))),
            )

        # Draw placed annotation boxes (both tracked on this SDF and any extra passed in)
        all_boxes: list[BBox2D] = list(self.placed_boxes)
        if placed_boxes:
            all_boxes.extend(placed_boxes)

        box_fill = (16, 185, 129, 72) if is_dark else (220, 38, 38, 48)
        box_outline = (52, 211, 153, 245) if is_dark else (220, 38, 38, 240)
        for bb in all_boxes:
            x0, y0 = min(bb[0], bb[2]) * res, min(bb[1], bb[3]) * res
            x1, y1 = max(bb[0], bb[2]) * res, max(bb[1], bb[3]) * res
            draw.rectangle(
                [x0, y0, x1, y1],
                fill=box_fill,
                outline=box_outline,
                width=max(1, int(round(0.6 * res))),
            )

        return Image.alpha_composite(img, overlay)


# ---------------------------------------------------------------------------
# Public Sheet SDF Builder
# ---------------------------------------------------------------------------


def compute_sheet_sdf(
    sheet: DrawingSheet,
    *,
    resolution_px_per_mm: float = 2.0,
    gpu: bool | None = None,
    include_annotations: bool = True,
    include_labels: bool = True,
    include_notes: bool = True,
    geometry_stroke_mm: float = 0.8,
) -> SheetDistanceField:
    """Build a 2D Euclidean clearance field (:class:`SheetDistanceField`) for ``sheet``.

    Rasterizes:
    - Sheet border / margin exclusion bands,
    - ISO 7200 title block, revision table, BOM table, and general notes block,
    - Exact projected 2D geometry contours and solid silhouettes of every ``SheetView``,
    - View title labels and existing annotations' bounding boxes.

    Computes Euclidean millimeter clearance on GPU via WGSL Jump Flooding (JFA+1) when
    ``is_gpu_enabled(gpu)`` is ``True``, seamlessly falling back to exact CPU SciPy EDT
    when ``gpu=False`` or ``[gpu]`` is unavailable.
    """
    if resolution_px_per_mm <= 0.0:
        raise ValueError(f"resolution_px_per_mm must be positive, got {resolution_px_per_mm}.")

    res = float(resolution_px_per_mm)
    width_mm = float(sheet.width_mm)
    height_mm = float(sheet.height_mm)
    width_px = max(1, int(round(width_mm * res)))
    height_px = max(1, int(round(height_mm * res)))

    obstacle_mask = np.zeros((height_px, width_px), dtype=np.bool_)
    frame_mask = np.zeros((height_px, width_px), dtype=np.bool_)
    geometry_mask = np.zeros((height_px, width_px), dtype=np.bool_)
    contour_mask = np.zeros((height_px, width_px), dtype=np.bool_)
    label_mask = np.zeros((height_px, width_px), dtype=np.bool_)

    drawable_bounds: BBox2D = (
        float(sheet.left_margin),
        float(sheet.top_margin),
        float(width_mm - sheet.right_margin),
        float(height_mm - sheet.bottom_margin),
    )

    # 1. Sheet margins / border exclusion zone
    left_px = max(0, min(width_px, int(math.ceil(drawable_bounds[0] * res))))
    top_px = max(0, min(height_px, int(math.ceil(drawable_bounds[1] * res))))
    right_px = max(0, min(width_px, int(math.floor(drawable_bounds[2] * res))))
    bottom_px = max(0, min(height_px, int(math.floor(drawable_bounds[3] * res))))

    if left_px > 0:
        frame_mask[:, :left_px] = True
    if right_px < width_px:
        frame_mask[:, right_px:] = True
    if top_px > 0:
        frame_mask[:top_px, :] = True
    if bottom_px < height_px:
        frame_mask[bottom_px:, :] = True

    # 2. ISO 7200 title block, revision table, BOM table, and general notes block
    for ui_box in (
        _sheet_title_block_bbox(sheet),
        _sheet_revision_table_bbox(sheet),
        _sheet_bom_table_bbox(sheet),
        _sheet_notes_bbox(sheet) if include_notes else None,
    ):
        if ui_box is not None:
            _mark_mask_bbox(frame_mask, ui_box, res, pad_mm=1.0)

    obstacle_mask |= frame_mask

    # 3. Actual projected 2D geometry contours + solid interiors of every SheetView
    for view in sheet.views:
        v_solid, v_contours = _rasterize_view_geometry(
            view,
            width_mm=width_mm,
            height_mm=height_mm,
            res=res,
            stroke_mm=geometry_stroke_mm,
        )
        geometry_mask |= v_solid
        contour_mask |= v_contours

        if include_labels:
            lbl_bb = _view_label_bbox(view)
            if lbl_bb is not None:
                _mark_mask_bbox(label_mask, lbl_bb, res, pad_mm=0.5)
                _mark_mask_bbox(obstacle_mask, lbl_bb, res, pad_mm=0.5)

    obstacle_mask |= geometry_mask

    # 4. Existing annotations' bounding boxes (excluding CenterMark)
    if include_annotations:
        for ann in sheet.annotations:
            if isinstance(ann, CenterMark):
                continue
            ann_bb = ann.bounding_box_2d()
            if ann_bb is not None:
                _mark_mask_bbox(obstacle_mask, ann_bb, res, pad_mm=1.0)

    distance_mm, backend = compute_distance_field_from_mask(
        obstacle_mask,
        resolution_px_per_mm=res,
        gpu=gpu,
    )

    return SheetDistanceField(
        distance_mm=distance_mm,
        obstacle_mask=obstacle_mask,
        resolution_px_per_mm=res,
        backend=backend,
        width_mm=width_mm,
        height_mm=height_mm,
        geometry_mask=geometry_mask,
        contour_mask=contour_mask,
        frame_mask=frame_mask,
        label_mask=label_mask,
        drawable_bounds=drawable_bounds,
    )
