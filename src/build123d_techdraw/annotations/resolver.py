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
