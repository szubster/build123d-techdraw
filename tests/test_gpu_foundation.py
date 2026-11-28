"""Tests for the opt-in wgpu foundation, OCCT mesh/camera bridge, and CI raster diff harness."""

from __future__ import annotations

import base64
import io
import sys
from pathlib import Path
from typing import Any

import build123d as bd
import numpy as np
import pytest
from build123d.exporters import Drawing
from PIL import Image

import build123d_techdraw as td
from build123d_techdraw.core.view import VIEW_VECTORS, ViewType
from build123d_techdraw.gpu import (
    GPU_ENV_VAR,
    OrthoCamera,
    SampledEdgeSet,
    TessellatedMesh,
    compute_ortho_camera,
    compute_ortho_camera_for_points,
    get_gpu_device,
    gpu_info,
    is_gpu_available,
    is_gpu_enabled,
    is_gpu_installed,
    reset_gpu_cache,
    sample_shape_edges,
    tessellate_shape,
)
from tests.raster_diff import (
    assert_images_match,
    compare_images,
    extract_embedded_pngs_from_svg,
)


@pytest.fixture(autouse=True)
def _clean_gpu_runtime(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Keep GPU cache and env var isolated between tests."""
    reset_gpu_cache()
    monkeypatch.delenv(GPU_ENV_VAR, raising=False)
    yield
    reset_gpu_cache()


# ---------------------------------------------------------------------------
# 1. Opt-in runtime detection & environment controls
# ---------------------------------------------------------------------------


def test_top_level_gpu_exports() -> None:
    assert td.is_gpu_available is is_gpu_available
    assert td.is_gpu_enabled is is_gpu_enabled
    assert td.gpu_info is gpu_info


def test_gpu_explicit_false_never_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail_probe(**_kwargs: Any) -> Any:
        raise AssertionError("Adapter probe must not run when gpu=False")

    monkeypatch.setattr("build123d_techdraw.gpu.runtime._probe_device", _fail_probe)
    assert is_gpu_enabled(False) is False


@pytest.mark.parametrize("env_val", ["0", "false", "FALSE", "off", "no", "  0  "])
def test_gpu_env_var_disables(monkeypatch: pytest.MonkeyPatch, env_val: str) -> None:
    monkeypatch.setenv(GPU_ENV_VAR, env_val)
    assert is_gpu_enabled() is False
    assert is_gpu_enabled(None) is False


def test_gpu_env_var_invalid_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GPU_ENV_VAR, "sometimes")
    with pytest.raises(ValueError, match=GPU_ENV_VAR):
        is_gpu_enabled()


def test_missing_wgpu_graceful_and_actionable_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "wgpu", None)
    reset_gpu_cache()

    assert is_gpu_installed() is False
    assert is_gpu_available() is False
    assert is_gpu_enabled(None) is False
    assert is_gpu_enabled(False) is False

    with pytest.raises(ImportError, match=r"pip install build123d-techdraw\[gpu\]"):
        is_gpu_enabled(True)

    with pytest.raises(ImportError, match=r"pip install build123d-techdraw\[gpu\]"):
        get_gpu_device()

    monkeypatch.setenv(GPU_ENV_VAR, "1")
    with pytest.raises(ImportError, match=r"pip install build123d-techdraw\[gpu\]"):
        is_gpu_enabled()

    info = gpu_info()
    assert info["installed"] is False
    assert info["available"] is False
    assert info["enabled"] is False
    assert info["backend"] is None
    assert info["adapter_name"] is None


def test_no_adapter_available_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("build123d_techdraw.gpu.runtime.is_gpu_installed", lambda: True)
    monkeypatch.setattr(
        "build123d_techdraw.gpu.runtime._probe_device",
        lambda **_kwargs: None,
    )

    assert is_gpu_available() is False
    assert is_gpu_enabled(None) is False

    with pytest.raises(RuntimeError, match="no compatible"):
        is_gpu_enabled(True)

    monkeypatch.setenv(GPU_ENV_VAR, "true")
    with pytest.raises(RuntimeError, match="no compatible"):
        is_gpu_enabled()

    with pytest.raises(RuntimeError, match="No compatible"):
        get_gpu_device()


def test_runtime_allow_software_fallback_logic(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeAdapter:
        def __init__(self, info: dict[str, Any]) -> None:
            self.info = info

        def request_device_sync(self) -> str:
            return f"device:{self.info['device']}"

    sw_adapter = _FakeAdapter(
        {
            "adapter_type": "CPU",
            "backend_type": "Vulkan",
            "device": "llvmpipe (LLVM)",
            "vendor": "llvmpipe",
            "description": "Mesa",
        }
    )

    class _FakeGPU:
        def request_adapter_sync(
            self,
            *,
            power_preference: str | None = None,
            force_fallback_adapter: bool = False,
        ) -> _FakeAdapter | None:
            _ = (power_preference, force_fallback_adapter)
            return sw_adapter

        def enumerate_adapters_sync(self) -> list[_FakeAdapter]:
            return [sw_adapter]

    fake_mod: Any = type("_FakeWGPU", (), {"__version__": "0.99.0-test", "gpu": _FakeGPU()})()
    monkeypatch.setitem(sys.modules, "wgpu", fake_mod)
    reset_gpu_cache()

    assert is_gpu_available(allow_software=False) is False
    with pytest.raises(RuntimeError, match="No compatible hardware"):
        get_gpu_device(allow_software=False)

    assert is_gpu_available(allow_software=True) is True
    adapter, device = get_gpu_device(allow_software=True)
    assert adapter is sw_adapter
    assert device == "device:llvmpipe (LLVM)"

    info = gpu_info(allow_software=True)
    assert info["is_software"] is True
    assert info["backend"] == "Vulkan"
    assert info["device_type"] == "CPU"
    assert info["wgpu_version"] == "0.99.0-test"


# ---------------------------------------------------------------------------
# 2. Actual wgpu compute & offscreen render smoke tests (default + Lavapipe)
# ---------------------------------------------------------------------------


def _run_compute_smoke(device: Any) -> np.ndarray[Any, np.dtype[np.float32]]:
    import wgpu

    shader = device.create_shader_module(
        code="""
        @group(0) @binding(0) var<storage, read_write> values: array<f32>;

        @compute @workgroup_size(4)
        fn main(@builtin(global_invocation_id) gid: vec3<u32>) {
            values[gid.x] = values[gid.x] * 3.0 + 2.0;
        }
        """
    )
    src = np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float32)
    buf = device.create_buffer_with_data(
        data=src,
        usage=wgpu.BufferUsage.STORAGE | wgpu.BufferUsage.COPY_SRC,
    )
    pipeline = device.create_compute_pipeline(
        layout="auto",
        compute={"module": shader, "entry_point": "main"},
    )
    bind_group = device.create_bind_group(
        layout=pipeline.get_bind_group_layout(0),
        entries=[{"binding": 0, "resource": {"buffer": buf}}],
    )
    encoder = device.create_command_encoder()
    cpass = encoder.begin_compute_pass()
    cpass.set_pipeline(pipeline)
    cpass.set_bind_group(0, bind_group)
    cpass.dispatch_workgroups(1)
    cpass.end()
    device.queue.submit([encoder.finish()])
    raw = device.queue.read_buffer(buf)
    return np.frombuffer(raw, dtype=np.float32).copy()


def _render_shaded_mesh_smoke(
    device: Any,
    mesh: TessellatedMesh,
    camera: OrthoCamera,
) -> Image.Image:
    import wgpu

    w, h = camera.width_px, camera.height_px
    color_tex = device.create_texture(
        size=(w, h, 1),
        format=wgpu.TextureFormat.rgba8unorm,
        usage=wgpu.TextureUsage.RENDER_ATTACHMENT | wgpu.TextureUsage.COPY_SRC,
    )
    depth_tex = device.create_texture(
        size=(w, h, 1),
        format=wgpu.TextureFormat.depth24plus,
        usage=wgpu.TextureUsage.RENDER_ATTACHMENT,
    )

    # Interleave position (vec3) + normal (vec3) per vertex.
    verts = np.ascontiguousarray(np.hstack([mesh.positions, mesh.normals]), dtype=np.float32)
    vbuf = device.create_buffer_with_data(data=verts, usage=wgpu.BufferUsage.VERTEX)
    ibuf = device.create_buffer_with_data(
        data=np.ascontiguousarray(mesh.indices.reshape(-1), dtype=np.uint32),
        usage=wgpu.BufferUsage.INDEX,
    )
    ubuf = device.create_buffer_with_data(
        data=camera.uniform_view_proj(),
        usage=wgpu.BufferUsage.UNIFORM,
    )

    shader = device.create_shader_module(
        code="""
        struct Uniforms {
            view_proj: mat4x4<f32>,
        };
        @group(0) @binding(0) var<uniform> u: Uniforms;

        struct VsOut {
            @builtin(position) pos: vec4<f32>,
            @location(0) normal: vec3<f32>,
        };

        @vertex
        fn vs_main(@location(0) position: vec3<f32>, @location(1) normal: vec3<f32>) -> VsOut {
            var out: VsOut;
            out.pos = u.view_proj * vec4<f32>(position, 1.0);
            out.normal = normal;
            return out;
        }

        @fragment
        fn fs_main(in: VsOut) -> @location(0) vec4<f32> {
            let light = normalize(vec3<f32>(0.4, -0.6, 0.7));
            let ndl = max(dot(normalize(in.normal), light), 0.0);
            let shade = 0.25 + 0.75 * ndl;
            return vec4<f32>(shade * 0.65, shade * 0.78, shade * 0.92, 1.0);
        }
        """
    )

    pipeline = device.create_render_pipeline(
        layout="auto",
        vertex={
            "module": shader,
            "entry_point": "vs_main",
            "buffers": [
                {
                    "array_stride": 24,
                    "step_mode": wgpu.VertexStepMode.vertex,
                    "attributes": [
                        {"format": wgpu.VertexFormat.float32x3, "offset": 0, "shader_location": 0},
                        {"format": wgpu.VertexFormat.float32x3, "offset": 12, "shader_location": 1},
                    ],
                }
            ],
        },
        primitive={
            "topology": wgpu.PrimitiveTopology.triangle_list,
            "front_face": wgpu.FrontFace.ccw,
            "cull_mode": wgpu.CullMode.back,
        },
        depth_stencil={
            "format": wgpu.TextureFormat.depth24plus,
            "depth_write_enabled": True,
            "depth_compare": wgpu.CompareFunction.less,
        },
        fragment={
            "module": shader,
            "entry_point": "fs_main",
            "targets": [{"format": wgpu.TextureFormat.rgba8unorm}],
        },
    )

    bind_group = device.create_bind_group(
        layout=pipeline.get_bind_group_layout(0),
        entries=[{"binding": 0, "resource": {"buffer": ubuf}}],
    )

    encoder = device.create_command_encoder()
    rpass = encoder.begin_render_pass(
        color_attachments=[
            {
                "view": color_tex.create_view(),
                "load_op": wgpu.LoadOp.clear,
                "store_op": wgpu.StoreOp.store,
                "clear_value": (0.0, 0.0, 0.0, 0.0),
            }
        ],
        depth_stencil_attachment={
            "view": depth_tex.create_view(),
            "depth_load_op": wgpu.LoadOp.clear,
            "depth_store_op": wgpu.StoreOp.store,
            "depth_clear_value": 1.0,
        },
    )
    rpass.set_pipeline(pipeline)
    rpass.set_bind_group(0, bind_group)
    rpass.set_vertex_buffer(0, vbuf)
    rpass.set_index_buffer(ibuf, wgpu.IndexFormat.uint32)
    rpass.draw_indexed(int(mesh.indices.size))
    rpass.end()
    device.queue.submit([encoder.finish()])

    raw = device.queue.read_texture({"texture": color_tex}, {"bytes_per_row": w * 4}, (w, h, 1))
    arr = np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 4).copy()
    return Image.fromarray(arr, mode="RGBA")


@pytest.mark.gpu
def test_wgpu_runtime_info_and_compute_render_smoke() -> None:
    assert is_gpu_installed() is True
    assert is_gpu_available() is True
    assert is_gpu_enabled() is True
    assert is_gpu_enabled(True) is True

    info = gpu_info()
    assert info["installed"] is True
    assert info["available"] is True
    assert info["enabled"] is True
    assert info["backend"] is not None
    assert info["adapter_name"] is not None
    assert isinstance(info["is_software"], bool)
    assert info["wgpu_version"] is not None

    # Default adapter (hardware if available, else software fallback).
    default_adapter, default_device = get_gpu_device()
    assert default_adapter is not None
    np.testing.assert_allclose(_run_compute_smoke(default_device), [5.0, 8.0, 11.0, 14.0])

    # Explicit Mesa Lavapipe / software fallback adapter.
    fallback_adapter, fallback_device = get_gpu_device(force_fallback=True)
    assert fallback_adapter is not None
    np.testing.assert_allclose(_run_compute_smoke(fallback_device), [5.0, 8.0, 11.0, 14.0])

    # Render an actual OCCT part on both adapters and confirm perceptual match.
    part = bd.Box(24, 16, 10) - bd.Cylinder(4, 10)
    mesh = tessellate_shape(part, linear_deflection=0.05)
    look_from, look_up = VIEW_VECTORS[ViewType.ISOMETRIC]
    cam = compute_ortho_camera_for_points(
        mesh.positions, look_from, look_up, 128, 128, padding_mm=1.0
    )

    img_default = _render_shaded_mesh_smoke(default_device, mesh, cam)
    img_fallback = _render_shaded_mesh_smoke(fallback_device, mesh, cam)

    # Rendered pixels must be non-empty (part visible against transparent background).
    alpha = np.asarray(img_fallback)[:, :, 3]
    coverage = float(np.mean(alpha > 0))
    assert 0.15 < coverage < 0.90

    # Hardware/default render and Mesa Lavapipe render must perceptually match!
    result = assert_images_match(img_default, img_fallback, label="default-vs-lavapipe")
    assert result.ssim >= 0.985


# ---------------------------------------------------------------------------
# 3. OCCT Mesh Tessellation (mesh.py)
# ---------------------------------------------------------------------------


def test_tessellate_box_sharp_vs_smooth_normals() -> None:
    box = bd.Box(20.0, 10.0, 5.0)
    mesh_sharp = tessellate_shape(box, split_sharp_edges_deg=35.0)

    assert isinstance(mesh_sharp, TessellatedMesh)
    assert mesh_sharp.positions.dtype == np.float32
    assert mesh_sharp.normals.dtype == np.float32
    assert mesh_sharp.indices.dtype == np.uint32
    assert mesh_sharp.face_ids.dtype == np.uint32
    assert mesh_sharp.part_ids.dtype == np.uint32

    assert mesh_sharp.positions.shape[1] == 3
    assert mesh_sharp.normals.shape == mesh_sharp.positions.shape
    assert mesh_sharp.indices.shape[1] == 3
    assert mesh_sharp.face_ids.shape == (mesh_sharp.num_triangles,)
    assert mesh_sharp.part_ids.shape == (mesh_sharp.num_triangles,)

    assert set(mesh_sharp.face_ids.tolist()) == {1, 2, 3, 4, 5, 6}
    assert set(mesh_sharp.part_ids.tolist()) == {0}
    assert len(mesh_sharp.part_names) == 1
    assert mesh_sharp.bbox_min == pytest.approx((-10.0, -5.0, -2.5), abs=1e-4)
    assert mesh_sharp.bbox_max == pytest.approx((10.0, 5.0, 2.5), abs=1e-4)

    # All vertex normals must be unit-length and axis-aligned on a sharp box.
    norms = np.linalg.norm(mesh_sharp.normals, axis=1)
    np.testing.assert_allclose(norms, 1.0, atol=1e-5)
    max_axis_component = np.max(np.abs(mesh_sharp.normals), axis=1)
    np.testing.assert_allclose(max_axis_component, 1.0, atol=1e-5)

    # With split_sharp_edges_deg=None, corner normals average across the 3 orthogonal faces.
    mesh_smooth = tessellate_shape(box, split_sharp_edges_deg=None)
    smooth_max_comp = np.max(np.abs(mesh_smooth.normals), axis=1)
    assert float(np.min(smooth_max_comp)) < 0.7


def test_tessellate_cylinder_with_hole_normals_and_winding() -> None:
    part = bd.Pos(15.0, -8.0, 3.0) * bd.Rot(0, 0, 30) * (bd.Box(20, 10, 5) - bd.Cylinder(3, 5))
    mesh = tessellate_shape(part, linear_deflection=0.05)

    # 6 box faces + 1 inner cylindrical bore = 7 faces.
    assert set(mesh.face_ids.tolist()) == {1, 2, 3, 4, 5, 6, 7}

    # Verify every vertex normal agrees with its triangle's outward geometric winding normal.
    p0 = mesh.positions[mesh.indices[:, 0]].astype(np.float64)
    p1 = mesh.positions[mesh.indices[:, 1]].astype(np.float64)
    p2 = mesh.positions[mesh.indices[:, 2]].astype(np.float64)
    tri_n = np.cross(p1 - p0, p2 - p0)
    tri_len = np.linalg.norm(tri_n, axis=1, keepdims=True)
    valid = tri_len[:, 0] > 1e-8
    tri_unit = tri_n[valid] / tri_len[valid]

    avg_vnorm = (
        mesh.normals[mesh.indices[valid, 0]]
        + mesh.normals[mesh.indices[valid, 1]]
        + mesh.normals[mesh.indices[valid, 2]]
    ).astype(np.float64)
    avg_vnorm /= np.linalg.norm(avg_vnorm, axis=1, keepdims=True)
    dots = np.sum(tri_unit * avg_vnorm, axis=1)
    assert float(np.min(dots)) > 0.9


def test_tessellate_multi_solid_compound_assembly() -> None:
    plate = bd.Box(40, 20, 4)
    plate.label = "BasePlate"
    boss = bd.Pos(0, 0, 7) * bd.Cylinder(5, 10)
    boss.label = "Boss"
    pin = bd.Pos(12, 0, 5) * bd.Cylinder(2, 6)
    pin.label = "DowelPin"
    asm = bd.Compound(children=[plate, boss, pin])

    mesh = tessellate_shape(asm, linear_deflection=0.1)
    assert mesh.part_names == ["BasePlate", "Boss", "DowelPin"]
    assert set(mesh.part_ids.tolist()) == {0, 1, 2}
    # 6 (plate) + 3 (boss) + 3 (pin) = 12 faces, contiguous 1..12.
    assert set(mesh.face_ids.tolist()) == set(range(1, 13))
    assert mesh.bbox_min == pytest.approx((-20.0, -10.0, -2.0), abs=1e-3)
    assert mesh.bbox_max == pytest.approx((20.0, 10.0, 12.0), abs=1e-3)


def test_tessellate_validation_errors() -> None:
    box = bd.Box(5, 5, 5)
    with pytest.raises(ValueError, match="linear_deflection"):
        tessellate_shape(box, linear_deflection=0.0)
    with pytest.raises(ValueError, match="angular_deflection"):
        tessellate_shape(box, angular_deflection=-0.1)
    with pytest.raises(TypeError, match="Expected a build123d Shape"):
        tessellate_shape("not-a-shape")


# ---------------------------------------------------------------------------
# 4. B-Rep edge sampling & analytic silhouettes (mesh.py)
# ---------------------------------------------------------------------------


def test_sample_edges_box_matches_mesh_face_ids() -> None:
    box = bd.Box(20, 10, 5)
    mesh = tessellate_shape(box)
    edge_set = sample_shape_edges(box, look_from=VIEW_VECTORS[ViewType.ISOMETRIC][0])

    assert isinstance(edge_set, SampledEdgeSet)
    assert len(edge_set.edges) == 12
    assert edge_set.num_faces == 6
    mesh_faces = set(mesh.face_ids.tolist())

    for e in edge_set.edges:
        assert e.points_3d.dtype == np.float32
        assert e.points_3d.shape == (2, 3)
        assert e.geom_type == "line"
        assert e.face_id_a in mesh_faces
        assert e.face_id_b in mesh_faces
        assert e.face_id_a != e.face_id_b
        assert e.is_smooth is False

    # Isometric view of a box has 6 silhouette edges (outer hexagon).
    sil_edges = [e for e in edge_set.edges if e.is_silhouette_candidate]
    assert len(sil_edges) == 6

    starts, ends, meta = edge_set.pack_segments()
    assert starts.shape == (12, 3)
    assert ends.shape == (12, 3)
    assert meta.shape == (12, 4)
    assert starts.dtype == np.float32
    assert meta.dtype == np.uint32


def test_sample_edges_cylinder_silhouettes_and_seam() -> None:
    cyl = bd.Cylinder(5.0, 12.0)
    mesh = tessellate_shape(cyl)
    mesh_faces = set(mesh.face_ids.tolist())

    # FRONT view (looking along +Y): should find 2 straight silhouette lines at x = -5 and x = +5.
    front_edges = sample_shape_edges(
        cyl, deflection=0.05, look_from=VIEW_VECTORS[ViewType.FRONT][0]
    )
    seam_edges = [e for e in front_edges.edges if e.is_smooth]
    sil_edges = [e for e in front_edges.edges if e.geom_type == "silhouette"]

    assert len(seam_edges) == 1
    assert seam_edges[0].is_silhouette_candidate is False

    assert len(sil_edges) == 2
    xs = sorted(float(e.points_3d[0, 0]) for e in sil_edges)
    assert xs == pytest.approx([-5.0, 5.0], abs=1e-4)
    for se in sil_edges:
        assert se.face_id_a in mesh_faces
        assert se.face_id_b == 0
        assert se.is_silhouette_candidate is True
        z_span = sorted(se.points_3d[:, 2].tolist())
        assert z_span == pytest.approx([-6.0, 6.0], abs=1e-3)

    # TOP view (looking straight down cylinder axis Z): 0 analytic silhouette lines.
    top_edges = sample_shape_edges(cyl, deflection=0.05, look_from=VIEW_VECTORS[ViewType.TOP][0])
    assert [e for e in top_edges.edges if e.geom_type == "silhouette"] == []


def test_sample_edges_cylinder_with_side_hole_trims_silhouette() -> None:
    # Cylinder along Z with a transverse hole passing through X (cuts the x = ±5 silhouettes!).
    cyl_with_hole = bd.Cylinder(5.0, 20.0) - bd.Rot(0, 90, 0) * bd.Cylinder(2.5, 20.0)
    edge_set = sample_shape_edges(
        cyl_with_hole, deflection=0.05, look_from=VIEW_VECTORS[ViewType.FRONT][0]
    )
    # Outer cylinder silhouettes (vertical lines at x = ±5) split above & below z = [-2.5, 2.5].
    all_sil = [e for e in edge_set.edges if e.geom_type == "silhouette"]
    vertical_outer = [
        e
        for e in all_sil
        if abs(float(e.points_3d[0, 0]) - float(e.points_3d[-1, 0])) < 1e-3
        and abs(abs(float(e.points_3d[0, 0])) - 5.0) < 0.1
    ]
    # Horizontal bore silhouettes at z = ±2.5.
    horiz_bore = [
        e
        for e in all_sil
        if abs(float(e.points_3d[0, 2]) - float(e.points_3d[-1, 2])) < 1e-3
        and abs(abs(float(e.points_3d[0, 2])) - 2.5) < 0.1
    ]
    assert len(vertical_outer) == 4
    assert len(horiz_bore) == 2


def test_sample_edges_cone_sphere_torus_silhouettes() -> None:
    iso_dir = VIEW_VECTORS[ViewType.ISOMETRIC][0]
    v_unit = np.asarray(iso_dir, dtype=np.float64)
    v_unit /= float(np.linalg.norm(v_unit))

    # Cone: 2 straight silhouette lines from front view.
    cone = bd.Cone(6.0, 2.0, 12.0)
    cone_edges = sample_shape_edges(
        cone, deflection=0.05, look_from=VIEW_VECTORS[ViewType.FRONT][0]
    )
    cone_sils = [e for e in cone_edges.edges if e.geom_type == "silhouette"]
    assert len(cone_sils) == 2

    # Sphere: closed great circle perpendicular to look_from at radius 6.0.
    sphere = bd.Sphere(6.0)
    sph_edges = sample_shape_edges(sphere, deflection=0.05, look_from=iso_dir)
    sph_sils = [e for e in sph_edges.edges if e.geom_type == "silhouette"]
    assert len(sph_sils) == 1
    pts = sph_sils[0].points_3d.astype(np.float64)
    radii = np.linalg.norm(pts, axis=1)
    np.testing.assert_allclose(radii, 6.0, atol=1e-4)
    np.testing.assert_allclose(pts @ v_unit, 0.0, atol=1e-4)
    np.testing.assert_allclose(pts[0], pts[-1], atol=1e-5)

    # Torus: silhouettes extracted from both isometric and front views.
    torus = bd.Torus(10.0, 3.0)
    tor_iso = sample_shape_edges(torus, deflection=0.05, look_from=iso_dir)
    assert len([e for e in tor_iso.edges if e.geom_type == "silhouette"]) >= 2
    tor_front = sample_shape_edges(
        torus, deflection=0.05, look_from=VIEW_VECTORS[ViewType.FRONT][0]
    )
    assert len([e for e in tor_front.edges if e.geom_type == "silhouette"]) >= 4


# ---------------------------------------------------------------------------
# 5. Orthographic camera alignment against build123d.Drawing (camera.py)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "view_type",
    [
        ViewType.FRONT,
        ViewType.TOP,
        ViewType.RIGHT,
        ViewType.LEFT,
        ViewType.REAR,
        ViewType.BOTTOM,
        ViewType.ISOMETRIC,
    ],
)
def test_ortho_camera_aligns_with_build123d_drawing(view_type: ViewType) -> None:
    # Asymmetric off-origin shape so sign flips or axis swaps fail immediately.
    shape = bd.Pos(13.0, -7.0, 5.0) * (
        bd.Box(30.0, 20.0, 10.0) + bd.Pos(10.0, 5.0, 8.0) * bd.Box(10.0, 6.0, 8.0)
    )
    look_from, look_up = VIEW_VECTORS[view_type]
    dwg = Drawing(shape, look_from=look_from, look_up=look_up, with_hidden=False)
    dbb = dwg.visible_lines.bounding_box()

    verts = np.array([[v.X, v.Y, v.Z] for v in shape.vertices()], dtype=np.float64)
    origin = (float(shape.center().X), float(shape.center().Y), float(shape.center().Z))

    # Build tight camera from mesh/shape vertices matching exact Drawing extent.
    width_px = int(round(float(dbb.size.X) * 10.0))
    height_px = int(round(float(dbb.size.Y) * 10.0))
    cam = compute_ortho_camera_for_points(verts, look_from, look_up, width_px, height_px)

    assert cam.model_width == pytest.approx(float(dbb.size.X), abs=1e-4)
    assert cam.model_height == pytest.approx(float(dbb.size.Y), abs=1e-4)

    # Projected 2D coordinates around shape.center() match Drawing bounding box to 1e-5 mm.
    proj_2d = cam.project_points_2d(verts, origin=origin)
    assert float(np.min(proj_2d[:, 0])) == pytest.approx(float(dbb.min.X), abs=1e-5)
    assert float(np.max(proj_2d[:, 0])) == pytest.approx(float(dbb.max.X), abs=1e-5)
    assert float(np.min(proj_2d[:, 1])) == pytest.approx(float(dbb.min.Y), abs=1e-5)
    assert float(np.max(proj_2d[:, 1])) == pytest.approx(float(dbb.max.Y), abs=1e-5)

    # WebGPU NDC bounds: X, Y inside [-1, 1] and Z strictly inside (0, 1).
    ndc = cam.project_points_ndc(verts)
    assert float(np.min(ndc[:, 0])) >= -1.0 - 1e-4
    assert float(np.max(ndc[:, 0])) <= 1.0 + 1e-4
    assert float(np.min(ndc[:, 1])) >= -1.0 - 1e-4
    assert float(np.max(ndc[:, 1])) <= 1.0 + 1e-4
    assert float(np.min(ndc[:, 2])) > 0.0
    assert float(np.max(ndc[:, 2])) < 1.0

    # For orthographic views, AABB-based compute_ortho_camera matches Drawing width/height exactly.
    if view_type != ViewType.ISOMETRIC:
        bb = shape.bounding_box()
        cam_bb = compute_ortho_camera(
            (float(bb.min.X), float(bb.min.Y), float(bb.min.Z)),
            (float(bb.max.X), float(bb.max.Y), float(bb.max.Z)),
            look_from,
            look_up,
            width_px,
            height_px,
        )
        assert cam_bb.model_width == pytest.approx(float(dbb.size.X), abs=1e-4)
        assert cam_bb.model_height == pytest.approx(float(dbb.size.Y), abs=1e-4)
        assert cam_bb.z_near > 0.0
        assert cam_bb.z_far > cam_bb.z_near


def test_ortho_camera_padding_aspect_and_validation() -> None:
    cam = compute_ortho_camera(
        (-10.0, -5.0, -2.5),
        (10.0, 5.0, 2.5),
        look_from=(0.0, -1.0, 0.0),
        look_up=(0.0, 0.0, 1.0),
        width_px=200,
        height_px=200,
        padding_mm=2.0,
    )
    # FRONT sees X=20 and Z=5, plus 2*padding_mm=4 on each axis -> 24 x 9.
    assert cam.model_width == pytest.approx(24.0)
    assert cam.model_height == pytest.approx(9.0)
    # Square 200x200 viewport uses uniform isotropic scale limited by width (200 / 24).
    assert cam.pixels_per_mm == pytest.approx(200.0 / 24.0)
    assert cam.view_width_mm == pytest.approx(24.0)
    assert cam.view_height_mm == pytest.approx(24.0)

    # Pixel projection maps center to (100, 100) and top (+Z) to smaller py than bottom (-Z).
    px = cam.project_points_px(np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 2.5], [0.0, 0.0, -2.5]]))
    assert px[0, 0] == pytest.approx(100.0, abs=1e-3)
    assert px[0, 1] == pytest.approx(100.0, abs=1e-3)
    assert px[1, 1] < px[0, 1] < px[2, 1]

    # WGSL uniform transpose helper matches matrix transpose.
    np.testing.assert_array_equal(cam.uniform_view_proj(), cam.view_proj.T)

    with pytest.raises(ValueError, match="non-zero"):
        compute_ortho_camera((0, 0, 0), (1, 1, 1), (0, 0, 0), (0, 0, 1), 64, 64)
    with pytest.raises(ValueError, match="parallel"):
        compute_ortho_camera((0, 0, 0), (1, 1, 1), (0, 0, 1), (0, 0, 1), 64, 64)
    with pytest.raises(ValueError, match="positive"):
        compute_ortho_camera((0, 0, 0), (1, 1, 1), (0, -1, 0), (0, 0, 1), 0, 64)


# ---------------------------------------------------------------------------
# 6. CI Raster Visual Regression Harness (tests/raster_diff.py)
# ---------------------------------------------------------------------------


def _make_gradient_image(size: tuple[int, int] = (64, 64)) -> Image.Image:
    w, h = size
    xs = np.linspace(20, 220, w, dtype=np.uint8)
    ys = np.linspace(30, 210, h, dtype=np.uint8)
    gx, gy = np.meshgrid(xs, ys)
    arr = np.stack(
        [
            gx,
            gy,
            ((gx.astype(np.uint16) + gy.astype(np.uint16)) // 2).astype(np.uint8),
            np.full_like(gx, 255),
        ],
        axis=-1,
    )
    return Image.fromarray(arr, mode="RGBA")


def test_raster_diff_identical_and_small_driver_noise(tmp_path: Path) -> None:
    ref = _make_gradient_image((64, 64))

    # Identical images pass cleanly without writing a diff file.
    diff_file = tmp_path / "should_not_exist.png"
    res = assert_images_match(ref, ref.copy(), save_diff_to=diff_file)
    assert res.passed is True
    assert res.ssim == pytest.approx(1.0)
    assert res.max_abs_diff == 0
    assert res.mean_abs_diff == pytest.approx(0.0)
    assert res.diff_pixel_ratio == pytest.approx(0.0)
    assert res.diff_image_path is None
    assert not diff_file.exists()

    # Tiny driver-like ±2 LSB rounding noise passes within tolerance.
    noisy_arr = np.asarray(ref, dtype=np.int16).copy()
    noisy_arr[:, :, 0] = np.clip(noisy_arr[:, :, 0] + 2, 0, 255)
    noisy_arr[:, :, 1] = np.clip(noisy_arr[:, :, 1] - 1, 0, 255)
    noisy = Image.fromarray(noisy_arr.astype(np.uint8), mode="RGBA")

    res_noisy = assert_images_match(noisy, ref, label="driver-rounding")
    assert res_noisy.passed is True
    assert res_noisy.max_abs_diff == 2
    assert res_noisy.diff_pixel_ratio == pytest.approx(0.0)
    assert res_noisy.ssim > 0.995


def test_raster_diff_detects_regression_and_writes_diff_png(tmp_path: Path) -> None:
    ref = _make_gradient_image((64, 64))
    broken_arr = np.asarray(ref, dtype=np.uint8).copy()
    # Introduce a clear 20x20 visual regression block.
    broken_arr[16:36, 16:36, :3] = (255, 0, 0)
    broken = Image.fromarray(broken_arr, mode="RGBA")

    diff_file = tmp_path / "diffs" / "regression.png"
    res = compare_images(broken, ref, save_diff_to=diff_file)
    assert res.passed is False
    assert res.diff_pixel_ratio > 0.05
    assert res.ssim < 0.985
    assert res.diff_image_path == diff_file
    assert diff_file.exists()

    with Image.open(diff_file) as diff_img:
        assert diff_img.size == (64 * 3, 64)

    with pytest.raises(AssertionError, match="Visual regression detected for 'shaded-iso'"):
        assert_images_match(broken, ref, save_diff_to=diff_file, label="shaded-iso")

    # Dimension mismatch also fails cleanly and writes a diff PNG.
    wrong_size = _make_gradient_image((32, 32))
    mismatch_file = tmp_path / "mismatch.png"
    res_mm = compare_images(wrong_size, ref, save_diff_to=mismatch_file)
    assert res_mm.passed is False
    assert res_mm.ssim == 0.0
    assert mismatch_file.exists()


def test_extract_embedded_pngs_from_svg(tmp_path: Path) -> None:
    img1 = _make_gradient_image((16, 12))
    img2 = Image.new("RGBA", (8, 8), (10, 120, 240, 200))

    def _to_b64(img: Image.Image) -> str:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")

    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
    <svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="100" height="100">
      <g transform="translate(10, 10)">
        <image x="0" y="0" width="16" height="12" href="data:image/png;base64,{_to_b64(img1)}"/>
        <image x="20" y="0" width="8" height="8" xlink:href="data:image/png;base64,{_to_b64(img2)}"/>
      </g>
    </svg>
    """

    extracted_str = extract_embedded_pngs_from_svg(svg)
    assert len(extracted_str) == 2
    assert_images_match(extracted_str[0], img1)
    assert_images_match(extracted_str[1], img2)

    svg_path = tmp_path / "drawing.svg"
    svg_path.write_text(svg, encoding="utf-8")
    extracted_file = extract_embedded_pngs_from_svg(svg_path)
    assert len(extracted_file) == 2
    assert_images_match(extracted_file[0], img1)
    assert_images_match(extracted_file[1], img2)
