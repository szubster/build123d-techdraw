"""1:1 DXF flat pattern and multi-layer full drawing sheet export using ezdxf."""

from __future__ import annotations

import contextlib
import io
import math
import os
import warnings
from typing import TYPE_CHECKING, Any

import ezdxf
import svgelements as se
from ezdxf.document import Drawing

if TYPE_CHECKING:
    from build123d_techdraw.core.sheet import DrawingSheet


def _to_2d(pt: Any, plane: Any | None, scale: float = 1.0) -> tuple[float, float]:
    """Project a 3D point onto a 2D plane (or XY if plane is None)."""
    local = plane.to_local_coords(pt) if plane is not None else pt
    return (float(local.X) * scale, float(local.Y) * scale)


def _sample_edge_adaptive(
    edge: Any,
    plane: Any | None,
    tolerance: float = 0.05,
    scale: float = 1.0,
    max_depth: int = 10,
) -> list[tuple[float, float]]:
    """Adaptively tessellate a curved edge within chordal tolerance (in mm)."""
    pts: list[tuple[float, float]] = [_to_2d(edge.position_at(0.0), plane, scale)]

    def _subdivide(
        t0: float, p0: tuple[float, float], t1: float, p1: tuple[float, float], depth: int
    ) -> None:
        tm = 0.5 * (t0 + t1)
        pm = _to_2d(edge.position_at(tm), plane, scale)
        dx = p1[0] - p0[0]
        dy = p1[1] - p0[1]
        seg_len = math.hypot(dx, dy)
        if depth < max_depth:
            if depth < 2:
                need_split = True
            elif seg_len > 1e-9:
                dist = abs(dx * (p0[1] - pm[1]) - (p0[0] - pm[0]) * dy) / seg_len
                need_split = dist > tolerance
            else:
                need_split = math.hypot(pm[0] - p0[0], pm[1] - p0[1]) > tolerance
            if need_split:
                _subdivide(t0, p0, tm, pm, depth + 1)
                _subdivide(tm, pm, t1, p1, depth + 1)
                return
        pts.append(p1)

    p_end = _to_2d(edge.position_at(1.0), plane, scale)
    _subdivide(0.0, pts[0], 1.0, p_end, 0)
    return pts


def _detect_primary_flat_face(shape: Any) -> tuple[Any, Any | None]:
    """Extract the largest planar face and its datum Plane when given a 3D solid/part."""
    import build123d as bd

    solids = shape.solids() if hasattr(shape, "solids") else []
    if not solids:
        return shape, None

    faces = shape.faces() if hasattr(shape, "faces") else []
    planar_faces = [f for f in faces if getattr(f, "geom_type", None) == bd.GeomType.PLANE]
    if not planar_faces:
        return shape, None

    primary = max(planar_faces, key=lambda f: float(f.area))
    return primary, bd.Plane(primary)


def add_edge_to_dxf(
    msp: Any,
    edge: Any,
    layer: str = "VISIBLE",
    scale: float = 1.0,
    plane: Any | None = None,
    tolerance: float = 0.05,
) -> None:
    """Convert a single build123d Edge to DXF entities on the given layer."""
    gtype_str = str(getattr(edge, "geom_type", "")).upper()
    if "LINE" in gtype_str:
        v0 = _to_2d(edge.start_point(), plane, scale)
        v1 = _to_2d(edge.end_point(), plane, scale)
        msp.add_line(v0, v1, dxfattribs={"layer": layer})
    elif "CIRCLE" in gtype_str:
        c2d = _to_2d(edge.arc_center, plane, scale)
        radius = float(edge.radius) * scale
        p_start = _to_2d(edge.start_point(), plane, scale)
        p_mid = _to_2d(edge.position_at(0.5), plane, scale)
        p_end = _to_2d(edge.end_point(), plane, scale)
        coplanar = all(
            abs(math.hypot(pt[0] - c2d[0], pt[1] - c2d[1]) - radius) <= max(1e-3, tolerance)
            for pt in (p_start, p_mid, p_end)
        )
        if not coplanar:
            pts = _sample_edge_adaptive(edge, plane=plane, tolerance=tolerance, scale=scale)
            if len(pts) >= 2:
                msp.add_lwpolyline(pts, dxfattribs={"layer": layer})
        elif getattr(edge, "is_closed", False):
            msp.add_circle(c2d, radius, dxfattribs={"layer": layer})
        else:
            a_start = math.degrees(math.atan2(p_start[1] - c2d[1], p_start[0] - c2d[0]))
            a_mid = math.degrees(math.atan2(p_mid[1] - c2d[1], p_mid[0] - c2d[0]))
            a_end = math.degrees(math.atan2(p_end[1] - c2d[1], p_end[0] - c2d[0]))
            # Ensure CCW sweep from start through mid to end for DXF ARC
            sweep_mid = (a_mid - a_start) % 360.0
            sweep_end = (a_end - a_start) % 360.0
            if sweep_mid > sweep_end:
                a_start, a_end = a_end, a_start
            msp.add_arc(
                c2d, radius, start_angle=a_start, end_angle=a_end, dxfattribs={"layer": layer}
            )
    else:
        pts = _sample_edge_adaptive(edge, plane=plane, tolerance=tolerance, scale=scale)
        if len(pts) >= 2:
            msp.add_lwpolyline(pts, dxfattribs={"layer": layer})


class DXFRenderer:
    """Exports 2D CAD views and complete drawing sheets to standard multi-layer DXF files."""

    @classmethod
    def export_shape_to_dxf(
        cls,
        shape: Any,
        output_path: str,
        dxfversion: str = "R2010",
        plane: Any | None = None,
        tolerance: float = 0.05,
        strict: bool = False,
    ) -> None:
        """Export 2D cross-section, flat pattern, or 3D plate primary face to a 1:1 DXF file."""
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        doc: Drawing = ezdxf.new(dxfversion=dxfversion)  # type: ignore[attr-defined]
        msp = doc.modelspace()

        # Set up standard CAD layers
        doc.layers.new(name="VISIBLE", dxfattribs={"color": 7})  # White/Black
        doc.layers.new(name="HIDDEN", dxfattribs={"color": 8, "linetype": "DASHED"})  # Gray
        doc.layers.new(name="CENTER", dxfattribs={"color": 4, "linetype": "CENTER"})  # Cyan

        target_shape = shape
        active_plane = plane
        if active_plane is None:
            target_shape, active_plane = _detect_primary_flat_face(shape)

        edges: list[Any] = target_shape.edges() if hasattr(target_shape, "edges") else []
        for idx, edge in enumerate(edges):
            try:
                add_edge_to_dxf(
                    msp,
                    edge,
                    layer="VISIBLE",
                    scale=1.0,
                    plane=active_plane,
                    tolerance=tolerance,
                )
            except Exception as exc:
                if strict:
                    raise
                warnings.warn(
                    f"Failed to export edge {idx} to DXF: {exc}",
                    RuntimeWarning,
                    stacklevel=2,
                )

        doc.saveas(output_path)

    @classmethod
    def export_sheet_to_dxf(
        cls,
        sheet: DrawingSheet,
        output_path: str,
        dxfversion: str = "R2010",
    ) -> None:
        """Export the complete composed drawing sheet with standard CAD layers to DXF."""
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        doc: Drawing = ezdxf.new(dxfversion=dxfversion)  # type: ignore[attr-defined]
        msp = doc.modelspace()

        # Define standard CAD layers and colors (AutoCAD ACI colors)
        layers_cfg = [
            ("BORDER", 7, "CONTINUOUS"),  # White/Black
            ("TITLE_BLOCK", 7, "CONTINUOUS"),  # White/Black
            ("VISIBLE", 7, "CONTINUOUS"),  # White/Black
            ("HIDDEN", 8, "DASHED"),  # Gray
            ("CENTER", 4, "CENTER"),  # Cyan
            ("DIMENSIONS", 2, "CONTINUOUS"),  # Yellow
            ("ANNOTATIONS", 3, "CONTINUOUS"),  # Green
            ("HATCH", 9, "CONTINUOUS"),  # Light Gray
        ]
        for name, color, ltype in layers_cfg:
            with contextlib.suppress(Exception):
                doc.layers.new(name=name, dxfattribs={"color": color, "linetype": ltype})

        svg_code = sheet.to_svg()
        h_mm = sheet.height_mm

        doc_svg = se.SVG.parse(io.BytesIO(svg_code.encode("utf-8")))

        for el in doc_svg.elements():
            cls_str = el.values.get("class", "")
            cls_list = cls_str.split() if cls_str else []

            # Skip background fills
            if "sheet-bg" in cls_list or "blueprint-grid" in cls_list:
                continue

            # Layer mapping
            is_hidden = "line-hidden" in cls_list or "layer_hidden" in cls_list
            if not is_hidden:
                parent = getattr(el, "parent", None)
                while parent:
                    p_id = getattr(parent, "id", "") or parent.values.get("id", "")
                    p_cls = getattr(parent, "class", "") or parent.values.get("class", "")
                    if "hidden" in p_id or "hidden" in p_cls:
                        is_hidden = True
                        break
                    parent = getattr(parent, "parent", None)

            if is_hidden:
                target_layer = "HIDDEN"
            elif any(
                c in cls_list for c in ["border-main", "border-sub", "grid-ticks", "zone-text"]
            ):
                target_layer = "BORDER"
            elif any(
                c in cls_list
                for c in [
                    "title-bg",
                    "tb-title",
                    "tb-field-label",
                    "tb-field-val",
                    "tb-field-val-bold",
                    "tb-field-val-sm",
                ]
            ):
                target_layer = "TITLE_BLOCK"
            elif any(c in cls_list for c in ["line-center"]):
                target_layer = "CENTER"
            elif any(
                c in cls_list
                for c in [
                    "dim-line",
                    "dim-arrow",
                    "dim-text",
                    "radial-dim",
                    "diameter-dim",
                    "angular-dim",
                    "ordinate-dim",
                ]
            ):
                target_layer = "DIMENSIONS"
            elif any(
                c in cls_list
                for c in [
                    "leader-note",
                    "leader-text",
                    "bom-balloon",
                    "balloon-text",
                    "gdt-fcf",
                    "datum-symbol",
                    "surface-finish-symbol",
                    "welding-symbol",
                    "notes-text",
                    "view-title",
                    "view-scale",
                ]
            ):
                target_layer = "ANNOTATIONS"
            elif any(c in cls_list for c in ["section-hatch-fill", "sectionHatch"]):
                target_layer = "HATCH"
            else:
                target_layer = "VISIBLE"

            if isinstance(el, se.Shape):
                p = se.Path(el)
                p.reify()
                p.approximate_arcs_with_cubics()

                curr_pt: tuple[float, float] | None = None
                for seg in p:
                    if isinstance(seg, se.Move):
                        curr_pt = (float(seg.end.x), float(h_mm - seg.end.y))
                    elif isinstance(seg, se.Line):
                        nxt_pt = (float(seg.end.x), float(h_mm - seg.end.y))
                        if curr_pt is not None:
                            msp.add_line(curr_pt, nxt_pt, dxfattribs={"layer": target_layer})
                        curr_pt = nxt_pt
                    elif isinstance(seg, (se.CubicBezier, se.QuadraticBezier)):
                        # Discretize bezier curve
                        curve_pts = [seg.point(t) for t in [i / 10.0 for i in range(11)]]
                        for i in range(len(curve_pts) - 1):
                            p_a = (float(curve_pts[i].x), float(h_mm - curve_pts[i].y))
                            p_b = (float(curve_pts[i + 1].x), float(h_mm - curve_pts[i + 1].y))
                            msp.add_line(p_a, p_b, dxfattribs={"layer": target_layer})
                        curr_pt = (float(seg.end.x), float(h_mm - seg.end.y))
                    elif isinstance(seg, se.Close):
                        curr_pt = None

            elif isinstance(el, se.SVGText):
                txt = el.text
                if txt:
                    pt = se.Point(el.x or 0, el.y or 0) * el.transform
                    x = float(pt.x)
                    y = float(h_mm - pt.y)
                    fs_mm = float(el.font_size or 2.8)

                    # Extract rotation
                    a, b = el.transform.a, el.transform.b
                    angle_deg = math.degrees(math.atan2(b, a))

                    msp.add_text(
                        txt,
                        dxfattribs={
                            "layer": target_layer,
                            "height": fs_mm,
                            "rotation": angle_deg,
                            "insert": (x, y),
                        },
                    )

        doc.saveas(output_path)
