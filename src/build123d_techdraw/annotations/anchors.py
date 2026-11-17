"""Semantic topological anchoring: defense against the Topological Naming Problem (TNP)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

import build123d as bd
from build123d.topology import Shape

from build123d_techdraw.recognizers.features import _is_internal_cylinder

if TYPE_CHECKING:
    from build123d_techdraw.core.view import SheetView

#: Declarative cylinder/hole anchors (``Anchor.hole`` / ``Anchor.cylinder``) only bind to
#: cylinders whose radius is within this fraction of the requested radius ...
CYLINDER_RADIUS_REL_TOL = 0.25
#: ... or within this absolute amount (model units), so tiny holes tolerate small resizes.
CYLINDER_RADIUS_ABS_TOL = 0.25

#: Fingerprint anchors (``Anchor.of``) follow a captured feature through edits, so a resize is
#: expected; a candidate whose radius differs from the captured one by more than this factor
#: (in either direction) is treated as a different feature rather than a re-bind.
FINGERPRINT_RADIUS_MAX_RATIO = 2.0
#: Best fingerprint match score above which the captured entity is considered deleted.
FINGERPRINT_MAX_SCORE = 0.85


def _radius_within_tolerance(target: float, candidate: float) -> bool:
    """Whether ``candidate`` is close enough to ``target`` for a declarative cylinder anchor."""
    return abs(candidate - target) <= max(
        abs(target) * CYLINDER_RADIUS_REL_TOL, CYLINDER_RADIUS_ABS_TOL
    )


def _radius_ratio_ok(ref: float, candidate: float) -> bool:
    """Whether two radii are within :data:`FINGERPRINT_RADIUS_MAX_RATIO` of each other."""
    lo, hi = sorted((abs(ref), abs(candidate)))
    if hi <= 0.0:
        return True
    if lo <= 0.0:
        return False
    return hi / lo <= FINGERPRINT_RADIUS_MAX_RATIO


class AnchorDirection(Enum):
    """Direction along an axis for semantic extreme queries."""

    MAX = 1
    MIN = -1


Direction = AnchorDirection


def _parse_direction(direction: AnchorDirection | str | int | float | None) -> int:
    """Normalize an AnchorDirection, string ('max'/'min'), or integer (+1/-1) to +1 or -1."""
    if direction is None:
        return 1
    if isinstance(direction, AnchorDirection):
        return int(direction.value)
    if isinstance(direction, (int, float)):
        return 1 if float(direction) >= 0.0 else -1
    s = str(direction).strip().lower()
    if s in ("min", "minimum", "-", "-1", "neg", "negative", "bottom", "left", "front"):
        return -1
    return 1


def _parse_axis_vector(axis: Any) -> tuple[float, float, float]:
    """Convert a build123d.Axis, Vector, string ('X', 'Y', 'Z'), or 3-tuple into a unit 3-tuple."""
    if isinstance(axis, bd.Axis):
        d = axis.direction
        return (float(d.X), float(d.Y), float(d.Z))
    if isinstance(axis, bd.Vector):
        vn = axis.normalized() if axis.length > 1e-9 else bd.Vector(0.0, 0.0, 1.0)
        return (float(vn.X), float(vn.Y), float(vn.Z))
    if isinstance(axis, str):
        s = axis.strip().upper()
        mapping: dict[str, tuple[float, float, float]] = {
            "X": (1.0, 0.0, 0.0),
            "+X": (1.0, 0.0, 0.0),
            "-X": (-1.0, 0.0, 0.0),
            "Y": (0.0, 1.0, 0.0),
            "+Y": (0.0, 1.0, 0.0),
            "-Y": (0.0, -1.0, 0.0),
            "Z": (0.0, 0.0, 1.0),
            "+Z": (0.0, 0.0, 1.0),
            "-Z": (0.0, 0.0, -1.0),
        }
        if s in mapping:
            return mapping[s]
        raise ValueError(f"Unknown axis string: {axis!r}")
    if isinstance(axis, Sequence) and len(axis) == 3:
        vx, vy, vz = float(axis[0]), float(axis[1]), float(axis[2])
        mag = math.sqrt(vx * vx + vy * vy + vz * vz)
        if mag < 1e-9:
            return (0.0, 0.0, 1.0)
        return (vx / mag, vy / mag, vz / mag)
    raise TypeError(f"Cannot interpret {type(axis).__name__} as a 3D axis.")


def _parse_geom_type(geom_type: bd.GeomType | str | None) -> str | None:
    """Normalize a build123d GeomType or string name to uppercase GeomType name."""
    if geom_type is None:
        return None
    if isinstance(geom_type, bd.GeomType):
        return geom_type.name.upper()
    return str(geom_type).strip().upper()


def _radius(radius: float | None, diameter: float | None) -> float | None:
    """Return ``radius``, or half of ``diameter``, as a float (``None`` if neither is given)."""
    if radius is not None:
        return float(radius)
    return float(diameter) / 2.0 if diameter is not None else None


def entity_center_3d(entity: Any) -> tuple[float, float, float]:
    """Compute the characteristic 3D point of a Vertex, Edge, Face, or Solid."""
    if isinstance(entity, (bd.Vertex, bd.Vector)):
        return (float(entity.X), float(entity.Y), float(entity.Z))
    if isinstance(entity, bd.Edge) and entity.geom_type == bd.GeomType.CIRCLE:
        c = entity.arc_center
        return (float(c.X), float(c.Y), float(c.Z))
    if (
        isinstance(entity, bd.Face)
        and entity.geom_type == bd.GeomType.CYLINDER
        and entity.axis_of_rotation is not None
    ):
        pos = entity.axis_of_rotation.position
        return (float(pos.X), float(pos.Y), float(pos.Z))
    if isinstance(entity, Shape):
        bb = entity.bounding_box()
        mid = (bb.min + bb.max) * 0.5
        return (float(mid.X), float(mid.Y), float(mid.Z))
    return (0.0, 0.0, 0.0)


def _normalize_in_bbox(
    pt: tuple[float, float, float], bb: bd.BoundBox
) -> tuple[float, float, float]:
    """Return normalized [0, 1] coordinates of a 3D point inside a bounding box."""
    sx = float(bb.size.X) if float(bb.size.X) > 1e-6 else 1.0
    sy = float(bb.size.Y) if float(bb.size.Y) > 1e-6 else 1.0
    sz = float(bb.size.Z) if float(bb.size.Z) > 1e-6 else 1.0
    return (
        (pt[0] - float(bb.min.X)) / sx,
        (pt[1] - float(bb.min.Y)) / sy,
        (pt[2] - float(bb.min.Z)) / sz,
    )


def _extract_orientation(entity: Any) -> tuple[tuple[float, float, float] | None, bool]:
    """Extract primary orientation vector and whether sign is meaningful."""
    if isinstance(entity, bd.Face):
        if entity.geom_type in (bd.GeomType.CYLINDER, bd.GeomType.CONE):
            axis = entity.axis_of_rotation
            if axis is not None:
                d = axis.direction
                return ((float(d.X), float(d.Y), float(d.Z)), False)
        try:
            n = entity.normal_at()
            return ((float(n.X), float(n.Y), float(n.Z)), True)
        except Exception:
            return (None, False)
    if isinstance(entity, bd.Edge):
        if entity.geom_type == bd.GeomType.LINE:
            v = entity.end_point() - entity.start_point()
            if v.length > 1e-9:
                vn = v.normalized()
                return ((float(vn.X), float(vn.Y), float(vn.Z)), False)
        elif entity.geom_type == bd.GeomType.CIRCLE:
            try:
                n = entity.normal()
                return ((float(n.X), float(n.Y), float(n.Z)), False)
            except Exception:
                return (None, False)
    return (None, False)


@dataclass
class SemanticFingerprint:
    """Geometric and topological signature of a 3D entity for TNP-resilient re-binding."""

    entity_kind: str  # "vertex", "edge", "face", "solid"
    geom_type: str | None
    center_3d: tuple[float, float, float]
    normalized_pos: tuple[float, float, float] | None = None
    orientation: tuple[float, float, float] | None = None
    signed_orientation: bool = False
    is_internal_cylinder: bool | None = None
    radius: float | None = None
    length: float | None = None
    area: float | None = None
    start_3d: tuple[float, float, float] | None = None
    end_3d: tuple[float, float, float] | None = None

    @classmethod
    def from_entity(
        cls, entity: Any, shape: Shape[Any] | None = None, shape_bbox: bd.BoundBox | None = None
    ) -> SemanticFingerprint:
        """Create a SemanticFingerprint from a build123d Vertex, Edge, Face, or Solid.

        ``shape_bbox`` may be passed to reuse an already-computed bounding box of ``shape``.
        """
        if isinstance(entity, (bd.Vertex, bd.Vector)):
            kind = "vertex"
            gtype = "VERTEX"
        elif isinstance(entity, bd.Edge):
            kind = "edge"
            gtype = entity.geom_type.name.upper()
        elif isinstance(entity, bd.Face):
            kind = "face"
            gtype = entity.geom_type.name.upper()
        else:
            kind = "solid"
            gtype = None

        c3d = entity_center_3d(entity)
        norm_pos: tuple[float, float, float] | None = None
        try:
            if shape_bbox is None and shape is not None:
                shape_bbox = shape.bounding_box()
            if shape_bbox is not None:
                norm_pos = _normalize_in_bbox(c3d, shape_bbox)
        except Exception:
            norm_pos = None

        orient, signed_orient = _extract_orientation(entity)
        is_int_cyl: bool | None = None
        radius: float | None = None
        length: float | None = None
        area: float | None = None
        start_3d: tuple[float, float, float] | None = None
        end_3d: tuple[float, float, float] | None = None

        if isinstance(entity, bd.Face):
            area = float(entity.area)
            if entity.geom_type == bd.GeomType.CYLINDER:
                is_int_cyl = _is_internal_cylinder(entity)
                r_val = entity.radius
                if r_val is not None:
                    radius = float(r_val)
        elif isinstance(entity, bd.Edge):
            length = float(entity.length)
            if entity.geom_type == bd.GeomType.CIRCLE:
                radius = float(entity.radius)
            try:
                s0 = entity.start_point()
                s1 = entity.end_point()
                start_3d = (float(s0.X), float(s0.Y), float(s0.Z))
                end_3d = (float(s1.X), float(s1.Y), float(s1.Z))
            except Exception:
                pass

        return cls(
            entity_kind=kind,
            geom_type=gtype,
            center_3d=c3d,
            normalized_pos=norm_pos,
            orientation=orient,
            signed_orientation=signed_orient,
            is_internal_cylinder=is_int_cyl,
            radius=radius,
            length=length,
            area=area,
            start_3d=start_3d,
            end_3d=end_3d,
        )


class Anchor:
    """Persistent semantic topological descriptor immune to Topological Naming shifts."""

    MAX = AnchorDirection.MAX
    MIN = AnchorDirection.MIN

    def __init__(
        self,
        kind: str,
        *,
        fingerprint: SemanticFingerprint | None = None,
        initial_entity: Any = None,
        initial_shape: Shape[Any] | None = None,
        axis: tuple[float, float, float] | None = None,
        extreme_axis: tuple[float, float, float] | None = None,
        direction: int = 1,
        entity_type: str = "point",
        geom_type: str | None = None,
        radius: float | None = None,
        length: float | None = None,
        area: float | None = None,
        internal: bool | None = None,
        near: tuple[float, float, float] | None = None,
        index: int = 0,
        point_mode: str = "default",
        child_anchor: Anchor | None = None,
    ):
        self.kind = kind
        self.fingerprint = fingerprint
        self._initial_entity = initial_entity
        self._initial_shape = initial_shape
        self.axis = axis
        self.extreme_axis = extreme_axis
        self.direction = direction
        self.entity_type = entity_type
        self.geom_type = geom_type
        self.radius = radius
        self.length = length
        self.area = area
        self.internal = internal
        self.near = near
        self.index = index
        self.point_mode = point_mode
        self.child_anchor = child_anchor

        self.is_stale: bool = False
        self.stale_reason: str | None = None
        self.last_resolved_entity: Any = initial_entity
        self.last_resolved_point_3d: tuple[float, float, float] | None = (
            fingerprint.center_3d
            if fingerprint is not None
            else (near if near is not None else None)
        )
        self.last_resolved_radius: float | None = (
            fingerprint.radius if fingerprint is not None else radius
        )

    @classmethod
    def of(
        cls,
        entity: Any,
        *,
        shape: Shape[Any] | None = None,
        point_mode: str = "default",
    ) -> Anchor:
        """Create a persistent semantic anchor from a build123d Vertex, Edge, Face, or Solid."""
        if isinstance(entity, cls):
            return entity
        fp = SemanticFingerprint.from_entity(entity, shape=shape)
        return cls(
            "fingerprint",
            fingerprint=fp,
            initial_entity=entity,
            initial_shape=shape,
            entity_type=fp.entity_kind,
            geom_type=fp.geom_type,
            radius=fp.radius,
            point_mode=point_mode,
        )

    from_topology = of

    @classmethod
    def center_of(
        cls,
        target: Any = None,
        *,
        radius: float | None = None,
        diameter: float | None = None,
        axis: Any = None,
        near: Sequence[float] | bd.Vector | None = None,
        internal: bool | None = None,
        shape: Shape[Any] | None = None,
    ) -> Anchor:
        """Anchor to the center of a cylindrical face, circular edge, hole, or shape."""
        if isinstance(target, cls):
            return cls(
                "center_of",
                child_anchor=target,
                point_mode="center",
            )
        if target is not None and isinstance(
            target, (bd.Face, bd.Edge, bd.Vertex, bd.Solid, bd.Compound)
        ):
            return cls.of(target, shape=shape, point_mode="center")

        eff_r = _radius(radius, diameter)
        near_pt = _coerce_point_3d(near)
        if eff_r is not None or axis is not None or internal is not None or near_pt is not None:
            return cls.cylinder(
                radius=eff_r,
                axis=axis,
                internal=internal,
                near=near_pt,
            )
        return cls("center_of", point_mode="center")

    @classmethod
    def extreme(
        cls,
        axis: Any = bd.Axis.Z,
        direction: AnchorDirection | str | int = AnchorDirection.MAX,
        *,
        entity: str = "point",
        geom_type: bd.GeomType | str | None = None,
        near: Sequence[float] | bd.Vector | None = None,
    ) -> Anchor:
        """Anchor to the extreme point, vertex, edge, or face along a given 3D axis."""
        ax_vec = _parse_axis_vector(axis)
        dir_val = _parse_direction(direction)
        return cls(
            "extreme",
            axis=ax_vec,
            direction=dir_val,
            entity_type=entity.strip().lower(),
            geom_type=_parse_geom_type(geom_type),
            near=_coerce_point_3d(near),
        )

    @classmethod
    def max(
        cls,
        axis: Any = bd.Axis.Z,
        *,
        entity: str = "point",
        geom_type: bd.GeomType | str | None = None,
        near: Sequence[float] | bd.Vector | None = None,
    ) -> Anchor:
        """Shortcut for ``Anchor.extreme(axis, direction=AnchorDirection.MAX, ...)``."""
        return cls.extreme(
            axis=axis,
            direction=AnchorDirection.MAX,
            entity=entity,
            geom_type=geom_type,
            near=near,
        )

    @classmethod
    def min(
        cls,
        axis: Any = bd.Axis.Z,
        *,
        entity: str = "point",
        geom_type: bd.GeomType | str | None = None,
        near: Sequence[float] | bd.Vector | None = None,
    ) -> Anchor:
        """Shortcut for ``Anchor.extreme(axis, direction=AnchorDirection.MIN, ...)``."""
        return cls.extreme(
            axis=axis,
            direction=AnchorDirection.MIN,
            entity=entity,
            geom_type=geom_type,
            near=near,
        )

    @classmethod
    def hole(
        cls,
        diameter: float | None = None,
        *,
        radius: float | None = None,
        axis: Any = None,
        near: Sequence[float] | bd.Vector | None = None,
        index: int = 0,
    ) -> Anchor:
        """Declaratively anchor to an internal cylindrical hole feature."""
        return cls.cylinder(
            radius=radius,
            diameter=diameter,
            axis=axis,
            internal=True,
            near=near,
            index=index,
        )

    @classmethod
    def cylinder(
        cls,
        radius: float | None = None,
        *,
        diameter: float | None = None,
        axis: Any = None,
        internal: bool | None = None,
        near: Sequence[float] | bd.Vector | None = None,
        index: int = 0,
    ) -> Anchor:
        """Declaratively anchor to a cylindrical face (hole or external boss/shaft)."""
        eff_r = _radius(radius, diameter)
        return cls(
            "cylinder",
            axis=_parse_axis_vector(axis) if axis is not None else None,
            entity_type="face",
            geom_type="CYLINDER",
            radius=eff_r,
            internal=internal,
            near=_coerce_point_3d(near),
            index=index,
            point_mode="center",
        )

    @classmethod
    def face(
        cls,
        normal: Any = None,
        *,
        geom_type: bd.GeomType | str | None = None,
        axis: Any = None,
        direction: AnchorDirection | str | int | None = None,
        near: Sequence[float] | bd.Vector | None = None,
        area: float | None = None,
    ) -> Anchor:
        """Declaratively anchor to a topological Face by normal, geometry type, or extreme position."""
        return cls(
            "face",
            axis=_parse_axis_vector(normal) if normal is not None else None,
            extreme_axis=_parse_axis_vector(axis) if axis is not None else None,
            direction=_parse_direction(direction),
            entity_type="face",
            geom_type=_parse_geom_type(geom_type),
            near=_coerce_point_3d(near),
            area=area,
        )

    @classmethod
    def edge(
        cls,
        axis: Any = None,
        *,
        geom_type: bd.GeomType | str | None = None,
        extreme_axis: Any = None,
        direction: AnchorDirection | str | int | None = None,
        near: Sequence[float] | bd.Vector | None = None,
        length: float | None = None,
    ) -> Anchor:
        """Declaratively anchor to a topological Edge by tangent axis, geometry type, or extreme."""
        return cls(
            "edge",
            axis=_parse_axis_vector(axis) if axis is not None else None,
            extreme_axis=_parse_axis_vector(extreme_axis) if extreme_axis is not None else None,
            direction=_parse_direction(direction),
            entity_type="edge",
            geom_type=_parse_geom_type(geom_type),
            near=_coerce_point_3d(near),
            length=length,
        )

    @classmethod
    def vertex(
        cls,
        near: Sequence[float] | bd.Vector | None = None,
        *,
        axis: Any = None,
        direction: AnchorDirection | str | int | None = None,
    ) -> Anchor:
        """Declaratively anchor to a topological Vertex by proximity or extreme axis."""
        return cls(
            "vertex",
            axis=_parse_axis_vector(axis) if axis is not None else None,
            direction=_parse_direction(direction),
            entity_type="vertex",
            near=_coerce_point_3d(near),
        )

    def _ensure_normalized_fingerprint(self, shape: Shape[Any] | None) -> None:
        """Populate normalized bounding-box coordinates on first attachment to a shape."""
        if (
            self.fingerprint is not None
            and self.fingerprint.normalized_pos is None
            and shape is not None
        ):
            try:
                ref_shape = self._initial_shape if self._initial_shape is not None else shape
                self.fingerprint.normalized_pos = _normalize_in_bbox(
                    self.fingerprint.center_3d, ref_shape.bounding_box()
                )
                if self._initial_shape is None:
                    self._initial_shape = shape
            except Exception:
                pass

    def resolve_entity(self, shape: Shape[Any] | None, *, prefer_kind: str | None = None) -> Any:
        """Resolve this anchor against ``shape`` to a build123d topological entity or Vector."""
        self.is_stale = False
        self.stale_reason = None

        if self.child_anchor is not None:
            ent = self.child_anchor.resolve_entity(shape, prefer_kind=prefer_kind)
            self.is_stale = self.child_anchor.is_stale
            self.stale_reason = self.child_anchor.stale_reason
            self.last_resolved_entity = ent
            if ent is not None:
                self.last_resolved_point_3d = entity_center_3d(ent)
            return ent

        if shape is None:
            if self._initial_entity is not None:
                return self._initial_entity
            self.is_stale = True
            self.stale_reason = "No 3D shape available on view to resolve anchor."
            return None

        self._ensure_normalized_fingerprint(shape)

        if self.kind == "fingerprint":
            if shape is self._initial_shape and self._initial_entity is not None:
                self.last_resolved_entity = self._initial_entity
                self.last_resolved_point_3d = entity_center_3d(self._initial_entity)
                return self._initial_entity
            return self._resolve_fingerprint(shape)

        if self.kind == "center_of":
            c3d = entity_center_3d(shape)
            self.last_resolved_point_3d = c3d
            vec = bd.Vector(*c3d)
            self.last_resolved_entity = vec
            return vec

        if self.kind == "extreme":
            eff_kind = prefer_kind or self.entity_type
            return self._resolve_extreme(shape, eff_kind)

        if self.kind == "cylinder":
            return self._resolve_cylinder(shape)

        if self.kind == "face":
            return self._resolve_face(shape)

        if self.kind == "edge":
            return self._resolve_edge(shape)

        if self.kind == "vertex":
            return self._resolve_vertex(shape)

        self.is_stale = True
        self.stale_reason = f"Unsupported anchor kind: {self.kind!r}"
        return None

    def _resolve_fingerprint(self, shape: Shape[Any]) -> Any:
        fp = self.fingerprint
        if fp is None:
            self.is_stale = True
            self.stale_reason = "Missing semantic fingerprint."
            return None

        if fp.entity_kind == "solid":
            self.last_resolved_entity = shape
            self.last_resolved_point_3d = entity_center_3d(shape)
            return shape

        bb = shape.bounding_box()
        diag = max(
            math.sqrt(float(bb.size.X) ** 2 + float(bb.size.Y) ** 2 + float(bb.size.Z) ** 2),
            1.0,
        )

        if fp.entity_kind == "face":
            candidates = list(shape.faces())
        elif fp.entity_kind == "edge":
            candidates = list(shape.edges())
        else:
            candidates = list(shape.vertices())

        if not candidates:
            self.is_stale = True
            self.stale_reason = f"Shape has no {fp.entity_kind} entities."
            return None

        best_ent: Any = None
        best_score = float("inf")

        for cand in candidates:
            # Cheap geom-type pre-filter before building a full fingerprint
            if (
                fp.geom_type is not None
                and fp.entity_kind != "vertex"
                and cand.geom_type.name.upper() != fp.geom_type
            ):
                continue
            cand_fp = SemanticFingerprint.from_entity(cand, shape=shape, shape_bbox=bb)

            # 1. Geometry type filter
            if fp.geom_type is not None and cand_fp.geom_type != fp.geom_type:
                continue

            # 2. Internal vs external cylinder filter
            if (
                fp.is_internal_cylinder is not None
                and cand_fp.is_internal_cylinder is not None
                and fp.is_internal_cylinder != cand_fp.is_internal_cylinder
            ):
                continue

            # 3. Radius gate: a gross radius change means a different feature (e.g. the
            #    captured hole was deleted and only a much larger bore remains).
            if (
                fp.radius is not None
                and cand_fp.radius is not None
                and not _radius_ratio_ok(fp.radius, cand_fp.radius)
            ):
                continue

            # 4. Orientation check
            orient_penalty = 0.0
            if fp.orientation is not None and cand_fp.orientation is not None:
                dot = (
                    fp.orientation[0] * cand_fp.orientation[0]
                    + fp.orientation[1] * cand_fp.orientation[1]
                    + fp.orientation[2] * cand_fp.orientation[2]
                )
                eff_dot = dot if fp.signed_orientation else abs(dot)
                if eff_dot < 0.7:
                    continue
                orient_penalty = (1.0 - eff_dot) * 2.0

            # 5. Normalized & absolute position score
            if fp.normalized_pos is not None and cand_fp.normalized_pos is not None:
                norm_dist = math.dist(fp.normalized_pos, cand_fp.normalized_pos)
            else:
                norm_dist = math.dist(fp.center_3d, cand_fp.center_3d) / diag

            abs_dist = math.dist(fp.center_3d, cand_fp.center_3d) / diag
            pos_score = 0.65 * norm_dist + 0.35 * abs_dist

            # 6. Characteristic size score (radius, length, area)
            size_score = 0.0
            if fp.radius is not None and cand_fp.radius is not None:
                size_score = abs(fp.radius - cand_fp.radius) / max(fp.radius, 1.0) * 0.25
            elif fp.length is not None and cand_fp.length is not None:
                size_score = abs(fp.length - cand_fp.length) / max(fp.length, 1.0) * 0.2
            elif fp.area is not None and cand_fp.area is not None:
                s_ref = math.sqrt(max(fp.area, 1e-4))
                s_cand = math.sqrt(max(cand_fp.area, 1e-4))
                size_score = abs(s_ref - s_cand) / max(s_ref, 1.0) * 0.15

            total_score = pos_score + orient_penalty + size_score
            if total_score < best_score:
                best_score = total_score
                best_ent = cand

        # If no candidate matched or best score is unreasonably far (feature was deleted)
        if best_ent is None or best_score > FINGERPRINT_MAX_SCORE:
            self.is_stale = True
            self.stale_reason = (
                f"Could not re-bind {fp.entity_kind} ({fp.geom_type}) on updated shape "
                f"(best_score={best_score:.2f})."
            )
            return None

        self.last_resolved_entity = best_ent
        self.last_resolved_point_3d = entity_center_3d(best_ent)
        best_r: float | None = None
        if (
            isinstance(best_ent, bd.Face)
            or isinstance(best_ent, bd.Edge)
            and best_ent.geom_type == bd.GeomType.CIRCLE
        ):
            best_r = best_ent.radius
        if best_r is not None:
            self.last_resolved_radius = float(best_r)
        return best_ent

    def _resolve_extreme(self, shape: Shape[Any], eff_kind: str) -> Any:
        ax = self.axis or (0.0, 0.0, 1.0)
        sx, sy, sz = ax[0] * self.direction, ax[1] * self.direction, ax[2] * self.direction

        if eff_kind in ("face", "edge"):
            ents: list[Shape[Any]] = list(shape.faces() if eff_kind == "face" else shape.edges())
            if self.geom_type is not None:
                ents = [e for e in ents if e.geom_type.name.upper() == self.geom_type]
            if not ents:
                self.is_stale = True
                self.stale_reason = f"No matching {eff_kind}s for extreme anchor."
                return None

            def ent_key(e: Any) -> tuple[tuple[float, float], tuple[float, float, float]]:
                c = entity_center_3d(e)
                proj = round(c[0] * sx + c[1] * sy + c[2] * sz, 4)
                if self.near is not None:
                    return (proj, -math.dist(c, self.near)), c
                size = float(e.area) if eff_kind == "face" else float(e.length)
                return (proj, size), c

            (_, best_c), best = max(((ent_key(e), e) for e in ents), key=lambda t: t[0][0])
            self.last_resolved_entity = best
            self.last_resolved_point_3d = best_c
            return best

        # Vertex or point
        verts = list(shape.vertices())
        if not verts:
            self.is_stale = True
            self.stale_reason = "Shape has no vertices for extreme anchor."
            return None

        projs = [float(v.X) * sx + float(v.Y) * sy + float(v.Z) * sz for v in verts]
        max_proj = max(projs)
        extreme_verts = [v for v, p in zip(verts, projs, strict=False) if abs(p - max_proj) <= 1e-3]

        near = self.near
        if near is not None:
            best_v = min(
                extreme_verts,
                key=lambda v: math.dist((float(v.X), float(v.Y), float(v.Z)), near),
            )
            self.last_resolved_entity = best_v
            self.last_resolved_point_3d = (float(best_v.X), float(best_v.Y), float(best_v.Z))
            return best_v

        cx = sum(float(v.X) for v in extreme_verts) / len(extreme_verts)
        cy = sum(float(v.Y) for v in extreme_verts) / len(extreme_verts)
        cz = sum(float(v.Z) for v in extreme_verts) / len(extreme_verts)

        if eff_kind == "vertex":
            # Pick vertex closest to centroid of extreme vertices for deterministic symmetry
            best_v = min(
                extreme_verts,
                key=lambda v: (
                    round(math.dist((float(v.X), float(v.Y), float(v.Z)), (cx, cy, cz)), 4),
                    round(float(v.X), 4),
                    round(float(v.Y), 4),
                    round(float(v.Z), 4),
                ),
            )
            self.last_resolved_entity = best_v
            self.last_resolved_point_3d = (float(best_v.X), float(best_v.Y), float(best_v.Z))
            return best_v

        # For entity="point" (default), return centroid of the extreme boundary vertices
        pt_vec = bd.Vector(cx, cy, cz)
        self.last_resolved_entity = pt_vec
        self.last_resolved_point_3d = (cx, cy, cz)
        return pt_vec

    def _resolve_cylinder(self, shape: Shape[Any]) -> Any:
        faces = list(shape.faces())
        cyls = [f for f in faces if f.geom_type == bd.GeomType.CYLINDER]
        if self.internal is not None:
            cyls = [f for f in cyls if _is_internal_cylinder(f) == self.internal]
        if self.axis is not None:
            ax = self.axis
            filtered: list[bd.Face] = []
            for f in cyls:
                f_ax = f.axis_of_rotation
                if f_ax is not None:
                    d = f_ax.direction
                    dot = abs(float(d.X) * ax[0] + float(d.Y) * ax[1] + float(d.Z) * ax[2])
                    if dot >= 0.85:
                        filtered.append(f)
            cyls = filtered

        if not cyls:
            self.is_stale = True
            self.stale_reason = "No matching cylindrical face found on shape."
            return None

        if self.radius is not None:
            target_r = self.radius
            in_tol = [f for f in cyls if _radius_within_tolerance(target_r, float(f.radius or 0.0))]
            if not in_tol:
                closest = min(
                    (float(f.radius or 0.0) for f in cyls),
                    key=lambda r: abs(r - target_r),
                )
                self.is_stale = True
                self.stale_reason = (
                    f"No cylindrical face with radius {target_r:.2f} "
                    f"(\u00b1{CYLINDER_RADIUS_REL_TOL:.0%}); closest is {closest:.2f}."
                )
                return None
            cyls = in_tol

        def cyl_score(f: bd.Face) -> tuple[float, float, float, float, float]:
            r_val = float(f.radius or 0.0)
            r_diff = abs(r_val - self.radius) if self.radius is not None else 0.0
            c3d = entity_center_3d(f)
            near_dist = math.dist(c3d, self.near) if self.near is not None else 0.0
            return (
                round(r_diff, 4),
                round(near_dist, 4),
                round(c3d[0], 4),
                round(c3d[1], 4),
                round(c3d[2], 4),
            )

        cyls_sorted = sorted(cyls, key=cyl_score)
        if not 0 <= self.index < len(cyls_sorted):
            self.is_stale = True
            self.stale_reason = (
                f"Cylinder index {self.index} out of range: only {len(cyls_sorted)} "
                "matching cylindrical face(s) found."
            )
            return None
        chosen = cyls_sorted[self.index]

        self.last_resolved_entity = chosen
        self.last_resolved_point_3d = entity_center_3d(chosen)
        self.last_resolved_radius = float(chosen.radius or 0.0)
        return chosen

    def _resolve_face(self, shape: Shape[Any]) -> Any:
        faces = list(shape.faces())
        if self.geom_type is not None:
            faces = [f for f in faces if f.geom_type.name.upper() == self.geom_type]
        if self.axis is not None:
            nx, ny, nz = self.axis
            matched_faces: list[bd.Face] = []
            for f in faces:
                orient, signed = _extract_orientation(f)
                if orient is not None:
                    dot = orient[0] * nx + orient[1] * ny + orient[2] * nz
                    eff = dot if signed else abs(dot)
                    if eff >= 0.8:
                        matched_faces.append(f)
            faces = matched_faces

        if not faces:
            self.is_stale = True
            self.stale_reason = "No matching face found on shape."
            return None

        def face_score(f: bd.Face) -> tuple[float, float, float]:
            c3d = entity_center_3d(f)
            ext_proj = 0.0
            if self.extreme_axis is not None:
                ex, ey, ez = self.extreme_axis
                ext_proj = -(c3d[0] * ex + c3d[1] * ey + c3d[2] * ez) * self.direction
            near_d = math.dist(c3d, self.near) if self.near is not None else 0.0
            area_d = abs(float(f.area) - self.area) if self.area is not None else -float(f.area)
            return (round(ext_proj, 4), round(near_d, 4), round(area_d, 4))

        best_f = min(faces, key=face_score)
        self.last_resolved_entity = best_f
        self.last_resolved_point_3d = entity_center_3d(best_f)
        return best_f

    def _resolve_edge(self, shape: Shape[Any]) -> Any:
        edges = list(shape.edges())
        if self.geom_type is not None:
            edges = [e for e in edges if e.geom_type.name.upper() == self.geom_type]
        if self.axis is not None:
            ax, ay, az = self.axis
            matched_edges: list[bd.Edge] = []
            for e in edges:
                orient, _ = _extract_orientation(e)
                if orient is not None:
                    dot = abs(orient[0] * ax + orient[1] * ay + orient[2] * az)
                    if dot >= 0.85:
                        matched_edges.append(e)
            edges = matched_edges

        if not edges:
            self.is_stale = True
            self.stale_reason = "No matching edge found on shape."
            return None

        def edge_score(e: bd.Edge) -> tuple[float, float, float]:
            c3d = entity_center_3d(e)
            ext_proj = 0.0
            if self.extreme_axis is not None:
                ex, ey, ez = self.extreme_axis
                ext_proj = -(c3d[0] * ex + c3d[1] * ey + c3d[2] * ez) * self.direction
            near_d = math.dist(c3d, self.near) if self.near is not None else 0.0
            len_d = (
                abs(float(e.length) - self.length) if self.length is not None else -float(e.length)
            )
            return (round(ext_proj, 4), round(near_d, 4), round(len_d, 4))

        best_e = min(edges, key=edge_score)
        self.last_resolved_entity = best_e
        self.last_resolved_point_3d = entity_center_3d(best_e)
        return best_e

    def _resolve_vertex(self, shape: Shape[Any]) -> Any:
        verts = list(shape.vertices())
        if not verts:
            self.is_stale = True
            self.stale_reason = "Shape has no vertices."
            return None

        def vert_score(v: bd.Vertex) -> tuple[float, float]:
            c3d = (float(v.X), float(v.Y), float(v.Z))
            ext_proj = 0.0
            if self.axis is not None:
                ax, ay, az = self.axis
                ext_proj = -(c3d[0] * ax + c3d[1] * ay + c3d[2] * az) * self.direction
            near_d = math.dist(c3d, self.near) if self.near is not None else 0.0
            return (round(ext_proj, 4), round(near_d, 4))

        best_v = min(verts, key=vert_score)
        self.last_resolved_entity = best_v
        self.last_resolved_point_3d = (float(best_v.X), float(best_v.Y), float(best_v.Z))
        return best_v

    def resolve(
        self, shape: Shape[Any] | None, *, prefer_kind: str | None = None
    ) -> tuple[Any, tuple[float, float, float]]:
        """Resolve once against ``shape``, returning ``(entity, point_3d)``.

        ``entity`` is what :meth:`resolve_entity` returns (``None`` when stale) and
        ``point_3d`` is what :meth:`resolve_point_3d` returns. Use this when a caller
        needs both, to avoid re-running the topology search.
        """
        ent = self.resolve_entity(shape, prefer_kind=prefer_kind)
        if ent is not None:
            if isinstance(ent, bd.Edge) and self.point_mode in ("start", "end"):
                pt = ent.start_point() if self.point_mode == "start" else ent.end_point()
                res = (float(pt.X), float(pt.Y), float(pt.Z))
            else:
                res = entity_center_3d(ent)
            self.last_resolved_point_3d = res
            return ent, res

        if self.last_resolved_point_3d is not None:
            return None, self.last_resolved_point_3d
        return None, (0.0, 0.0, 0.0)

    def resolve_point_3d(self, shape: Shape[Any] | None) -> tuple[float, float, float]:
        """Resolve this anchor against ``shape`` to a 3D model point (X, Y, Z)."""
        return self.resolve(shape)[1]

    def resolve_to_sheet(self, view: SheetView) -> tuple[float, float]:
        """Resolve this anchor to 2D paper sheet coordinates (mm) on ``view``."""
        pt_3d = self.resolve_point_3d(view.shape)
        return view.to_sheet_3d(pt_3d[0], pt_3d[1], pt_3d[2])


def _coerce_point_3d(
    pt: Sequence[float] | bd.Vector | bd.Vertex | None,
) -> tuple[float, float, float] | None:
    if pt is None:
        return None
    if isinstance(pt, (bd.Vector, bd.Vertex)):
        return (float(pt.X), float(pt.Y), float(pt.Z))
    if len(pt) == 3:
        return (float(pt[0]), float(pt[1]), float(pt[2]))
    if len(pt) == 2:
        return (float(pt[0]), float(pt[1]), 0.0)
    return None
