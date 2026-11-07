"""Topological entity resolution: projects 3D CAD geometry to 2D view space."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import build123d as bd

if TYPE_CHECKING:
    from build123d_techdraw.core.view import SheetView


def is_topological_entity(obj: Any) -> bool:
    """Check if an object is a build123d 3D topological shape (Edge, Vertex, Face, Solid)."""
    if obj is None:
        return False
    return isinstance(obj, (bd.Edge, bd.Vertex, bd.Face, bd.Solid, bd.Compound, bd.Vector))


def resolve_point_to_sheet(target: Any, view: SheetView) -> tuple[float, float]:
    """Resolve a 3D vertex, vector, edge center, or face center to 2D sheet coordinates."""
    if isinstance(target, (tuple, list)):
        if len(target) == 2:
            return float(target[0]), float(target[1])
        if len(target) == 3:
            return view.to_sheet_3d(float(target[0]), float(target[1]), float(target[2]))

    if isinstance(target, (bd.Vertex, bd.Vector)):
        return view.to_sheet_3d(float(target.X), float(target.Y), float(target.Z))

    if isinstance(target, bd.Edge):
        if target.geom_type == bd.GeomType.CIRCLE:
            c = target.arc_center
            return view.to_sheet_3d(float(c.X), float(c.Y), float(c.Z))
        bb = target.bounding_box()
        mid = (bb.min + bb.max) / 2.0
        return view.to_sheet_3d(float(mid.X), float(mid.Y), float(mid.Z))

    if isinstance(target, bd.Face):
        if target.geom_type == bd.GeomType.CYLINDER and target.axis_of_rotation:
            pos = target.axis_of_rotation.position
            return view.to_sheet_3d(float(pos.X), float(pos.Y), float(pos.Z))
        bb = target.bounding_box()
        mid = (bb.min + bb.max) / 2.0
        return view.to_sheet_3d(float(mid.X), float(mid.Y), float(mid.Z))

    if isinstance(target, (bd.Solid, bd.Compound)):
        bb = target.bounding_box()
        mid = (bb.min + bb.max) / 2.0
        return view.to_sheet_3d(float(mid.X), float(mid.Y), float(mid.Z))

    raise TypeError(f"Cannot resolve target of type {type(target).__name__} to sheet coordinates.")


def resolve_edge_to_sheet(
    edge: Any, view: SheetView
) -> tuple[tuple[float, float], tuple[float, float], float]:
    """Resolve an Edge to its projected sheet start and end points and true 3D model length."""
    if not isinstance(edge, bd.Edge):
        raise TypeError(f"Expected build123d.Edge, got {type(edge).__name__}")

    v0 = edge.start_point()
    v1 = edge.end_point()
    s0 = view.to_sheet_3d(float(v0.X), float(v0.Y), float(v0.Z))
    s1 = view.to_sheet_3d(float(v1.X), float(v1.Y), float(v1.Z))
    length = float(edge.length)
    return s0, s1, length


def resolve_circle_or_cylinder_to_sheet(
    target: Any, view: SheetView
) -> tuple[tuple[float, float], float, float]:
    """Resolve a circular Edge or cylindrical Face to (center_on_sheet, paper_radius, model_radius)."""
    if isinstance(target, bd.Edge) and target.geom_type == bd.GeomType.CIRCLE:
        c = target.arc_center
        r = float(target.radius)
        paper_center = view.to_sheet_3d(float(c.X), float(c.Y), float(c.Z))
        return paper_center, r * view.scale, r

    if isinstance(target, bd.Face) and target.geom_type == bd.GeomType.CYLINDER:
        axis = target.axis_of_rotation
        c = axis.position if axis else (target.bounding_box().min + target.bounding_box().max) / 2.0
        r_val = getattr(target, "radius", None)
        r = float(r_val) if r_val is not None else 5.0
        paper_center = view.to_sheet_3d(float(c.X), float(c.Y), float(c.Z))
        return paper_center, r * view.scale, r

    raise TypeError(
        f"Target {type(target).__name__} is neither a circular Edge nor a cylindrical Face."
    )


def resolve_lines_intersection_2d(
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    p4: tuple[float, float],
) -> tuple[float, float] | None:
    """Find 2D intersection point of two infinite lines passing through (p1, p2) and (p3, p4)."""
    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3
    x4, y4 = p4
    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denom) < 1e-6:
        return None
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
    ix = x1 + t * (x2 - x1)
    iy = y1 + t * (y2 - y1)
    return (ix, iy)


def resolve_angle_to_sheet(
    target1: Any,
    target2: Any,
    view: SheetView,
    target_apex: Any = None,
) -> tuple[tuple[float, float], float, float, float]:
    """Resolve two edges, faces, or points to (apex_2d, start_angle_deg, end_angle_deg, angle_deg)."""
    import math

    if isinstance(target1, bd.Edge) and isinstance(target2, bd.Edge):
        s0_1, s1_1, _ = resolve_edge_to_sheet(target1, view)
        s0_2, s1_2, _ = resolve_edge_to_sheet(target2, view)

        if target_apex is not None:
            apex = resolve_point_to_sheet(target_apex, view)
        else:
            intersect = resolve_lines_intersection_2d(s0_1, s1_1, s0_2, s1_2)
            apex = intersect if intersect is not None else s0_1

        # Ray directions away from apex
        def ray_angle(
            p_start: tuple[float, float], p_end: tuple[float, float], ap: tuple[float, float]
        ) -> float:
            d_start = math.hypot(p_start[0] - ap[0], p_start[1] - ap[1])
            d_end = math.hypot(p_end[0] - ap[0], p_end[1] - ap[1])
            far_p = p_end if d_end > d_start else p_start
            return math.degrees(math.atan2(-(far_p[1] - ap[1]), far_p[0] - ap[0])) % 360.0

        a1 = ray_angle(s0_1, s1_1, apex)
        a2 = ray_angle(s0_2, s1_2, apex)

        # Compute 3D true angle between edge directions if available
        v1_dir = (target1.end_point() - target1.start_point()).normalized()
        v2_dir = (target2.end_point() - target2.start_point()).normalized()
        dot_val = min(1.0, max(-1.0, v1_dir.dot(v2_dir)))
        true_angle = math.degrees(math.acos(abs(dot_val)))

        diff = (a2 - a1) % 360.0
        if diff > 180.0:
            a1, a2 = a2, a1
            diff = (a2 - a1) % 360.0

        angle_val = (
            true_angle
            if abs(true_angle - diff) < 5.0 or abs(true_angle - (180.0 - diff)) < 5.0
            else diff
        )
        return apex, a1, a2, angle_val

    # Fallback / point resolution
    p_apex = resolve_point_to_sheet(target_apex if target_apex is not None else target1, view)
    p1 = resolve_point_to_sheet(target1, view)
    p2 = resolve_point_to_sheet(target2, view)

    a1 = math.degrees(math.atan2(-(p1[1] - p_apex[1]), p1[0] - p_apex[0])) % 360.0
    a2 = math.degrees(math.atan2(-(p2[1] - p_apex[1]), p2[0] - p_apex[0])) % 360.0

    diff = (a2 - a1) % 360.0
    if diff > 180.0:
        a1, a2 = a2, a1
        diff = (a2 - a1) % 360.0

    return p_apex, a1, a2, diff
