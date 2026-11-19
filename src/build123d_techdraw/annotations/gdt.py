"""ISO 1101 and ASME Y14.5 Geometric Dimensioning and Tolerancing (GD&T) annotations."""

from __future__ import annotations

import html
import math
from enum import Enum
from typing import TYPE_CHECKING, Any

import build123d as bd

from build123d_techdraw.annotations.anchors import Anchor
from build123d_techdraw.annotations.base import Annotation, BBox2D
from build123d_techdraw.annotations.dimensions import render_arrowhead
from build123d_techdraw.annotations.resolver import (
    ensure_anchor,
    is_topological_entity,
    resolve_point_to_sheet,
    stale_point_to_sheet,
)
from build123d_techdraw.context import CURRENT_SHEET, CURRENT_VIEW

if TYPE_CHECKING:
    from build123d_techdraw.core.view import SheetView


class GDTCharacteristic(Enum):
    """Geometric tolerance characteristics according to ISO 1101."""

    POSITION = "⌖"
    FLATNESS = "⏥"
    STRAIGHTNESS = "—"
    CIRCULARITY = "○"
    CYLINDRICITY = "⌭"
    PROFILE_SURFACE = "⌓"
    PROFILE_LINE = "⌒"
    PERPENDICULARITY = "⟂"
    PARALLELISM = "∥"
    ANGULARITY = "∠"
    CONCENTRICITY = "◎"
    SYMMETRY = "⌯"
    CIRCULAR_RUNOUT = "↗"
    TOTAL_RUNOUT = "⇗"


FORM_CHARACTERISTICS: frozenset[GDTCharacteristic] = frozenset(
    {
        GDTCharacteristic.STRAIGHTNESS,
        GDTCharacteristic.FLATNESS,
        GDTCharacteristic.CIRCULARITY,
        GDTCharacteristic.CYLINDRICITY,
    }
)
"""Form tolerances (ISO 1101 Table 2): they control a single feature and never take a datum."""

DATUM_DIRECTIONS: tuple[str, ...] = ("up", "down", "left", "right")
"""Sheet directions in which a datum feature symbol's frame can be placed from its triangle."""


def _sheet_direction_name(dx: float, dy: float) -> str:
    """Map a sheet-space vector (y grows downwards) to the nearest of ``DATUM_DIRECTIONS``."""
    if abs(dy) >= abs(dx):
        return "up" if dy < 0.0 else "down"
    return "left" if dx < 0.0 else "right"


def outward_sheet_direction(
    entity: Any, point: tuple[float, float], view: SheetView
) -> tuple[float, float]:
    """Return the unit sheet-space direction pointing *out of the material* at ``point``.

    For a planar ``Face`` the outward surface normal is projected through ``view``; a face seen
    edge-on (the usual case for a datum or texture surface) then yields a vector perpendicular to
    its projected edge, away from the part. When the entity is not a face, or the face is seen
    face-on so its normal projects to (almost) nothing, the direction from the view centre to
    ``point`` is used instead, which points outside the view silhouette. Falls back to "up"
    (``(0, -1)``) when ``point`` coincides with the view centre.
    """
    if isinstance(entity, bd.Face):
        center = entity.center()
        normal = entity.normal_at(center)
        p0 = view.to_sheet_3d(float(center.X), float(center.Y), float(center.Z))
        p1 = view.to_sheet_3d(
            float(center.X + normal.X), float(center.Y + normal.Y), float(center.Z + normal.Z)
        )
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        length = math.hypot(dx, dy)
        # Ignore normals that are (nearly) parallel to the viewing direction.
        if length > 0.1 * view.scale:
            return (dx / length, dy / length)

    dx, dy = point[0] - view.center_x, point[1] - view.center_y
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return (0.0, -1.0)
    return (dx / length, dy / length)


class FeatureControlFrame(Annotation):
    """ISO 1101 Feature Control Frame ([ Symbol | Tolerance | Datum A | Datum B | Datum C ]).

    Raises:
        ValueError: if a form characteristic (``FORM_CHARACTERISTICS``: straightness, flatness,
            circularity, cylindricity) is given datum references. ISO 1101 form tolerances
            apply to the feature alone and never reference a datum.
    """

    def __init__(
        self,
        characteristic: GDTCharacteristic = GDTCharacteristic.FLATNESS,
        tolerance_value: str = "0.05",
        datum_a: str | None = None,
        datum_b: str | None = None,
        datum_c: str | None = None,
        position: tuple[float, float] | None = None,
        target: Any = None,
        leader_offset: tuple[float, float] = (12.0, -10.0),
        frame_height: float = 6.0,
        symbol_font_size: float = 3.6,
        text_font_size: float = 3.0,
        leader_target: tuple[float, float] | None = None,
        datum_refs: list[str] | None = None,
        view: SheetView | None = None,
        *,
        anchor: Any = None,
    ):
        if target is None and anchor is not None:
            target = anchor

        self.characteristic = characteristic
        self.tolerance_value = tolerance_value
        self.frame_height = frame_height
        self.symbol_font_size = symbol_font_size
        self.text_font_size = text_font_size
        self.leader_offset = leader_offset
        self.view = view
        self.target = target if target is not None else leader_target
        self.resolved = False
        self._explicit_coords = False
        self.anchor_stale = False
        self.stale_reason: str | None = None

        if datum_refs:
            self.datum_a = datum_refs[0] if len(datum_refs) > 0 else None
            self.datum_b = datum_refs[1] if len(datum_refs) > 1 else None
            self.datum_c = datum_refs[2] if len(datum_refs) > 2 else None
        else:
            self.datum_a = datum_a
            self.datum_b = datum_b
            self.datum_c = datum_c

        if characteristic in FORM_CHARACTERISTICS and (
            self.datum_a or self.datum_b or self.datum_c
        ):
            raise ValueError(
                f"{characteristic.name} is an ISO 1101 form tolerance and must not reference a "
                "datum; use an orientation tolerance (e.g. PARALLELISM) to relate it to a datum."
            )

        if position is not None:
            self.position = (float(position[0]), float(position[1]))
            self.position_explicit = True
        else:
            self.position = (100.0, 100.0)
            self.position_explicit = False

        self.leader_target: tuple[float, float] | None
        if leader_target is not None and not is_topological_entity(leader_target):
            self.leader_target = (float(leader_target[0]), float(leader_target[1]))
            self.resolved = True
            self._explicit_coords = True
        else:
            self.leader_target = None

        target_view = view or CURRENT_VIEW.get()
        if target_view is not None:
            self._attach_to_view(target_view)
        else:
            target_sheet = CURRENT_SHEET.get()
            if target_sheet is not None and self not in target_sheet.annotations:
                target_sheet.annotations.append(self)

    def _rebind_to_view(self, view: SheetView) -> None:
        if not self._explicit_coords:
            self.resolved = False
            self._attach_to_view(view)

    def _attach_to_view(self, view: SheetView) -> None:
        self.view = view
        if not self.resolved and self.target is not None:
            if not isinstance(self.target, (tuple, list)):
                self.target = ensure_anchor(self.target, view.shape) or self.target
            target_pt = resolve_point_to_sheet(self.target, view)
            if isinstance(self.target, Anchor) and self.target.is_stale:
                self.anchor_stale = True
                self.stale_reason = self.target.stale_reason
                target_pt = stale_point_to_sheet(self.target, view) or target_pt
            else:
                self.anchor_stale = False
                self.stale_reason = None

            self.leader_target = target_pt
            if not self.position_explicit:
                self.position = (
                    target_pt[0] + self.leader_offset[0],
                    target_pt[1] + self.leader_offset[1],
                )
            self.resolved = True
        elif not self.resolved and self.target is None and not self.position_explicit:
            self.position = (view.center_x, view.center_y)
            self.resolved = True

        self._register(view)

    def bounding_box_2d(self) -> BBox2D:
        x, y = self.position
        w = 35.0  # approximate frame width
        return (x, y, x + w, y + self.frame_height)

    def to_svg(self) -> str:
        x, y = self.position
        h = self.frame_height

        # Calculate cell widths
        sym_w = 7.0
        tol_w = max(14.0, len(self.tolerance_value) * 2.2 + 4.0)

        datums: list[str] = []
        for d in [self.datum_a, self.datum_b, self.datum_c]:
            if d:
                datums.append(d)

        datum_w = 7.0
        total_w = sym_w + tol_w + len(datums) * datum_w

        leader_svg = ""
        if self.leader_target is not None:
            tx, ty = self.leader_target
            att_x = x if tx < x else (x + total_w if tx > x + total_w else x + total_w / 2.0)
            att_y = y + h / 2.0
            dx = att_x - tx
            dy = att_y - ty
            dist = math.hypot(dx, dy)
            arrow_svg = (
                render_arrowhead(tx, ty, -dx, -dy, length=2.5, half_width=0.7) if dist > 0 else ""
            )
            leader_svg = f"""
            {arrow_svg}
            <line x1="{tx:.2f}" y1="{ty:.2f}" x2="{att_x:.2f}" y2="{att_y:.2f}" class="dim-line" stroke-width="0.3"/>
            """

        elements: list[str] = [
            leader_svg,
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{total_w:.2f}" height="{h:.2f}" class="title-bg" stroke-width="0.35"/>',
            f'<line x1="{x + sym_w:.2f}" y1="{y:.2f}" x2="{x + sym_w:.2f}" y2="{y + h:.2f}" class="border-sub" stroke-width="0.3"/>',
            # Characteristic symbol
            f'<text x="{x + sym_w / 2.0:.2f}" y="{y + h * 0.68:.2f}" class="tb-title" text-anchor="middle" font-size="{self.symbol_font_size:.2f}">{self.characteristic.value}</text>',
            # Tolerance value
            f'<text x="{x + sym_w + tol_w / 2.0:.2f}" y="{y + h * 0.68:.2f}" class="dim-text" text-anchor="middle" font-size="{self.text_font_size:.2f}">{html.escape(self.tolerance_value)}</text>',
        ]

        curr_x = x + sym_w + tol_w
        for d in datums:
            elements.append(
                f'<line x1="{curr_x:.2f}" y1="{y:.2f}" x2="{curr_x:.2f}" y2="{y + h:.2f}" class="border-sub" stroke-width="0.3"/>'
            )
            elements.append(
                f'<text x="{curr_x + datum_w / 2.0:.2f}" y="{y + h * 0.68:.2f}" class="tb-field-val-bold" text-anchor="middle" font-size="{self.text_font_size:.2f}">{html.escape(d)}</text>'
            )
            curr_x += datum_w

        return f'<g class="gdt-fcf">{"".join(elements)}</g>'


class DatumFeatureSymbol(Annotation):
    """ISO 5459 datum feature indicator: a filled triangle on the feature joined to a framed letter.

    The triangle's base sits on the feature's outline (``target``) and the frame is placed on the
    side *outside the material*. With ``direction=None`` (the default) and a topological target,
    that side is found from the face's outward normal projected into the view (or, for edges,
    vertices and face-on faces, from the side away from the view centre). Pass ``"up"``,
    ``"down"``, ``"left"`` or ``"right"`` to force a side; explicit 2D coordinates without a
    direction default to ``"up"``.
    """

    def __init__(
        self,
        label: str = "A",
        target: Any = None,
        stem_length: float = 7.0,
        box_size: float = 5.5,
        direction: str | None = None,
        view: SheetView | None = None,
        *,
        anchor: Any = None,
    ):
        if target is None and anchor is not None:
            target = anchor

        if direction is not None and direction not in DATUM_DIRECTIONS:
            raise ValueError(
                f"direction must be one of {DATUM_DIRECTIONS} or None, got {direction!r}"
            )

        self.label = label
        self.stem_length = stem_length
        self.box_size = box_size
        self._auto_direction = direction is None
        self.direction: str = direction if direction is not None else "up"
        self.view = view
        self.raw_target = target
        self.target: tuple[float, float] = (0.0, 0.0)
        self.resolved = False
        self._explicit_coords = False
        self.anchor_stale = False
        self.stale_reason: str | None = None

        if (
            target is not None
            and not is_topological_entity(target)
            and isinstance(target, (tuple, list))
        ):
            self.target = (float(target[0]), float(target[1]))
            self.resolved = True
            self._explicit_coords = True

        target_view = view or CURRENT_VIEW.get()
        if target_view is not None:
            self._attach_to_view(target_view)
        else:
            target_sheet = CURRENT_SHEET.get()
            if target_sheet is not None and self not in target_sheet.annotations:
                target_sheet.annotations.append(self)

    def _rebind_to_view(self, view: SheetView) -> None:
        if not self._explicit_coords:
            self.resolved = False
            self._attach_to_view(view)

    def _attach_to_view(self, view: SheetView) -> None:
        self.view = view
        if not self.resolved and self.raw_target is not None:
            if not isinstance(self.raw_target, (tuple, list)):
                self.raw_target = ensure_anchor(self.raw_target, view.shape) or self.raw_target
            self.target = resolve_point_to_sheet(self.raw_target, view)
            if isinstance(self.raw_target, Anchor) and self.raw_target.is_stale:
                self.anchor_stale = True
                self.stale_reason = self.raw_target.stale_reason
                self.target = stale_point_to_sheet(self.raw_target, view) or self.target
            else:
                self.anchor_stale = False
                self.stale_reason = None
            if self._auto_direction:
                entity = (
                    self.raw_target.last_resolved_entity
                    if isinstance(self.raw_target, Anchor)
                    else self.raw_target
                )
                dx, dy = outward_sheet_direction(entity, self.target, view)
                self.direction = _sheet_direction_name(dx, dy)
            self.resolved = True
        elif not self.resolved and self.raw_target is None:
            self.target = (view.center_x, view.min_y)
            if self._auto_direction:
                self.direction = "up"
            self.resolved = True

        self._register(view)

    def _geometry(
        self,
    ) -> tuple[list[tuple[float, float]], tuple[float, float], tuple[float, float]]:
        """Return (triangle points, stem end, frame top-left corner) in sheet mm.

        The triangle's base lies on the feature outline at ``target`` and its apex points away
        from the material, towards the frame (ISO 5459).
        """
        tx, ty = self.target
        half = 2.4  # half base width
        height = half * 1.5
        ux, uy = {"up": (0.0, -1.0), "down": (0.0, 1.0), "left": (-1.0, 0.0)}.get(
            self.direction, (1.0, 0.0)
        )
        # Perpendicular to the outward direction, along the feature outline.
        px, py = -uy, ux
        tri = [
            (tx + px * half, ty + py * half),
            (tx - px * half, ty - py * half),
            (tx + ux * height, ty + uy * height),
        ]
        stem_end = (tx + ux * self.stem_length, ty + uy * self.stem_length)
        b = self.box_size
        if self.direction == "up":
            box = (stem_end[0] - b / 2.0, stem_end[1] - b)
        elif self.direction == "down":
            box = (stem_end[0] - b / 2.0, stem_end[1])
        elif self.direction == "left":
            box = (stem_end[0] - b, stem_end[1] - b / 2.0)
        else:
            box = (stem_end[0], stem_end[1] - b / 2.0)
        return tri, stem_end, box

    def bounding_box_2d(self) -> BBox2D:
        tri, _stem_end, (box_x, box_y) = self._geometry()
        xs = [p[0] for p in tri] + [box_x, box_x + self.box_size]
        ys = [p[1] for p in tri] + [box_y, box_y + self.box_size]
        return (min(xs), min(ys), max(xs), max(ys))

    def to_svg(self) -> str:
        tx, ty = self.target
        tri, (stem_end_x, stem_end_y), (box_x, box_y) = self._geometry()
        tri_pts = " ".join(f"{px:.2f},{py:.2f}" for px, py in tri)

        elements = [
            f'<polygon points="{tri_pts}" class="dim-arrow"/>',
            f'<line x1="{tx:.2f}" y1="{ty:.2f}" x2="{stem_end_x:.2f}" y2="{stem_end_y:.2f}" class="dim-line" stroke-width="0.35"/>',
            f'<rect x="{box_x:.2f}" y="{box_y:.2f}" width="{self.box_size:.2f}" height="{self.box_size:.2f}" class="title-bg" stroke-width="0.4"/>',
            f'<text x="{box_x + self.box_size / 2.0:.2f}" y="{box_y + self.box_size * 0.72:.2f}" class="tb-field-val-bold" text-anchor="middle" font-size="3.4">{html.escape(self.label)}</text>',
        ]

        return f'<g class="datum-symbol">{"".join(elements)}</g>'


# Familiar build123d-style alias
DatumFeature = DatumFeatureSymbol
