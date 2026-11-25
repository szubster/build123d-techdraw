"""Orthographic camera math aligned pixel-for-pixel with ``build123d.Drawing`` and ``SheetView``.

Coordinate conventions
----------------------
Given ``(look_from, look_up)`` matching :data:`build123d_techdraw.core.view.VIEW_VECTORS`:

- ``eye_dir = normalize(look_from)`` points from the model toward the camera.
- ``forward = -eye_dir`` is the camera viewing direction (into the scene).
- ``right = normalize(cross(normalize(look_up), eye_dir))`` matches ``gp_Ax2`` X direction.
- ``up = normalize(cross(eye_dir, right))`` matches ``gp_Ax2`` Y direction.

Matrices use standard mathematical row-major layout in NumPy (``clip = view_proj @ p``)
with WebGPU/Vulkan clip-space depth ``z in [0, 1]`` (``0`` at ``z_near``, ``1`` at ``z_far``)
and ``+y`` upward in NDC (mapping ``+up`` to the top of rendered raster images, matching
SVG ``<image>`` top-down pixel coordinates). Use :meth:`OrthoCamera.uniform_view_proj`
when uploading ``mat4x4<f32>`` uniforms to WGSL (column-major memory order).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

Vec3 = tuple[float, float, float]

_MIN_EXTENT_MM = 1e-3


@dataclass(frozen=True)
class OrthoCamera:
    """Orthographic WebGPU camera matching ``SheetView`` / ``build123d.Drawing`` projection."""

    view_matrix: NDArray[np.float32]
    proj_matrix: NDArray[np.float32]
    view_proj: NDArray[np.float32]
    right: Vec3
    up: Vec3
    forward: Vec3
    model_width: float
    model_height: float
    model_center_2d: tuple[float, float]
    z_near: float
    z_far: float
    pixels_per_mm: float
    width_px: int
    height_px: int
    view_width_mm: float
    view_height_mm: float

    def uniform_view_proj(self) -> NDArray[np.float32]:
        """Return ``view_proj`` in contiguous column-major layout for WGSL ``mat4x4<f32>``."""
        return np.ascontiguousarray(self.view_proj.T, dtype=np.float32)

    def uniform_view_matrix(self) -> NDArray[np.float32]:
        """Return ``view_matrix`` in contiguous column-major layout for WGSL ``mat4x4<f32>``."""
        return np.ascontiguousarray(self.view_matrix.T, dtype=np.float32)

    def project_points_ndc(self, points_3d: NDArray[np.floating[Any]]) -> NDArray[np.float32]:
        """Project ``(N, 3)`` world points into WebGPU NDC ``(x_ndc, y_ndc, z_ndc)``."""
        pts = np.asarray(points_3d, dtype=np.float64)
        if pts.ndim != 2 or pts.shape[1] != 3:
            raise ValueError(f"Expected points_3d shape (N, 3), got {pts.shape}.")
        hom = np.empty((pts.shape[0], 4), dtype=np.float64)
        hom[:, :3] = pts
        hom[:, 3] = 1.0
        clip = hom @ self.view_proj.astype(np.float64).T
        return np.ascontiguousarray(clip[:, :3], dtype=np.float32)

    def project_points_px(self, points_3d: NDArray[np.floating[Any]]) -> NDArray[np.float32]:
        """Project ``(N, 3)`` world points into top-left image pixels ``(px, py, z_ndc)``."""
        ndc = self.project_points_ndc(points_3d).astype(np.float64)
        out = np.empty_like(ndc, dtype=np.float32)
        out[:, 0] = ((ndc[:, 0] + 1.0) * 0.5 * self.width_px).astype(np.float32)
        out[:, 1] = ((1.0 - ndc[:, 1]) * 0.5 * self.height_px).astype(np.float32)
        out[:, 2] = ndc[:, 2].astype(np.float32)
        return out

    def project_points_2d(
        self,
        points_3d: NDArray[np.floating[Any]],
        *,
        origin: Vec3 = (0.0, 0.0, 0.0),
    ) -> NDArray[np.float64]:
        """Project ``(N, 3)`` world points onto the view ``(right, up)`` plane around ``origin``.

        When ``origin`` equals ``shape.center()``, the resulting ``(u, v)`` coordinates
        match ``build123d.Drawing(..., look_from=..., look_up=...)`` directly.
        """
        pts = np.asarray(points_3d, dtype=np.float64)
        if pts.ndim != 2 or pts.shape[1] != 3:
            raise ValueError(f"Expected points_3d shape (N, 3), got {pts.shape}.")
        rel = pts - np.asarray(origin, dtype=np.float64)
        r = np.asarray(self.right, dtype=np.float64)
        u = np.asarray(self.up, dtype=np.float64)
        out = np.empty((pts.shape[0], 2), dtype=np.float64)
        out[:, 0] = rel @ r
        out[:, 1] = rel @ u
        return out


def _orthonormal_basis(
    look_from: Vec3,
    look_up: Vec3,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """Compute ``(right, up, eye_dir)`` matching OCCT ``gp_Ax2`` in ``build123d.Drawing``."""
    lf = np.asarray(look_from, dtype=np.float64)
    lu = np.asarray(look_up, dtype=np.float64)

    lf_norm = float(np.linalg.norm(lf))
    if lf_norm < 1e-12:
        raise ValueError(f"look_from vector must be non-zero, got {look_from}.")
    eye_dir = lf / lf_norm

    lu_norm = float(np.linalg.norm(lu))
    if lu_norm < 1e-12:
        raise ValueError(f"look_up vector must be non-zero, got {look_up}.")
    up_in = lu / lu_norm

    right = np.cross(up_in, eye_dir)
    right_norm = float(np.linalg.norm(right))
    if right_norm < 1e-9:
        raise ValueError(
            f"look_up {look_up} is parallel to look_from {look_from}; cannot form camera basis."
        )
    right = right / right_norm

    up = np.cross(eye_dir, right)
    up = up / float(np.linalg.norm(up))
    return right, up, eye_dir


def _build_ortho_camera(
    sample_points: NDArray[np.float64],
    look_from: Vec3,
    look_up: Vec3,
    width_px: int,
    height_px: int,
    *,
    padding_mm: float = 0.0,
) -> OrthoCamera:
    """Build an :class:`OrthoCamera` tightly framing ``sample_points``."""
    if width_px <= 0 or height_px <= 0:
        raise ValueError(f"Viewport dimensions must be positive, got ({width_px}, {height_px}).")
    if padding_mm < 0.0:
        raise ValueError(f"padding_mm must be non-negative, got {padding_mm}.")
    if sample_points.ndim != 2 or sample_points.shape[1] != 3 or sample_points.shape[0] == 0:
        raise ValueError(f"Expected non-empty (N, 3) points, got shape {sample_points.shape}.")

    right, up, eye_dir = _orthonormal_basis(look_from, look_up)
    forward = -eye_dir

    u_vals = sample_points @ right
    v_vals = sample_points @ up
    w_vals = sample_points @ eye_dir

    u_min, u_max = float(np.min(u_vals)), float(np.max(u_vals))
    v_min, v_max = float(np.min(v_vals)), float(np.max(v_vals))
    w_min, w_max = float(np.min(w_vals)), float(np.max(w_vals))

    center_u = 0.5 * (u_min + u_max)
    center_v = 0.5 * (v_min + v_max)

    model_width = max((u_max - u_min) + 2.0 * padding_mm, _MIN_EXTENT_MM)
    model_height = max((v_max - v_min) + 2.0 * padding_mm, _MIN_EXTENT_MM)

    # Uniform isotropic scale so circles stay circular and align with CAD vector views.
    pixels_per_mm = min(width_px / model_width, height_px / model_height)
    view_width_mm = width_px / pixels_per_mm
    view_height_mm = height_px / pixels_per_mm
    half_w = 0.5 * view_width_mm
    half_h = 0.5 * view_height_mm

    # Place camera in front of w_max with generous depth margin so front/back faces never clip.
    depth_span = max(w_max - w_min, 1.0)
    z_margin = max(0.05 * depth_span, 1.0)
    w_eye = w_max + 2.0 * z_margin
    z_near = z_margin
    z_far = (w_max - w_min) + 3.0 * z_margin

    eye = center_u * right + center_v * up + w_eye * eye_dir

    view_64 = np.eye(4, dtype=np.float64)
    view_64[0, :3] = right
    view_64[1, :3] = up
    view_64[2, :3] = eye_dir
    view_64[0, 3] = -float(np.dot(right, eye))
    view_64[1, 3] = -float(np.dot(up, eye))
    view_64[2, 3] = -float(np.dot(eye_dir, eye))

    # WebGPU orthographic projection: x,y in [-1, 1], z_view in [-z_near, -z_far] -> z_ndc in [0, 1].
    inv_depth = 1.0 / (z_far - z_near)
    proj_64 = np.zeros((4, 4), dtype=np.float64)
    proj_64[0, 0] = 1.0 / half_w
    proj_64[1, 1] = 1.0 / half_h
    proj_64[2, 2] = -inv_depth
    proj_64[2, 3] = -z_near * inv_depth
    proj_64[3, 3] = 1.0

    view_proj_64 = proj_64 @ view_64

    return OrthoCamera(
        view_matrix=np.ascontiguousarray(view_64, dtype=np.float32),
        proj_matrix=np.ascontiguousarray(proj_64, dtype=np.float32),
        view_proj=np.ascontiguousarray(view_proj_64, dtype=np.float32),
        right=(float(right[0]), float(right[1]), float(right[2])),
        up=(float(up[0]), float(up[1]), float(up[2])),
        forward=(float(forward[0]), float(forward[1]), float(forward[2])),
        model_width=model_width,
        model_height=model_height,
        model_center_2d=(center_u, center_v),
        z_near=z_near,
        z_far=z_far,
        pixels_per_mm=pixels_per_mm,
        width_px=width_px,
        height_px=height_px,
        view_width_mm=view_width_mm,
        view_height_mm=view_height_mm,
    )


def compute_ortho_camera(
    bbox_min: Vec3,
    bbox_max: Vec3,
    look_from: Vec3,
    look_up: Vec3,
    width_px: int,
    height_px: int,
    *,
    padding_mm: float = 0.0,
) -> OrthoCamera:
    """Compute an orthographic WebGPU camera framing the 3D AABB ``[bbox_min, bbox_max]``.

    The camera basis matches ``build123d.Drawing(shape, look_from=..., look_up=...)`` and
    :data:`build123d_techdraw.core.view.VIEW_VECTORS` so rasterized GPU views align
    pixel-for-pixel with projected vector views.
    """
    x0, y0, z0 = (float(bbox_min[0]), float(bbox_min[1]), float(bbox_min[2]))
    x1, y1, z1 = (float(bbox_max[0]), float(bbox_max[1]), float(bbox_max[2]))
    corners = np.array(
        [
            [x0, y0, z0],
            [x1, y0, z0],
            [x0, y1, z0],
            [x1, y1, z0],
            [x0, y0, z1],
            [x1, y0, z1],
            [x0, y1, z1],
            [x1, y1, z1],
        ],
        dtype=np.float64,
    )
    return _build_ortho_camera(
        corners,
        look_from,
        look_up,
        width_px,
        height_px,
        padding_mm=padding_mm,
    )


def compute_ortho_camera_for_points(
    points_3d: NDArray[np.floating[Any]],
    look_from: Vec3,
    look_up: Vec3,
    width_px: int,
    height_px: int,
    *,
    padding_mm: float = 0.0,
) -> OrthoCamera:
    """Compute an orthographic WebGPU camera tightly framing a ``(N, 3)`` 3D point cloud.

    Prefer this over :func:`compute_ortho_camera` for isometric or angled views of
    non-box shapes (cylinders, L-brackets, assemblies) when exact tight alignment
    with ``build123d.Drawing.visible_lines.bounding_box()`` is required.
    """
    pts = np.asarray(points_3d, dtype=np.float64)
    return _build_ortho_camera(
        pts,
        look_from,
        look_up,
        width_px,
        height_px,
        padding_mm=padding_mm,
    )
