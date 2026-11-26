"""Interactive self-contained HTML5 blueprint renderer with live WebGL2 3D CAD viewport.

Combines a full dual-theme 2D ``DrawingSheet`` SVG with an embedded, zero-CDN WebGL2 3D
CAD inspector synchronized to the sheet's theme, assembly BOM, and camera conventions.
"""

from __future__ import annotations

import base64
import html
import json
import math
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import build123d as bd
import numpy as np
from build123d.topology import Shape
from numpy.typing import NDArray
from OCP.BRep import BRep_Tool
from OCP.BRepGProp import BRepGProp_Face
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.gp import gp_Pnt, gp_Vec
from OCP.Poly import Poly_Triangulation
from OCP.TopAbs import TopAbs_REVERSED
from OCP.TopLoc import TopLoc_Location

from build123d_techdraw.core.part_info import get_part_info
from build123d_techdraw.core.view import ViewType
from build123d_techdraw.renderers.theme import THEME_COLORS, Theme, ThemeColors

if TYPE_CHECKING:
    from build123d_techdraw.core.sheet import DrawingSheet

_XML_DECL_RE = re.compile(r"^\s*<\?xml[^?]*\?>\s*", re.IGNORECASE)
_ISO_VIEW_TYPES: frozenset[ViewType] = frozenset(
    {ViewType.ISOMETRIC, ViewType.ISOMETRIC_LEFT, ViewType.ISOMETRIC_REAR}
)
_DERIVED_VIEW_TYPES: frozenset[ViewType] = frozenset({ViewType.SECTION, ViewType.DETAIL})


@dataclass(frozen=True)
class SerializedPartMesh:
    """Compact base64-encoded triangle + sharp crease-edge payload for one 3D component."""

    part_id: int
    name: str
    bom_item_no: int | None
    part_no: str
    material: str
    qty: int
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    center: tuple[float, float, float]
    explode_vector: tuple[float, float, float]
    vertex_count: int
    triangle_count: int
    edge_segment_count: int
    positions_b64: str
    normals_b64: str
    indices_b64: str
    edges_b64: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize part metadata and base64 mesh buffers into a JSON-ready dictionary."""
        return {
            "id": self.part_id,
            "name": self.name,
            "bom_item_no": self.bom_item_no,
            "part_no": self.part_no,
            "material": self.material,
            "qty": self.qty,
            "bbox_min": [round(v, 4) for v in self.bbox_min],
            "bbox_max": [round(v, 4) for v in self.bbox_max],
            "center": [round(v, 4) for v in self.center],
            "explode_vector": [round(v, 4) for v in self.explode_vector],
            "vertex_count": self.vertex_count,
            "triangle_count": self.triangle_count,
            "edge_segment_count": self.edge_segment_count,
            "positions_b64": self.positions_b64,
            "normals_b64": self.normals_b64,
            "indices_b64": self.indices_b64,
            "edges_b64": self.edges_b64,
        }


def _coerce_theme(theme: Theme | str) -> Theme:
    if isinstance(theme, Theme):
        return theme
    return (
        Theme.ISO_WHITE
        if theme.strip().lower() in ("print", "white", "iso_white")
        else Theme.DARK_BLUEPRINT
    )


def _extract_components(shape: Shape[Any]) -> list[Shape[Any]]:
    """Recursively extract leaf assembly components, or solids for a single shape."""
    if shape.children:
        parts: list[Shape[Any]] = []
        for child in shape.children:
            if child.children:
                parts.extend(_extract_components(child))
            else:
                parts.append(child)
        return parts
    solids = shape.solids()
    return list(solids) if solids else [shape]


def _find_sheet_shape_and_iso_bounds(
    sheet: DrawingSheet,
    override_shape: Shape[Any] | None = None,
) -> tuple[Shape[Any] | None, list[float] | None]:
    """Select the primary 3D shape from sheet views, preferring an isometric view."""
    iso_bounds: list[float] | None = None
    iso_shape: Shape[Any] | None = None
    primary_shape: Shape[Any] | None = None
    fallback_shape: Shape[Any] | None = None

    for view in sheet.views:
        if view.view_type in _ISO_VIEW_TYPES:
            if iso_bounds is None:
                iso_bounds = [
                    round(view.min_x, 2),
                    round(view.min_y, 2),
                    round(view.max_x, 2),
                    round(view.max_y, 2),
                ]
            if iso_shape is None and isinstance(view.shape, Shape):
                iso_shape = view.shape
        elif isinstance(view.shape, Shape):
            if view.view_type not in _DERIVED_VIEW_TYPES and primary_shape is None:
                primary_shape = view.shape
            if fallback_shape is None:
                fallback_shape = view.shape

    if override_shape is not None:
        return override_shape, iso_bounds
    return iso_shape or primary_shape or fallback_shape, iso_bounds


def _match_component_bom(
    component: Shape[Any],
    sheet: DrawingSheet,
    part_index: int,
    total_parts: int,
) -> tuple[int | None, str, str, str, int]:
    """Resolve (bom_item_no, part_no, name, material, qty) for a component."""
    label = component.label or ""
    info = get_part_info(component)
    comp_part_no = info.part_no if info is not None else ""
    if info is not None and info.material_spec:
        comp_material = info.material_spec
    elif component.material is not None:
        comp_material = component.material.name.replace("_", " ").upper()
    else:
        comp_material = "STANDARD"

    match_name = label or type(component).__name__

    if sheet.bom_items:
        if comp_part_no:
            for item in sheet.bom_items:
                if item.part_no == comp_part_no:
                    return (item.item_no, item.part_no, item.description, item.material, item.qty)
        for item in sheet.bom_items:
            if item.description == match_name:
                return (item.item_no, item.part_no, item.description, item.material, item.qty)
        match_lower = match_name.lower()
        for item in sheet.bom_items:
            if item.description.lower() == match_lower:
                return (item.item_no, item.part_no, item.description, item.material, item.qty)

    default_name = (
        label
        or (sheet.title_info.drawing_title if total_parts == 1 else f"Part {part_index + 1}")
        or f"Part {part_index + 1}"
    )
    return (
        None,
        comp_part_no or f"PART-{part_index + 1:03d}",
        default_name,
        comp_material,
        1,
    )


def _compute_area_weighted_normals(
    positions: NDArray[np.float32],
    indices: NDArray[np.uint32],
) -> NDArray[np.float32]:
    """Compute smooth area-weighted vertex normals for a single face triangulation."""
    normals = np.zeros_like(positions, dtype=np.float64)
    if indices.size == 0:
        return normals.astype(np.float32)

    tri_idx = indices.reshape(-1, 3)
    p0 = positions[tri_idx[:, 0]].astype(np.float64)
    p1 = positions[tri_idx[:, 1]].astype(np.float64)
    p2 = positions[tri_idx[:, 2]].astype(np.float64)
    face_normals = np.cross(p1 - p0, p2 - p0)

    np.add.at(normals, tri_idx[:, 0], face_normals)
    np.add.at(normals, tri_idx[:, 1], face_normals)
    np.add.at(normals, tri_idx[:, 2], face_normals)

    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    valid = lengths[:, 0] > 1e-12
    normals[valid] /= lengths[valid]
    normals[~valid] = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    return normals.astype(np.float32)


def _tessellate_component(
    component: Shape[Any],
    *,
    linear_deflection: float,
    angular_deflection: float,
    crease_angle_deg: float,
) -> tuple[
    NDArray[np.float32],
    NDArray[np.float32],
    NDArray[np.uint32],
    NDArray[np.float32],
]:
    """Tessellate one build123d shape into triangles, exact CAD normals, and sharp crease edges."""
    mesher = BRepMesh_IncrementalMesh(
        component.wrapped,
        float(linear_deflection),
        False,
        float(angular_deflection),
        True,
    )
    mesher.Perform()

    pos_chunks: list[NDArray[np.float32]] = []
    norm_chunks: list[NDArray[np.float32]] = []
    idx_chunks: list[NDArray[np.uint32]] = []
    vertex_offset = 0

    face_tri_map: dict[int, tuple[Poly_Triangulation, TopLoc_Location]] = {}
    edge_to_faces: dict[bd.Edge, list[tuple[int, bd.Face]]] = {}

    faces = list(component.faces())
    for f_idx, face in enumerate(faces):
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face.wrapped, loc)
        if tri is None:
            continue
        nb_nodes = tri.NbNodes()
        nb_tris = tri.NbTriangles()
        if nb_nodes == 0 or nb_tris == 0:
            continue

        face_tri_map[f_idx] = (tri, loc)
        trsf = loc.Transformation()
        reversed_face = face.wrapped.Orientation() == TopAbs_REVERSED

        face_pos = np.empty((nb_nodes, 3), dtype=np.float32)
        for k in range(1, nb_nodes + 1):
            pt = tri.Node(k).Transformed(trsf)
            face_pos[k - 1, 0] = pt.X()
            face_pos[k - 1, 1] = pt.Y()
            face_pos[k - 1, 2] = pt.Z()

        face_idx = np.empty((nb_tris, 3), dtype=np.uint32)
        for t_i in range(1, nb_tris + 1):
            i1, i2, i3 = tri.Triangle(t_i).Get()
            if reversed_face:
                face_idx[t_i - 1, 0] = i1 - 1
                face_idx[t_i - 1, 1] = i3 - 1
                face_idx[t_i - 1, 2] = i2 - 1
            else:
                face_idx[t_i - 1, 0] = i1 - 1
                face_idx[t_i - 1, 1] = i2 - 1
                face_idx[t_i - 1, 2] = i3 - 1

        # Start from area-weighted normals, then replace with exact analytical OCCT
        # surface normals wherever valid (preserves seamless curvature across cylinder seams
        # while avoiding NaN poles on cones/spheres).
        face_norm = _compute_area_weighted_normals(face_pos, face_idx)
        if tri.HasUVNodes():
            prop = BRepGProp_Face(face.wrapped)
            p_tmp = gp_Pnt()
            n_tmp = gp_Vec()
            for k in range(1, nb_nodes + 1):
                uv = tri.UVNode(k)
                prop.Normal(uv.X(), uv.Y(), p_tmp, n_tmp)
                mag = n_tmp.Magnitude()
                if mag > 1e-12:
                    inv = 1.0 / mag
                    face_norm[k - 1, 0] = n_tmp.X() * inv
                    face_norm[k - 1, 1] = n_tmp.Y() * inv
                    face_norm[k - 1, 2] = n_tmp.Z() * inv

        pos_chunks.append(face_pos)
        norm_chunks.append(face_norm)
        idx_chunks.append(face_idx + np.uint32(vertex_offset))
        vertex_offset += nb_nodes

        for edge in face.edges():
            edge_to_faces.setdefault(edge, []).append((f_idx, face))

    edge_segments = _extract_sharp_crease_edges(
        edge_to_faces=edge_to_faces,
        face_tri_map=face_tri_map,
        crease_angle_deg=crease_angle_deg,
    )

    if pos_chunks:
        positions = np.ascontiguousarray(np.vstack(pos_chunks), dtype="<f4")
        normals = np.ascontiguousarray(np.vstack(norm_chunks), dtype="<f4")
        indices = np.ascontiguousarray(np.vstack(idx_chunks).reshape(-1), dtype="<u4")
    else:
        positions = np.empty((0, 3), dtype="<f4")
        normals = np.empty((0, 3), dtype="<f4")
        indices = np.empty((0,), dtype="<u4")

    return positions, normals, indices, edge_segments


def _extract_sharp_crease_edges(
    edge_to_faces: dict[bd.Edge, list[tuple[int, bd.Face]]],
    face_tri_map: dict[int, tuple[Poly_Triangulation, TopLoc_Location]],
    crease_angle_deg: float,
) -> NDArray[np.float32]:
    """Extract 3D line-segment pairs for boundary and sharp crease B-Rep edges."""
    cos_thresh = math.cos(math.radians(crease_angle_deg))
    seg_coords: list[tuple[float, float, float, float, float, float]] = []

    for edge, face_entries in edge_to_faces.items():
        if BRep_Tool.Degenerated_s(edge.wrapped):
            continue

        is_crease = False
        if len(face_entries) == 1:
            # Exclude closed surface seam edges; retain true open-shell boundary edges.
            is_crease = not BRep_Tool.IsClosed_s(edge.wrapped, face_entries[0][1].wrapped)
        elif len(face_entries) > 2:
            is_crease = True
        else:
            f1 = face_entries[0][1]
            f2 = face_entries[1][1]
            if not f1.wrapped.IsSame(f2.wrapped):
                is_crease = _faces_form_crease(edge, f1, f2, cos_thresh)

        if not is_crease:
            continue

        pts = _sample_edge_on_triangulation(edge, face_entries, face_tri_map)
        for i in range(len(pts) - 1):
            p0 = pts[i]
            p1 = pts[i + 1]
            seg_coords.append((p0[0], p0[1], p0[2], p1[0], p1[1], p1[2]))

    if not seg_coords:
        return np.empty((0, 6), dtype="<f4")
    return np.ascontiguousarray(np.asarray(seg_coords, dtype="<f4"))


def _faces_form_crease(
    edge: bd.Edge,
    f1: bd.Face,
    f2: bd.Face,
    cos_thresh: float,
) -> bool:
    """Return True when two adjacent faces meet at an angle sharper than cos_thresh."""
    for u in (0.5, 0.25):
        try:
            pt = edge.position_at(u)
            n1 = f1.normal_at(pt)
            n2 = f2.normal_at(pt)
            if float(n1.dot(n2)) < cos_thresh:
                return True
        except Exception:
            return True
    return False


def _sample_edge_on_triangulation(
    edge: bd.Edge,
    face_entries: list[tuple[int, bd.Face]],
    face_tri_map: dict[int, tuple[Poly_Triangulation, TopLoc_Location]],
) -> list[tuple[float, float, float]]:
    """Extract exact polyline vertices matching the face triangulation when available."""
    for f_idx, _face in face_entries:
        tri_pair = face_tri_map.get(f_idx)
        if tri_pair is None:
            continue
        tri, loc = tri_pair
        pot = BRep_Tool.PolygonOnTriangulation_s(edge.wrapped, tri, loc)
        if pot is not None and pot.NbNodes() >= 2:
            trsf = loc.Transformation()
            nodes = pot.Nodes()
            pts: list[tuple[float, float, float]] = []
            for idx in range(nodes.Lower(), nodes.Upper() + 1):
                p = tri.Node(nodes.Value(idx)).Transformed(trsf)
                pts.append((float(p.X()), float(p.Y()), float(p.Z())))
            return pts

    # Fallback: direct curve discretization if PolygonOnTriangulation is absent.
    n_samples = 2 if edge.geom_type == bd.GeomType.LINE else 24
    params = [i / (n_samples - 1) for i in range(n_samples)]
    return [(float(v.X), float(v.Y), float(v.Z)) for v in edge.positions(params)]


def _compute_explode_vectors(
    bboxes: list[tuple[tuple[float, float, float], tuple[float, float, float]]],
    asm_min: tuple[float, float, float],
    asm_max: tuple[float, float, float],
) -> list[tuple[float, float, float]]:
    """Compute smooth radial/axial explosion offset vectors for multi-part assemblies."""
    n_parts = len(bboxes)
    if n_parts <= 1:
        return [(0.0, 0.0, 0.0)] * n_parts

    asm_cx = 0.5 * (asm_min[0] + asm_max[0])
    asm_cy = 0.5 * (asm_min[1] + asm_max[1])
    asm_cz = 0.5 * (asm_min[2] + asm_max[2])
    dx_a = asm_max[0] - asm_min[0]
    dy_a = asm_max[1] - asm_min[1]
    dz_a = asm_max[2] - asm_min[2]
    asm_radius = max(0.5 * math.sqrt(dx_a * dx_a + dy_a * dy_a + dz_a * dz_a), 1.0)

    centers: list[tuple[float, float, float]] = []
    volumes: list[float] = []
    extents: list[tuple[float, float, float]] = []
    for bmin, bmax in bboxes:
        cx = 0.5 * (bmin[0] + bmax[0])
        cy = 0.5 * (bmin[1] + bmax[1])
        cz = 0.5 * (bmin[2] + bmax[2])
        ex = max(bmax[0] - bmin[0], 1e-4)
        ey = max(bmax[1] - bmin[1], 1e-4)
        ez = max(bmax[2] - bmin[2], 1e-4)
        centers.append((cx, cy, cz))
        extents.append((ex, ey, ez))
        volumes.append(ex * ey * ez)

    # Keep the dominant central body (e.g., main housing/baseplate) anchored when clearly
    # larger than other components so fasteners, shafts, and bushings separate cleanly around it.
    ranked = sorted(range(n_parts), key=lambda i: volumes[i], reverse=True)
    anchor_idx: int | None = None
    top_idx = ranked[0]
    top_dist = math.dist(centers[top_idx], (asm_cx, asm_cy, asm_cz))
    if volumes[top_idx] > 1.35 * volumes[ranked[1]] and top_dist < 0.4 * asm_radius:
        anchor_idx = top_idx

    concentric_count = 0
    vectors: list[tuple[float, float, float]] = []
    for i in range(n_parts):
        if i == anchor_idx:
            vectors.append((0.0, 0.0, 0.0))
            continue

        cx, cy, cz = centers[i]
        vx, vy, vz = cx - asm_cx, cy - asm_cy, cz - asm_cz
        dist = math.sqrt(vx * vx + vy * vy + vz * vz)

        if dist > 0.06 * asm_radius:
            ux, uy, uz = vx / dist, vy / dist, vz / dist
            mag = 0.25 * asm_radius + 1.15 * dist
            vectors.append((ux * mag, uy * mag, uz * mag))
        else:
            # Concentric component (e.g. central drive shaft): explode cleanly along its major axis.
            ex, ey, ez = extents[i]
            sign = 1.0 if (concentric_count % 2 == 0) else -1.0
            concentric_count += 1
            mag = 0.85 * asm_radius
            if ex >= ey and ex >= ez:
                vectors.append((sign * mag, 0.0, 0.0))
            elif ey >= ex and ey >= ez:
                vectors.append((0.0, sign * mag, 0.0))
            else:
                vectors.append((0.0, 0.0, sign * mag))

    return vectors


def _theme_colors_dict(colors: ThemeColors) -> dict[str, str]:
    return {
        "bg": colors.bg,
        "border": colors.border,
        "border_sub": colors.border_sub,
        "title_bg": colors.title_bg,
        "text": colors.text,
        "text_muted": colors.text_muted,
        "text_accent": colors.text_accent,
        "line_visible": colors.line_visible,
        "line_hidden": colors.line_hidden,
        "line_center": colors.line_center,
        "line_section": colors.line_section,
        "hatch": colors.hatch,
        "dim_line": colors.dim_line,
        "balloon_border": colors.balloon_border,
        "table_header_bg": colors.table_header_bg,
    }


def _strip_xml_declaration(svg_str: str) -> str:
    return _XML_DECL_RE.sub("", svg_str, count=1).strip()


def _escape_json_for_html_script(payload: dict[str, Any]) -> str:
    """Serialize JSON safely for embedding inside a <script type="application/json"> tag."""
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    return raw.replace("</", "<\\/")


class HTMLRenderer:
    """Renders DrawingSheet instances to self-contained interactive 3D-in-2D HTML5 blueprints."""

    @classmethod
    def serialize_sheet_scene(
        cls,
        sheet: DrawingSheet,
        theme: Theme | str = Theme.DARK_BLUEPRINT,
        *,
        shape: Shape[Any] | None = None,
        linear_deflection: float = 0.08,
        angular_deflection: float = 0.25,
        crease_angle_deg: float = 20.0,
    ) -> dict[str, Any]:
        """Tessellate and serialize the sheet's 3D model, parts, BOM, and theme metadata."""
        resolved_theme = _coerce_theme(theme)
        target_shape, iso_bounds = _find_sheet_shape_and_iso_bounds(sheet, override_shape=shape)

        raw_parts: list[
            tuple[
                Shape[Any],
                NDArray[np.float32],
                NDArray[np.float32],
                NDArray[np.uint32],
                NDArray[np.float32],
                tuple[float, float, float],
                tuple[float, float, float],
            ]
        ] = []

        if target_shape is not None:
            components = _extract_components(target_shape)
            for comp in components:
                positions, normals, indices, edges = _tessellate_component(
                    comp,
                    linear_deflection=linear_deflection,
                    angular_deflection=angular_deflection,
                    crease_angle_deg=crease_angle_deg,
                )
                if positions.shape[0] == 0 or indices.size == 0:
                    continue
                p_min = positions.min(axis=0)
                p_max = positions.max(axis=0)
                bmin = (float(p_min[0]), float(p_min[1]), float(p_min[2]))
                bmax = (float(p_max[0]), float(p_max[1]), float(p_max[2]))
                raw_parts.append((comp, positions, normals, indices, edges, bmin, bmax))

        if raw_parts:
            asm_min = (
                min(p[5][0] for p in raw_parts),
                min(p[5][1] for p in raw_parts),
                min(p[5][2] for p in raw_parts),
            )
            asm_max = (
                max(p[6][0] for p in raw_parts),
                max(p[6][1] for p in raw_parts),
                max(p[6][2] for p in raw_parts),
            )
            asm_center = (
                0.5 * (asm_min[0] + asm_max[0]),
                0.5 * (asm_min[1] + asm_max[1]),
                0.5 * (asm_min[2] + asm_max[2]),
            )
            asm_radius = max(
                0.5 * math.dist(asm_min, asm_max),
                1.0,
            )
        else:
            asm_min = (-10.0, -10.0, -10.0)
            asm_max = (10.0, 10.0, 10.0)
            asm_center = (0.0, 0.0, 0.0)
            asm_radius = 15.0

        bboxes = [(p[5], p[6]) for p in raw_parts]
        explode_vectors = _compute_explode_vectors(bboxes, asm_min, asm_max)

        serialized_parts: list[SerializedPartMesh] = []
        total_parts = len(raw_parts)
        for idx, (comp, positions, normals, indices, edges, bmin, bmax) in enumerate(raw_parts):
            bom_item_no, part_no, name, material, qty = _match_component_bom(
                comp, sheet, idx, total_parts
            )
            center = (
                0.5 * (bmin[0] + bmax[0]),
                0.5 * (bmin[1] + bmax[1]),
                0.5 * (bmin[2] + bmax[2]),
            )
            serialized_parts.append(
                SerializedPartMesh(
                    part_id=idx,
                    name=name,
                    bom_item_no=bom_item_no,
                    part_no=part_no,
                    material=material,
                    qty=qty,
                    bbox_min=bmin,
                    bbox_max=bmax,
                    center=center,
                    explode_vector=explode_vectors[idx],
                    vertex_count=int(positions.shape[0]),
                    triangle_count=int(indices.size // 3),
                    edge_segment_count=int(edges.shape[0]),
                    positions_b64=base64.b64encode(positions.tobytes()).decode("ascii"),
                    normals_b64=base64.b64encode(normals.tobytes()).decode("ascii"),
                    indices_b64=base64.b64encode(indices.tobytes()).decode("ascii"),
                    edges_b64=base64.b64encode(edges.tobytes()).decode("ascii"),
                )
            )

        bom_list = [
            {
                "item_no": item.item_no,
                "qty": item.qty,
                "part_no": item.part_no,
                "description": item.description,
                "material": item.material,
            }
            for item in sheet.bom_items
        ]

        return {
            "version": "1.0",
            "title": sheet.title_info.drawing_title,
            "drawing_no": sheet.title_info.drawing_no,
            "rev": sheet.title_info.rev,
            "scale": sheet.title_info.scale,
            "initial_theme": resolved_theme.value,
            "sheet_width_mm": round(sheet.width_mm, 2),
            "sheet_height_mm": round(sheet.height_mm, 2),
            "iso_view_bounds": iso_bounds,
            "bbox_min": [round(v, 4) for v in asm_min],
            "bbox_max": [round(v, 4) for v in asm_max],
            "center": [round(v, 4) for v in asm_center],
            "radius": round(asm_radius, 4),
            "bom_items": bom_list,
            "parts": [p.to_dict() for p in serialized_parts],
            "themes": {
                Theme.DARK_BLUEPRINT.value: _theme_colors_dict(THEME_COLORS[Theme.DARK_BLUEPRINT]),
                Theme.ISO_WHITE.value: _theme_colors_dict(THEME_COLORS[Theme.ISO_WHITE]),
            },
        }

    @classmethod
    def render_sheet(
        cls,
        sheet: DrawingSheet,
        theme: Theme | str = Theme.DARK_BLUEPRINT,
        *,
        shape: Shape[Any] | None = None,
        linear_deflection: float = 0.08,
        angular_deflection: float = 0.25,
        crease_angle_deg: float = 20.0,
        title: str | None = None,
        initial_layout: str = "split",
    ) -> str:
        """Render a complete, self-contained HTML5 document string with 2D SVG + 3D WebGL2."""
        resolved_theme = _coerce_theme(theme)
        layout_mode = "inspector" if initial_layout == "inspector" else "split"
        svg_inline = _strip_xml_declaration(sheet.to_svg(theme=resolved_theme))
        scene_data = cls.serialize_sheet_scene(
            sheet,
            theme=resolved_theme,
            shape=shape,
            linear_deflection=linear_deflection,
            angular_deflection=angular_deflection,
            crease_angle_deg=crease_angle_deg,
        )
        scene_data["initial_layout"] = layout_mode

        page_title = title or f"{sheet.title_info.drawing_no} — {sheet.title_info.drawing_title}"
        doc_title_esc = html.escape(page_title)
        drawing_title_esc = html.escape(sheet.title_info.drawing_title)
        drawing_no_esc = html.escape(sheet.title_info.drawing_no)
        sheet_dim_badge = f"{sheet.width_mm:.0f} × {sheet.height_mm:.0f} mm"
        scene_json = _escape_json_for_html_script(scene_data)

        theme_attr = "iso-white" if resolved_theme == Theme.ISO_WHITE else "dark-blueprint"
        default_shading = "iso" if resolved_theme == Theme.ISO_WHITE else "blueprint"

        return _build_html_document(
            doc_title=doc_title_esc,
            drawing_title=drawing_title_esc,
            drawing_no=drawing_no_esc,
            sheet_dim_badge=sheet_dim_badge,
            theme_attr=theme_attr,
            layout_mode=layout_mode,
            default_shading=default_shading,
            svg_inline=svg_inline,
            scene_json=scene_json,
        )

    @classmethod
    def save(
        cls,
        sheet: DrawingSheet,
        output_path: str | os.PathLike[str],
        theme: Theme | str = Theme.DARK_BLUEPRINT,
        *,
        shape: Shape[Any] | None = None,
        linear_deflection: float = 0.08,
        angular_deflection: float = 0.25,
        crease_angle_deg: float = 20.0,
        title: str | None = None,
        initial_layout: str = "split",
    ) -> None:
        """Render and write a self-contained interactive 3D-in-2D HTML blueprint file."""
        abs_path = os.path.abspath(os.fspath(output_path))
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        html_doc = cls.render_sheet(
            sheet,
            theme=theme,
            shape=shape,
            linear_deflection=linear_deflection,
            angular_deflection=angular_deflection,
            crease_angle_deg=crease_angle_deg,
            title=title,
            initial_layout=initial_layout,
        )
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(html_doc)


def render_interactive_html(
    sheet: DrawingSheet,
    theme: Theme | str = Theme.DARK_BLUEPRINT,
    *,
    shape: Shape[Any] | None = None,
    linear_deflection: float = 0.08,
    angular_deflection: float = 0.25,
    crease_angle_deg: float = 20.0,
    title: str | None = None,
    initial_layout: str = "split",
) -> str:
    """Render a DrawingSheet to a self-contained interactive HTML5 document string."""
    return HTMLRenderer.render_sheet(
        sheet,
        theme=theme,
        shape=shape,
        linear_deflection=linear_deflection,
        angular_deflection=angular_deflection,
        crease_angle_deg=crease_angle_deg,
        title=title,
        initial_layout=initial_layout,
    )


def export_interactive_html(
    sheet: DrawingSheet,
    output_path: str | os.PathLike[str],
    theme: Theme | str = Theme.DARK_BLUEPRINT,
    *,
    shape: Shape[Any] | None = None,
    linear_deflection: float = 0.08,
    angular_deflection: float = 0.25,
    crease_angle_deg: float = 20.0,
    title: str | None = None,
    initial_layout: str = "split",
) -> None:
    """Export a DrawingSheet to a self-contained interactive HTML5 blueprint file."""
    HTMLRenderer.save(
        sheet,
        output_path,
        theme=theme,
        shape=shape,
        linear_deflection=linear_deflection,
        angular_deflection=angular_deflection,
        crease_angle_deg=crease_angle_deg,
        title=title,
        initial_layout=initial_layout,
    )


def _build_html_document(
    *,
    doc_title: str,
    drawing_title: str,
    drawing_no: str,
    sheet_dim_badge: str,
    theme_attr: str,
    layout_mode: str,
    default_shading: str,
    svg_inline: str,
    scene_json: str,
) -> str:
    """Assemble the standalone HTML5 template (zero external CDN requests)."""
    c_dark = THEME_COLORS[Theme.DARK_BLUEPRINT]
    c_white = THEME_COLORS[Theme.ISO_WHITE]

    css = _VIEWER_CSS.format(
        dark_bg=c_dark.bg,
        dark_border=c_dark.border,
        dark_border_sub=c_dark.border_sub,
        dark_text=c_dark.text,
        dark_muted=c_dark.text_muted,
        dark_accent=c_dark.text_accent,
        dark_section=c_dark.line_section,
        dark_header=c_dark.table_header_bg,
        white_bg=c_white.bg,
        white_border=c_white.border,
        white_border_sub=c_white.border_sub,
        white_text=c_white.text,
        white_muted=c_white.text_muted,
        white_accent=c_white.text_accent,
        white_section=c_white.line_section,
        white_header=c_white.table_header_bg,
    )

    is_split = layout_mode == "split"
    btn_split_cls = "seg-btn active" if is_split else "seg-btn"
    btn_insp_cls = "seg-btn active" if not is_split else "seg-btn"
    ws_layout_cls = "workspace layout-split" if is_split else "workspace layout-inspector"

    return f"""<!DOCTYPE html>
<html lang="en" data-theme="{theme_attr}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{doc_title}</title>
  <style>{css}</style>
</head>
<body>
  <div class="app-shell">
    <header class="topbar">
      <div class="brand-group">
        <span class="brand-mark" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8">
            <polygon points="12,2 21,7 21,17 12,22 3,17 3,7"/>
            <polyline points="3,7 12,12 21,7"/>
            <line x1="12" y1="12" x2="12" y2="22"/>
          </svg>
        </span>
        <div class="title-stack">
          <span class="doc-title">{drawing_title}</span>
          <span class="doc-meta">
            <span class="meta-pill">{drawing_no}</span>
            <span class="meta-sep">·</span>
            <span>{sheet_dim_badge}</span>
          </span>
        </div>
      </div>

      <div class="toolbar-center">
        <div class="seg-group" role="group" aria-label="Workspace Layout">
          <button type="button" id="btn-layout-split" class="{btn_split_cls}" data-layout="split">Sheet + 3D Dock</button>
          <button type="button" id="btn-layout-inspector" class="{btn_insp_cls}" data-layout="inspector">Expanded 3D Inspector</button>
        </div>

        <div class="seg-group" role="group" aria-label="3D Shading Mode">
          <button type="button" class="seg-btn{" active" if default_shading == "blueprint" else ""}" data-shading="blueprint" title="Gooch Blueprint Shading + Glowing Crease Edges">Blueprint</button>
          <button type="button" class="seg-btn{" active" if default_shading == "iso" else ""}" data-shading="iso" title="Clean Technical Illustration Shading + Ink Edges">ISO Shaded</button>
          <button type="button" class="seg-btn" data-shading="xray" title="Fresnel Translucent CAD X-Ray Inspection">X-Ray</button>
          <button type="button" class="seg-btn" data-shading="wireframe" title="3D Hidden-Line Technical Wireframe">Wireframe</button>
        </div>
      </div>

      <div class="toolbar-right">
        <button type="button" id="btn-theme-toggle" class="action-btn theme-btn" title="Switch synchronized 2D + 3D blueprint theme">
          <span class="theme-dot" aria-hidden="true"></span>
          <span id="theme-toggle-label">{"ISO White" if theme_attr == "iso-white" else "Dark Blueprint"}</span>
        </button>
      </div>
    </header>

    <main id="workspace" class="{ws_layout_cls}">
      <section class="pane pane-2d" id="pane-2d" aria-label="2D Technical Blueprint Sheet">
        <div class="pane-hud hud-top-left">
          <span class="hud-badge">2D SHEET</span>
          <div class="mini-btn-group">
            <button type="button" id="btn-svg-zoom-in" class="mini-btn" title="Zoom in 2D sheet">+</button>
            <button type="button" id="btn-svg-zoom-out" class="mini-btn" title="Zoom out 2D sheet">−</button>
            <button type="button" id="btn-svg-fit" class="mini-btn" title="Fit sheet to viewport">Fit</button>
          </div>
          <span id="svg-zoom-readout" class="hud-mono">100%</span>
        </div>
        <div class="svg-viewport" id="svg-viewport">
          <div class="svg-stage" id="svg-stage">
            {svg_inline}
          </div>
        </div>
      </section>

      <div class="split-divider" id="split-divider" role="separator" aria-orientation="vertical" aria-label="Resize panes"></div>

      <section class="pane pane-3d" id="pane-3d" aria-label="Interactive 3D CAD Viewport">
        <div class="viewport-3d-wrap" id="viewport-3d-wrap">
          <canvas id="cad-canvas"></canvas>

          <div class="pane-hud hud-top-left">
            <span class="hud-badge hud-badge-accent">3D CAD</span>
            <div class="mini-btn-group" role="group" aria-label="Camera Presets">
              <button type="button" class="mini-btn" data-cam="iso">ISO</button>
              <button type="button" class="mini-btn" data-cam="front">FRONT</button>
              <button type="button" class="mini-btn" data-cam="top">TOP</button>
              <button type="button" class="mini-btn" data-cam="right">RIGHT</button>
              <button type="button" class="mini-btn" data-cam="reset">RESET</button>
            </div>
            <button type="button" id="btn-proj-toggle" class="mini-btn" title="Toggle Perspective / Orthographic projection">PERSP</button>
          </div>

          <div class="triad-box" aria-hidden="true">
            <canvas id="triad-canvas" width="72" height="72"></canvas>
          </div>

          <div class="pane-hud hud-bottom-left" id="hud-3d-status">
            <span id="hud-part-readout" class="hud-mono">Orbit: drag · Pan: shift+drag · Zoom: scroll</span>
          </div>

          <div class="webgl-fallback" id="webgl-fallback" hidden>
            <canvas id="fallback-2d-canvas"></canvas>
            <div class="fallback-banner" id="fallback-banner">
              <strong>WebGL2 Unavailable</strong> — Showing software 2D wireframe fallback. Open in a WebGL2-enabled browser for full shaded shaders &amp; live section cuts.
            </div>
          </div>
        </div>

        <aside class="inspector-panel" id="inspector-panel">
          <div class="control-block">
            <div class="control-head">
              <span class="control-title">SECTION PLANE</span>
              <span id="section-readout" class="hud-mono">OFF</span>
            </div>
            <div class="section-row">
              <div class="mini-btn-group" role="group" aria-label="Section Plane Axis">
                <button type="button" class="mini-btn active" data-sec-axis="off">OFF</button>
                <button type="button" class="mini-btn" data-sec-axis="x">X</button>
                <button type="button" class="mini-btn" data-sec-axis="y">Y</button>
                <button type="button" class="mini-btn" data-sec-axis="z">Z</button>
              </div>
              <button type="button" id="btn-sec-flip" class="mini-btn" title="Flip section cut side" disabled>± Flip</button>
            </div>
            <input type="range" id="slider-section" class="cad-slider" min="0" max="100" step="0.5" value="50" disabled aria-label="Section Plane Position">
          </div>

          <div class="control-block" id="explode-block">
            <div class="control-head">
              <span class="control-title">ASSEMBLY EXPLODE</span>
              <span id="explode-readout" class="hud-mono">0%</span>
            </div>
            <input type="range" id="slider-explode" class="cad-slider" min="0" max="100" step="1" value="0" aria-label="Assembly Explode Factor">
          </div>

          <div class="control-block parts-block">
            <div class="control-head">
              <span class="control-title">PARTS &amp; BOM <span id="parts-count-badge" class="count-badge">0</span></span>
              <div class="mini-btn-group">
                <button type="button" id="btn-isolate-part" class="mini-btn" disabled title="Isolate selected part">Isolate</button>
                <button type="button" id="btn-show-all" class="mini-btn" title="Show all parts and clear selection">All</button>
              </div>
            </div>
            <div class="parts-list" id="parts-list" role="listbox" aria-label="Assembly Parts List"></div>
          </div>
        </aside>
      </section>
    </main>
  </div>

  <script id="techdraw-scene-data" type="application/json">{scene_json}</script>
  <script>{_VIEWER_JS}</script>
</body>
</html>
"""


_VIEWER_CSS = """
:root, [data-theme="dark-blueprint"] {{
  --ui-bg: #071120;
  --ui-surface: #0D1E35;
  --ui-surface-alt: #132846;
  --ui-canvas-bg: {dark_bg};
  --ui-border: {dark_border_sub};
  --ui-border-strong: {dark_border};
  --ui-text: {dark_text};
  --ui-muted: {dark_muted};
  --ui-accent: {dark_accent};
  --ui-section: {dark_section};
  --ui-btn-bg: rgba(15, 29, 52, 0.88);
  --ui-btn-hover: rgba(30, 58, 95, 0.95);
  --ui-shadow: 0 8px 24px rgba(0, 0, 0, 0.42);
}}
[data-theme="iso-white"] {{
  --ui-bg: #E2E8F0;
  --ui-surface: #F8FAFC;
  --ui-surface-alt: #EEF2F6;
  --ui-canvas-bg: {white_bg};
  --ui-border: {white_border_sub};
  --ui-border-strong: {white_border};
  --ui-text: {white_text};
  --ui-muted: {white_muted};
  --ui-accent: {white_accent};
  --ui-section: {white_section};
  --ui-btn-bg: rgba(255, 255, 255, 0.92);
  --ui-btn-hover: rgba(226, 232, 240, 0.96);
  --ui-shadow: 0 6px 20px rgba(15, 23, 42, 0.12);
}}
* {{ box-sizing: border-box; }}
html, body {{
  margin: 0;
  padding: 0;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: var(--ui-bg);
  color: var(--ui-text);
  font-family: 'SF Pro Display', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Liberation Sans', sans-serif;
  -webkit-font-smoothing: antialiased;
}}
.app-shell {{
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
}}
.topbar {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 50px;
  padding: 6px 14px;
  background: var(--ui-surface);
  border-bottom: 1px solid var(--ui-border);
  flex-shrink: 0;
  z-index: 20;
  flex-wrap: wrap;
}}
.brand-group {{
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}}
.brand-mark {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 6px;
  border: 1px solid var(--ui-accent);
  color: var(--ui-accent);
  background: color-mix(in srgb, var(--ui-accent) 12%, transparent);
  flex-shrink: 0;
}}
.brand-mark svg {{
  background-color: transparent !important;
}}
.title-stack {{
  display: flex;
  flex-direction: column;
  min-width: 0;
}}
.doc-title {{
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.03em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 280px;
}}
.doc-meta {{
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--ui-muted);
  font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}}
.meta-pill {{
  color: var(--ui-accent);
  font-weight: 600;
}}
.meta-sep {{ opacity: 0.5; }}
.toolbar-center {{
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}}
.toolbar-right {{
  display: flex;
  align-items: center;
  gap: 8px;
}}
.seg-group {{
  display: inline-flex;
  background: var(--ui-surface-alt);
  border: 1px solid var(--ui-border);
  border-radius: 7px;
  padding: 2px;
  gap: 2px;
}}
.seg-btn {{
  appearance: none;
  border: 1px solid transparent;
  background: transparent;
  color: var(--ui-muted);
  font-size: 11.5px;
  font-weight: 600;
  padding: 5px 10px;
  border-radius: 5px;
  cursor: pointer;
  transition: background 120ms ease, color 120ms ease, border-color 120ms ease;
  white-space: nowrap;
}}
.seg-btn:hover {{
  color: var(--ui-text);
  background: var(--ui-btn-hover);
}}
.seg-btn.active {{
  color: var(--ui-text);
  background: color-mix(in srgb, var(--ui-accent) 18%, var(--ui-btn-bg));
  border-color: var(--ui-accent);
}}
.action-btn {{
  display: inline-flex;
  align-items: center;
  gap: 7px;
  border: 1px solid var(--ui-border-strong);
  background: var(--ui-btn-bg);
  color: var(--ui-text);
  font-size: 11.5px;
  font-weight: 600;
  padding: 6px 11px;
  border-radius: 7px;
  cursor: pointer;
  transition: background 120ms ease, border-color 120ms ease;
}}
.action-btn:hover {{
  background: var(--ui-btn-hover);
}}
.theme-dot {{
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--ui-accent);
  box-shadow: 0 0 8px var(--ui-accent);
}}
.workspace {{
  display: flex;
  flex: 1;
  min-height: 0;
  width: 100%;
  position: relative;
}}
.pane {{
  position: relative;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}}
.workspace.layout-split .pane-2d {{
  flex: 0 0 var(--split-left, 57%);
}}
.workspace.layout-split .pane-3d {{
  flex: 1 1 auto;
}}
.workspace.layout-inspector .pane-2d,
.workspace.layout-inspector .split-divider {{
  display: none;
}}
.workspace.layout-inspector .pane-3d {{
  flex: 1 1 100%;
  flex-direction: row;
}}
.workspace.layout-inspector .inspector-panel {{
  width: 295px;
  max-height: none;
  height: 100%;
  border-top: none;
  border-left: 1px solid var(--ui-border);
  flex-direction: column;
}}
.split-divider {{
  width: 6px;
  flex-shrink: 0;
  cursor: col-resize;
  background: var(--ui-surface);
  border-left: 1px solid var(--ui-border);
  border-right: 1px solid var(--ui-border);
  position: relative;
  transition: background 120ms ease;
}}
.split-divider:hover, .split-divider.dragging {{
  background: color-mix(in srgb, var(--ui-accent) 45%, var(--ui-surface));
}}
.svg-viewport {{
  flex: 1;
  position: relative;
  overflow: hidden;
  background: var(--ui-bg);
  cursor: grab;
  user-select: none;
}}
.svg-viewport.panning {{ cursor: grabbing; }}
.svg-stage {{
  position: absolute;
  top: 0;
  left: 0;
  transform-origin: 0 0;
  will-change: transform;
  box-shadow: var(--ui-shadow);
}}
.svg-stage > svg {{
  display: block;
  width: 100%;
  height: 100%;
}}
.svg-stage .bom-balloon {{
  cursor: pointer;
  transition: opacity 120ms ease;
}}
.svg-stage .bom-balloon.is-highlighted .balloon-circle {{
  stroke: var(--ui-section) !important;
  stroke-width: 1.35px !important;
  filter: drop-shadow(0 0 2.2px var(--ui-section));
}}
.svg-stage .bom-balloon.is-highlighted .balloon-leader {{
  stroke: var(--ui-section) !important;
  stroke-width: 0.9px !important;
}}
.svg-stage .bom-balloon.is-dimmed {{
  opacity: 0.35;
}}
.svg-stage .bom-row-hit {{
  fill: transparent;
  cursor: pointer;
}}
.svg-stage .bom-row-hit.is-highlighted {{
  fill: rgba(244, 63, 94, 0.24);
  stroke: var(--ui-section);
  stroke-width: 0.45px;
}}
.viewport-3d-wrap {{
  position: relative;
  flex: 1;
  min-height: 220px;
  background: var(--ui-canvas-bg);
  overflow: hidden;
}}
#cad-canvas {{
  display: block;
  width: 100%;
  height: 100%;
  cursor: grab;
  touch-action: none;
}}
#cad-canvas:active {{ cursor: grabbing; }}
.pane-hud {{
  position: absolute;
  display: flex;
  align-items: center;
  gap: 6px;
  z-index: 10;
  pointer-events: auto;
  background: var(--ui-btn-bg);
  border: 1px solid var(--ui-border);
  border-radius: 7px;
  padding: 4px 7px;
  backdrop-filter: blur(8px);
  box-shadow: var(--ui-shadow);
}}
.hud-top-left {{ top: 10px; left: 10px; flex-wrap: wrap; }}
.hud-bottom-left {{ bottom: 10px; left: 10px; max-width: calc(100% - 100px); }}
.hud-badge {{
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--ui-muted);
  padding: 2px 5px;
  border-radius: 4px;
  background: var(--ui-surface-alt);
}}
.hud-badge-accent {{
  color: var(--ui-accent);
  border: 1px solid color-mix(in srgb, var(--ui-accent) 40%, transparent);
}}
.hud-mono {{
  font-size: 11px;
  font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  color: var(--ui-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.mini-btn-group {{
  display: inline-flex;
  gap: 3px;
}}
.mini-btn {{
  appearance: none;
  border: 1px solid var(--ui-border);
  background: var(--ui-surface-alt);
  color: var(--ui-text);
  font-size: 10.5px;
  font-weight: 600;
  padding: 3px 7px;
  border-radius: 4px;
  cursor: pointer;
  transition: background 100ms ease, border-color 100ms ease, color 100ms ease;
}}
.mini-btn:hover:not(:disabled) {{
  border-color: var(--ui-accent);
  background: var(--ui-btn-hover);
}}
.mini-btn.active {{
  border-color: var(--ui-accent);
  color: var(--ui-accent);
  background: color-mix(in srgb, var(--ui-accent) 16%, var(--ui-surface-alt));
}}
.mini-btn:disabled {{
  opacity: 0.4;
  cursor: not-allowed;
}}
.triad-box {{
  position: absolute;
  right: 10px;
  bottom: 10px;
  width: 72px;
  height: 72px;
  pointer-events: none;
  z-index: 10;
  border-radius: 8px;
  background: color-mix(in srgb, var(--ui-btn-bg) 70%, transparent);
  border: 1px solid var(--ui-border);
}}
.webgl-fallback {{
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: var(--ui-canvas-bg);
  z-index: 15;
}}
.webgl-fallback[hidden] {{ display: none; }}
#fallback-2d-canvas {{
  width: 100%;
  height: 100%;
  display: block;
}}
.fallback-banner {{
  position: absolute;
  bottom: 14px;
  left: 14px;
  right: 14px;
  padding: 8px 12px;
  border-radius: 6px;
  background: var(--ui-btn-bg);
  border: 1px solid var(--ui-section);
  color: var(--ui-text);
  font-size: 11.5px;
  text-align: center;
}}
.inspector-panel {{
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 12px;
  background: var(--ui-surface);
  border-top: 1px solid var(--ui-border);
  max-height: 255px;
  overflow-y: auto;
  flex-shrink: 0;
}}
.control-block {{
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding-bottom: 7px;
  border-bottom: 1px solid var(--ui-border);
}}
.control-block:last-child {{
  border-bottom: none;
  padding-bottom: 0;
}}
.control-head {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}}
.control-title {{
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--ui-muted);
  display: inline-flex;
  align-items: center;
  gap: 6px;
}}
.count-badge {{
  font-size: 10px;
  padding: 1px 5px;
  border-radius: 99px;
  background: var(--ui-surface-alt);
  border: 1px solid var(--ui-border);
  color: var(--ui-accent);
}}
.section-row {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}}
.cad-slider {{
  width: 100%;
  accent-color: var(--ui-accent);
  cursor: pointer;
  margin: 0;
}}
.cad-slider:disabled {{
  opacity: 0.35;
  cursor: not-allowed;
}}
.parts-block {{
  flex: 1;
  min-height: 0;
}}
.parts-list {{
  display: flex;
  flex-direction: column;
  gap: 4px;
  overflow-y: auto;
  max-height: 150px;
}}
.workspace.layout-inspector .parts-list {{
  max-height: none;
  flex: 1;
}}
.part-item {{
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 8px;
  border-radius: 5px;
  border: 1px solid var(--ui-border);
  background: var(--ui-surface-alt);
  cursor: pointer;
  font-size: 11.5px;
  transition: border-color 110ms ease, background 110ms ease;
}}
.part-item:hover {{
  border-color: var(--ui-accent);
}}
.part-item.is-selected {{
  border-color: var(--ui-section);
  background: color-mix(in srgb, var(--ui-section) 16%, var(--ui-surface-alt));
}}
.part-badge {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 22px;
  height: 22px;
  padding: 0 5px;
  border-radius: 99px;
  border: 1px solid var(--ui-section);
  font-size: 10.5px;
  font-weight: 700;
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  color: var(--ui-text);
  flex-shrink: 0;
}}
.part-info {{
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}}
.part-name {{
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.part-sub {{
  font-size: 10px;
  color: var(--ui-muted);
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.part-qty {{
  font-size: 10.5px;
  font-weight: 700;
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  color: var(--ui-accent);
  flex-shrink: 0;
}}
@media (max-width: 900px) {{
  .workspace.layout-split {{
    flex-direction: column;
  }}
  .workspace.layout-split .pane-2d {{
    flex: 0 0 46%;
  }}
  .split-divider {{
    width: 100%;
    height: 6px;
    cursor: row-resize;
  }}
  .workspace.layout-inspector .pane-3d {{
    flex-direction: column;
  }}
  .workspace.layout-inspector .inspector-panel {{
    width: 100%;
    max-height: 240px;
    border-left: none;
    border-top: 1px solid var(--ui-border);
  }}
}}
"""


_VIEWER_JS = r"""
(function () {
  "use strict";

  const sceneEl = document.getElementById("techdraw-scene-data");
  if (!sceneEl) return;
  const SCENE = JSON.parse(sceneEl.textContent || "{}");

  // ---------- Base64 binary buffer decoders ----------
  function decodeBase64Bytes(b64) {
    if (!b64) return new Uint8Array(0);
    const bin = atob(b64);
    const len = bin.length;
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) {
      bytes[i] = bin.charCodeAt(i);
    }
    return bytes;
  }

  function decodeFloat32(b64) {
    const bytes = decodeBase64Bytes(b64);
    return new Float32Array(bytes.buffer, bytes.byteOffset, Math.floor(bytes.byteLength / 4));
  }

  function decodeUint32(b64) {
    const bytes = decodeBase64Bytes(b64);
    return new Uint32Array(bytes.buffer, bytes.byteOffset, Math.floor(bytes.byteLength / 4));
  }

  function hexToRgb01(hex) {
    const clean = (hex || "#38BDF8").replace("#", "").slice(0, 6);
    const n = parseInt(clean, 16);
    return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
  }

  function materialTintRgb(matName, partIdx) {
    const m = (matName || "").toUpperCase();
    if (m.includes("BRONZE") || m.includes("BRASS") || m.includes("C932") || m.includes("CU")) {
      return [0.85, 0.68, 0.36];
    }
    if (m.includes("COPPER")) {
      return [0.86, 0.52, 0.38];
    }
    if (m.includes("ALU") || m.includes("6061") || m.includes("7075")) {
      return [0.78, 0.84, 0.90];
    }
    if (m.includes("STAINLESS") || m.includes("SS ") || m.includes("A2") || m.includes("316") || m.includes("304")) {
      return [0.84, 0.87, 0.91];
    }
    if (m.includes("STEEL") || m.includes("4140") || m.includes("IRON")) {
      return [0.64, 0.70, 0.77];
    }
    if (m.includes("PLASTIC") || m.includes("NYLON") || m.includes("POM") || m.includes("ABS")) {
      return [0.58, 0.67, 0.74];
    }
    if (m.includes("RUBBER") || m.includes("VITON") || m.includes("NITRILE")) {
      return [0.34, 0.38, 0.43];
    }
    const palette = [
      [0.76, 0.83, 0.90],
      [0.84, 0.74, 0.56],
      [0.67, 0.75, 0.83],
      [0.72, 0.82, 0.78],
      [0.80, 0.76, 0.86],
    ];
    return palette[partIdx % palette.length];
  }

  // ---------- Minimal 3D linear algebra (column-major Float32Array) ----------
  const Vec3 = {
    sub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; },
    add(a, b) { return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]; },
    scale(a, s) { return [a[0] * s, a[1] * s, a[2] * s]; },
    dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; },
    cross(a, b) {
      return [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
      ];
    },
    len(a) { return Math.hypot(a[0], a[1], a[2]); },
    norm(a) {
      const l = Math.hypot(a[0], a[1], a[2]);
      return l > 1e-9 ? [a[0] / l, a[1] / l, a[2] / l] : [0, 0, 1];
    },
  };

  function mat4LookAt(eye, target, up) {
    const z = Vec3.norm(Vec3.sub(eye, target));
    let x = Vec3.cross(up, z);
    if (Vec3.len(x) < 1e-6) {
      x = Vec3.cross([0, 1, 0], z);
    }
    x = Vec3.norm(x);
    const y = Vec3.cross(z, x);
    return new Float32Array([
      x[0], y[0], z[0], 0,
      x[1], y[1], z[1], 0,
      x[2], y[2], z[2], 0,
      -Vec3.dot(x, eye), -Vec3.dot(y, eye), -Vec3.dot(z, eye), 1,
    ]);
  }

  function mat4Perspective(fovRad, aspect, near, far) {
    const f = 1.0 / Math.tan(fovRad * 0.5);
    const nf = 1.0 / (near - far);
    return new Float32Array([
      f / aspect, 0, 0, 0,
      0, f, 0, 0,
      0, 0, (far + near) * nf, -1,
      0, 0, 2 * far * near * nf, 0,
    ]);
  }

  function mat4Ortho(halfW, halfH, near, far) {
    const nf = 1.0 / (near - far);
    return new Float32Array([
      1.0 / halfW, 0, 0, 0,
      0, 1.0 / halfH, 0, 0,
      0, 0, 2.0 * nf, 0,
      0, 0, (far + near) * nf, 1,
    ]);
  }

  // ---------- Application State ----------
  const state = {
    theme: SCENE.initial_theme === "iso_white" ? "iso_white" : "dark_blueprint",
    layout: SCENE.initial_layout === "inspector" ? "inspector" : "split",
    shadingMode: SCENE.initial_theme === "iso_white" ? "iso" : "blueprint",
    shadingManuallyLocked: false,
    projection: "persp",
    sectionAxis: "off",
    sectionNorm: 0.5,
    sectionFlip: false,
    explode: 0.0,
    hoveredPartId: -1,
    selectedPartId: -1,
    isolatedPartId: -1,
    hoveredBomItem: null,
    selectedBomItem: null,
    dirty: true,
  };

  // Decode mesh payloads once
  const parts = (SCENE.parts || []).map((p, idx) => ({
    id: p.id,
    name: p.name || ("Part " + (idx + 1)),
    bom_item_no: p.bom_item_no,
    part_no: p.part_no || "",
    material: p.material || "STANDARD",
    qty: p.qty || 1,
    bbox_min: p.bbox_min || [-1, -1, -1],
    bbox_max: p.bbox_max || [1, 1, 1],
    center: p.center || [0, 0, 0],
    explode_vector: p.explode_vector || [0, 0, 0],
    triangle_count: p.triangle_count || 0,
    edge_segment_count: p.edge_segment_count || 0,
    positions: decodeFloat32(p.positions_b64),
    normals: decodeFloat32(p.normals_b64),
    indices: decodeUint32(p.indices_b64),
    edges: decodeFloat32(p.edges_b64),
    tint: materialTintRgb(p.material, idx),
    gl: null,
  }));

  const sceneCenter = SCENE.center || [0, 0, 0];
  const sceneRadius = Math.max(SCENE.radius || 25, 1.0);
  const sceneMin = SCENE.bbox_min || [-10, -10, -10];
  const sceneMax = SCENE.bbox_max || [10, 10, 10];

  // ---------- Camera Orbit State (Z-up CAD frame) ----------
  const ISO_AZIMUTH = Math.atan2(1.8, 1.2);
  const ISO_ELEVATION = Math.atan2(1.2, Math.hypot(1.2, 1.8));
  const DEFAULT_DIST = sceneRadius * 3.55;

  const cam = {
    azimuth: ISO_AZIMUTH,
    elevation: ISO_ELEVATION,
    distance: DEFAULT_DIST,
    target: [sceneCenter[0], sceneCenter[1], sceneCenter[2]],
    anim: null,
  };

  function getCameraVectors() {
    const ce = Math.cos(cam.elevation);
    const se = Math.sin(cam.elevation);
    const ca = Math.cos(cam.azimuth);
    const sa = Math.sin(cam.azimuth);
    const eye = [
      cam.target[0] + cam.distance * ce * ca,
      cam.target[1] + cam.distance * ce * sa,
      cam.target[2] + cam.distance * se,
    ];
    // Smoothly avoid gimbal singularity near top/bottom poles
    const up = Math.abs(se) > 0.995
      ? [-ca * Math.sign(se), -sa * Math.sign(se), 0]
      : [0, 0, 1];
    return { eye, target: cam.target, up };
  }

  function animateCameraTo(azimuth, elevation, distance, target) {
    let dAz = ((azimuth - cam.azimuth + Math.PI) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI) - Math.PI;
    cam.anim = {
      startTime: performance.now(),
      duration: 320,
      from: {
        azimuth: cam.azimuth,
        elevation: cam.elevation,
        distance: cam.distance,
        target: cam.target.slice(),
      },
      to: {
        azimuth: cam.azimuth + dAz,
        elevation,
        distance,
        target: target.slice(),
      },
    };
    state.dirty = true;
  }

  function setCameraPreset(preset) {
    const baseT = [sceneCenter[0], sceneCenter[1], sceneCenter[2]];
    const d = DEFAULT_DIST;
    if (preset === "iso" || preset === "reset") {
      animateCameraTo(ISO_AZIMUTH, ISO_ELEVATION, d, baseT);
    } else if (preset === "front") {
      animateCameraTo(-Math.PI * 0.5, 0.0, d, baseT);
    } else if (preset === "right") {
      animateCameraTo(0.0, 0.0, d, baseT);
    } else if (preset === "top") {
      animateCameraTo(-Math.PI * 0.5, Math.PI * 0.498, d, baseT);
    }
  }

  // ---------- 2D SVG Pan/Zoom & Bidirectional BOM Linking ----------
  const svgViewport = document.getElementById("svg-viewport");
  const svgStage = document.getElementById("svg-stage");
  const svgEl = svgStage ? svgStage.querySelector("svg") : null;
  const svgZoomReadout = document.getElementById("svg-zoom-readout");

  const svgView = {
    scale: 1,
    tx: 0,
    ty: 0,
    baseW: SCENE.sheet_width_mm || 420,
    baseH: SCENE.sheet_height_mm || 297,
  };

  function applySvgTransform() {
    if (!svgStage) return;
    svgStage.style.width = svgView.baseW + "px";
    svgStage.style.height = svgView.baseH + "px";
    svgStage.style.transform =
      "translate(" + svgView.tx.toFixed(1) + "px, " + svgView.ty.toFixed(1) + "px) scale(" + svgView.scale.toFixed(4) + ")";
    if (svgZoomReadout) {
      svgZoomReadout.textContent = Math.round(svgView.scale * 100) + "%";
    }
  }

  function fitSvgToViewport() {
    if (!svgViewport || !svgStage) return;
    const rect = svgViewport.getBoundingClientRect();
    if (rect.width < 10 || rect.height < 10) return;
    const pad = 20;
    const scaleX = (rect.width - pad * 2) / svgView.baseW;
    const scaleY = (rect.height - pad * 2) / svgView.baseH;
    svgView.scale = Math.max(0.15, Math.min(scaleX, scaleY));
    svgView.tx = (rect.width - svgView.baseW * svgView.scale) * 0.5;
    svgView.ty = (rect.height - svgView.baseH * svgView.scale) * 0.5;
    applySvgTransform();
  }

  function zoomSvgAt(factor, clientX, clientY) {
    if (!svgViewport) return;
    const rect = svgViewport.getBoundingClientRect();
    const px = clientX - rect.left;
    const py = clientY - rect.top;
    const nextScale = Math.max(0.25, Math.min(12.0, svgView.scale * factor));
    const ratio = nextScale / svgView.scale;
    svgView.tx = px - (px - svgView.tx) * ratio;
    svgView.ty = py - (py - svgView.ty) * ratio;
    svgView.scale = nextScale;
    applySvgTransform();
  }

  if (svgViewport) {
    let svgDragging = false;
    let lastX = 0;
    let lastY = 0;
    svgViewport.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || e.target.closest(".bom-balloon, .bom-row-hit")) return;
      svgDragging = true;
      lastX = e.clientX;
      lastY = e.clientY;
      svgViewport.classList.add("panning");
      svgViewport.setPointerCapture(e.pointerId);
    });
    svgViewport.addEventListener("pointermove", (e) => {
      if (!svgDragging) return;
      svgView.tx += e.clientX - lastX;
      svgView.ty += e.clientY - lastY;
      lastX = e.clientX;
      lastY = e.clientY;
      applySvgTransform();
    });
    svgViewport.addEventListener("pointerup", () => {
      svgDragging = false;
      svgViewport.classList.remove("panning");
    });
    svgViewport.addEventListener("wheel", (e) => {
      e.preventDefault();
      zoomSvgAt(e.deltaY < 0 ? 1.14 : 1 / 1.14, e.clientX, e.clientY);
    }, { passive: false });
    svgViewport.addEventListener("dblclick", () => fitSvgToViewport());
  }

  const btnSvgIn = document.getElementById("btn-svg-zoom-in");
  const btnSvgOut = document.getElementById("btn-svg-zoom-out");
  const btnSvgFit = document.getElementById("btn-svg-fit");
  if (btnSvgIn) btnSvgIn.addEventListener("click", () => {
    const r = svgViewport.getBoundingClientRect();
    zoomSvgAt(1.25, r.left + r.width * 0.5, r.top + r.height * 0.5);
  });
  if (btnSvgOut) btnSvgOut.addEventListener("click", () => {
    const r = svgViewport.getBoundingClientRect();
    zoomSvgAt(0.8, r.left + r.width * 0.5, r.top + r.height * 0.5);
  });
  if (btnSvgFit) btnSvgFit.addEventListener("click", () => fitSvgToViewport());

  // Instrument 2D SVG BOM balloons and BOM table rows dynamically (zero mutation to golden SVG format)
  const svgBalloonMap = new Map();
  const svgBomRowMap = new Map();

  function instrumentSvgBomElements() {
    if (!svgEl) return;
    const balloons = svgEl.querySelectorAll("g.bom-balloon");
    balloons.forEach((g) => {
      const txt = g.querySelector("text.balloon-text");
      if (!txt) return;
      const itemNo = parseInt((txt.textContent || "").trim(), 10);
      if (!Number.isFinite(itemNo)) return;
      g.setAttribute("data-bom-item", String(itemNo));
      if (!svgBalloonMap.has(itemNo)) svgBalloonMap.set(itemNo, []);
      svgBalloonMap.get(itemNo).push(g);
      g.addEventListener("mouseenter", () => setHoverByBom(itemNo));
      g.addEventListener("mouseleave", () => setHoverByBom(null));
      g.addEventListener("click", (e) => {
        e.stopPropagation();
        toggleSelectByBom(itemNo);
      });
    });

    const bomTable = svgEl.querySelector("g.bom-table");
    if (bomTable) {
      const headerBg = bomTable.querySelector("rect.table-header-bg");
      const tableRect = bomTable.querySelector("rect.title-bg");
      const headerH = headerBg ? parseFloat(headerBg.getAttribute("height") || "5.5") : 5.5;
      const tableW = tableRect ? parseFloat(tableRect.getAttribute("width") || "175") : 175;
      const itemTexts = Array.from(bomTable.querySelectorAll('text[x="5"].tb-field-val-sm'));
      const rowH = itemTexts.length >= 2
        ? Math.abs(parseFloat(itemTexts[1].getAttribute("y") || "0") - parseFloat(itemTexts[0].getAttribute("y") || "0"))
        : 5.2;

      itemTexts.forEach((t, idx) => {
        const itemNo = parseInt((t.textContent || "").trim(), 10);
        if (!Number.isFinite(itemNo)) return;
        const rect = document.createElementNS(svgEl.namespaceURI, "rect");
        rect.setAttribute("x", "0");
        rect.setAttribute("y", (headerH + idx * rowH).toFixed(2));
        rect.setAttribute("width", tableW.toFixed(2));
        rect.setAttribute("height", rowH.toFixed(2));
        rect.setAttribute("class", "bom-row-hit");
        rect.setAttribute("data-bom-item", String(itemNo));
        bomTable.appendChild(rect);
        svgBomRowMap.set(itemNo, rect);

        rect.addEventListener("mouseenter", () => setHoverByBom(itemNo));
        rect.addEventListener("mouseleave", () => setHoverByBom(null));
        rect.addEventListener("click", (e) => {
          e.stopPropagation();
          toggleSelectByBom(itemNo);
        });
      });
    }
  }

  function updateSvgHighlights() {
    const activeBom = state.hoveredBomItem !== null ? state.hoveredBomItem : state.selectedBomItem;
    svgBalloonMap.forEach((els, itemNo) => {
      els.forEach((el) => {
        const isHit = activeBom !== null && itemNo === activeBom;
        el.classList.toggle("is-highlighted", isHit);
        el.classList.toggle("is-dimmed", activeBom !== null && !isHit);
      });
    });
    svgBomRowMap.forEach((rect, itemNo) => {
      rect.classList.toggle("is-highlighted", activeBom !== null && itemNo === activeBom);
    });
  }

  // ---------- Parts & BOM Panel ----------
  const partsListEl = document.getElementById("parts-list");
  const partsCountBadge = document.getElementById("parts-count-badge");
  const explodeBlock = document.getElementById("explode-block");
  const btnIsolate = document.getElementById("btn-isolate-part");
  const btnShowAll = document.getElementById("btn-show-all");
  const hudPartReadout = document.getElementById("hud-part-readout");
  const partRowEls = new Map();

  function partMatchesSelection(p) {
    if (state.selectedPartId >= 0 && p.id === state.selectedPartId) return true;
    if (state.selectedBomItem !== null && p.bom_item_no === state.selectedBomItem) return true;
    return false;
  }

  function partMatchesHover(p) {
    if (state.hoveredPartId >= 0 && p.id === state.hoveredPartId) return true;
    if (state.hoveredBomItem !== null && p.bom_item_no === state.hoveredBomItem) return true;
    return false;
  }

  function partIsVisible(p) {
    if (state.isolatedPartId >= 0) {
      const isoPart = parts[state.isolatedPartId];
      if (!isoPart) return true;
      if (isoPart.bom_item_no !== null && p.bom_item_no === isoPart.bom_item_no) return true;
      return p.id === state.isolatedPartId;
    }
    return true;
  }

  function syncSelectionUI() {
    updateSvgHighlights();
    partRowEls.forEach((rowEl, key) => {
      const isSelected = (key.type === "bom" && state.selectedBomItem === key.val)
        || (key.type === "part" && state.selectedPartId === key.val);
      rowEl.classList.toggle("is-selected", isSelected);
    });
    if (btnIsolate) {
      const hasSel = state.selectedPartId >= 0 || state.selectedBomItem !== null;
      btnIsolate.disabled = !hasSel;
      btnIsolate.classList.toggle("active", state.isolatedPartId >= 0);
      btnIsolate.textContent = state.isolatedPartId >= 0 ? "Un-isolate" : "Isolate";
    }
    if (hudPartReadout) {
      const targetPart =
        (state.hoveredPartId >= 0 && parts[state.hoveredPartId])
        || (state.hoveredBomItem !== null && parts.find((p) => p.bom_item_no === state.hoveredBomItem))
        || (state.selectedPartId >= 0 && parts[state.selectedPartId])
        || (state.selectedBomItem !== null && parts.find((p) => p.bom_item_no === state.selectedBomItem));
      if (targetPart) {
        const prefix = targetPart.bom_item_no !== null ? ("ITEM #" + targetPart.bom_item_no + " · ") : "";
        hudPartReadout.textContent = prefix + targetPart.name + " [" + targetPart.part_no + " · " + targetPart.material + "]";
      } else {
        const totalTris = parts.reduce((acc, p) => acc + p.triangle_count, 0);
        hudPartReadout.textContent = parts.length + (parts.length === 1 ? " part" : " parts") + " · " + totalTris.toLocaleString() + " tris · Click part to highlight";
      }
    }
    state.dirty = true;
  }

  function setHoverByPart(partId) {
    state.hoveredPartId = partId;
    const p = partId >= 0 ? parts[partId] : null;
    state.hoveredBomItem = p && p.bom_item_no !== null ? p.bom_item_no : null;
    syncSelectionUI();
  }

  function setHoverByBom(itemNo) {
    state.hoveredBomItem = itemNo;
    state.hoveredPartId = -1;
    syncSelectionUI();
  }

  function toggleSelectByPart(partId) {
    if (partId < 0) {
      state.selectedPartId = -1;
      state.selectedBomItem = null;
      state.isolatedPartId = -1;
      syncSelectionUI();
      return;
    }
    const p = parts[partId];
    if (p && p.bom_item_no !== null) {
      toggleSelectByBom(p.bom_item_no);
      return;
    }
    if (state.selectedPartId === partId) {
      state.selectedPartId = -1;
      state.isolatedPartId = -1;
    } else {
      state.selectedPartId = partId;
      state.selectedBomItem = null;
      if (state.isolatedPartId >= 0) state.isolatedPartId = partId;
    }
    syncSelectionUI();
  }

  function toggleSelectByBom(itemNo) {
    if (state.selectedBomItem === itemNo) {
      state.selectedBomItem = null;
      state.selectedPartId = -1;
      state.isolatedPartId = -1;
    } else {
      state.selectedBomItem = itemNo;
      const first = parts.find((p) => p.bom_item_no === itemNo);
      state.selectedPartId = first ? first.id : -1;
      if (state.isolatedPartId >= 0 && first) state.isolatedPartId = first.id;
    }
    syncSelectionUI();
  }

  function populatePartsList() {
    if (!partsListEl) return;
    partsListEl.innerHTML = "";
    partRowEls.clear();

    const bomItems = SCENE.bom_items || [];
    if (bomItems.length > 0) {
      if (partsCountBadge) partsCountBadge.textContent = String(bomItems.length);
      bomItems.forEach((item) => {
        const row = document.createElement("div");
        row.className = "part-item";
        row.setAttribute("role", "option");
        row.setAttribute("data-bom-item", String(item.item_no));
        row.innerHTML =
          '<span class="part-badge">' + item.item_no + '</span>' +
          '<div class="part-info">' +
            '<span class="part-name"></span>' +
            '<span class="part-sub"></span>' +
          '</div>' +
          '<span class="part-qty">×' + item.qty + '</span>';
        row.querySelector(".part-name").textContent = item.description;
        row.querySelector(".part-sub").textContent = item.part_no + " · " + item.material;
        row.addEventListener("mouseenter", () => setHoverByBom(item.item_no));
        row.addEventListener("mouseleave", () => setHoverByBom(null));
        row.addEventListener("click", () => toggleSelectByBom(item.item_no));
        partsListEl.appendChild(row);
        partRowEls.set({ type: "bom", val: item.item_no }, row);
      });
    } else {
      if (partsCountBadge) partsCountBadge.textContent = String(parts.length);
      parts.forEach((p, idx) => {
        const row = document.createElement("div");
        row.className = "part-item";
        row.setAttribute("role", "option");
        row.innerHTML =
          '<span class="part-badge">' + (idx + 1) + '</span>' +
          '<div class="part-info">' +
            '<span class="part-name"></span>' +
            '<span class="part-sub"></span>' +
          '</div>' +
          '<span class="part-qty">×1</span>';
        row.querySelector(".part-name").textContent = p.name;
        row.querySelector(".part-sub").textContent = p.part_no + " · " + p.material;
        row.addEventListener("mouseenter", () => setHoverByPart(p.id));
        row.addEventListener("mouseleave", () => setHoverByPart(-1));
        row.addEventListener("click", () => toggleSelectByPart(p.id));
        partsListEl.appendChild(row);
        partRowEls.set({ type: "part", val: p.id }, row);
      });
    }

    if (explodeBlock && parts.length <= 1) {
      const slider = document.getElementById("slider-explode");
      if (slider) slider.disabled = true;
      const readout = document.getElementById("explode-readout");
      if (readout) readout.textContent = "Single part";
    }
  }

  if (btnIsolate) {
    btnIsolate.addEventListener("click", () => {
      if (state.isolatedPartId >= 0) {
        state.isolatedPartId = -1;
      } else if (state.selectedPartId >= 0) {
        state.isolatedPartId = state.selectedPartId;
      } else if (state.selectedBomItem !== null) {
        const first = parts.find((p) => p.bom_item_no === state.selectedBomItem);
        if (first) state.isolatedPartId = first.id;
      }
      syncSelectionUI();
    });
  }

  if (btnShowAll) {
    btnShowAll.addEventListener("click", () => {
      state.selectedPartId = -1;
      state.selectedBomItem = null;
      state.isolatedPartId = -1;
      syncSelectionUI();
    });
  }

  // ---------- Theme, Layout, Shading, Section, Explode Controls ----------
  const workspaceEl = document.getElementById("workspace");
  const themeBtn = document.getElementById("btn-theme-toggle");
  const themeLabel = document.getElementById("theme-toggle-label");
  const shadingButtons = Array.from(document.querySelectorAll("[data-shading]"));
  const layoutButtons = Array.from(document.querySelectorAll("[data-layout]"));
  const secAxisButtons = Array.from(document.querySelectorAll("[data-sec-axis]"));
  const secFlipBtn = document.getElementById("btn-sec-flip");
  const secSlider = document.getElementById("slider-section");
  const secReadout = document.getElementById("section-readout");
  const explodeSlider = document.getElementById("slider-explode");
  const explodeReadout = document.getElementById("explode-readout");
  const projBtn = document.getElementById("btn-proj-toggle");

  function updateShadingButtons() {
    shadingButtons.forEach((b) => {
      b.classList.toggle("active", b.getAttribute("data-shading") === state.shadingMode);
    });
  }

  function applyTheme(nextTheme) {
    state.theme = nextTheme;
    const isWhite = nextTheme === "iso_white";
    document.documentElement.setAttribute("data-theme", isWhite ? "iso-white" : "dark-blueprint");
    if (svgEl) {
      svgEl.classList.toggle("theme-iso-white", isWhite);
      svgEl.classList.toggle("theme-dark-blueprint", !isWhite);
    }
    if (themeLabel) {
      themeLabel.textContent = isWhite ? "ISO White" : "Dark Blueprint";
    }
    if (!state.shadingManuallyLocked) {
      state.shadingMode = isWhite ? "iso" : "blueprint";
      updateShadingButtons();
    }
    state.dirty = true;
  }

  if (themeBtn) {
    themeBtn.addEventListener("click", () => {
      applyTheme(state.theme === "dark_blueprint" ? "iso_white" : "dark_blueprint");
    });
  }

  shadingButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const mode = btn.getAttribute("data-shading");
      if (!mode) return;
      state.shadingMode = mode;
      state.shadingManuallyLocked = (mode === "xray" || mode === "wireframe");
      updateShadingButtons();
      state.dirty = true;
    });
  });

  layoutButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const next = btn.getAttribute("data-layout");
      if (!next || !workspaceEl) return;
      state.layout = next;
      workspaceEl.classList.toggle("layout-split", next === "split");
      workspaceEl.classList.toggle("layout-inspector", next === "inspector");
      layoutButtons.forEach((b) => b.classList.toggle("active", b.getAttribute("data-layout") === next));
      requestAnimationFrame(() => {
        if (next === "split") fitSvgToViewport();
        resizeCanvases();
        state.dirty = true;
      });
    });
  });

  document.querySelectorAll("[data-cam]").forEach((btn) => {
    btn.addEventListener("click", () => {
      setCameraPreset(btn.getAttribute("data-cam") || "iso");
    });
  });

  if (projBtn) {
    projBtn.addEventListener("click", () => {
      state.projection = state.projection === "persp" ? "ortho" : "persp";
      projBtn.textContent = state.projection === "persp" ? "PERSP" : "ORTHO";
      projBtn.classList.toggle("active", state.projection === "ortho");
      state.dirty = true;
    });
  }

  function getSectionParams() {
    if (state.sectionAxis === "off") {
      return { enabled: 0, axisIdx: 2, normal: [0, 0, 1], offset: 0, coord: 0 };
    }
    const axisIdx = state.sectionAxis === "x" ? 0 : (state.sectionAxis === "y" ? 1 : 2);
    const pad = Math.max((sceneMax[axisIdx] - sceneMin[axisIdx]) * 0.06, 0.5);
    const lo = sceneMin[axisIdx] - pad;
    const hi = sceneMax[axisIdx] + pad;
    const coord = lo + (hi - lo) * state.sectionNorm;
    const sign = state.sectionFlip ? -1.0 : 1.0;
    const normal = [
      axisIdx === 0 ? sign : 0,
      axisIdx === 1 ? sign : 0,
      axisIdx === 2 ? sign : 0,
    ];
    return { enabled: 1, axisIdx, normal, offset: sign * coord, coord };
  }

  function updateSectionUI() {
    const isOff = state.sectionAxis === "off";
    secAxisButtons.forEach((b) => b.classList.toggle("active", b.getAttribute("data-sec-axis") === state.sectionAxis));
    if (secSlider) secSlider.disabled = isOff;
    if (secFlipBtn) {
      secFlipBtn.disabled = isOff;
      secFlipBtn.classList.toggle("active", !isOff && state.sectionFlip);
    }
    if (secReadout) {
      if (isOff) {
        secReadout.textContent = "OFF";
      } else {
        const sp = getSectionParams();
        const signStr = sp.coord >= 0 ? "+" : "";
        secReadout.textContent = state.sectionAxis.toUpperCase() + " = " + signStr + sp.coord.toFixed(1) + " mm";
      }
    }
    state.dirty = true;
  }

  secAxisButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      state.sectionAxis = btn.getAttribute("data-sec-axis") || "off";
      updateSectionUI();
    });
  });
  if (secFlipBtn) {
    secFlipBtn.addEventListener("click", () => {
      state.sectionFlip = !state.sectionFlip;
      updateSectionUI();
    });
  }
  if (secSlider) {
    secSlider.addEventListener("input", () => {
      state.sectionNorm = parseFloat(secSlider.value) / 100.0;
      updateSectionUI();
    });
  }
  if (explodeSlider) {
    explodeSlider.addEventListener("input", () => {
      const pct = parseFloat(explodeSlider.value);
      state.explode = pct / 100.0;
      if (explodeReadout) explodeReadout.textContent = Math.round(pct) + "%";
      state.dirty = true;
    });
  }

  // Draggable split divider
  const splitDivider = document.getElementById("split-divider");
  if (splitDivider && workspaceEl) {
    let dragging = false;
    splitDivider.addEventListener("pointerdown", (e) => {
      dragging = true;
      splitDivider.classList.add("dragging");
      splitDivider.setPointerCapture(e.pointerId);
    });
    splitDivider.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      const rect = workspaceEl.getBoundingClientRect();
      const pct = Math.max(25, Math.min(78, ((e.clientX - rect.left) / rect.width) * 100));
      workspaceEl.style.setProperty("--split-left", pct.toFixed(1) + "%");
      resizeCanvases();
      state.dirty = true;
    });
    splitDivider.addEventListener("pointerup", () => {
      dragging = false;
      splitDivider.classList.remove("dragging");
      fitSvgToViewport();
    });
  }

  // ---------- GLSL 300 es Shaders ----------
  const SOLID_VS = `#version 300 es
precision highp float;
layout(location = 0) in vec3 aPosition;
layout(location = 1) in vec3 aNormal;

uniform mat4 uProj;
uniform mat4 uView;
uniform vec3 uPartOffset;

out vec3 vWorldPos;
out vec3 vNormal;

void main() {
  vec3 worldPos = aPosition + uPartOffset;
  vWorldPos = worldPos;
  vNormal = aNormal;
  gl_Position = uProj * uView * vec4(worldPos, 1.0);
}
`;

  const SOLID_FS = `#version 300 es
precision highp float;

in vec3 vWorldPos;
in vec3 vNormal;

uniform int uShadingMode;
uniform int uTheme;
uniform int uPartId;
uniform vec3 uCameraPos;
uniform vec3 uBaseTint;
uniform vec3 uBgColor;
uniform vec3 uAccentColor;
uniform vec3 uSectionColor;
uniform vec3 uHatchColor;
uniform float uHighlight;
uniform float uDimmed;
uniform float uClipEnabled;
uniform vec3 uClipNormal;
uniform float uClipOffset;
uniform int uClipAxis;
uniform float uHatchPitch;

out vec4 fragColor;

void main() {
  float clipDist = dot(vWorldPos, uClipNormal) - uClipOffset;
  if (uClipEnabled > 0.5 && clipDist > 0.0) {
    discard;
  }

  // Interior section cut-cap back-face procedural 45-deg ISO hatching
  if (uClipEnabled > 0.5 && !gl_FrontFacing) {
    vec2 cutUv = (uClipAxis == 0) ? vWorldPos.yz : ((uClipAxis == 1) ? vWorldPos.xz : vWorldPos.xy);
    float hatchSign = (mod(float(uPartId), 2.0) < 0.5) ? 1.0 : -1.0;
    float pitch = max(uHatchPitch, 0.25);
    float coord = (cutUv.x + hatchSign * cutUv.y) / pitch;
    float fw = max(fwidth(coord), 0.03);
    float stripe = abs(fract(coord + 0.5) - 0.5);
    float lineMask = 1.0 - smoothstep(0.065, 0.065 + fw * 1.35, stripe);

    vec3 capBase = (uTheme == 0)
      ? mix(vec3(0.06, 0.14, 0.25), uBaseTint * 0.26, 0.35)
      : mix(vec3(0.89, 0.92, 0.96), uBaseTint * 0.92, 0.25);
    vec3 hatchInk = (uTheme == 0) ? mix(uHatchColor, uSectionColor, 0.35) : mix(uHatchColor, uSectionColor, 0.25);
    vec3 col = mix(capBase, hatchInk, lineMask * 0.85);
    float cutRim = 1.0 - smoothstep(0.0, pitch * 0.45, abs(clipDist));
    col = mix(col, uSectionColor, cutRim * 0.35);
    if (uHighlight > 0.1) {
      col = mix(col, uSectionColor, uHighlight * 0.42);
    }
    fragColor = vec4(col, 1.0);
    return;
  }

  // Wireframe / Hidden-Line mode depth-prepass fill
  if (uShadingMode == 3) {
    fragColor = vec4(uBgColor, 1.0);
    return;
  }

  vec3 N = normalize(gl_FrontFacing ? vNormal : -vNormal);
  vec3 V = normalize(uCameraPos - vWorldPos);
  vec3 L1 = normalize(vec3(0.55, 0.35, 0.76));
  vec3 L2 = normalize(vec3(-0.62, -0.48, 0.38));

  float ndotl1 = dot(N, L1);
  float ndotl2 = max(dot(N, L2), 0.0);
  float ndotv = max(abs(dot(N, V)), 0.0);
  float fresnel = pow(1.0 - ndotv, 2.4);

  vec3 H = normalize(L1 + V);
  float spec = pow(max(dot(N, H), 0.0), 36.0);

  vec3 col = vec3(0.5);
  float alpha = 1.0;

  if (uShadingMode == 0) {
    // Blueprint Technical: Gooch-inspired deep navy-to-cyan shading + rim silhouette
    float goochT = 0.5 + 0.5 * ndotl1;
    vec3 coolCol = mix(vec3(0.045, 0.115, 0.220), uBaseTint * 0.22, 0.25);
    vec3 warmCol = mix(vec3(0.145, 0.355, 0.560), uBaseTint * 0.58, 0.30);
    col = mix(coolCol, warmCol, goochT);
    col += vec3(0.04, 0.11, 0.19) * ndotl2;
    col += uAccentColor * (fresnel * 0.48 + spec * 0.28);
  } else if (uShadingMode == 1) {
    // ISO Shaded + Edges: Cool-warm technical illustration shading
    float goochT = 0.5 + 0.5 * ndotl1;
    vec3 coolCol = mix(vec3(0.52, 0.61, 0.72), uBaseTint * 0.66, 0.48);
    vec3 warmCol = mix(vec3(0.97, 0.98, 0.99), uBaseTint * 1.06, 0.36);
    col = mix(coolCol, warmCol, goochT);
    col += vec3(0.08, 0.10, 0.13) * ndotl2;
    col += vec3(0.95, 0.98, 1.0) * (spec * 0.24 + fresnel * 0.10);
  } else {
    // X-Ray / Phantom: Fresnel translucent CAD inspection
    vec3 baseX = (uTheme == 0) ? mix(uAccentColor, uBaseTint, 0.3) : mix(vec3(0.05, 0.38, 0.72), uBaseTint * 0.5, 0.25);
    col = baseX * (0.42 + 0.58 * fresnel) + vec3(0.45, 0.78, 1.0) * (spec * 0.35);
    alpha = clamp(0.10 + 0.62 * pow(1.0 - ndotv, 1.85), 0.10, 0.78);
  }

  if (uDimmed > 0.5) {
    float luma = dot(col, vec3(0.299, 0.587, 0.114));
    col = mix(col, mix(vec3(luma), uBgColor, 0.38), 0.55);
    alpha *= 0.65;
  }
  if (uHighlight > 0.05) {
    vec3 hiCol = (uHighlight > 0.75) ? uSectionColor : uAccentColor;
    col = mix(col, hiCol, uHighlight * 0.38) + hiCol * (fresnel * 0.45 * uHighlight);
    if (uShadingMode == 2) alpha = min(0.92, alpha + 0.24 * uHighlight);
  }

  fragColor = vec4(col, alpha);
}
`;

  const EDGE_VS = `#version 300 es
precision highp float;
layout(location = 0) in vec3 aPosition;

uniform mat4 uProj;
uniform mat4 uView;
uniform vec3 uPartOffset;
uniform float uDepthBias;

out vec3 vWorldPos;

void main() {
  vec3 worldPos = aPosition + uPartOffset;
  vWorldPos = worldPos;
  vec4 clip = uProj * uView * vec4(worldPos, 1.0);
  clip.z -= uDepthBias * clip.w;
  gl_Position = clip;
}
`;

  const EDGE_FS = `#version 300 es
precision highp float;

in vec3 vWorldPos;

uniform vec4 uColor;
uniform float uClipEnabled;
uniform vec3 uClipNormal;
uniform float uClipOffset;

out vec4 fragColor;

void main() {
  if (uClipEnabled > 0.5) {
    float clipDist = dot(vWorldPos, uClipNormal) - uClipOffset;
    if (clipDist > 0.0) discard;
  }
  fragColor = uColor;
}
`;

  const PICK_VS = `#version 300 es
precision highp float;
layout(location = 0) in vec3 aPosition;
uniform mat4 uProj;
uniform mat4 uView;
uniform vec3 uPartOffset;
out vec3 vWorldPos;
void main() {
  vec3 worldPos = aPosition + uPartOffset;
  vWorldPos = worldPos;
  gl_Position = uProj * uView * vec4(worldPos, 1.0);
}
`;

  const PICK_FS = `#version 300 es
precision highp float;
in vec3 vWorldPos;
uniform vec4 uPickColor;
uniform float uClipEnabled;
uniform vec3 uClipNormal;
uniform float uClipOffset;
out vec4 fragColor;
void main() {
  if (uClipEnabled > 0.5) {
    float clipDist = dot(vWorldPos, uClipNormal) - uClipOffset;
    if (clipDist > 0.0) discard;
  }
  fragColor = uPickColor;
}
`;

  // ---------- WebGL2 Renderer ----------
  const canvas = document.getElementById("cad-canvas");
  const triadCanvas = document.getElementById("triad-canvas");
  const fallbackWrap = document.getElementById("webgl-fallback");
  const fallbackCanvas = document.getElementById("fallback-2d-canvas");

  let gl = null;
  let solidProg = null;
  let edgeProg = null;
  let pickProg = null;
  let gridVao = null;
  let gridCount = 0;
  let cutFrameVao = null;
  let cutFrameBuf = null;
  let pickFbo = null;
  let pickTex = null;
  let pickDepth = null;
  let pickW = 0;
  let pickH = 0;

  function compileShader(glCtx, type, src) {
    const sh = glCtx.createShader(type);
    glCtx.shaderSource(sh, src);
    glCtx.compileShader(sh);
    if (!glCtx.getShaderParameter(sh, glCtx.COMPILE_STATUS)) {
      const log = glCtx.getShaderInfoLog(sh);
      glCtx.deleteShader(sh);
      throw new Error("GLSL compile error: " + log);
    }
    return sh;
  }

  function linkProgram(glCtx, vsSrc, fsSrc) {
    const vs = compileShader(glCtx, glCtx.VERTEX_SHADER, vsSrc);
    const fs = compileShader(glCtx, glCtx.FRAGMENT_SHADER, fsSrc);
    const prog = glCtx.createProgram();
    glCtx.attachShader(prog, vs);
    glCtx.attachShader(prog, fs);
    glCtx.linkProgram(prog);
    glCtx.deleteShader(vs);
    glCtx.deleteShader(fs);
    if (!glCtx.getProgramParameter(prog, glCtx.LINK_STATUS)) {
      const log = glCtx.getProgramInfoLog(prog);
      glCtx.deleteProgram(prog);
      throw new Error("GLSL link error: " + log);
    }
    return prog;
  }

  function buildReferenceGrid(glCtx) {
    const extent = sceneRadius * 1.45;
    const step = Math.pow(10, Math.floor(Math.log10(Math.max(extent / 5, 1))));
    const halfN = Math.min(16, Math.max(4, Math.ceil(extent / step)));
    const zPlane = sceneMin[2] - Math.max(sceneRadius * 0.02, 0.25);
    const cx = Math.round(sceneCenter[0] / step) * step;
    const cy = Math.round(sceneCenter[1] / step) * step;
    const lim = halfN * step;
    const verts = [];
    for (let i = -halfN; i <= halfN; i++) {
      const offset = i * step;
      verts.push(cx - lim, cy + offset, zPlane, cx + lim, cy + offset, zPlane);
      verts.push(cx + offset, cy - lim, zPlane, cx + offset, cy + lim, zPlane);
    }
    const arr = new Float32Array(verts);
    gridVao = glCtx.createVertexArray();
    glCtx.bindVertexArray(gridVao);
    const vbo = glCtx.createBuffer();
    glCtx.bindBuffer(glCtx.ARRAY_BUFFER, vbo);
    glCtx.bufferData(glCtx.ARRAY_BUFFER, arr, glCtx.STATIC_DRAW);
    glCtx.enableVertexAttribArray(0);
    glCtx.vertexAttribPointer(0, 3, glCtx.FLOAT, false, 0, 0);
    glCtx.bindVertexArray(null);
    gridCount = verts.length / 3;
  }

  function initWebGL2() {
    if (!canvas) return false;
    try {
      gl = canvas.getContext("webgl2", {
        antialias: true,
        alpha: false,
        preserveDrawingBuffer: true,
        powerPreference: "high-performance",
      });
    } catch (_err) {
      gl = null;
    }
    if (!gl) return false;

    try {
      solidProg = linkProgram(gl, SOLID_VS, SOLID_FS);
      edgeProg = linkProgram(gl, EDGE_VS, EDGE_FS);
      pickProg = linkProgram(gl, PICK_VS, PICK_FS);
    } catch (err) {
      console.error(err);
      gl = null;
      return false;
    }

    parts.forEach((p) => {
      const meshVao = gl.createVertexArray();
      gl.bindVertexArray(meshVao);

      const posBuf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, posBuf);
      gl.bufferData(gl.ARRAY_BUFFER, p.positions, gl.STATIC_DRAW);
      gl.enableVertexAttribArray(0);
      gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);

      const normBuf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, normBuf);
      gl.bufferData(gl.ARRAY_BUFFER, p.normals, gl.STATIC_DRAW);
      gl.enableVertexAttribArray(1);
      gl.vertexAttribPointer(1, 3, gl.FLOAT, false, 0, 0);

      const idxBuf = gl.createBuffer();
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, idxBuf);
      gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, p.indices, gl.STATIC_DRAW);
      gl.bindVertexArray(null);

      let edgeVao = null;
      let edgeVertCount = 0;
      if (p.edges.length >= 6) {
        edgeVao = gl.createVertexArray();
        gl.bindVertexArray(edgeVao);
        const edgeBuf = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, edgeBuf);
        gl.bufferData(gl.ARRAY_BUFFER, p.edges, gl.STATIC_DRAW);
        gl.enableVertexAttribArray(0);
        gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
        gl.bindVertexArray(null);
        edgeVertCount = Math.floor(p.edges.length / 3);
      }

      p.gl = {
        meshVao,
        indexCount: p.indices.length,
        edgeVao,
        edgeVertCount,
      };
    });

    buildReferenceGrid(gl);

    cutFrameVao = gl.createVertexArray();
    gl.bindVertexArray(cutFrameVao);
    cutFrameBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, cutFrameBuf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(24), gl.DYNAMIC_DRAW);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
    gl.bindVertexArray(null);

    return true;
  }

  function ensurePickFbo(w, h) {
    if (!gl || (pickFbo && pickW === w && pickH === h)) return;
    if (pickFbo) {
      gl.deleteFramebuffer(pickFbo);
      gl.deleteTexture(pickTex);
      gl.deleteRenderbuffer(pickDepth);
    }
    pickW = Math.max(1, w);
    pickH = Math.max(1, h);
    pickFbo = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, pickFbo);
    pickTex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, pickTex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, pickW, pickH, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, pickTex, 0);

    pickDepth = gl.createRenderbuffer();
    gl.bindRenderbuffer(gl.RENDERBUFFER, pickDepth);
    gl.renderbufferStorage(gl.RENDERBUFFER, gl.DEPTH_COMPONENT16, pickW, pickH);
    gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, pickDepth);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  }

  function computeMatrices(width, height) {
    const aspect = Math.max(width, 1) / Math.max(height, 1);
    const { eye, target, up } = getCameraVectors();
    const view = mat4LookAt(eye, target, up);
    const near = Math.max(sceneRadius * 0.01, cam.distance * 0.01);
    const far = Math.max(sceneRadius * 40.0, cam.distance * 10.0);
    let proj;
    if (state.projection === "ortho") {
      const halfH = cam.distance * Math.tan((32.0 * Math.PI / 180.0) * 0.5);
      proj = mat4Ortho(halfH * aspect, halfH, near, far);
    } else {
      proj = mat4Perspective(32.0 * Math.PI / 180.0, aspect, near, far);
    }
    return { proj, view, eye };
  }

  function pickPartAtClient(clientX, clientY) {
    if (!gl || !pickProg || !canvas || parts.length === 0) return -1;
    const rect = canvas.getBoundingClientRect();
    const px = Math.floor((clientX - rect.left) * (canvas.width / Math.max(rect.width, 1)));
    const py = Math.floor((rect.height - (clientY - rect.top)) * (canvas.height / Math.max(rect.height, 1)));
    if (px < 0 || py < 0 || px >= canvas.width || py >= canvas.height) return -1;

    ensurePickFbo(canvas.width, canvas.height);
    gl.bindFramebuffer(gl.FRAMEBUFFER, pickFbo);
    gl.viewport(0, 0, pickW, pickH);
    gl.enable(gl.SCISSOR_TEST);
    gl.scissor(px, py, 1, 1);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.enable(gl.DEPTH_TEST);
    gl.depthMask(true);
    gl.disable(gl.BLEND);
    gl.disable(gl.CULL_FACE);

    const { proj, view } = computeMatrices(pickW, pickH);
    const sp = getSectionParams();

    gl.useProgram(pickProg);
    gl.uniformMatrix4fv(gl.getUniformLocation(pickProg, "uProj"), false, proj);
    gl.uniformMatrix4fv(gl.getUniformLocation(pickProg, "uView"), false, view);
    gl.uniform1f(gl.getUniformLocation(pickProg, "uClipEnabled"), sp.enabled);
    gl.uniform3fv(gl.getUniformLocation(pickProg, "uClipNormal"), sp.normal);
    gl.uniform1f(gl.getUniformLocation(pickProg, "uClipOffset"), sp.offset);

    const locOff = gl.getUniformLocation(pickProg, "uPartOffset");
    const locCol = gl.getUniformLocation(pickProg, "uPickColor");

    for (let i = 0; i < parts.length; i++) {
      const p = parts[i];
      if (!p.gl || !partIsVisible(p)) continue;
      const code = i + 1;
      gl.uniform3f(
        locOff,
        p.explode_vector[0] * state.explode,
        p.explode_vector[1] * state.explode,
        p.explode_vector[2] * state.explode
      );
      gl.uniform4f(locCol, (code & 255) / 255, ((code >> 8) & 255) / 255, ((code >> 16) & 255) / 255, 1.0);
      gl.bindVertexArray(p.gl.meshVao);
      gl.drawElements(gl.TRIANGLES, p.gl.indexCount, gl.UNSIGNED_INT, 0);
    }

    const pixel = new Uint8Array(4);
    gl.readPixels(px, py, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
    gl.disable(gl.SCISSOR_TEST);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);

    const id = pixel[0] + (pixel[1] << 8) + (pixel[2] << 16);
    return id > 0 && id <= parts.length ? id - 1 : -1;
  }

  function updateCutFrameGeometry(sp) {
    if (!gl || !cutFrameBuf || sp.enabled < 0.5) return;
    const pad = Math.max(sceneRadius * 0.14, 2.0);
    const x0 = sceneMin[0] - pad, x1 = sceneMax[0] + pad;
    const y0 = sceneMin[1] - pad, y1 = sceneMax[1] + pad;
    const z0 = sceneMin[2] - pad, z1 = sceneMax[2] + pad;
    const c = sp.coord;
    let pts;
    if (sp.axisIdx === 0) {
      pts = [
        c, y0, z0, c, y1, z0,
        c, y1, z0, c, y1, z1,
        c, y1, z1, c, y0, z1,
        c, y0, z1, c, y0, z0,
      ];
    } else if (sp.axisIdx === 1) {
      pts = [
        x0, c, z0, x1, c, z0,
        x1, c, z0, x1, c, z1,
        x1, c, z1, x0, c, z1,
        x0, c, z1, x0, c, z0,
      ];
    } else {
      pts = [
        x0, y0, c, x1, y0, c,
        x1, y0, c, x1, y1, c,
        x1, y1, c, x0, y1, c,
        x0, y1, c, x0, y0, c,
      ];
    }
    gl.bindBuffer(gl.ARRAY_BUFFER, cutFrameBuf);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, new Float32Array(pts));
  }

  function renderTriad(viewMat) {
    if (!triadCanvas) return;
    const ctx = triadCanvas.getContext("2d");
    if (!ctx) return;
    const w = triadCanvas.width;
    const h = triadCanvas.height;
    ctx.clearRect(0, 0, w, h);
    const cx = w * 0.5;
    const cy = h * 0.5;
    const len = 24;
    const axes = [
      { label: "X", v: [1, 0, 0], col: "#F43F5E" },
      { label: "Y", v: [0, 1, 0], col: "#10B981" },
      { label: "Z", v: [0, 0, 1], col: "#38BDF8" },
    ];
    const projected = axes.map((a) => {
      const rx = viewMat[0] * a.v[0] + viewMat[4] * a.v[1] + viewMat[8] * a.v[2];
      const ry = viewMat[1] * a.v[0] + viewMat[5] * a.v[1] + viewMat[9] * a.v[2];
      const rz = viewMat[2] * a.v[0] + viewMat[6] * a.v[1] + viewMat[10] * a.v[2];
      return { label: a.label, col: a.col, x: rx, y: -ry, z: rz };
    }).sort((a, b) => a.z - b.z);

    ctx.lineWidth = 2;
    ctx.font = "bold 10px 'JetBrains Mono', ui-monospace, monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    projected.forEach((a) => {
      const ex = cx + a.x * len;
      const ey = cy + a.y * len;
      ctx.strokeStyle = a.col;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(ex, ey);
      ctx.stroke();
      ctx.fillStyle = a.col;
      ctx.fillText(a.label, cx + a.x * (len + 9), cy + a.y * (len + 9));
    });
  }

  function renderWebGL2() {
    if (!gl || !canvas) return;
    const w = canvas.width;
    const h = canvas.height;
    if (w < 2 || h < 2) return;

    const isWhite = state.theme === "iso_white";
    const themeTokens = (SCENE.themes && SCENE.themes[state.theme]) || {};
    const bgRgb = hexToRgb01(themeTokens.bg || (isWhite ? "#FFFFFF" : "#0B192C"));
    const accentRgb = hexToRgb01(themeTokens.text_accent || (isWhite ? "#0284C7" : "#38BDF8"));
    const sectionRgb = hexToRgb01(themeTokens.line_section || (isWhite ? "#DC2626" : "#F43F5E"));
    const hatchRgb = hexToRgb01(themeTokens.hatch || (isWhite ? "#64748B" : "#0284C7"));
    const visEdgeRgb = hexToRgb01(themeTokens.line_visible || (isWhite ? "#0F172A" : "#FFFFFF"));
    const hidEdgeRgb = hexToRgb01(themeTokens.line_hidden || (isWhite ? "#94A3B8" : "#475569"));

    gl.viewport(0, 0, w, h);
    gl.clearColor(bgRgb[0], bgRgb[1], bgRgb[2], 1.0);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);

    const { proj, view, eye } = computeMatrices(w, h);
    renderTriad(view);
    const sp = getSectionParams();

    // 1. Reference grid on ground plane
    if (gridVao && gridCount > 0) {
      gl.useProgram(edgeProg);
      gl.enable(gl.DEPTH_TEST);
      gl.depthMask(false);
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
      gl.uniformMatrix4fv(gl.getUniformLocation(edgeProg, "uProj"), false, proj);
      gl.uniformMatrix4fv(gl.getUniformLocation(edgeProg, "uView"), false, view);
      gl.uniform3f(gl.getUniformLocation(edgeProg, "uPartOffset"), 0, 0, 0);
      gl.uniform1f(gl.getUniformLocation(edgeProg, "uDepthBias"), 0.0);
      gl.uniform1f(gl.getUniformLocation(edgeProg, "uClipEnabled"), 0.0);
      gl.uniform3fv(gl.getUniformLocation(edgeProg, "uClipNormal"), sp.normal);
      gl.uniform1f(gl.getUniformLocation(edgeProg, "uClipOffset"), sp.offset);
      if (isWhite) {
        gl.uniform4f(gl.getUniformLocation(edgeProg, "uColor"), 0.06, 0.09, 0.16, 0.09);
      } else {
        gl.uniform4f(gl.getUniformLocation(edgeProg, "uColor"), accentRgb[0], accentRgb[1], accentRgb[2], 0.14);
      }
      gl.bindVertexArray(gridVao);
      gl.drawArrays(gl.LINES, 0, gridCount);
      gl.depthMask(true);
    }

    const modeMap = { blueprint: 0, iso: 1, xray: 2, wireframe: 3 };
    const modeIdx = modeMap[state.shadingMode] ?? 0;
    const anySelection = state.selectedPartId >= 0 || state.selectedBomItem !== null;
    const hatchPitch = Math.max(sceneRadius * 0.055, 1.8);

    // 2. Solid / X-Ray / Hidden-Line Depth Pass
    gl.useProgram(solidProg);
    gl.enable(gl.DEPTH_TEST);
    gl.depthFunc(gl.LEQUAL);
    gl.enable(gl.POLYGON_OFFSET_FILL);
    gl.polygonOffset(1.4, 1.8);

    if (modeIdx === 2) {
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
      gl.depthMask(false);
      gl.disable(gl.CULL_FACE);
    } else {
      gl.disable(gl.BLEND);
      gl.depthMask(true);
      if (sp.enabled > 0.5) {
        gl.disable(gl.CULL_FACE);
      } else {
        gl.enable(gl.CULL_FACE);
        gl.cullFace(gl.BACK);
      }
    }

    gl.uniformMatrix4fv(gl.getUniformLocation(solidProg, "uProj"), false, proj);
    gl.uniformMatrix4fv(gl.getUniformLocation(solidProg, "uView"), false, view);
    gl.uniform1i(gl.getUniformLocation(solidProg, "uShadingMode"), modeIdx);
    gl.uniform1i(gl.getUniformLocation(solidProg, "uTheme"), isWhite ? 1 : 0);
    gl.uniform3fv(gl.getUniformLocation(solidProg, "uCameraPos"), eye);
    gl.uniform3fv(gl.getUniformLocation(solidProg, "uBgColor"), bgRgb);
    gl.uniform3fv(gl.getUniformLocation(solidProg, "uAccentColor"), accentRgb);
    gl.uniform3fv(gl.getUniformLocation(solidProg, "uSectionColor"), sectionRgb);
    gl.uniform3fv(gl.getUniformLocation(solidProg, "uHatchColor"), hatchRgb);
    gl.uniform1f(gl.getUniformLocation(solidProg, "uClipEnabled"), sp.enabled);
    gl.uniform3fv(gl.getUniformLocation(solidProg, "uClipNormal"), sp.normal);
    gl.uniform1f(gl.getUniformLocation(solidProg, "uClipOffset"), sp.offset);
    gl.uniform1i(gl.getUniformLocation(solidProg, "uClipAxis"), sp.axisIdx);
    gl.uniform1f(gl.getUniformLocation(solidProg, "uHatchPitch"), hatchPitch);

    const uPartOffS = gl.getUniformLocation(solidProg, "uPartOffset");
    const uPartIdS = gl.getUniformLocation(solidProg, "uPartId");
    const uTintS = gl.getUniformLocation(solidProg, "uBaseTint");
    const uHiS = gl.getUniformLocation(solidProg, "uHighlight");
    const uDimS = gl.getUniformLocation(solidProg, "uDimmed");

    for (let i = 0; i < parts.length; i++) {
      const p = parts[i];
      if (!p.gl || !partIsVisible(p)) continue;
      const isSel = partMatchesSelection(p);
      const isHov = partMatchesHover(p);
      const hi = isSel ? 1.0 : (isHov ? 0.55 : 0.0);
      const dim = anySelection && !isSel && !isHov && state.isolatedPartId < 0 ? 1.0 : 0.0;

      gl.uniform3f(
        uPartOffS,
        p.explode_vector[0] * state.explode,
        p.explode_vector[1] * state.explode,
        p.explode_vector[2] * state.explode
      );
      gl.uniform1i(uPartIdS, p.bom_item_no !== null ? p.bom_item_no : p.id);
      gl.uniform3fv(uTintS, p.tint);
      gl.uniform1f(uHiS, hi);
      gl.uniform1f(uDimS, dim);

      gl.bindVertexArray(p.gl.meshVao);
      gl.drawElements(gl.TRIANGLES, p.gl.indexCount, gl.UNSIGNED_INT, 0);
    }
    gl.disable(gl.POLYGON_OFFSET_FILL);
    gl.depthMask(true);

    // 3. Crisp Crease Edge Pass
    gl.useProgram(edgeProg);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    gl.uniformMatrix4fv(gl.getUniformLocation(edgeProg, "uProj"), false, proj);
    gl.uniformMatrix4fv(gl.getUniformLocation(edgeProg, "uView"), false, view);
    gl.uniform1f(gl.getUniformLocation(edgeProg, "uClipEnabled"), sp.enabled);
    gl.uniform3fv(gl.getUniformLocation(edgeProg, "uClipNormal"), sp.normal);
    gl.uniform1f(gl.getUniformLocation(edgeProg, "uClipOffset"), sp.offset);

    const uPartOffE = gl.getUniformLocation(edgeProg, "uPartOffset");
    const uColE = gl.getUniformLocation(edgeProg, "uColor");
    const uBiasE = gl.getUniformLocation(edgeProg, "uDepthBias");

    // In Wireframe/Hidden mode, first render occluded rear edges faintly for authentic blueprint HLR feel
    if (modeIdx === 3) {
      gl.depthFunc(gl.GREATER);
      gl.depthMask(false);
      gl.uniform1f(uBiasE, 0.0);
      for (let i = 0; i < parts.length; i++) {
        const p = parts[i];
        if (!p.gl || !p.gl.edgeVao || !partIsVisible(p)) continue;
        gl.uniform3f(
          uPartOffE,
          p.explode_vector[0] * state.explode,
          p.explode_vector[1] * state.explode,
          p.explode_vector[2] * state.explode
        );
        gl.uniform4f(uColE, hidEdgeRgb[0], hidEdgeRgb[1], hidEdgeRgb[2], isWhite ? 0.28 : 0.34);
        gl.bindVertexArray(p.gl.edgeVao);
        gl.drawArrays(gl.LINES, 0, p.gl.edgeVertCount);
      }
      gl.depthFunc(gl.LEQUAL);
      gl.depthMask(true);
    }

    gl.uniform1f(uBiasE, 0.00035);
    for (let i = 0; i < parts.length; i++) {
      const p = parts[i];
      if (!p.gl || !p.gl.edgeVao || !partIsVisible(p)) continue;
      const isSel = partMatchesSelection(p);
      const isHov = partMatchesHover(p);
      const dim = anySelection && !isSel && !isHov && state.isolatedPartId < 0;

      gl.uniform3f(
        uPartOffE,
        p.explode_vector[0] * state.explode,
        p.explode_vector[1] * state.explode,
        p.explode_vector[2] * state.explode
      );

      let er, eg, eb, ea;
      if (isSel) {
        er = sectionRgb[0]; eg = sectionRgb[1]; eb = sectionRgb[2]; ea = 1.0;
      } else if (isHov) {
        er = accentRgb[0]; eg = accentRgb[1]; eb = accentRgb[2]; ea = 1.0;
      } else if (modeIdx === 0) {
        // Blueprint Technical glowing crisp cyan-white edges
        er = 0.78; eg = 0.94; eb = 1.0; ea = dim ? 0.30 : 0.92;
      } else if (modeIdx === 1) {
        // ISO Shaded crisp dark technical ink edges
        er = 0.06; eg = 0.09; eb = 0.16; ea = dim ? 0.25 : 0.88;
      } else if (modeIdx === 2) {
        // X-Ray luminous edges
        er = isWhite ? 0.04 : accentRgb[0];
        eg = isWhite ? 0.34 : accentRgb[1];
        eb = isWhite ? 0.68 : accentRgb[2];
        ea = dim ? 0.22 : 0.72;
      } else {
        // Wireframe visible edges
        er = visEdgeRgb[0]; eg = visEdgeRgb[1]; eb = visEdgeRgb[2]; ea = dim ? 0.28 : 0.96;
      }

      gl.uniform4f(uColE, er, eg, eb, ea);
      gl.bindVertexArray(p.gl.edgeVao);
      gl.drawArrays(gl.LINES, 0, p.gl.edgeVertCount);
    }

    // 4. Active Section Plane Indicator Frame
    if (sp.enabled > 0.5 && cutFrameVao) {
      updateCutFrameGeometry(sp);
      gl.uniform3f(uPartOffE, 0, 0, 0);
      gl.uniform1f(uClipEnabledLoc(), 0.0);
      gl.uniform1f(uBiasE, 0.0);
      gl.uniform4f(uColE, sectionRgb[0], sectionRgb[1], sectionRgb[2], 0.82);
      gl.bindVertexArray(cutFrameVao);
      gl.drawArrays(gl.LINES, 0, 8);
    }

    gl.bindVertexArray(null);
  }

  function uClipEnabledLoc() {
    return gl.getUniformLocation(edgeProg, "uClipEnabled");
  }

  // ---------- Software 2D Canvas Fallback when WebGL2 is unavailable ----------
  function renderSoftwareFallback() {
    if (!fallbackCanvas) return;
    const ctx = fallbackCanvas.getContext("2d");
    if (!ctx) return;
    const w = fallbackCanvas.width;
    const h = fallbackCanvas.height;
    const isWhite = state.theme === "iso_white";
    ctx.fillStyle = isWhite ? "#FFFFFF" : "#0B192C";
    ctx.fillRect(0, 0, w, h);

    const { proj, view } = computeMatrices(w, h);
    renderTriad(view);
    const vp = new Float32Array(16);
    for (let c = 0; c < 4; c++) {
      for (let r = 0; r < 4; r++) {
        vp[c * 4 + r] =
          proj[0 * 4 + r] * view[c * 4 + 0] +
          proj[1 * 4 + r] * view[c * 4 + 1] +
          proj[2 * 4 + r] * view[c * 4 + 2] +
          proj[3 * 4 + r] * view[c * 4 + 3];
      }
    }

    function projectPt(x, y, z) {
      const cx = vp[0] * x + vp[4] * y + vp[8] * z + vp[12];
      const cy = vp[1] * x + vp[5] * y + vp[9] * z + vp[13];
      const cw = vp[3] * x + vp[7] * y + vp[11] * z + vp[15];
      if (cw <= 1e-4) return null;
      return [(cx / cw * 0.5 + 0.5) * w, (0.5 - cy / cw * 0.5) * h];
    }

    ctx.lineWidth = 1.2;
    parts.forEach((p) => {
      if (!partIsVisible(p)) return;
      const isSel = partMatchesSelection(p);
      ctx.strokeStyle = isSel ? "#F43F5E" : (isWhite ? "#0F172A" : "#38BDF8");
      ctx.beginPath();
      const ox = p.explode_vector[0] * state.explode;
      const oy = p.explode_vector[1] * state.explode;
      const oz = p.explode_vector[2] * state.explode;
      const e = p.edges;
      for (let i = 0; i + 5 < e.length; i += 6) {
        const a = projectPt(e[i] + ox, e[i + 1] + oy, e[i + 2] + oz);
        const b = projectPt(e[i + 3] + ox, e[i + 4] + oy, e[i + 5] + oz);
        if (a && b) {
          ctx.moveTo(a[0], a[1]);
          ctx.lineTo(b[0], b[1]);
        }
      }
      ctx.stroke();
    });
  }

  // ---------- 3D Viewport Pointer & Wheel Interaction ----------
  function attachOrbitListeners(targetEl) {
    if (!targetEl) return;
    let mode = "none";
    let startX = 0;
    let startY = 0;
    let lastX = 0;
    let lastY = 0;
    let moved = false;

    targetEl.addEventListener("contextmenu", (e) => e.preventDefault());
    targetEl.addEventListener("pointerdown", (e) => {
      cam.anim = null;
      startX = lastX = e.clientX;
      startY = lastY = e.clientY;
      moved = false;
      if (e.button === 2 || e.button === 1 || e.shiftKey) {
        mode = "pan";
      } else if (e.button === 0) {
        mode = "orbit";
      }
      targetEl.setPointerCapture(e.pointerId);
    });

    targetEl.addEventListener("pointermove", (e) => {
      if (mode === "none") return;
      const dx = e.clientX - lastX;
      const dy = e.clientY - lastY;
      lastX = e.clientX;
      lastY = e.clientY;
      if (Math.hypot(e.clientX - startX, e.clientY - startY) > 4) moved = true;

      if (mode === "orbit") {
        cam.azimuth -= dx * 0.008;
        cam.elevation = Math.max(-Math.PI * 0.495, Math.min(Math.PI * 0.495, cam.elevation + dy * 0.008));
        state.dirty = true;
      } else if (mode === "pan") {
        const { eye, target, up } = getCameraVectors();
        const fwd = Vec3.norm(Vec3.sub(target, eye));
        const right = Vec3.norm(Vec3.cross(fwd, up));
        const camUp = Vec3.cross(right, fwd);
        const panScale = cam.distance * 0.0014;
        cam.target = Vec3.add(
          cam.target,
          Vec3.add(Vec3.scale(right, -dx * panScale), Vec3.scale(camUp, dy * panScale))
        );
        state.dirty = true;
      }
    });

    targetEl.addEventListener("pointerup", (e) => {
      const wasClick = !moved && mode === "orbit";
      mode = "none";
      if (wasClick && gl) {
        const picked = pickPartAtClient(e.clientX, e.clientY);
        toggleSelectByPart(picked);
      }
    });

    targetEl.addEventListener("wheel", (e) => {
      e.preventDefault();
      cam.anim = null;
      const factor = e.deltaY > 0 ? 1.12 : 1 / 1.12;
      cam.distance = Math.max(sceneRadius * 0.65, Math.min(sceneRadius * 16.0, cam.distance * factor));
      state.dirty = true;
    }, { passive: false });

    targetEl.addEventListener("dblclick", () => setCameraPreset("reset"));
  }

  // ---------- Resize & Animation Loop ----------
  function resizeCanvases() {
    const wrap = document.getElementById("viewport-3d-wrap");
    if (!wrap) return;
    const rect = wrap.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(1, Math.floor(rect.width * dpr));
    const h = Math.max(1, Math.floor(rect.height * dpr));
    if (canvas && (canvas.width !== w || canvas.height !== h)) {
      canvas.width = w;
      canvas.height = h;
      state.dirty = true;
    }
    if (fallbackCanvas && (fallbackCanvas.width !== w || fallbackCanvas.height !== h)) {
      fallbackCanvas.width = w;
      fallbackCanvas.height = h;
      state.dirty = true;
    }
  }

  function frameLoop(now) {
    if (cam.anim) {
      const t = Math.min(1.0, (now - cam.anim.startTime) / cam.anim.duration);
      const ease = 1.0 - Math.pow(1.0 - t, 3);
      cam.azimuth = cam.anim.from.azimuth + (cam.anim.to.azimuth - cam.anim.from.azimuth) * ease;
      cam.elevation = cam.anim.from.elevation + (cam.anim.to.elevation - cam.anim.from.elevation) * ease;
      cam.distance = cam.anim.from.distance + (cam.anim.to.distance - cam.anim.from.distance) * ease;
      cam.target = [
        cam.anim.from.target[0] + (cam.anim.to.target[0] - cam.anim.from.target[0]) * ease,
        cam.anim.from.target[1] + (cam.anim.to.target[1] - cam.anim.from.target[1]) * ease,
        cam.anim.from.target[2] + (cam.anim.to.target[2] - cam.anim.from.target[2]) * ease,
      ];
      state.dirty = true;
      if (t >= 1.0) cam.anim = null;
    }
    if (state.dirty) {
      state.dirty = false;
      if (gl) {
        renderWebGL2();
      } else {
        renderSoftwareFallback();
      }
    }
    requestAnimationFrame(frameLoop);
  }

  // ---------- Boot ----------
  instrumentSvgBomElements();
  populatePartsList();
  updateSectionUI();
  syncSelectionUI();

  const hasWebGL2 = initWebGL2();
  if (!hasWebGL2 && fallbackWrap) {
    fallbackWrap.hidden = false;
    attachOrbitListeners(fallbackCanvas);
  } else {
    attachOrbitListeners(canvas);
  }

  if (typeof ResizeObserver !== "undefined") {
    const ro = new ResizeObserver(() => {
      resizeCanvases();
      if (state.layout === "split") fitSvgToViewport();
    });
    const wrap = document.getElementById("viewport-3d-wrap");
    if (wrap) ro.observe(wrap);
    if (svgViewport) ro.observe(svgViewport);
  } else {
    window.addEventListener("resize", () => {
      resizeCanvases();
      if (state.layout === "split") fitSvgToViewport();
    });
  }

  // Optional URL hash initial state (e.g. #theme=iso_white&mode=xray&section=x:50&explode=65&select=2)
  function applyInitialHash() {
    const raw = (window.location.hash || "").replace(/^#/, "");
    if (!raw) return;
    const params = new URLSearchParams(raw);
    const t = params.get("theme");
    if (t === "iso_white" || t === "dark_blueprint") applyTheme(t);
    const l = params.get("layout");
    if ((l === "split" || l === "inspector") && workspaceEl) {
      state.layout = l;
      workspaceEl.classList.toggle("layout-split", l === "split");
      workspaceEl.classList.toggle("layout-inspector", l === "inspector");
      layoutButtons.forEach((b) => b.classList.toggle("active", b.getAttribute("data-layout") === l));
    }
    const m = params.get("mode");
    if (m === "blueprint" || m === "iso" || m === "xray" || m === "wireframe") {
      state.shadingMode = m;
      state.shadingManuallyLocked = true;
      updateShadingButtons();
    }
    const c = params.get("cam");
    if (c === "iso" || c === "front" || c === "top" || c === "right") {
      setCameraPreset(c);
      if (cam.anim) {
        cam.azimuth = cam.anim.to.azimuth;
        cam.elevation = cam.anim.to.elevation;
        cam.distance = cam.anim.to.distance;
        cam.target = cam.anim.to.target.slice();
        cam.anim = null;
      }
    }
    const sec = params.get("section");
    if (sec) {
      const segs = sec.split(":");
      const ax = segs[0];
      if (ax === "x" || ax === "y" || ax === "z") {
        state.sectionAxis = ax;
        const pct = parseFloat(segs[1]);
        if (Number.isFinite(pct)) {
          state.sectionNorm = Math.max(0, Math.min(1, pct / 100));
          if (secSlider) secSlider.value = String(Math.round(state.sectionNorm * 100));
        }
        state.sectionFlip = segs[2] === "flip";
        updateSectionUI();
      }
    }
    const ex = params.get("explode");
    if (ex !== null) {
      const pct = parseFloat(ex);
      if (Number.isFinite(pct)) {
        state.explode = Math.max(0, Math.min(1, pct / 100));
        if (explodeSlider) explodeSlider.value = String(Math.round(state.explode * 100));
        if (explodeReadout) explodeReadout.textContent = Math.round(state.explode * 100) + "%";
      }
    }
    const sel = params.get("select");
    if (sel !== null) {
      const itemNo = parseInt(sel, 10);
      if (Number.isFinite(itemNo)) toggleSelectByBom(itemNo);
    }
  }

  applyInitialHash();
  resizeCanvases();
  fitSvgToViewport();
  state.dirty = true;
  // Expose minimal inspection hook on window for automated testing / iframe hosts
  window.__TECHDRAW_VIEWER__ = {
    scene: SCENE,
    state,
    setTheme: applyTheme,
    setCameraPreset,
    setShadingMode(mode) {
      state.shadingMode = mode;
      state.shadingManuallyLocked = true;
      updateShadingButtons();
      state.dirty = true;
    },
    setSection(axis, norm, flip) {
      state.sectionAxis = axis;
      if (typeof norm === "number") {
        state.sectionNorm = Math.max(0, Math.min(1, norm));
        if (secSlider) secSlider.value = String(Math.round(state.sectionNorm * 100));
      }
      if (typeof flip === "boolean") state.sectionFlip = flip;
      updateSectionUI();
    },
    setExplode(factor) {
      state.explode = Math.max(0, Math.min(1, factor));
      if (explodeSlider) explodeSlider.value = String(Math.round(state.explode * 100));
      if (explodeReadout) explodeReadout.textContent = Math.round(state.explode * 100) + "%";
      state.dirty = true;
    },
    hasWebGL2,
  };
  requestAnimationFrame(frameLoop);
})();
"""
