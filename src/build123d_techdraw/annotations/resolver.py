"""Topological entity resolution: projects 3D CAD geometry and semantic anchors to 2D view space."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import build123d as bd
from build123d.topology import Shape

from build123d_techdraw.annotations.anchors import Anchor, entity_center_3d

if TYPE_CHECKING:
    from build123d_techdraw.core.view import SheetView


def is_topological_entity(obj: Any) -> bool:
    """Check if an object is a build123d 3D topological shape or semantic Anchor."""
    if obj is None:
        return False
    return isinstance(obj, (Anchor, bd.Edge, bd.Vertex, bd.Face, bd.Solid, bd.Compound, bd.Vector))


def ensure_anchor(target: Any, shape: Shape[Any] | None = None) -> Anchor | None:
    """Return ``target`` if it is an ``Anchor``, or wrap a 3D topological entity in ``Anchor.of``."""
    if isinstance(target, Anchor):
        target._ensure_normalized_fingerprint(shape)
        return target
    if isinstance(target, (bd.Edge, bd.Vertex, bd.Face, bd.Solid, bd.Compound)):
        return Anchor.of(target, shape=shape)
    return None


def stale_reason_of(*targets: Any) -> str | None:
    """Return the first ``stale_reason`` among the stale ``Anchor`` targets, if any."""
    return next(
        (
            t.stale_reason
            for t in targets
            if isinstance(t, Anchor) and t.is_stale and t.stale_reason
        ),
        None,
    )


def resolve_point_3d(
    target: Any, view: SheetView | None = None
) -> tuple[float, float, float] | None:
    """Resolve an Anchor, topological entity, or 3D tuple to a 3D model point (X, Y, Z)."""
    shape = view.shape if view is not None else None
    if isinstance(target, Anchor):
        return target.resolve_point_3d(shape)
    if isinstance(target, (bd.Vertex, bd.Vector, bd.Edge, bd.Face, bd.Solid, bd.Compound)):
        return entity_center_3d(target)
    if isinstance(target, (tuple, list)) and len(target) == 3:
        return (float(target[0]), float(target[1]), float(target[2]))
    return None


def stale_point_to_sheet(target: Any, view: SheetView) -> tuple[float, float] | None:
    """Return the last-known sheet position of a stale anchor in the view's *current* placement.

    When ``target`` is a stale :class:`Anchor` with a remembered 3D model point, that point is
    projected through ``view`` as it is now (after any ``locate``/``scale_to``), so stale
    annotations follow the view instead of staying at stale paper coordinates. Returns ``None``
    if ``target`` is not a stale anchor or no model point was ever resolved.
    """
    if not (isinstance(target, Anchor) and target.is_stale):
        return None
    pt = target.last_resolved_point_3d
    if pt is None:
        return None
    return view.to_sheet_3d(pt[0], pt[1], pt[2])


def stale_radius_to_paper(target: Any, view: SheetView) -> float | None:
    """Return the last-known radius of a stale anchor in paper mm at the view's current scale."""
    if not (isinstance(target, Anchor) and target.is_stale):
        return None
    r = target.last_resolved_radius
    if r is None:
        return None
    return float(r) * view.scale


def resolve_point_to_sheet(target: Any, view: SheetView) -> tuple[float, float]:
    """Resolve a semantic Anchor, 3D vertex, vector, edge, or face to 2D sheet coordinates."""
    if isinstance(target, Anchor):
        return target.resolve_to_sheet(view)

    if isinstance(target, (tuple, list)):
        if len(target) == 2:
            return float(target[0]), float(target[1])
        if len(target) == 3:
            return view.to_sheet_3d(float(target[0]), float(target[1]), float(target[2]))

    pt_3d = resolve_point_3d(target, view)
    if pt_3d is not None:
        return view.to_sheet_3d(pt_3d[0], pt_3d[1], pt_3d[2])

    raise TypeError(f"Cannot resolve target of type {type(target).__name__} to sheet coordinates.")


def resolve_edge_to_sheet(
    edge: Any, view: SheetView
) -> tuple[tuple[float, float], tuple[float, float], float]:
    """Resolve an Edge or semantic Anchor to its projected sheet start/end points and 3D model length."""
    if isinstance(edge, Anchor):
        shape = view.shape
        ent = edge.resolve_entity(shape, prefer_kind="edge")
        if ent is None and edge.is_stale:
            # Fall back to the last-known model-space edge; it is projected through the
            # view's *current* placement below, so stale dimensions follow locate/scale_to.
            ent = edge.last_resolved_entity
        if isinstance(ent, bd.Edge):
            edge = ent
        elif edge.fingerprint is not None and edge.fingerprint.start_3d and edge.fingerprint.end_3d:
            s0 = view.to_sheet_3d(*edge.fingerprint.start_3d)
            s1 = view.to_sheet_3d(*edge.fingerprint.end_3d)
            return s0, s1, float(edge.fingerprint.length or 0.0)
        else:
            raise TypeError("Anchor did not resolve to a build123d.Edge.")

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
    """Resolve a circular Edge, cylindrical Face, or Anchor to (center_on_sheet, paper_radius, model_radius)."""
    if isinstance(target, Anchor):
        return circle_from_resolved_anchor(target, target.resolve_entity(view.shape), view)
    return _circle_or_cylinder_to_sheet(target, view)


def circle_from_resolved_anchor(
    anchor: Anchor, entity: Any, view: SheetView
) -> tuple[tuple[float, float], float, float]:
    """Like :func:`resolve_circle_or_cylinder_to_sheet` for an anchor already resolved to ``entity``.

    Lets callers that also need the anchor's point (via :meth:`Anchor.resolve`) avoid
    resolving the anchor a second time.
    """
    if isinstance(entity, (bd.Edge, bd.Face)):
        return _circle_or_cylinder_to_sheet(entity, view)
    if anchor.last_resolved_point_3d is not None and anchor.last_resolved_radius is not None:
        paper_center = view.to_sheet_3d(*anchor.last_resolved_point_3d)
        r = float(anchor.last_resolved_radius)
        return paper_center, r * view.scale, r
    return _circle_or_cylinder_to_sheet(anchor, view)


def _circle_or_cylinder_to_sheet(
    target: Any, view: SheetView
) -> tuple[tuple[float, float], float, float]:
    if isinstance(target, bd.Edge) and target.geom_type == bd.GeomType.CIRCLE:
        c = target.arc_center
        r = float(target.radius)
        paper_center = view.to_sheet_3d(float(c.X), float(c.Y), float(c.Z))
        return paper_center, r * view.scale, r

    if isinstance(target, bd.Face) and target.geom_type == bd.GeomType.CYLINDER:
        axis = target.axis_of_rotation
        c = axis.position if axis else (target.bounding_box().min + target.bounding_box().max) / 2.0
        r_val = target.radius
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
    """Resolve two edges, faces, anchors, or points to (apex_2d, start_angle_deg, end_angle_deg, angle_deg)."""
    shape = view.shape

    def as_edge(t: Any) -> Any:
        if isinstance(t, Anchor):
            ent = t.resolve_entity(shape, prefer_kind="edge")
            if ent is None and t.is_stale:
                # Last-known model-space entity; projected through the current view below.
                ent = t.last_resolved_entity
            return t if ent is None else ent
        return t

    eff1 = as_edge(target1)
    eff2 = as_edge(target2)

    if isinstance(eff1, bd.Edge) and isinstance(eff2, bd.Edge):
        s0_1, s1_1, _ = resolve_edge_to_sheet(eff1, view)
        s0_2, s1_2, _ = resolve_edge_to_sheet(eff2, view)

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
        v1_dir = (eff1.end_point() - eff1.start_point()).normalized()
        v2_dir = (eff2.end_point() - eff2.start_point()).normalized()
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
