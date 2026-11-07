"""Automated 3D B-Rep feature recognition for build123d CAD solids and compounds."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import build123d as bd


@dataclass
class RecognizedHole:
    """Represents a recognized drilled, counterbored, or countersunk hole."""

    center_3d: tuple[float, float, float]
    diameter: float
    depth: float | str = "THRU"
    counterbore_dia: float | None = None
    counterbore_depth: float | None = None
    countersink_dia: float | None = None
    countersink_angle: float = 90.0
    axis_dir: tuple[float, float, float] = (0.0, 0.0, 1.0)
    quantity: int = 1


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


def recognize_holes(shape: bd.Solid | bd.Compound | Any) -> list[RecognizedHole]:
    """Inspect build123d topological faces to detect hole features, counterbores, and countersinks."""
    if not hasattr(shape, "faces"):
        return []

    try:
        faces = list(shape.faces())
    except Exception:
        return []

    cyl_faces = [f for f in faces if f.geom_type == bd.GeomType.CYLINDER]
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
        pos = axis.position

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

    # Group identical holes by (diameter, depth, cbore_dia, csink_dia) to assign quantity
    grouped_counts: dict[tuple[Any, ...], list[RecognizedHole]] = {}
    for r_hole in recognized:
        sig = (r_hole.diameter, str(r_hole.depth), r_hole.counterbore_dia, r_hole.countersink_dia)
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
