"""GPU visibility-buffer hidden-line removal (HLR) producing crisp vector output.

The engine replaces OCCT ``HLRBRep_Algo`` with a three-pass ``wgpu`` pipeline:

1. **Raster pass** - the tessellated shape is drawn orthographically into an offscreen
   ``depth32float`` attachment plus two colour targets: an ``r32float`` copy of the
   fragment depth and an ``r32uint`` 1-based B-Rep face ID per pixel.
2. **Compute pass** - every B-Rep edge, analytic silhouette and mesh silhouette is cut
   into ~1 px sample intervals. A WGSL compute shader projects each interval midpoint
   and inspects the 3x3 pixel neighbourhood: the sample is *visible* if any neighbour
   shows background, a surface behind it, or one of the edge's own adjacent faces at
   (nearly) the edge depth; otherwise it is *hidden*.
3. **Vector pass** - the per-sample flags are de-speckled, hidden runs coinciding with
   visible lines (and duplicate visible lines) are removed, contiguous runs are stitched
   into polylines, simplified with Ramer-Douglas-Peucker and emitted with the exact SVG
   DOM structure of ``build123d.ExportSVG`` used by the OCCT path, so every downstream
   consumer (views, breaks, details, PDF/DXF renderers, themes) works unchanged.

Model-space output coordinates match ``build123d.Drawing``: ``(u, v)`` on the view plane
around ``shape.center()``, ``+u`` right and ``+v`` up (SVG flips with ``scale(1,-1)``).
"""

from __future__ import annotations

import math
import time
import weakref
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypedDict

import numpy as np
from build123d.topology import Shape
from numpy.typing import NDArray
from OCP.collections import IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp
from OCP.TopoDS import TopoDS_Shape

from build123d_techdraw.gpu.camera import OrthoCamera, compute_ortho_camera_for_points
from build123d_techdraw.gpu.mesh import (
    SampledEdge,
    SampledEdgeSet,
    TessellatedMesh,
    sample_shape_edges,
    tessellate_shape,
)
from build123d_techdraw.gpu.runtime import get_gpu_device, is_gpu_enabled

if TYPE_CHECKING:
    import wgpu

Vec3 = tuple[float, float, float]
Polyline2D = NDArray[np.float64]

HLRMode = Literal["exact", "gpu", "auto"]
"""Hidden-line removal backend: OCCT ``HLRBRep_Algo``, GPU visibility buffer, or automatic."""

_HLR_MODES: tuple[str, ...] = ("exact", "gpu", "auto")

# Sample flags produced by the classifier and the vector post-process.
_HIDDEN = 0
_VISIBLE = 1
_DROPPED = 2

# Line weights / dash pattern of the OCCT path (``_compute_view_projection``).
_VISIBLE_WEIGHT = 0.35
_HIDDEN_WEIGHT = 0.18
_HIDDEN_DASH = "2.286 1.143"

_WORKGROUP = 64
_MAX_GROUPS_X = 32768
_MAX_TEXTURE_DIM = 8192
_COINCIDENT_MIN_RUN = 8

# Per-device compiled pipeline caches (auto-evicted when device is GC'd).
_RASTER_CACHE: weakref.WeakKeyDictionary[Any, tuple[Any, Any]] = weakref.WeakKeyDictionary()
_CLASSIFY_CACHE: weakref.WeakKeyDictionary[Any, tuple[Any, Any]] = weakref.WeakKeyDictionary()

_RASTER_WGSL = """
struct Camera { view_proj: mat4x4<f32> };
@group(0) @binding(0) var<uniform> cam: Camera;

struct VsOut {
    @builtin(position) pos: vec4<f32>,
    @location(0) @interpolate(flat) fid: u32,
};

@vertex
fn vs_main(@location(0) position: vec3<f32>, @location(1) fid: u32) -> VsOut {
    var out: VsOut;
    out.pos = cam.view_proj * vec4<f32>(position, 1.0);
    out.fid = fid;
    return out;
}

struct FsOut {
    @location(0) packed: vec2<u32>,
};

@fragment
fn fs_main(in: VsOut) -> FsOut {
    var out: FsOut;
    out.packed = vec2<u32>(bitcast<u32>(in.pos.z), in.fid);
    return out;
}
"""

_CLASSIFY_WGSL = """
struct Params {
    view_proj: mat4x4<f32>,
    size: vec2<u32>,
    count: u32,
    row_stride: u32,
    bias: f32,
    own_bias: f32,
    radius: i32,
    pad0: u32,
};

@group(0) @binding(0) var<uniform> params: Params;
@group(0) @binding(1) var vis_tex: texture_2d<u32>;
@group(0) @binding(2) var<storage, read> samples: array<vec4<f32>>;
@group(0) @binding(3) var<storage, read> faces: array<vec2<u32>>;
@group(0) @binding(4) var<storage, read_write> flags: array<u32>;

@compute @workgroup_size(64)
fn main(@builtin(global_invocation_id) gid: vec3<u32>) {
    let i = gid.x + gid.y * params.row_stride;
    if (i >= params.count) {
        return;
    }
    let p = samples[i];
    let clip = params.view_proj * vec4<f32>(p.xyz, 1.0);
    let w = i32(params.size.x);
    let h = i32(params.size.y);
    let px = (clip.x + 1.0) * 0.5 * f32(w);
    let py = (1.0 - clip.y) * 0.5 * f32(h);
    let z = clip.z;
    let cx = i32(floor(px));
    let cy = i32(floor(py));
    let own = faces[i];
    let r = params.radius;

    var visible = false;
    for (var dy = -r; dy <= r; dy = dy + 1) {
        for (var dx = -r; dx <= r; dx = dx + 1) {
            let q = vec2<i32>(clamp(cx + dx, 0, w - 1), clamp(cy + dy, 0, h - 1));
            let texel = textureLoad(vis_tex, q, 0);
            let d = bitcast<f32>(texel.x);
            let f = texel.y;
            if (f == 0u) {
                visible = true;
            } else if ((f == own.x || f == own.y) && d + params.own_bias >= z) {
                visible = true;
            } else if (d + params.bias >= z) {
                visible = true;
            }
        }
    }
    flags[i] = select(0u, 1u, visible);
}
"""


class HLROptions(TypedDict, total=False):
    """Hidden-line removal keyword options accepted by view factories (``**hlr_options``)."""

    hlr: HLRMode
    hlr_resolution: int
    hlr_gpu_face_threshold: int
    gpu: bool | None


@dataclass(frozen=True)
class GPUHLRStats:
    """Problem size and wall-clock timings (seconds) of one GPU HLR projection."""

    num_faces: int
    num_triangles: int
    num_edges: int
    num_samples: int
    num_visible_samples: int
    num_hidden_samples: int
    num_visible_polylines: int
    num_hidden_polylines: int
    raster_width: int
    raster_height: int
    tessellate_s: float
    edges_s: float
    gpu_s: float
    vectorize_s: float
    total_s: float


@dataclass(frozen=True)
class GPUHLRResult:
    """Vector output of :func:`project_shape_gpu_hlr` in ``build123d.Drawing`` coordinates."""

    visible_polylines_2d: list[Polyline2D]
    hidden_polylines_2d: list[Polyline2D]
    inner_svg: str
    vb_min_x: float
    vb_min_y: float
    vb_w: float
    vb_h: float
    exact_w: float
    exact_h: float
    visible_circles: tuple[tuple[float, float, float], ...]
    stats: GPUHLRStats


@dataclass(frozen=True)
class _Samples:
    """Edge sample intervals packed for GPU classification and CPU stitching."""

    mid_3d: NDArray[np.float32]  # (N, 3)
    faces: NDArray[np.uint32]  # (N, 2)
    edge_idx: NDArray[np.int64]  # (N,) index into edge list
    start_2d: NDArray[np.float64]  # (N, 2)
    end_2d: NDArray[np.float64]  # (N, 2)
    pixel: NDArray[np.int64]  # (N,) linear pixel index of the midpoint


def count_shape_faces(shape: Shape[Any] | TopoDS_Shape) -> int:
    """Return the number of distinct B-Rep faces of ``shape``."""
    topo = shape.wrapped if isinstance(shape, Shape) else shape
    if topo is None or topo.IsNull():
        return 0
    face_map = IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapes_s(topo, TopAbs_FACE, face_map)
    return int(face_map.Extent())


def resolve_hlr_backend(
    shape: Shape[Any] | TopoDS_Shape,
    hlr: HLRMode,
    *,
    gpu: bool | None = None,
    face_threshold: int = 250,
) -> bool:
    """Return True if ``shape`` should be projected with GPU HLR rather than OCCT.

    - ``"exact"``: always OCCT ``HLRBRep_Algo``.
    - ``"gpu"``: always GPU; raises if ``gpu=False`` or no wgpu adapter is available
      (``gpu=None`` is treated as an explicit GPU request).
    - ``"auto"``: GPU when :func:`is_gpu_enabled` (``gpu`` / ``BUILD123D_TECHDRAW_GPU``)
      and the shape has at least ``face_threshold`` faces; otherwise OCCT.
    """
    if hlr not in _HLR_MODES:
        raise ValueError(f"Invalid hlr mode {hlr!r}; expected one of {_HLR_MODES}.")
    if hlr == "exact":
        return False
    if hlr == "gpu":
        _require_gpu(gpu)
        return True
    try:
        enabled = is_gpu_enabled(gpu)
    except (ImportError, RuntimeError):
        if gpu is True:
            raise
        enabled = False
    return enabled and count_shape_faces(shape) >= face_threshold


def _require_gpu(gpu: bool | None) -> None:
    """Raise an actionable error unless an explicit GPU request can be honoured."""
    if gpu is False:
        raise ValueError(
            "hlr='gpu' requires the GPU but gpu=False was passed; use hlr='exact' "
            "(OCCT) or hlr='auto' (GPU only when available)."
        )
    is_gpu_enabled(True)


# ---------------------------------------------------------------------------
# Pass 1: depth + face-ID raster
# ---------------------------------------------------------------------------


def _raster_size(
    mesh: TessellatedMesh,
    look_from: Vec3,
    look_up: Vec3,
    resolution: int,
    pad_px: int,
) -> OrthoCamera:
    """Build a camera whose long raster side is ``resolution`` px, aspect-fitted to the view."""
    probe = compute_ortho_camera_for_points(mesh.positions, look_from, look_up, 1, 1)
    uv = probe.project_points_2d(mesh.positions)
    span_u = max(float(np.max(uv[:, 0]) - np.min(uv[:, 0])), 1e-6)
    span_v = max(float(np.max(uv[:, 1]) - np.min(uv[:, 1])), 1e-6)
    usable = max(resolution - 2 * pad_px, 16)
    px_per_mm = usable / max(span_u, span_v)
    width = min(max(int(math.ceil(span_u * px_per_mm)) + 2 * pad_px, 4), _MAX_TEXTURE_DIM)
    height = min(max(int(math.ceil(span_v * px_per_mm)) + 2 * pad_px, 4), _MAX_TEXTURE_DIM)
    return compute_ortho_camera_for_points(
        mesh.positions,
        look_from,
        look_up,
        width,
        height,
        padding_mm=pad_px / px_per_mm,
    )


def _get_raster_pipeline(device: wgpu.GPUDevice) -> tuple[Any, Any]:
    """Return cached ``(bind_group_layout, render_pipeline)`` for ``device``."""
    cached = _RASTER_CACHE.get(device)
    if cached is not None:
        return cached
    import wgpu as wgpu_mod

    bgl = device.create_bind_group_layout(
        entries=[
            {
                "binding": 0,
                "visibility": wgpu_mod.ShaderStage.VERTEX,
                "buffer": {"type": wgpu_mod.BufferBindingType.uniform},
            }
        ]
    )
    shader = device.create_shader_module(code=_RASTER_WGSL)
    pipeline = device.create_render_pipeline(
        layout=device.create_pipeline_layout(bind_group_layouts=[bgl]),
        vertex={
            "module": shader,
            "entry_point": "vs_main",
            "buffers": [
                {
                    "array_stride": 12,
                    "step_mode": "vertex",
                    "attributes": [{"format": "float32x3", "offset": 0, "shader_location": 0}],
                },
                {
                    "array_stride": 4,
                    "step_mode": "vertex",
                    "attributes": [{"format": "uint32", "offset": 0, "shader_location": 1}],
                },
            ],
        },
        primitive={"topology": "triangle-list", "cull_mode": "none"},
        depth_stencil={
            "format": "depth32float",
            "depth_write_enabled": True,
            "depth_compare": "less",
        },
        fragment={
            "module": shader,
            "entry_point": "fs_main",
            "targets": [{"format": "rg32uint"}],
        },
    )
    pair = (bgl, pipeline)
    _RASTER_CACHE[device] = pair
    return pair


def _rasterize(
    device: wgpu.GPUDevice,
    camera: OrthoCamera,
    mesh: TessellatedMesh,
) -> wgpu.GPUTexture:
    """Render ``mesh`` into a single ``rg32uint`` texture ``(depth_bits, face_id)``."""
    import wgpu as wgpu_mod

    size = (camera.width_px, camera.height_px, 1)
    vis_tex = device.create_texture(
        size=size,
        format="rg32uint",
        usage=wgpu_mod.TextureUsage.RENDER_ATTACHMENT | wgpu_mod.TextureUsage.TEXTURE_BINDING,
    )
    depth_stencil = device.create_texture(
        size=size,
        format="depth32float",
        usage=wgpu_mod.TextureUsage.RENDER_ATTACHMENT,
    )

    vert_fid = np.zeros(mesh.num_vertices, dtype=np.uint32)
    for k in range(3):
        vert_fid[mesh.indices[:, k]] = mesh.face_ids
    vbuf = device.create_buffer_with_data(
        data=np.ascontiguousarray(mesh.positions, dtype=np.float32),
        usage=wgpu_mod.BufferUsage.VERTEX,
    )
    fbuf = device.create_buffer_with_data(data=vert_fid, usage=wgpu_mod.BufferUsage.VERTEX)
    ibuf = device.create_buffer_with_data(
        data=np.ascontiguousarray(mesh.indices.reshape(-1), dtype=np.uint32),
        usage=wgpu_mod.BufferUsage.INDEX,
    )
    ubuf = device.create_buffer_with_data(
        data=camera.uniform_view_proj(), usage=wgpu_mod.BufferUsage.UNIFORM
    )

    bgl, pipeline = _get_raster_pipeline(device)
    bind_group = device.create_bind_group(
        layout=bgl,
        entries=[{"binding": 0, "resource": {"buffer": ubuf, "offset": 0, "size": ubuf.size}}],
    )

    encoder = device.create_command_encoder()
    rpass = encoder.begin_render_pass(
        color_attachments=[
            {
                "view": vis_tex.create_view(),
                "clear_value": (0x3F800000, 0, 0, 0),
                "load_op": "clear",
                "store_op": "store",
            },
        ],
        depth_stencil_attachment={
            "view": depth_stencil.create_view(),
            "depth_clear_value": 1.0,
            "depth_load_op": "clear",
            "depth_store_op": "store",
        },
    )
    rpass.set_pipeline(pipeline)
    rpass.set_bind_group(0, bind_group)
    rpass.set_vertex_buffer(0, vbuf)
    rpass.set_vertex_buffer(1, fbuf)
    rpass.set_index_buffer(ibuf, "uint32")
    rpass.draw_indexed(int(mesh.indices.size))
    rpass.end()
    device.queue.submit([encoder.finish()])
    return vis_tex


# ---------------------------------------------------------------------------
# Pass 2: compute-shader edge visibility classification
# ---------------------------------------------------------------------------


def _get_classify_pipeline(device: wgpu.GPUDevice) -> tuple[Any, Any]:
    """Return cached ``(bind_group_layout, compute_pipeline)`` for ``device``."""
    cached = _CLASSIFY_CACHE.get(device)
    if cached is not None:
        return cached
    import wgpu as wgpu_mod

    vis = wgpu_mod.ShaderStage.COMPUTE
    bgl = device.create_bind_group_layout(
        entries=[
            {"binding": 0, "visibility": vis, "buffer": {"type": "uniform"}},
            {
                "binding": 1,
                "visibility": vis,
                "texture": {"sample_type": "uint", "view_dimension": "2d"},
            },
            {"binding": 2, "visibility": vis, "buffer": {"type": "read-only-storage"}},
            {"binding": 3, "visibility": vis, "buffer": {"type": "read-only-storage"}},
            {"binding": 4, "visibility": vis, "buffer": {"type": "storage"}},
        ]
    )
    pipeline = device.create_compute_pipeline(
        layout=device.create_pipeline_layout(bind_group_layouts=[bgl]),
        compute={
            "module": device.create_shader_module(code=_CLASSIFY_WGSL),
            "entry_point": "main",
        },
    )
    pair = (bgl, pipeline)
    _CLASSIFY_CACHE[device] = pair
    return pair


def _classify(
    device: wgpu.GPUDevice,
    camera: OrthoCamera,
    vis_tex: wgpu.GPUTexture,
    samples: _Samples,
    *,
    bias: float,
    own_bias: float,
) -> NDArray[np.uint32]:
    """Classify every sample midpoint as visible (1) or hidden (0) on the GPU."""
    import wgpu as wgpu_mod

    count = int(samples.mid_3d.shape[0])
    groups = (count + _WORKGROUP - 1) // _WORKGROUP
    groups_x = min(groups, _MAX_GROUPS_X)
    groups_y = (groups + groups_x - 1) // groups_x
    row_stride = groups_x * _WORKGROUP

    params = (
        camera.uniform_view_proj().tobytes()
        + np.array([camera.width_px, camera.height_px, count, row_stride], np.uint32).tobytes()
        + np.array([bias, own_bias], np.float32).tobytes()
        + np.array([1, 0], np.int32).tobytes()
    )
    points = np.zeros((count, 4), dtype=np.float32)
    points[:, :3] = samples.mid_3d

    storage = wgpu_mod.BufferUsage.STORAGE
    pbuf = device.create_buffer_with_data(data=params, usage=wgpu_mod.BufferUsage.UNIFORM)
    sbuf = device.create_buffer_with_data(data=points, usage=storage)
    fbuf = device.create_buffer_with_data(
        data=np.ascontiguousarray(samples.faces, dtype=np.uint32), usage=storage
    )
    obuf = device.create_buffer(size=4 * count, usage=storage | wgpu_mod.BufferUsage.COPY_SRC)

    bgl, pipeline = _get_classify_pipeline(device)

    def _buf(binding: int, buf: wgpu.GPUBuffer) -> dict[str, Any]:
        return {"binding": binding, "resource": {"buffer": buf, "offset": 0, "size": buf.size}}

    bind_group = device.create_bind_group(
        layout=bgl,
        entries=[
            _buf(0, pbuf),
            {"binding": 1, "resource": vis_tex.create_view()},
            _buf(2, sbuf),
            _buf(3, fbuf),
            _buf(4, obuf),
        ],
    )
    encoder = device.create_command_encoder()
    cpass = encoder.begin_compute_pass()
    cpass.set_pipeline(pipeline)
    cpass.set_bind_group(0, bind_group)
    cpass.dispatch_workgroups(groups_x, groups_y, 1)
    cpass.end()
    device.queue.submit([encoder.finish()])
    raw = device.queue.read_buffer(obuf)
    return np.frombuffer(raw, dtype=np.uint32, count=count).copy()


# ---------------------------------------------------------------------------
# Edge extraction and sampling
# ---------------------------------------------------------------------------


def _chain_segments(pairs: NDArray[np.int64]) -> list[list[int]]:
    """Chain undirected vertex-index segments into maximal polylines."""
    adjacency: dict[int, list[int]] = {}
    for a, b in pairs.tolist():
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)
    used: set[tuple[int, int]] = set()

    def walk(start: int, nxt: int) -> list[int]:
        chain = [start]
        prev, cur = start, nxt
        used.add((min(prev, cur), max(prev, cur)))
        while True:
            chain.append(cur)
            cands = [
                n for n in adjacency[cur] if (min(cur, n), max(cur, n)) not in used and n != prev
            ]
            if len(adjacency[cur]) != 2 or not cands:
                return chain
            prev, cur = cur, cands[0]
            used.add((min(prev, cur), max(prev, cur)))

    chains: list[list[int]] = []
    # Open chains first (start at endpoints / junctions), then closed loops.
    for vertex, nbrs in adjacency.items():
        if len(nbrs) != 2:
            for n in nbrs:
                if (min(vertex, n), max(vertex, n)) not in used:
                    chains.append(walk(vertex, n))
    for vertex, nbrs in adjacency.items():
        for n in nbrs:
            if (min(vertex, n), max(vertex, n)) not in used:
                chains.append(walk(vertex, n))
    return chains


def _mesh_silhouettes(
    mesh: TessellatedMesh,
    eye_dir: NDArray[np.float64],
    skip_faces: set[int],
    first_edge_id: int,
) -> list[SampledEdge]:
    """Extract triangle-mesh silhouette polylines on faces without analytic silhouettes.

    A mesh edge is a silhouette when it is shared by a front-facing and a back-facing
    triangle of the same B-Rep face (vertices are never shared across faces), which
    gives crisp outer contours for NURBS / fillet surfaces OCCT has no closed form for.
    """
    if mesh.num_triangles == 0:
        return []
    pos = mesh.positions.astype(np.float64)
    tri = mesh.indices.astype(np.int64)
    normals = np.cross(pos[tri[:, 1]] - pos[tri[:, 0]], pos[tri[:, 2]] - pos[tri[:, 0]])
    lengths = np.linalg.norm(normals, axis=1)
    facing = (normals @ eye_dir) / np.maximum(lengths, 1e-30)
    side = np.where(facing > 1e-4, 1, np.where(facing < -1e-4, -1, 0))

    edges = np.concatenate([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]], axis=0)
    edges.sort(axis=1)
    owner = np.tile(np.arange(tri.shape[0]), 3)
    key = edges[:, 0] * (mesh.num_vertices + 1) + edges[:, 1]
    order = np.argsort(key, kind="stable")
    key_s = key[order]
    pair = np.flatnonzero(key_s[1:] == key_s[:-1])
    t_a = owner[order[pair]]
    t_b = owner[order[pair + 1]]
    sil = side[t_a] * side[t_b] < 0
    if skip_faces:
        skip = np.isin(mesh.face_ids[t_a], np.fromiter(skip_faces, dtype=np.uint32))
        sil &= ~skip
    if not np.any(sil):
        return []

    seg_pairs = edges[order[pair[sil]]]
    seg_faces = mesh.face_ids[t_a[sil]]
    out: list[SampledEdge] = []
    next_id = first_edge_id
    for fid in np.unique(seg_faces).tolist():
        for chain in _chain_segments(seg_pairs[seg_faces == fid]):
            if len(chain) < 2:
                continue
            out.append(
                SampledEdge(
                    edge_id=next_id,
                    face_id_a=int(fid),
                    face_id_b=0,
                    points_3d=np.ascontiguousarray(mesh.positions[chain], dtype=np.float32),
                    is_silhouette_candidate=True,
                    geom_type="mesh_silhouette",
                )
            )
            next_id += 1
    return out


def _drawable_edges(edge_set: SampledEdgeSet) -> list[SampledEdge]:
    """Drop periodic seam edges, which OCCT HLR never draws."""
    return [e for e in edge_set.edges if not (e.is_smooth and e.face_id_b == 0)]


def _build_samples(
    edges: list[SampledEdge],
    camera: OrthoCamera,
    origin: Vec3,
    step_px: float,
) -> _Samples:
    """Split every edge polyline into ~``step_px`` sample intervals."""
    edge_set = SampledEdgeSet(edges=edges, bbox_min=origin, bbox_max=origin, num_faces=0)
    starts, ends, meta = edge_set.pack_segments()
    edge_of_seg = np.repeat(
        np.arange(len(edges), dtype=np.int64),
        [max(0, int(e.points_3d.shape[0]) - 1) for e in edges],
    )
    px0 = camera.project_points_px(starts).astype(np.float64)
    px1 = camera.project_points_px(ends).astype(np.float64)
    seg_px = np.hypot(px1[:, 0] - px0[:, 0], px1[:, 1] - px0[:, 1])
    n_sub = np.maximum(1, np.ceil(seg_px / step_px)).astype(np.int64)

    seg_idx = np.repeat(np.arange(starts.shape[0], dtype=np.int64), n_sub)
    first = np.cumsum(n_sub) - n_sub
    j = np.arange(seg_idx.shape[0], dtype=np.int64) - np.repeat(first, n_sub)
    n_rep = n_sub[seg_idx].astype(np.float64)
    t0 = (j / n_rep)[:, None]
    t1 = ((j + 1) / n_rep)[:, None]
    tm = ((j + 0.5) / n_rep)[:, None]

    s3 = starts.astype(np.float64)[seg_idx]
    d3 = ends.astype(np.float64)[seg_idx] - s3
    s2 = camera.project_points_2d(starts, origin=origin)[seg_idx]
    d2 = camera.project_points_2d(ends, origin=origin)[seg_idx] - s2
    mid_px = px0[seg_idx, :2] + (px1[seg_idx, :2] - px0[seg_idx, :2]) * tm
    ix = np.clip(np.floor(mid_px[:, 0]).astype(np.int64), 0, camera.width_px - 1)
    iy = np.clip(np.floor(mid_px[:, 1]).astype(np.int64), 0, camera.height_px - 1)

    return _Samples(
        mid_3d=np.ascontiguousarray(s3 + d3 * tm, dtype=np.float32),
        faces=np.ascontiguousarray(meta[seg_idx, 1:3], dtype=np.uint32),
        edge_idx=edge_of_seg[seg_idx],
        start_2d=s2 + d2 * t0,
        end_2d=s2 + d2 * t1,
        pixel=iy * camera.width_px + ix,
    )


# ---------------------------------------------------------------------------
# Pass 3: vector post-processing
# ---------------------------------------------------------------------------


def _despeckle(flags: NDArray[np.uint32], edge_idx: NDArray[np.int64]) -> NDArray[np.uint32]:
    """Majority-of-3 filter along each edge; edge end samples follow their neighbour."""
    n = flags.shape[0]
    if n < 3:
        return flags
    prev = np.empty_like(flags)
    nxt = np.empty_like(flags)
    prev[1:] = flags[:-1]
    nxt[:-1] = flags[1:]
    head = np.ones(n, dtype=np.bool_)
    tail = np.ones(n, dtype=np.bool_)
    head[1:] = edge_idx[1:] != edge_idx[:-1]
    tail[:-1] = edge_idx[1:] != edge_idx[:-1]
    prev[0] = flags[0]
    nxt[-1] = flags[-1]
    lonely = head & tail
    prev = np.where(head & ~lonely, nxt, prev)
    nxt = np.where(tail & ~lonely, prev, nxt)
    # Edges with only two samples keep their raw flags.
    pair = head & np.roll(tail, -1)
    pair |= tail & np.roll(head, 1)
    votes = flags.astype(np.int64) + prev.astype(np.int64) + nxt.astype(np.int64)
    out = np.where(votes >= 2, _VISIBLE, _HIDDEN).astype(np.uint32)
    return np.where(pair | lonely, flags, out).astype(np.uint32)


def _long_runs(
    mask: NDArray[np.bool_], edge_idx: NDArray[np.int64], min_run: int
) -> NDArray[np.bool_]:
    """Return ``mask`` restricted to runs of ``>= min_run`` consecutive samples per edge."""
    n = mask.shape[0]
    if n == 0 or not np.any(mask):
        return np.zeros(n, dtype=np.bool_)
    brk = np.ones(n, dtype=np.bool_)
    brk[1:] = (mask[1:] != mask[:-1]) | (edge_idx[1:] != edge_idx[:-1])
    run_id = np.cumsum(brk) - 1
    run_len = np.bincount(run_id)
    return mask & (run_len[run_id] >= min_run)


def _min_filter_3x3(grid: NDArray[np.int32]) -> NDArray[np.int32]:
    """Separable 3x3 minimum filter with edge replication."""
    tmp = grid.copy()
    np.minimum(tmp[:, 1:], grid[:, :-1], out=tmp[:, 1:])
    np.minimum(tmp[:, :-1], grid[:, 1:], out=tmp[:, :-1])
    out = tmp.copy()
    np.minimum(out[1:, :], tmp[:-1, :], out=out[1:, :])
    np.minimum(out[:-1, :], tmp[1:, :], out=out[:-1, :])
    return out


def _remove_coincident(
    flags: NDArray[np.uint32],
    samples: _Samples,
    camera: OrthoCamera,
) -> NDArray[np.uint32]:
    """Drop hidden runs lying on visible lines and duplicate coincident visible runs."""
    visible = flags == _VISIBLE
    if not np.any(visible):
        return flags
    sentinel = np.iinfo(np.int32).max
    grid = np.full(camera.width_px * camera.height_px, sentinel, dtype=np.int32)
    edge_i32 = samples.edge_idx.astype(np.int32)
    np.minimum.at(grid, samples.pixel[visible], edge_i32[visible])
    owner = _min_filter_3x3(grid.reshape(camera.height_px, camera.width_px)).reshape(-1)
    near = owner[samples.pixel]
    covered = np.where(visible, near < edge_i32, near != sentinel)
    drop = _long_runs(covered, samples.edge_idx, _COINCIDENT_MIN_RUN)
    return np.where(drop, _DROPPED, flags).astype(np.uint32)


def _rdp(points: Polyline2D, tol: float) -> Polyline2D:
    """Ramer-Douglas-Peucker polyline simplification using exact point-to-segment distance."""
    n = points.shape[0]
    if n <= 2:
        return points
    keep = np.zeros(n, dtype=np.bool_)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i0, i1 = stack.pop()
        if i1 - i0 < 2:
            continue
        a = points[i0]
        ab = points[i1] - a
        rel = points[i0 + 1 : i1] - a
        len2 = float(ab[0] * ab[0] + ab[1] * ab[1])
        if len2 < 1e-24:
            dist = np.hypot(rel[:, 0], rel[:, 1])
        else:
            # Clamp projection to [0, 1] so collinear fold-backs (edge-on circles) are preserved.
            t = np.clip((rel[:, 0] * ab[0] + rel[:, 1] * ab[1]) / len2, 0.0, 1.0)
            dist = np.hypot(rel[:, 0] - t * ab[0], rel[:, 1] - t * ab[1])
        k = int(np.argmax(dist))
        if float(dist[k]) > tol:
            mid = i0 + 1 + k
            keep[mid] = True
            stack.append((i0, mid))
            stack.append((mid, i1))
    return points[keep]


def _stitch(
    flags: NDArray[np.uint32],
    samples: _Samples,
    edges: list[SampledEdge],
    tol: float,
) -> tuple[list[Polyline2D], list[Polyline2D]]:
    """Stitch contiguous same-flag samples of each edge into simplified 2D polylines."""
    n = flags.shape[0]
    visible: list[Polyline2D] = []
    hidden: list[Polyline2D] = []
    if n == 0:
        return visible, hidden
    brk = np.ones(n, dtype=np.bool_)
    brk[1:] = (flags[1:] != flags[:-1]) | (samples.edge_idx[1:] != samples.edge_idx[:-1])
    run_start = np.flatnonzero(brk)
    run_end = np.append(run_start[1:], n) - 1
    for a, b in zip(run_start.tolist(), run_end.tolist(), strict=True):
        flag = int(flags[a])
        if flag == _DROPPED:
            continue
        edge = edges[int(samples.edge_idx[a])]
        if edge.points_3d.shape[0] == 2:
            pts = np.vstack([samples.start_2d[a], samples.end_2d[b]])
        else:
            pts = _rdp(np.vstack([samples.start_2d[a : b + 1], samples.end_2d[b]]), tol)
        if float(np.max(np.ptp(pts, axis=0))) < 1e-9:
            continue
        (visible if flag == _VISIBLE else hidden).append(pts)
    return visible, hidden


def _fit_visible_circles(
    edges: list[SampledEdge],
    flags: NDArray[np.uint32],
    samples: _Samples,
    camera: OrthoCamera,
    origin: Vec3,
) -> tuple[tuple[float, float, float], ...]:
    """Return ``(cx, cy, r)`` of visible circular edges seen face-on (for centre lines)."""
    shown = np.zeros(len(edges), dtype=np.bool_)
    shown[samples.edge_idx[flags == _VISIBLE]] = True
    circles: list[tuple[float, float, float]] = []
    for idx, edge in enumerate(edges):
        if edge.geom_type != "circle" or not shown[idx] or edge.points_3d.shape[0] < 4:
            continue
        pts = camera.project_points_2d(edge.points_3d, origin=origin)
        n = pts.shape[0]
        p1, p2, p3 = pts[0], pts[n // 3], pts[(2 * n) // 3]
        det = 2.0 * (p1[0] * (p2[1] - p3[1]) + p2[0] * (p3[1] - p1[1]) + p3[0] * (p1[1] - p2[1]))
        if abs(det) < 1e-12:
            continue
        s1, s2, s3 = float(p1 @ p1), float(p2 @ p2), float(p3 @ p3)
        cx = (s1 * (p2[1] - p3[1]) + s2 * (p3[1] - p1[1]) + s3 * (p1[1] - p2[1])) / det
        cy = (s1 * (p3[0] - p2[0]) + s2 * (p1[0] - p3[0]) + s3 * (p2[0] - p1[0])) / det
        radii = np.hypot(pts[:, 0] - cx, pts[:, 1] - cy)
        radius = float(np.mean(radii))
        if radius > 1e-9 and float(np.ptp(radii)) <= max(1e-3, 0.01 * radius):
            circles.append((float(cx), float(cy), radius))
    return tuple(circles)


def _fmt(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".") if value != 0.0 else "0.0"


def _polyline_svg(pts: Polyline2D) -> str:
    if pts.shape[0] == 2:
        (x1, y1), (x2, y2) = pts.tolist()
        return f'<line x1="{_fmt(x1)}" y1="{_fmt(y1)}" x2="{_fmt(x2)}" y2="{_fmt(y2)}" />'
    coords = [f"{_fmt(x)},{_fmt(y)}" for x, y in pts.tolist()]
    return f'<path d="M {coords[0]} L {" L ".join(coords[1:])}" />'


def _emit_svg(visible: list[Polyline2D], hidden: list[Polyline2D]) -> str:
    """Emit the ``build123d.ExportSVG`` DOM used by the OCCT projection path."""
    lines = ['<g transform="scale(1,-1)" stroke-linecap="round">']
    if hidden:
        lines.append(
            f'    <g fill="none" stroke="var(--line-hidden)" stroke-width="{_HIDDEN_WEIGHT}" '
            f'id="layer_hidden" class="line-hidden" stroke-dasharray="{_HIDDEN_DASH}">'
        )
        lines.extend(f"      {_polyline_svg(p)}" for p in hidden)
        lines.append("    </g>")
    lines.append(
        f'    <g fill="none" stroke="var(--line-visible)" stroke-width="{_VISIBLE_WEIGHT}" '
        'id="layer_visible" class="line-visible">'
    )
    lines.extend(f"      {_polyline_svg(p)}" for p in visible)
    lines.append("    </g>")
    lines.append("  </g>")
    return "\n".join(lines)


def _default_deflection(diag: float) -> float:
    """Tessellation deflection scaled to the model size (coarser for large assemblies)."""
    return min(max(diag * 4e-4, 0.005), 1.0)


def project_shape_gpu_hlr(
    shape: Any,
    look_from: Vec3,
    look_up: Vec3,
    *,
    show_hidden: bool = True,
    resolution: int = 2048,
    deflection: float | None = None,
    gpu: bool | None = None,
) -> GPUHLRResult:
    """Project ``shape`` with GPU visibility-buffer hidden-line removal.

    Args:
        shape: build123d shape (``Part``, ``Solid``, ``Compound`` assembly, ...).
        look_from: Direction from the model toward the camera (``VIEW_VECTORS``).
        look_up: Up direction of the view.
        show_hidden: Emit hidden lines (dashed ``layer_hidden``) when True.
        resolution: Long side, in pixels, of the depth / face-ID visibility buffer.
            Visibility transitions are located to about one pixel of this raster.
        deflection: Tessellation / edge discretisation tolerance in model units;
            defaults to ``4e-4`` of the bounding-box diagonal.
        gpu: ``False`` raises; ``None`` / ``True`` require a wgpu adapter.

    Raises:
        ValueError: If ``gpu=False`` or ``resolution`` is too small.
        ImportError: If ``wgpu`` is not installed.
        RuntimeError: If no wgpu adapter can be initialised.
    """
    if resolution < 64:
        raise ValueError(f"resolution must be >= 64 px, got {resolution}.")
    _require_gpu(gpu)
    _adapter, device = get_gpu_device()

    t_start = time.perf_counter()
    bbox = shape.bounding_box()
    diag = max(float(bbox.diagonal), 1e-6)
    defl = _default_deflection(diag) if deflection is None else float(deflection)
    if defl <= 0.0:
        raise ValueError(f"deflection must be positive, got {defl}.")
    center = shape.center()
    origin: Vec3 = (float(center.X), float(center.Y), float(center.Z))

    mesh = tessellate_shape(
        shape, linear_deflection=defl, angular_deflection=0.25, split_sharp_edges_deg=0.0
    )
    t_mesh = time.perf_counter()

    edge_set = sample_shape_edges(shape, deflection=0.5 * defl, look_from=look_from)
    analytic = {e.face_id_a for e in edge_set.edges if e.geom_type == "silhouette"}
    lf = np.asarray(look_from, dtype=np.float64)
    edges = _drawable_edges(edge_set)
    edges += _mesh_silhouettes(
        mesh, lf / float(np.linalg.norm(lf)), analytic, len(edge_set.edges) + 1
    )
    t_edges = time.perf_counter()

    empty = mesh.num_triangles == 0 or not edges
    if empty:
        return _assemble([], [], (), mesh, edge_set, 0, 0, 0, 0, (t_start, t_mesh, t_edges))

    pad_px = max(4, int(math.ceil(2.0 * defl * resolution / diag)) + 2)
    camera = _raster_size(mesh, look_from, look_up, resolution, pad_px)
    px_mm = 1.0 / camera.pixels_per_mm
    depth_range = camera.z_far - camera.z_near
    samples = _build_samples(edges, camera, origin, step_px=1.0)

    textures = _rasterize(device, camera, mesh)
    flags = _classify(
        device,
        camera,
        textures,
        samples,
        bias=max(2.0 * defl, 1.5 * px_mm) / depth_range,
        own_bias=max(4.0 * defl, 4.0 * px_mm) / depth_range,
    )
    t_gpu = time.perf_counter()

    n_visible_raw = int(np.count_nonzero(flags == _VISIBLE))
    flags = _despeckle(flags, samples.edge_idx)
    hidden_smooth = np.fromiter(
        (e.is_smooth or not show_hidden for e in edges), dtype=np.bool_, count=len(edges)
    )
    flags = np.where((flags == _HIDDEN) & hidden_smooth[samples.edge_idx], _DROPPED, flags).astype(
        np.uint32
    )
    flags = _remove_coincident(flags, samples, camera)
    tol = min(0.02, max(1e-3, 1e-4 * diag))
    visible, hidden = _stitch(flags, samples, edges, tol)
    circles = _fit_visible_circles(edges, flags, samples, camera, origin)

    return _assemble(
        visible,
        hidden,
        circles,
        mesh,
        edge_set,
        len(edges),
        int(flags.shape[0]),
        n_visible_raw,
        camera.width_px,
        (t_start, t_mesh, t_edges, t_gpu),
        raster_height=camera.height_px,
    )


def _assemble(
    visible: list[Polyline2D],
    hidden: list[Polyline2D],
    circles: tuple[tuple[float, float, float], ...],
    mesh: TessellatedMesh,
    edge_set: SampledEdgeSet,
    num_edges: int,
    num_samples: int,
    num_visible_samples: int,
    raster_width: int,
    times: tuple[float, ...],
    *,
    raster_height: int = 0,
) -> GPUHLRResult:
    """Build the :class:`GPUHLRResult` (SVG, viewBox, exact bounds, stats)."""
    pts = [*visible, *hidden]
    if pts:
        stacked = np.vstack(pts)
        lo = stacked.min(axis=0)
        hi = stacked.max(axis=0)
        min_u, min_v = float(lo[0]), float(lo[1])
        exact_w, exact_h = float(hi[0] - lo[0]), float(hi[1] - lo[1])
        max_v = float(hi[1])
    else:
        min_u = min_v = max_v = 0.0
        exact_w = exact_h = 0.0
    del min_v
    pad = 0.5 * _VISIBLE_WEIGHT
    t_end = time.perf_counter()
    t_start, t_mesh, t_edges = times[0], times[1], times[2]
    t_gpu = times[3] if len(times) > 3 else t_edges
    stats = GPUHLRStats(
        num_faces=edge_set.num_faces,
        num_triangles=mesh.num_triangles,
        num_edges=num_edges,
        num_samples=num_samples,
        num_visible_samples=num_visible_samples,
        num_hidden_samples=num_samples - num_visible_samples,
        num_visible_polylines=len(visible),
        num_hidden_polylines=len(hidden),
        raster_width=raster_width,
        raster_height=raster_height,
        tessellate_s=t_mesh - t_start,
        edges_s=t_edges - t_mesh,
        gpu_s=t_gpu - t_edges,
        vectorize_s=t_end - t_gpu,
        total_s=t_end - t_start,
    )
    return GPUHLRResult(
        visible_polylines_2d=visible,
        hidden_polylines_2d=hidden,
        inner_svg=_emit_svg(visible, hidden),
        vb_min_x=min_u - pad,
        vb_min_y=-max_v - pad,
        vb_w=exact_w + 2.0 * pad,
        vb_h=exact_h + 2.0 * pad,
        exact_w=exact_w,
        exact_h=exact_h,
        visible_circles=circles,
        stats=stats,
    )
