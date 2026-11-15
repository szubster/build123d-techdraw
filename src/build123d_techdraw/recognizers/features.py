"""Automated 3D B-Rep feature recognition for build123d CAD solids and compounds."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import build123d as bd


@dataclass
class RecognizedHole:
    """Represents a recognized drilled, counterbored, countersunk, or threaded hole."""

    center_3d: tuple[float, float, float]
    diameter: float
    depth: float | str = "THRU"
    counterbore_dia: float | None = None
    counterbore_depth: float | None = None
    countersink_dia: float | None = None
    countersink_angle: float = 90.0
    axis_dir: tuple[float, float, float] = (0.0, 0.0, 1.0)
    quantity: int = 1
    thread_spec: str | None = None
    is_threaded: bool = False
    thread_source: str | None = None


@dataclass
class RecognizedBoltCircle:
    """Represents a circular hole pattern (pitch circle diameter)."""

    center_3d: tuple[float, float, float]
    diameter: float
    num_holes: int
    hole_feature: RecognizedHole

    @property
    def pcd(self) -> float:
        return self.diameter

    @property
    def hole_count(self) -> int:
        return self.num_holes

    @property
    def hole_diameter(self) -> float:
        return self.hole_feature.diameter


@dataclass
class RecognizedBoss:
    """Represents an external cylindrical boss or shaft."""

    center_3d: tuple[float, float, float]
    diameter: float
    height: float
    axis_dir: tuple[float, float, float] = (0.0, 0.0, 1.0)


@dataclass
class RecognizedStep:
    """Represents an interior planar step or shoulder along a principal axis."""

    axis: str  # "X", "Y", or "Z"
    ref_coord: float
    step_coord: float
    value: float
    face_center_3d: tuple[float, float, float]


def _is_internal_cylinder(face: bd.Face) -> bool:
    """Return True if a cylindrical face's outward normal points toward its axis (hole)."""
    axis = face.axis_of_rotation
    if axis is None:
        return True
    try:
        center = face.center()
        normal = face.normal_at(center)
        radial = center - axis.position
        radial = radial - axis.direction * radial.dot(axis.direction)
        if radial.length < 1e-6:
            return True
        return float(radial.dot(normal)) < 0.0
    except Exception:
        return True


def _detect_bd_warehouse_threads(shape: Any) -> list[tuple[tuple[float, float, float], str]]:
    """Extract explicit ``bd_warehouse`` ``IsoThread`` or fastener metadata from ``shape``."""
    from build123d_techdraw.annotations.threads import (
        _require_bd_warehouse,
        coerce_thread_designation,
        get_thread_spec,
    )

    fastener_mod, thread_mod = _require_bd_warehouse()
    iso_cls = thread_mod.IsoThread
    fastener_cls = (fastener_mod.Screw, fastener_mod.Nut)

    found: list[tuple[tuple[float, float, float], str]] = []
    queue: list[Any] = [shape]
    visited: set[int] = set()
    while queue:
        obj = queue.pop(0)
        if id(obj) in visited:
            continue
        visited.add(id(obj))
        if isinstance(obj, (iso_cls, *fastener_cls)) or hasattr(obj, "thread_size"):
            loc = (
                obj.location.position
                if hasattr(obj, "location") and obj.location
                else bd.Vector(0, 0, 0)
            )
            raw_spec = coerce_thread_designation(obj)
            try:
                spec_str = get_thread_spec(raw_spec).designation
            except ValueError:
                spec_str = raw_spec
            found.append(((float(loc.X), float(loc.Y), float(loc.Z)), spec_str))
        children = getattr(obj, "children", None)
        if isinstance(children, (list, tuple)):
            queue.extend(children)
    return found


def recognize_holes(
    shape: bd.Solid | bd.Compound | Any,
    *,
    infer_threads: bool = False,
) -> list[RecognizedHole]:
    """Inspect build123d topological faces to detect hole features, counterbores, and countersinks."""
    if not hasattr(shape, "faces"):
        return []

    try:
        faces = list(shape.faces())
    except Exception:
        return []

    cyl_faces = [
        f for f in faces if f.geom_type == bd.GeomType.CYLINDER and _is_internal_cylinder(f)
    ]
    if not cyl_faces:
        return []

    cone_faces = [f for f in faces if f.geom_type == bd.GeomType.CONE]

    bb_solid = shape.bounding_box()

    raw_holes: list[dict[str, Any]] = []

    for f in cyl_faces:
        axis = f.axis_of_rotation
        if axis is None:
            continue

        r = getattr(f, "radius", None)
        if r is None or float(r) <= 0.0:
            continue
        radius = float(r)
        dia = 2.0 * radius

        bb = f.bounding_box()
        f_len = float(
            bb.size.Z
            if abs(axis.direction.Z) > 0.8
            else (bb.size.Y if abs(axis.direction.Y) > 0.8 else bb.size.X)
        )
        f_center = f.center()
        pos = axis.position + axis.direction * (f_center - axis.position).dot(axis.direction)

        # Hole depth
        solid_dim = float(
            bb_solid.size.Z
            if abs(axis.direction.Z) > 0.8
            else (bb_solid.size.Y if abs(axis.direction.Y) > 0.8 else bb_solid.size.X)
        )
        is_thru = abs(f_len - solid_dim) < 1.0 or f_len >= solid_dim - 0.5
        depth_val: float | str = "THRU" if is_thru else round(f_len, 2)

        raw_holes.append(
            {
                "axis_pos": (float(pos.X), float(pos.Y), float(pos.Z)),
                "axis_dir": (
                    float(axis.direction.X),
                    float(axis.direction.Y),
                    float(axis.direction.Z),
                ),
                "diameter": round(dia, 2),
                "depth": depth_val,
                "f_len": f_len,
                "face": f,
            }
        )

    # Group coaxial cylinders (potential counterbore pairs)
    coaxial_groups: list[list[dict[str, Any]]] = []
    for h in raw_holes:
        matched = False
        for grp in coaxial_groups:
            rep = grp[0]
            # Check axis collinearity
            p1 = rep["axis_pos"]
            p2 = h["axis_pos"]
            d1 = rep["axis_dir"]
            d2 = h["axis_dir"]
            # Dot product of directions
            dot = d1[0] * d2[0] + d1[1] * d2[1] + d1[2] * d2[2]
            if abs(abs(dot) - 1.0) < 1e-2:
                # Perpendicular distance between axis positions
                dx = p2[0] - p1[0]
                dy = p2[1] - p1[1]
                dz = p2[2] - p1[2]
                cross_x = dy * d1[2] - dz * d1[1]
                cross_y = dz * d1[0] - dx * d1[2]
                cross_z = dx * d1[1] - dy * d1[0]
                perp_dist = math.sqrt(cross_x**2 + cross_y**2 + cross_z**2)
                if perp_dist < 0.5:
                    grp.append(h)
                    matched = True
                    break
        if not matched:
            coaxial_groups.append([h])

    recognized: list[RecognizedHole] = []

    for grp in coaxial_groups:
        if len(grp) == 1:
            h = grp[0]
            # Check for adjacent countersink cone
            csink_dia = None
            csink_ang = 90.0
            for cf in cone_faces:
                c_axis = cf.axis_of_rotation
                if c_axis:
                    c_dot = c_axis.direction.dot(bd.Vector(h["axis_dir"]))
                    if abs(abs(c_dot) - 1.0) < 1e-2:
                        c_bb = cf.bounding_box()
                        csink_dia = round(float(max(c_bb.size.X, c_bb.size.Y)), 2)
                        break

            recognized.append(
                RecognizedHole(
                    center_3d=h["axis_pos"],
                    diameter=h["diameter"],
                    depth=h["depth"],
                    countersink_dia=csink_dia,
                    countersink_angle=csink_ang,
                    axis_dir=h["axis_dir"],
                )
            )
        else:
            # Sort by diameter ascending: smaller is main hole, larger is counterbore
            grp.sort(key=lambda item: item["diameter"])
            main_h = grp[0]
            cbore_h = grp[1]
            recognized.append(
                RecognizedHole(
                    center_3d=main_h["axis_pos"],
                    diameter=main_h["diameter"],
                    depth=main_h["depth"],
                    counterbore_dia=cbore_h["diameter"],
                    counterbore_depth=round(cbore_h["f_len"], 2),
                    axis_dir=main_h["axis_dir"],
                )
            )

    # Match explicit bd_warehouse IsoThread metadata first
    explicit_threads = _detect_bd_warehouse_threads(shape)
    for r_hole in recognized:
        for t_pos, t_spec in explicit_threads:
            dist = math.dist(
                (r_hole.center_3d[0], r_hole.center_3d[1]),
                (t_pos[0], t_pos[1]),
            )
            if dist < 0.5:
                r_hole.is_threaded = True
                r_hole.thread_spec = t_spec
                r_hole.thread_source = "bd_warehouse"
                break

    # Infer threads from tap-drill diameters only when explicitly requested
    if infer_threads:
        from build123d_techdraw.annotations.threads import find_threads_by_tap_drill

        for r_hole in recognized:
            if r_hole.is_threaded:
                continue
            matches = find_threads_by_tap_drill(r_hole.diameter)
            if matches:
                r_hole.is_threaded = True
                r_hole.thread_spec = matches[0].designation
                r_hole.thread_source = "inferred"

    # Group identical holes by (diameter, depth, cbore_dia, csink_dia, thread_spec) to assign quantity
    grouped_counts: dict[tuple[Any, ...], list[RecognizedHole]] = {}
    for r_hole in recognized:
        sig = (
            r_hole.diameter,
            str(r_hole.depth),
            r_hole.counterbore_dia,
            r_hole.countersink_dia,
            r_hole.thread_spec,
        )
        grouped_counts.setdefault(sig, []).append(r_hole)

    for hole_list in grouped_counts.values():
        total_q = len(hole_list)
        for hole_item in hole_list:
            hole_item.quantity = total_q

    return recognized


def recognize_bolt_circles(shape: bd.Solid | bd.Compound | Any) -> list[RecognizedBoltCircle]:
    """Detect circular hole patterns (PCD bolt circles) from CAD geometry."""
    holes = recognize_holes(shape)
    if len(holes) < 3:
        return []

    # Filter holes sharing parallel axis and identical diameter
    axis_groups: dict[tuple[tuple[int, int, int], float], list[RecognizedHole]] = {}
    for h in holes:
        dx, dy, dz = h.axis_dir
        # Canonicalize direction along axis line
        if (
            dz < -1e-4
            or (abs(dz) <= 1e-4 and dy < -1e-4)
            or (abs(dz) <= 1e-4 and abs(dy) <= 1e-4 and dx < 0)
        ):
            dx, dy, dz = -dx, -dy, -dz
        sig = ((int(round(dx)), int(round(dy)), int(round(dz))), round(h.diameter, 2))
        axis_groups.setdefault(sig, []).append(h)

    bolt_circles: list[RecognizedBoltCircle] = []

    for _, h_list in axis_groups.items():
        if len(h_list) < 3:
            continue
        # Check if centers are equidistant from their average center
        avg_x = sum(h.center_3d[0] for h in h_list) / len(h_list)
        avg_y = sum(h.center_3d[1] for h in h_list) / len(h_list)
        avg_z = sum(h.center_3d[2] for h in h_list) / len(h_list)

        dists = [math.hypot(h.center_3d[0] - avg_x, h.center_3d[1] - avg_y) for h in h_list]
        avg_r = sum(dists) / len(dists)
        if avg_r > 5.0 and all(abs(d - avg_r) < 0.5 for d in dists):
            bolt_circles.append(
                RecognizedBoltCircle(
                    center_3d=(avg_x, avg_y, avg_z),
                    diameter=round(avg_r * 2.0, 2),
                    num_holes=len(h_list),
                    hole_feature=h_list[0],
                )
            )

    return bolt_circles


def recognize_bosses(shape: bd.Solid | bd.Compound | Any) -> list[RecognizedBoss]:
    """Detect external cylindrical bosses and shafts."""
    if not hasattr(shape, "faces"):
        return []

    try:
        faces = list(shape.faces())
    except Exception:
        return []

    boss_faces = [
        f for f in faces if f.geom_type == bd.GeomType.CYLINDER and not _is_internal_cylinder(f)
    ]
    if not boss_faces:
        return []

    seen: set[tuple[Any, ...]] = set()
    bosses: list[RecognizedBoss] = []

    for f in boss_faces:
        axis = f.axis_of_rotation
        r = getattr(f, "radius", None)
        if axis is None or r is None or float(r) <= 0.0:
            continue
        dia = round(2.0 * float(r), 2)
        bb = f.bounding_box()
        dx, dy, dz = float(axis.direction.X), float(axis.direction.Y), float(axis.direction.Z)
        f_len = float(bb.size.Z if abs(dz) > 0.8 else (bb.size.Y if abs(dy) > 0.8 else bb.size.X))
        pos = (float(axis.position.X), float(axis.position.Y), float(axis.position.Z))
        sig = (
            round(pos[0], 2),
            round(pos[1], 2),
            round(pos[2], 2),
            dia,
            round(f_len, 2),
        )
        if sig in seen:
            continue
        seen.add(sig)
        bosses.append(
            RecognizedBoss(
                center_3d=pos,
                diameter=dia,
                height=round(f_len, 2),
                axis_dir=(dx, dy, dz),
            )
        )

    return bosses


def recognize_steps(shape: bd.Solid | bd.Compound | Any) -> list[RecognizedStep]:
    """Detect interior planar steps/shoulders aligned with principal axes (X, Y, Z)."""
    if not hasattr(shape, "faces"):
        return []

    try:
        faces = list(shape.faces())
        bb = shape.bounding_box()
    except Exception:
        return []

    tol = 0.25
    bounds = {
        "X": (float(bb.min.X), float(bb.max.X)),
        "Y": (float(bb.min.Y), float(bb.max.Y)),
        "Z": (float(bb.min.Z), float(bb.max.Z)),
    }

    seen: set[tuple[str, float]] = set()
    steps: list[RecognizedStep] = []

    for f in faces:
        if f.geom_type != bd.GeomType.PLANE:
            continue
        try:
            c = f.center()
            n = f.normal_at(c)
        except Exception:
            continue

        axis_name: str | None = None
        coord = 0.0
        if abs(n.X) > 0.99:
            axis_name = "X"
            coord = float(c.X)
        elif abs(n.Y) > 0.99:
            axis_name = "Y"
            coord = float(c.Y)
        elif abs(n.Z) > 0.99:
            axis_name = "Z"
            coord = float(c.Z)

        if axis_name is None:
            continue

        ax_min, ax_max = bounds[axis_name]
        if coord <= ax_min + tol or coord >= ax_max - tol:
            continue

        key = (axis_name, round(coord, 2))
        if key in seen:
            continue
        seen.add(key)

        steps.append(
            RecognizedStep(
                axis=axis_name,
                ref_coord=ax_min,
                step_coord=coord,
                value=round(coord - ax_min, 2),
                face_center_3d=(float(c.X), float(c.Y), float(c.Z)),
            )
        )

    steps.sort(key=lambda s: (s.axis, s.value))
    return steps
