"""OCCT-to-GPU triangle mesh tessellation and B-Rep/silhouette edge sampling.

Both :func:`tessellate_shape` and :func:`sample_shape_edges` traverse assembly parts
and faces in the exact same deterministic order so 1-based ``face_ids`` in
:class:`TessellatedMesh` match ``face_id_a`` / ``face_id_b`` in :class:`SampledEdge`
1-to-1 for GPU hidden-line removal (HLR) depth/ID buffer lookups.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from build123d.topology import Shape
from numpy.typing import NDArray
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Curve2d, BRepAdaptor_Surface
from OCP.BRepClass import BRepClass_FaceClassifier
from OCP.BRepLib import BRepLib_ToolTriangulatedShape
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepTools import BRepTools
from OCP.collections import (
    IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher,
    IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher,
)
from OCP.ElSLib import ElSLib
from OCP.GCPnts import GCPnts_QuasiUniformDeflection
from OCP.GeomAbs import (
    GeomAbs_BSplineCurve,
    GeomAbs_Circle,
    GeomAbs_Cone,
    GeomAbs_Cylinder,
    GeomAbs_Ellipse,
    GeomAbs_Line,
    GeomAbs_Sphere,
    GeomAbs_Torus,
)
from OCP.gp import gp_Pnt, gp_Pnt2d, gp_Vec
from OCP.TopAbs import (
    TopAbs_EDGE,
    TopAbs_FACE,
    TopAbs_IN,
    TopAbs_ON,
    TopAbs_REVERSED,
    TopAbs_SOLID,
)
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Edge, TopoDS_Face, TopoDS_Shape

Vec3 = tuple[float, float, float]

_CURVE_TYPE_NAMES: dict[Any, str] = {
    GeomAbs_Line: "line",
    GeomAbs_Circle: "circle",
    GeomAbs_Ellipse: "ellipse",
    GeomAbs_BSplineCurve: "bspline",
}

_CURVED_SURFACE_TYPES = (
    GeomAbs_Cylinder,
    GeomAbs_Cone,
    GeomAbs_Sphere,
    GeomAbs_Torus,
)


@dataclass
class TessellatedMesh:
    """GPU-ready triangle mesh extracted from a build123d / OCCT B-Rep shape."""

    positions: NDArray[np.float32]  # (V, 3)
    normals: NDArray[np.float32]  # (V, 3)
    indices: NDArray[np.uint32]  # (T, 3)
    face_ids: NDArray[np.uint32]  # (T,) 1-based face ID per triangle
    part_ids: NDArray[np.uint32]  # (T,) 0-based part/solid ID per triangle
    part_names: list[str]
    bbox_min: Vec3
    bbox_max: Vec3

    @property
    def num_vertices(self) -> int:
        """Number of vertices in the mesh."""
        return int(self.positions.shape[0])

    @property
    def num_triangles(self) -> int:
        """Number of triangles in the mesh."""
        return int(self.indices.shape[0])


@dataclass(frozen=True)
class SampledEdge:
    """A discretized 3D B-Rep edge or analytic silhouette polyline."""

    edge_id: int
    face_id_a: int  # 1-based face ID, or 0 if free edge
    face_id_b: int  # 1-based face ID, or 0 if boundary / analytic silhouette
    points_3d: NDArray[np.float32]  # (N, 3), N >= 2
    is_silhouette_candidate: bool = False
    is_smooth: bool = False
    geom_type: str = "other"


@dataclass
class SampledEdgeSet:
    """Collection of discretized 3D edges and optional analytic silhouettes for a shape."""

    edges: list[SampledEdge]
    bbox_min: Vec3
    bbox_max: Vec3
    num_faces: int

    @property
    def total_points(self) -> int:
        """Total number of 3D polyline sample points across all edges."""
        return sum(int(e.points_3d.shape[0]) for e in self.edges)

    @property
    def total_segments(self) -> int:
        """Total number of polyline line segments across all edges."""
        return sum(max(0, int(e.points_3d.shape[0]) - 1) for e in self.edges)

    def pack_segments(
        self,
    ) -> tuple[NDArray[np.float32], NDArray[np.float32], NDArray[np.uint32]]:
        """Pack all polyline segments into flat GPU-ready arrays.

        Returns:
            ``(starts, ends, meta)`` where ``starts`` and ``ends`` have shape ``(S, 3)``
            (``float32``) and ``meta`` has shape ``(S, 4)`` (``uint32``) containing
            ``[edge_id, face_id_a, face_id_b, flags]`` per segment.
            ``flags`` bit 0 = ``is_silhouette_candidate``, bit 1 = ``is_smooth``.
        """
        total_segs = self.total_segments
        if total_segs == 0:
            empty_pts = np.zeros((0, 3), dtype=np.float32)
            empty_meta = np.zeros((0, 4), dtype=np.uint32)
            return empty_pts, empty_pts.copy(), empty_meta

        starts = np.empty((total_segs, 3), dtype=np.float32)
        ends = np.empty((total_segs, 3), dtype=np.float32)
        meta = np.empty((total_segs, 4), dtype=np.uint32)

        cursor = 0
        for edge in self.edges:
            n_segs = int(edge.points_3d.shape[0]) - 1
            if n_segs <= 0:
                continue
            starts[cursor : cursor + n_segs] = edge.points_3d[:-1]
            ends[cursor : cursor + n_segs] = edge.points_3d[1:]
            flags = (1 if edge.is_silhouette_candidate else 0) | (2 if edge.is_smooth else 0)
            meta[cursor : cursor + n_segs, 0] = edge.edge_id
            meta[cursor : cursor + n_segs, 1] = edge.face_id_a
            meta[cursor : cursor + n_segs, 2] = edge.face_id_b
            meta[cursor : cursor + n_segs, 3] = flags
            cursor += n_segs

        return starts, ends, meta


def _extract_leaf_shapes(shape: Shape[Any]) -> list[Shape[Any]]:
    """Recursively extract leaf child shapes of a build123d Compound, or solids of a Part."""
    if shape.children:
        leaves: list[Shape[Any]] = []
        for child in shape.children:
            if child.children:
                leaves.extend(_extract_leaf_shapes(child))
            else:
                leaves.append(child)
        return leaves
    solids = shape.solids()
    if solids:
        if len(solids) == 1 and shape.label and not solids[0].label:
            return [shape]
        return list(solids)
    return [shape]


def _extract_parts(shape: Shape[Any] | TopoDS_Shape | Any) -> list[tuple[int, str, TopoDS_Shape]]:
    """Return deterministic ``[(part_id, part_name, topo_shape), ...]`` for any CAD input."""
    if isinstance(shape, Shape):
        leaves = _extract_leaf_shapes(shape)
        parts: list[tuple[int, str, TopoDS_Shape]] = []
        for idx, leaf in enumerate(leaves):
            wrapped = leaf.wrapped
            if isinstance(wrapped, TopoDS_Shape) and not wrapped.IsNull():
                name = leaf.label if leaf.label else f"Part_{idx + 1}"
                parts.append((len(parts), name, wrapped))
        return parts

    if isinstance(shape, TopoDS_Shape):
        if shape.IsNull():
            return []
        solids: list[TopoDS_Shape] = []
        exp = TopExp_Explorer(shape, TopAbs_SOLID)
        while exp.More():
            solids.append(exp.Current())
            exp.Next()
        if solids:
            return [(idx, f"Part_{idx + 1}", solid) for idx, solid in enumerate(solids)]
        return [(0, "Part_1", shape)]

    raise TypeError(f"Expected a build123d Shape or OCCT TopoDS_Shape, got {type(shape).__name__}.")


def _compute_area_weighted_normals(
    positions: NDArray[np.float32],
    indices: NDArray[np.uint32],
) -> NDArray[np.float32]:
    """Compute outward area-weighted vertex normals from triangle geometry."""
    normals = np.zeros_like(positions, dtype=np.float64)
    if indices.shape[0] == 0:
        return normals.astype(np.float32)

    p0 = positions[indices[:, 0]].astype(np.float64)
    p1 = positions[indices[:, 1]].astype(np.float64)
    p2 = positions[indices[:, 2]].astype(np.float64)
    tri_normals = np.cross(p1 - p0, p2 - p0)

    np.add.at(normals, indices[:, 0], tri_normals)
    np.add.at(normals, indices[:, 1], tri_normals)
    np.add.at(normals, indices[:, 2], tri_normals)

    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    valid = lengths[:, 0] > 1e-12
    normals[valid] /= lengths[valid]
    normals[~valid] = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    return normals.astype(np.float32)


def _smooth_coincident_normals(
    positions: NDArray[np.float32],
    normals: NDArray[np.float32],
    vertex_part_ids: NDArray[np.uint32],
    split_sharp_edges_deg: float | None,
) -> NDArray[np.float32]:
    """Smooth normals across coincident vertices within the same part when below crease angle.

    - ``split_sharp_edges_deg is None``: average normals across all coincident vertices
      in the same part regardless of angle.
    - ``split_sharp_edges_deg > 0``: average only coincident vertex normals whose angle
      difference is ``<= split_sharp_edges_deg``; sharp mechanical edges remain split.
    - ``split_sharp_edges_deg <= 0``: keep exact per-face analytical normals untouched.
    """
    n_verts = positions.shape[0]
    if n_verts == 0 or (split_sharp_edges_deg is not None and split_sharp_edges_deg <= 0.0):
        return normals

    # Quantize to 0.1 micron (1e-4 mm) and group by (part_id, qx, qy, qz).
    q = np.rint(positions.astype(np.float64) * 1e4).astype(np.int64)
    order = np.lexsort((q[:, 2], q[:, 1], q[:, 0], vertex_part_ids))

    q_sorted = q[order]
    p_sorted = vertex_part_ids[order]

    # Find run boundaries where any of (part_id, qx, qy, qz) changes.
    changed = np.empty(n_verts, dtype=np.bool_)
    changed[0] = True
    changed[1:] = (
        (p_sorted[1:] != p_sorted[:-1])
        | (q_sorted[1:, 0] != q_sorted[:-1, 0])
        | (q_sorted[1:, 1] != q_sorted[:-1, 1])
        | (q_sorted[1:, 2] != q_sorted[:-1, 2])
    )
    starts = np.flatnonzero(changed)
    ends = np.empty_like(starts)
    ends[:-1] = starts[1:]
    ends[-1] = n_verts

    multi = np.flatnonzero((ends - starts) > 1)
    if multi.size == 0:
        return normals

    out = normals.astype(np.float64, copy=True)
    src = normals.astype(np.float64, copy=False)
    cos_thresh = (
        None if split_sharp_edges_deg is None else math.cos(math.radians(split_sharp_edges_deg))
    )

    for run_idx in multi:
        bucket = order[starts[run_idx] : ends[run_idx]]
        bn = src[bucket]
        if cos_thresh is None:
            avg = bn.sum(axis=0)
            norm = float(np.linalg.norm(avg))
            if norm > 1e-12:
                out[bucket] = avg / norm
        else:
            # Pairwise cosine similarity among coincident vertices in this bucket.
            dots = bn @ bn.T
            mask = dots >= cos_thresh
            summed = mask.astype(np.float64) @ bn
            norms = np.linalg.norm(summed, axis=1, keepdims=True)
            valid = norms[:, 0] > 1e-12
            summed[valid] /= norms[valid]
            out[bucket[valid]] = summed[valid]

    return np.ascontiguousarray(out, dtype=np.float32)


def tessellate_shape(
    shape: Shape[Any] | TopoDS_Shape | Any,
    *,
    linear_deflection: float = 0.05,
    angular_deflection: float = 0.2,
    split_sharp_edges_deg: float | None = 35.0,
) -> TessellatedMesh:
    """Tessellate a build123d or OCCT shape into a GPU-ready :class:`TessellatedMesh`.

    Uses ``BRepMesh_IncrementalMesh`` + ``BRep_Tool.Triangulation_s`` with analytical
    OCCT surface normals (falling back to area-weighted triangle normals at degenerate
    poles) and optional sharp-edge preservation.

    Args:
        shape: build123d ``Shape`` (``Part``, ``Solid``, ``Compound``, ``Face``, etc.)
            or raw OCCT ``TopoDS_Shape``.
        linear_deflection: Maximum chordal deviation in model units (mm).
        angular_deflection: Maximum angular deviation in radians.
        split_sharp_edges_deg: Dihedral angle threshold in degrees above which edges
            remain sharp (split normals). Default ``35.0`` keeps 45° chamfers and 90°
            corners crisp while smoothing tangent/shallow bends. Pass ``None`` to
            average normals across all coincident vertices within each part.
    """
    if linear_deflection <= 0.0:
        raise ValueError(f"linear_deflection must be positive, got {linear_deflection}.")
    if angular_deflection <= 0.0:
        raise ValueError(f"angular_deflection must be positive, got {angular_deflection}.")

    parts = _extract_parts(shape)
    part_names = [name for _pid, name, _topo in parts]

    pos_chunks: list[NDArray[np.float32]] = []
    norm_chunks: list[NDArray[np.float32]] = []
    idx_chunks: list[NDArray[np.uint32]] = []
    face_id_chunks: list[NDArray[np.uint32]] = []
    part_id_chunks: list[NDArray[np.uint32]] = []
    vert_part_chunks: list[NDArray[np.uint32]] = []

    vertex_offset = 0
    face_id_offset = 0

    for part_id, _part_name, part_topo in parts:
        BRepMesh_IncrementalMesh(part_topo, linear_deflection, False, angular_deflection, True)

        face_map = IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
        TopExp.MapShapes_s(part_topo, TopAbs_FACE, face_map)
        n_faces = face_map.Extent()

        for local_face_idx in range(1, n_faces + 1):
            global_face_id = face_id_offset + local_face_idx
            face = TopoDS.Face(face_map.FindKey(local_face_idx))

            loc = TopLoc_Location()
            tri = BRep_Tool.Triangulation_s(face, loc)
            if tri is None:
                continue

            nb_nodes = tri.NbNodes()
            nb_tris = tri.NbTriangles()
            if nb_nodes == 0 or nb_tris == 0:
                continue

            trsf = loc.Transformation()
            flip = (face.Orientation() == TopAbs_REVERSED) ^ trsf.IsNegative()

            face_pos = np.empty((nb_nodes, 3), dtype=np.float32)
            for i in range(1, nb_nodes + 1):
                pt = tri.Node(i).Transformed(trsf)
                face_pos[i - 1, 0] = pt.X()
                face_pos[i - 1, 1] = pt.Y()
                face_pos[i - 1, 2] = pt.Z()

            local_tris = np.empty((nb_tris, 3), dtype=np.uint32)
            for t in range(1, nb_tris + 1):
                i1, i2, i3 = tri.Triangle(t).Get()
                if flip:
                    i2, i3 = i3, i2
                local_tris[t - 1, 0] = i1 - 1
                local_tris[t - 1, 1] = i2 - 1
                local_tris[t - 1, 2] = i3 - 1

            # Analytical surface normals via OCCT, falling back to geometric normals at poles.
            BRepLib_ToolTriangulatedShape.ComputeNormals_s(face, tri)
            geom_normals = _compute_area_weighted_normals(face_pos, local_tris)

            if tri.HasNormals():
                face_norm = np.empty((nb_nodes, 3), dtype=np.float32)
                for i in range(1, nb_nodes + 1):
                    nd = tri.Normal(i).Transformed(trsf)
                    if flip:
                        nd = nd.Reversed()
                    nx, ny, nz = float(nd.X()), float(nd.Y()), float(nd.Z())
                    mag = math.sqrt(nx * nx + ny * ny + nz * nz)
                    if mag > 1e-9 and math.isfinite(mag):
                        face_norm[i - 1, 0] = nx / mag
                        face_norm[i - 1, 1] = ny / mag
                        face_norm[i - 1, 2] = nz / mag
                    else:
                        face_norm[i - 1] = geom_normals[i - 1]
            else:
                face_norm = geom_normals

            pos_chunks.append(face_pos)
            norm_chunks.append(face_norm)
            idx_chunks.append(local_tris + np.uint32(vertex_offset))
            face_id_chunks.append(np.full(nb_tris, global_face_id, dtype=np.uint32))
            part_id_chunks.append(np.full(nb_tris, part_id, dtype=np.uint32))
            vert_part_chunks.append(np.full(nb_nodes, part_id, dtype=np.uint32))
            vertex_offset += nb_nodes

        face_id_offset += n_faces

    if not pos_chunks:
        empty_f = np.zeros((0, 3), dtype=np.float32)
        empty_i = np.zeros((0, 3), dtype=np.uint32)
        empty_u = np.zeros((0,), dtype=np.uint32)
        return TessellatedMesh(
            positions=empty_f,
            normals=empty_f.copy(),
            indices=empty_i,
            face_ids=empty_u,
            part_ids=empty_u.copy(),
            part_names=part_names,
            bbox_min=(0.0, 0.0, 0.0),
            bbox_max=(0.0, 0.0, 0.0),
        )

    positions = np.ascontiguousarray(np.concatenate(pos_chunks, axis=0), dtype=np.float32)
    normals = np.ascontiguousarray(np.concatenate(norm_chunks, axis=0), dtype=np.float32)
    indices = np.ascontiguousarray(np.concatenate(idx_chunks, axis=0), dtype=np.uint32)
    face_ids = np.ascontiguousarray(np.concatenate(face_id_chunks, axis=0), dtype=np.uint32)
    part_ids = np.ascontiguousarray(np.concatenate(part_id_chunks, axis=0), dtype=np.uint32)
    vert_parts = np.concatenate(vert_part_chunks, axis=0)

    normals = _smooth_coincident_normals(positions, normals, vert_parts, split_sharp_edges_deg)

    pmin = positions.min(axis=0)
    pmax = positions.max(axis=0)

    return TessellatedMesh(
        positions=positions,
        normals=normals,
        indices=indices,
        face_ids=face_ids,
        part_ids=part_ids,
        part_names=part_names,
        bbox_min=(float(pmin[0]), float(pmin[1]), float(pmin[2])),
        bbox_max=(float(pmax[0]), float(pmax[1]), float(pmax[2])),
    )


def _face_normal_at_edge_mid(edge: TopoDS_Edge, face: TopoDS_Face) -> NDArray[np.float64] | None:
    """Evaluate the outward unit normal of ``face`` at the midpoint of ``edge``."""
    try:
        c2d = BRepAdaptor_Curve2d(edge, face)
        t_mid = 0.5 * (c2d.FirstParameter() + c2d.LastParameter())
        uv = c2d.Value(t_mid)
        surf = BRepAdaptor_Surface(face)
        p = gp_Pnt()
        du = gp_Vec()
        dv = gp_Vec()
        surf.D1(uv.X(), uv.Y(), p, du, dv)
        cross = du.Crossed(dv)
        mag = cross.Magnitude()
        if mag < 1e-12:
            return None
        n = np.array([cross.X() / mag, cross.Y() / mag, cross.Z() / mag], dtype=np.float64)
        if face.Orientation() == TopAbs_REVERSED:
            n = -n
        return n
    except Exception:
        return None


def _discretize_edge(edge: TopoDS_Edge, deflection: float) -> tuple[NDArray[np.float32], str]:
    """Discretize a B-Rep edge into a ``(N, 3)`` float32 polyline and return its geometry name."""
    curve = BRepAdaptor_Curve(edge)
    ctype = curve.GetType()
    geom_name = _CURVE_TYPE_NAMES.get(ctype, "other")

    if ctype == GeomAbs_Line:
        p0 = curve.Value(curve.FirstParameter())
        p1 = curve.Value(curve.LastParameter())
        pts = np.array(
            [[p0.X(), p0.Y(), p0.Z()], [p1.X(), p1.Y(), p1.Z()]],
            dtype=np.float32,
        )
        return pts, geom_name

    disc = GCPnts_QuasiUniformDeflection(curve, deflection)
    if disc.IsDone() and disc.NbPoints() >= 2:
        nb = disc.NbPoints()
        pts = np.empty((nb, 3), dtype=np.float32)
        for i in range(1, nb + 1):
            p = curve.Value(disc.Parameter(i))
            pts[i - 1, 0] = p.X()
            pts[i - 1, 1] = p.Y()
            pts[i - 1, 2] = p.Z()
        return pts, geom_name

    # Fallback: uniform parameter sampling if QuasiUniformDeflection fails.
    t0 = curve.FirstParameter()
    t1 = curve.LastParameter()
    n_fallback = 16
    ts = np.linspace(t0, t1, n_fallback)
    pts = np.empty((n_fallback, 3), dtype=np.float32)
    for i, t in enumerate(ts):
        p = curve.Value(float(t))
        pts[i, 0] = p.X()
        pts[i, 1] = p.Y()
        pts[i, 2] = p.Z()
    return pts, geom_name


def _angles_in_range(angle: float, lo: float, hi: float, eps: float = 1e-6) -> list[float]:
    """Return distinct periodic equivalents ``angle + 2*pi*k`` lying within ``[lo, hi]``."""
    two_pi = 2.0 * math.pi
    k_start = math.floor((lo - eps - angle) / two_pi)
    k_end = math.ceil((hi + eps - angle) / two_pi)
    found: list[float] = []
    for k in range(k_start, k_end + 1):
        cand = angle + k * two_pi
        if lo - eps <= cand <= hi + eps:
            clamped = max(lo, min(hi, cand))
            is_dup = False
            for prev in found:
                diff = abs(clamped - prev) % two_pi
                if min(diff, two_pi - diff) < 1e-5:
                    is_dup = True
                    break
            if not is_dup:
                found.append(clamped)
    return found


def _wrap_into_range(val: float, lo: float, hi: float) -> float | None:
    """Wrap a periodic angle ``val`` into ``[lo, hi]``, returning ``None`` if outside."""
    matches = _angles_in_range(val, lo, hi)
    return matches[0] if matches else None


def _is_uv_in_face(
    classifier: BRepClass_FaceClassifier,
    face: TopoDS_Face,
    u: float,
    v: float,
) -> bool:
    """Return True if ``(u, v)`` lies inside or on the boundary of ``face``."""
    classifier.Perform(face, gp_Pnt2d(u, v), 1e-5)
    state = classifier.State()
    return bool(state in (TopAbs_IN, TopAbs_ON))


def _bisect_uv_boundary(
    classifier: BRepClass_FaceClassifier,
    face: TopoDS_Face,
    uv_in: tuple[float, float],
    uv_out: tuple[float, float],
    steps: int = 8,
) -> tuple[float, float]:
    """Refine the boundary crossing between an inside UV and outside UV point."""
    in_u, in_v = uv_in
    out_u, out_v = uv_out
    for _ in range(steps):
        mid_u = 0.5 * (in_u + out_u)
        mid_v = 0.5 * (in_v + out_v)
        if _is_uv_in_face(classifier, face, mid_u, mid_v):
            in_u, in_v = mid_u, mid_v
        else:
            out_u, out_v = mid_u, mid_v
    return (in_u, in_v)


def _trim_uv_curve(
    face: TopoDS_Face,
    surf: BRepAdaptor_Surface,
    uv_samples: list[tuple[float, float]],
    *,
    closed: bool = False,
    collapse_linear: bool = False,
) -> list[NDArray[np.float32]]:
    """Trim a UV-sampled curve against the trimmed B-Rep face boundary into 3D polylines."""
    n = len(uv_samples)
    if n < 2:
        return []

    classifier = BRepClass_FaceClassifier()
    inside = [_is_uv_in_face(classifier, face, u, v) for u, v in uv_samples]
    if not any(inside):
        return []

    if closed and all(inside):
        pts = np.empty((n + 1, 3), dtype=np.float32)
        for i, (u, v) in enumerate(uv_samples):
            p = surf.Value(u, v)
            pts[i, 0] = p.X()
            pts[i, 1] = p.Y()
            pts[i, 2] = p.Z()
        pts[n] = pts[0]
        return [pts]

    # Collect contiguous runs of inside UV points with bisected trim endpoints.
    runs: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []

    for i, uv in enumerate(uv_samples):
        if inside[i]:
            if not cur and i > 0 and not inside[i - 1]:
                cur.append(_bisect_uv_boundary(classifier, face, uv, uv_samples[i - 1]))
            cur.append(uv)
        elif cur:
            cur.append(_bisect_uv_boundary(classifier, face, uv_samples[i - 1], uv))
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)

    # Merge wrap-around run for closed curves that cross index 0.
    if closed and len(runs) >= 2 and inside[0] and inside[-1]:
        runs[0] = runs[-1] + runs[0]
        runs.pop()

    polylines: list[NDArray[np.float32]] = []
    for run in runs:
        if len(run) < 2:
            continue
        if collapse_linear:
            p0 = surf.Value(run[0][0], run[0][1])
            p1 = surf.Value(run[-1][0], run[-1][1])
            arr = np.array(
                [[p0.X(), p0.Y(), p0.Z()], [p1.X(), p1.Y(), p1.Z()]],
                dtype=np.float32,
            )
            if float(np.linalg.norm(arr[1] - arr[0])) > 1e-5:
                polylines.append(arr)
        else:
            arr = np.empty((len(run), 3), dtype=np.float32)
            for j, (u, v) in enumerate(run):
                p = surf.Value(u, v)
                arr[j, 0] = p.X()
                arr[j, 1] = p.Y()
                arr[j, 2] = p.Z()
            polylines.append(arr)
    return polylines


def _sample_ruled_silhouette(
    face: TopoDS_Face,
    surf: BRepAdaptor_Surface,
    u_angle: float,
    v_min: float,
    v_max: float,
    deflection: float,
) -> list[NDArray[np.float32]]:
    """Sample and trim an iso-U straight silhouette generator on a cylinder or cone."""
    p0 = surf.Value(u_angle, v_min)
    p1 = surf.Value(u_angle, v_max)
    span = p0.Distance(p1)
    if span < 1e-6:
        return []
    step = max(deflection * 5.0, 0.25)
    n_samples = max(32, min(256, int(math.ceil(span / step)) + 1))
    vs = np.linspace(v_min, v_max, n_samples)
    uv_samples = [(u_angle, float(v)) for v in vs]
    return _trim_uv_curve(face, surf, uv_samples, closed=False, collapse_linear=True)


def _angular_steps_for_radius(radius: float, arc_span: float, deflection: float) -> int:
    """Compute sample count for an arc of ``arc_span`` radians so chord error <= ``deflection``."""
    r = max(radius, 1e-6)
    cos_half = max(-1.0, min(1.0, 1.0 - deflection / r))
    dtheta = 2.0 * math.acos(cos_half)
    if dtheta < 1e-4:
        return 256
    return max(24, min(256, int(math.ceil(arc_span / dtheta))))


def _ax3_basis(
    pos: Any,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """Extract ``(x_dir, y_dir, axis)`` unit vectors from an OCCT ``gp_Ax3``."""
    x_dir = np.array(
        [pos.XDirection().X(), pos.XDirection().Y(), pos.XDirection().Z()], dtype=np.float64
    )
    y_dir = np.array(
        [pos.YDirection().X(), pos.YDirection().Y(), pos.YDirection().Z()], dtype=np.float64
    )
    axis = np.array(
        [pos.Direction().X(), pos.Direction().Y(), pos.Direction().Z()], dtype=np.float64
    )
    return x_dir, y_dir, axis


def _extract_analytic_silhouettes(
    face: TopoDS_Face,
    v_dir: NDArray[np.float64],
    deflection: float,
) -> list[NDArray[np.float32]]:
    """Extract analytic orthographic silhouette curves for Cylinder, Cone, Sphere, and Torus."""
    surf = BRepAdaptor_Surface(face)
    stype = surf.GetType()
    if stype not in _CURVED_SURFACE_TYPES:
        return []

    u_min, u_max, v_min, v_max = BRepTools.UVBounds_s(face)
    if not (
        math.isfinite(u_min)
        and math.isfinite(u_max)
        and math.isfinite(v_min)
        and math.isfinite(v_max)
    ):
        return []

    results: list[NDArray[np.float32]] = []

    if stype == GeomAbs_Cylinder:
        x_dir, y_dir, axis = _ax3_basis(surf.Cylinder().Position())
        if abs(float(np.dot(axis, v_dir))) > 1.0 - 1e-6:
            return []

        cross_vec = np.cross(axis, v_dir)
        cross_vec /= float(np.linalg.norm(cross_vec))
        u0 = math.atan2(float(np.dot(cross_vec, y_dir)), float(np.dot(cross_vec, x_dir)))

        for base_u in (u0, u0 + math.pi):
            for u_val in _angles_in_range(base_u, u_min, u_max):
                results.extend(
                    _sample_ruled_silhouette(face, surf, u_val, v_min, v_max, deflection)
                )
        return results

    if stype == GeomAbs_Cone:
        cone = surf.Cone()
        semi = float(cone.SemiAngle())
        x_dir, y_dir, axis = _ax3_basis(cone.Position())

        vx = float(np.dot(v_dir, x_dir))
        vy = float(np.dot(v_dir, y_dir))
        vz = float(np.dot(v_dir, axis))
        rho = math.hypot(vx, vy)
        if rho < 1e-9 or abs(math.cos(semi)) < 1e-9:
            return []

        rhs = math.tan(semi) * vz / rho
        if abs(rhs) >= 1.0 - 1e-7:
            return []

        phi = math.atan2(vy, vx)
        delta = math.acos(rhs)
        for base_u in (phi + delta, phi - delta):
            for u_val in _angles_in_range(base_u, u_min, u_max):
                results.extend(
                    _sample_ruled_silhouette(face, surf, u_val, v_min, v_max, deflection)
                )
        return results

    if stype == GeomAbs_Sphere:
        sph = surf.Sphere()
        center_pt = sph.Location()
        center = np.array([center_pt.X(), center_pt.Y(), center_pt.Z()], dtype=np.float64)
        radius = float(sph.Radius())

        ref = np.array([0.0, 0.0, 1.0], dtype=np.float64)
        if abs(float(np.dot(ref, v_dir))) > 0.9:
            ref = np.array([1.0, 0.0, 0.0], dtype=np.float64)
        e1 = np.cross(ref, v_dir)
        e1 /= float(np.linalg.norm(e1))
        e2 = np.cross(v_dir, e1)

        n_steps = _angular_steps_for_radius(radius, 2.0 * math.pi, deflection)
        uv_samples: list[tuple[float, float]] = []
        for k in range(n_steps):
            theta = (2.0 * math.pi * k) / n_steps
            pt = center + radius * (math.cos(theta) * e1 + math.sin(theta) * e2)
            u_raw, v_raw = ElSLib.Parameters_s(
                sph, gp_Pnt(float(pt[0]), float(pt[1]), float(pt[2]))
            )
            u_wrapped = _wrap_into_range(float(u_raw), u_min, u_max)
            u_use = u_wrapped if u_wrapped is not None else float(u_raw)
            uv_samples.append((u_use, float(v_raw)))

        return _trim_uv_curve(face, surf, uv_samples, closed=True)

    if stype == GeomAbs_Torus:
        tor = surf.Torus()
        major_r = float(tor.MajorRadius())
        minor_r = float(tor.MinorRadius())
        x_dir, y_dir, axis = _ax3_basis(tor.Position())

        vx = float(np.dot(v_dir, x_dir))
        vy = float(np.dot(v_dir, y_dir))
        vz = float(np.dot(v_dir, axis))

        u_span = u_max - u_min
        v_span = v_max - v_min
        u_closed = abs(u_span - 2.0 * math.pi) < 1e-4
        v_closed = abs(v_span - 2.0 * math.pi) < 1e-4

        if abs(vz) > 1e-6:
            n_u = _angular_steps_for_radius(major_r + minor_r, u_span, deflection)
            us = np.linspace(u_min, u_max, n_u, endpoint=not u_closed)
            branch_a: list[tuple[float, float]] = []
            branch_b: list[tuple[float, float]] = []
            for u_val in us:
                u_f = float(u_val)
                a_u = vx * math.cos(u_f) + vy * math.sin(u_f)
                v1 = math.atan2(-a_u, vz)
                v2 = v1 + math.pi
                w1 = _wrap_into_range(v1, v_min, v_max)
                w2 = _wrap_into_range(v2, v_min, v_max)
                branch_a.append((u_f, w1 if w1 is not None else v1))
                branch_b.append((u_f, w2 if w2 is not None else v2))
            results.extend(_trim_uv_curve(face, surf, branch_a, closed=u_closed))
            results.extend(_trim_uv_curve(face, surf, branch_b, closed=u_closed))
        else:
            # Viewing perpendicular to torus axis: horizontal outer/inner circles + vertical profiles.
            n_u = _angular_steps_for_radius(major_r + minor_r, u_span, deflection)
            us = np.linspace(u_min, u_max, n_u, endpoint=not u_closed)
            for base_v in (0.5 * math.pi, -0.5 * math.pi):
                for v_val in _angles_in_range(base_v, v_min, v_max):
                    uv_ring = [(float(u_f), v_val) for u_f in us]
                    results.extend(_trim_uv_curve(face, surf, uv_ring, closed=u_closed))

            n_v = _angular_steps_for_radius(minor_r, v_span, deflection)
            vs = np.linspace(v_min, v_max, n_v, endpoint=not v_closed)
            phi = math.atan2(vy, vx)
            for base_u in (phi + 0.5 * math.pi, phi - 0.5 * math.pi):
                for u_val in _angles_in_range(base_u, u_min, u_max):
                    uv_sec = [(u_val, float(v_f)) for v_f in vs]
                    results.extend(_trim_uv_curve(face, surf, uv_sec, closed=v_closed))

        return results

    return []


def sample_shape_edges(
    shape: Shape[Any] | TopoDS_Shape | Any,
    *,
    deflection: float = 0.05,
    look_from: Vec3 | None = None,
) -> SampledEdgeSet:
    """Extract discretized B-Rep edges and optional analytic silhouettes for GPU HLR.

    Args:
        shape: build123d ``Shape`` or OCCT ``TopoDS_Shape``.
        deflection: Chordal discretization tolerance in model units (mm).
        look_from: Optional orthographic camera direction ``(x, y, z)`` pointing from the
            model toward the camera (matching ``VIEW_VECTORS``). When provided, analytic
            silhouette lines/curves are generated for Cylinder, Cone, Sphere, and Torus
            faces, and B-Rep edges whose adjacent faces straddle the silhouette boundary
            are flagged ``is_silhouette_candidate=True``.
    """
    if deflection <= 0.0:
        raise ValueError(f"deflection must be positive, got {deflection}.")

    v_dir: NDArray[np.float64] | None = None
    if look_from is not None:
        raw_v = np.asarray(look_from, dtype=np.float64)
        norm_v = float(np.linalg.norm(raw_v))
        if norm_v < 1e-12:
            raise ValueError(f"look_from vector must be non-zero, got {look_from}.")
        v_dir = raw_v / norm_v

    parts = _extract_parts(shape)
    edges: list[SampledEdge] = []
    next_edge_id = 1
    face_id_offset = 0

    for _part_id, _part_name, part_topo in parts:
        face_map = IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
        TopExp.MapShapes_s(part_topo, TopAbs_FACE, face_map)
        n_faces = face_map.Extent()

        ef_map = IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher()
        TopExp.MapShapesAndAncestors_s(part_topo, TopAbs_EDGE, TopAbs_FACE, ef_map)

        # 1. Discretize all non-degenerated B-Rep edges with exact 1-based adjacent face IDs.
        for e_idx in range(1, ef_map.Extent() + 1):
            edge = TopoDS.Edge(ef_map.FindKey(e_idx))
            if BRep_Tool.Degenerated_s(edge):
                continue

            adj_faces: list[tuple[int, TopoDS_Face]] = []
            seen_ids: set[int] = set()
            for raw_face in ef_map.FindFromIndex(e_idx):
                local_fid = face_map.FindIndex(raw_face)
                if local_fid > 0:
                    global_fid = face_id_offset + local_fid
                    if global_fid not in seen_ids:
                        seen_ids.add(global_fid)
                        adj_faces.append((global_fid, TopoDS.Face(raw_face)))

            face_id_a = adj_faces[0][0] if len(adj_faces) >= 1 else 0
            face_id_b = adj_faces[1][0] if len(adj_faces) >= 2 else 0

            is_smooth = False
            is_silhouette_cand = False
            if len(adj_faces) >= 2:
                na = _face_normal_at_edge_mid(edge, adj_faces[0][1])
                nb = _face_normal_at_edge_mid(edge, adj_faces[1][1])
                if na is not None and nb is not None:
                    if float(np.dot(na, nb)) >= math.cos(math.radians(5.0)):
                        is_smooth = True
                    if v_dir is not None:
                        da = float(np.dot(na, v_dir))
                        db = float(np.dot(nb, v_dir))
                        # Straddles front-facing / back-facing boundary relative to camera.
                        if (da > 1e-6 and db < -1e-6) or (da < -1e-6 and db > 1e-6):
                            is_silhouette_cand = True
            elif len(adj_faces) == 1:
                if BRep_Tool.IsClosed_s(edge, adj_faces[0][1]):
                    # Periodic UV seam on a closed surface (cylinder/cone/sphere/torus).
                    is_smooth = True
                    is_silhouette_cand = False
                else:
                    # True open/boundary edge on an open shell or sheet.
                    is_silhouette_cand = True

            pts, geom_name = _discretize_edge(edge, deflection)
            if pts.shape[0] < 2:
                continue

            edges.append(
                SampledEdge(
                    edge_id=next_edge_id,
                    face_id_a=face_id_a,
                    face_id_b=face_id_b,
                    points_3d=pts,
                    is_silhouette_candidate=is_silhouette_cand,
                    is_smooth=is_smooth,
                    geom_type=geom_name,
                )
            )
            next_edge_id += 1

        # 2. Analytic silhouettes on curved faces when look_from is provided.
        if v_dir is not None:
            for local_fid in range(1, n_faces + 1):
                face = TopoDS.Face(face_map.FindKey(local_fid))
                global_fid = face_id_offset + local_fid
                sil_curves = _extract_analytic_silhouettes(face, v_dir, deflection)
                for pts in sil_curves:
                    if pts.shape[0] < 2:
                        continue
                    edges.append(
                        SampledEdge(
                            edge_id=next_edge_id,
                            face_id_a=global_fid,
                            face_id_b=0,
                            points_3d=pts,
                            is_silhouette_candidate=True,
                            is_smooth=False,
                            geom_type="silhouette",
                        )
                    )
                    next_edge_id += 1

        face_id_offset += n_faces

    if not edges:
        return SampledEdgeSet(
            edges=[],
            bbox_min=(0.0, 0.0, 0.0),
            bbox_max=(0.0, 0.0, 0.0),
            num_faces=face_id_offset,
        )

    all_min = np.min(np.stack([e.points_3d.min(axis=0) for e in edges], axis=0), axis=0)
    all_max = np.max(np.stack([e.points_3d.max(axis=0) for e in edges], axis=0), axis=0)
    return SampledEdgeSet(
        edges=edges,
        bbox_min=(float(all_min[0]), float(all_min[1]), float(all_min[2])),
        bbox_max=(float(all_max[0]), float(all_max[1]), float(all_max[2])),
        num_faces=face_id_offset,
    )
