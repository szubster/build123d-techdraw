"""ISO centerlines, center crosshairs, and bolt pitch circles (PCD)."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from build123d_techdraw.annotations.anchors import Anchor
from build123d_techdraw.annotations.base import Annotation, BBox2D
from build123d_techdraw.annotations.resolver import (
    ensure_anchor,
    is_topological_entity,
    resolve_circle_or_cylinder_to_sheet,
    resolve_point_to_sheet,
    stale_reason_of,
)
from build123d_techdraw.context import CURRENT_SHEET, CURRENT_VIEW

if TYPE_CHECKING:
    from build123d_techdraw.core.view import SheetView


class Centerline(Annotation):
    """ISO 128 compliant long-dash dot-dash centerline."""

    def __init__(
        self,
        target_or_start: Any = None,
        target2_or_end: Any = None,
        stroke_width: float = 0.25,
        start: tuple[float, float] | None = None,
        end: tuple[float, float] | None = None,
        overhang: float = 4.0,
        view: SheetView | None = None,
        *,
        anchor: Any = None,
        anchor1: Any = None,
        anchor2: Any = None,
    ):
        if target_or_start is None:
            target_or_start = anchor1 if anchor1 is not None else anchor
        if target2_or_end is None and anchor2 is not None:
            target2_or_end = anchor2

        self.stroke_width = stroke_width
        self.overhang = overhang
        self.view = view
        self.target: Any = None
        self.target2: Any = None
        self.resolved = False
        self._explicit_coords = False
        self.anchor_stale = False
        self.stale_reason: str | None = None

        if start is not None and end is not None:
            self.start = (float(start[0]), float(start[1]))
            self.end = (float(end[0]), float(end[1]))
            self.resolved = True
            self._explicit_coords = True
        elif (
            target_or_start is not None
            and target2_or_end is not None
            and not is_topological_entity(target_or_start)
            and not is_topological_entity(target2_or_end)
            and isinstance(target_or_start, (tuple, list))
            and isinstance(target2_or_end, (tuple, list))
        ):
            self.start = (float(target_or_start[0]), float(target_or_start[1]))
            self.end = (float(target2_or_end[0]), float(target2_or_end[1]))
            self.resolved = True
            self._explicit_coords = True
        else:
            self.start = (0.0, 0.0)
            self.end = (0.0, 0.0)
            self.target = target_or_start
            self.target2 = target2_or_end

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
            import build123d as bd

            v_shape = view.shape
            self.target = ensure_anchor(self.target, v_shape) or self.target
            self.target2 = ensure_anchor(self.target2, v_shape) or self.target2

            prev_start = self.start
            prev_end = self.end

            eff_target = (
                self.target.resolve_entity(v_shape)
                if isinstance(self.target, Anchor) and self.target2 is None
                else self.target
            )

            if isinstance(eff_target, bd.Face) and eff_target.geom_type == bd.GeomType.CYLINDER:
                axis = eff_target.axis_of_rotation
                if axis:
                    bb = eff_target.bounding_box()
                    p0 = view.to_sheet_3d(
                        float(axis.position.X), float(axis.position.Y), float(bb.min.Z)
                    )
                    p1 = view.to_sheet_3d(
                        float(axis.position.X), float(axis.position.Y), float(bb.max.Z)
                    )
                    # Apply overhang in sheet space along line
                    dx = p1[0] - p0[0]
                    dy = p1[1] - p0[1]
                    d = math.hypot(dx, dy)
                    if d > 1e-3:
                        ux = dx / d
                        uy = dy / d
                        self.start = (p0[0] - ux * self.overhang, p0[1] - uy * self.overhang)
                        self.end = (p1[0] + ux * self.overhang, p1[1] + uy * self.overhang)
                    else:
                        self.start = p0
                        self.end = p1
            elif self.target2 is not None:
                p0 = resolve_point_to_sheet(self.target, view)
                p1 = resolve_point_to_sheet(self.target2, view)
                self.start = p0
                self.end = p1

            stale1 = isinstance(self.target, Anchor) and self.target.is_stale
            stale2 = isinstance(self.target2, Anchor) and self.target2.is_stale
            if stale1 or stale2:
                self.anchor_stale = True
                self.stale_reason = stale_reason_of(self.target, self.target2)
                if prev_start != (0.0, 0.0) or prev_end != (0.0, 0.0):
                    self.start, self.end = prev_start, prev_end
            else:
                self.anchor_stale = False
                self.stale_reason = None
            self.resolved = True
        elif not self.resolved and self.target is None:
            self.start = (view.min_x - self.overhang, view.center_y)
            self.end = (view.max_x + self.overhang, view.center_y)
            self.resolved = True

        self._register(view)

    def to_svg(self) -> str:
        x1, y1 = self.start
        x2, y2 = self.end
        return (
            f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
            f'class="line-center" stroke-dasharray="10,2,2,2" stroke-width="{self.stroke_width:.2f}"/>'
        )


class CenterMark(Annotation):
    """ISO centermark crosshair with central intersection and overhang."""

    def __init__(
        self,
        target_or_center: Any = None,
        size: float | None = None,
        stroke_width: float = 0.25,
        center: tuple[float, float] | None = None,
        cy: float | None = None,
        view: SheetView | None = None,
        *,
        anchor: Any = None,
    ):
        if target_or_center is None and anchor is not None:
            target_or_center = anchor

        self._explicit_size = size is not None
        self.size = size if size is not None else 6.0
        self.stroke_width = stroke_width
        self.view = view
        self.target: Any = None
        self.resolved = False
        self._explicit_coords = False
        self.anchor_stale = False
        self.stale_reason: str | None = None

        if center is not None:
            self.center = (float(center[0]), float(center[1]))
            self.resolved = True
            self._explicit_coords = True
        elif cy is not None and isinstance(target_or_center, (int, float)):
            self.center = (float(target_or_center), float(cy))
            self.resolved = True
            self._explicit_coords = True
        elif (
            target_or_center is not None
            and not is_topological_entity(target_or_center)
            and isinstance(target_or_center, (tuple, list))
            and len(target_or_center) == 2
        ):
            self.center = (float(target_or_center[0]), float(target_or_center[1]))
            self.resolved = True
            self._explicit_coords = True
        else:
            self.center = (0.0, 0.0)
            self.target = target_or_center

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
            if not self._explicit_size:
                self.size = 6.0
            self._attach_to_view(view)

    def _attach_to_view(self, view: SheetView) -> None:
        self.view = view
        if not self.resolved and self.target is not None:
            if not isinstance(self.target, (tuple, list)):
                self.target = ensure_anchor(self.target, view.shape) or self.target
            prev_center = self.center
            prev_size = self.size
            try:
                paper_c, paper_r, _ = resolve_circle_or_cylinder_to_sheet(self.target, view)
                self.center = paper_c
                if not self._explicit_size:
                    self.size = (paper_r + 2.0) * 2.0
            except TypeError:
                self.center = resolve_point_to_sheet(self.target, view)

            if isinstance(self.target, Anchor) and self.target.is_stale:
                self.anchor_stale = True
                self.stale_reason = self.target.stale_reason
                if prev_center != (0.0, 0.0):
                    self.center = prev_center
                    self.size = prev_size
            else:
                self.anchor_stale = False
                self.stale_reason = None
            self.resolved = True
        elif not self.resolved and self.target is None:
            self.center = (view.center_x, view.center_y)
            self.resolved = True

        self._register(view)

    def bounding_box_2d(self) -> BBox2D:
        cx, cy = self.center
        s = self.size / 2.0
        return (cx - s, cy - s, cx + s, cy + s)

    def to_svg(self) -> str:
        cx, cy = self.center
        half = self.size / 2.0
        return f"""
        <g class="centermark">
            <line x1="{cx - half:.2f}" y1="{cy:.2f}" x2="{cx + half:.2f}" y2="{cy:.2f}" class="line-center" stroke-width="{self.stroke_width:.2f}"/>
            <line x1="{cx:.2f}" y1="{cy - half:.2f}" x2="{cx:.2f}" y2="{cy + half:.2f}" class="line-center" stroke-width="{self.stroke_width:.2f}"/>
        </g>
        """


class BoltCircle(Annotation):
    """Bolt Circle / Pitch Circle Diameter (PCD) annotation with radial hole crosshairs."""

    def __init__(
        self,
        target_or_center: Any = None,
        diameter: float | None = None,
        num_holes: int = 4,
        start_angle_deg: float = 0.0,
        hole_cross_size: float = 4.0,
        stroke_width: float = 0.25,
        center: tuple[float, float] | None = None,
        view: SheetView | None = None,
        *,
        anchor: Any = None,
    ):
        if target_or_center is None and anchor is not None:
            target_or_center = anchor

        self.num_holes = num_holes
        self.start_angle_deg = start_angle_deg
        self.hole_cross_size = hole_cross_size
        self.stroke_width = stroke_width
        self.view = view
        self.target: Any = None
        self.resolved = False
        self._explicit_diameter = diameter is not None
        self._explicit_coords = False
        self.anchor_stale = False
        self.stale_reason: str | None = None

        if center is not None and diameter is not None:
            self.center = (float(center[0]), float(center[1]))
            self.diameter = float(diameter)
            self.resolved = True
            self._explicit_coords = True
        elif (
            target_or_center is not None
            and diameter is not None
            and not is_topological_entity(target_or_center)
            and isinstance(target_or_center, (tuple, list))
        ):
            self.center = (float(target_or_center[0]), float(target_or_center[1]))
            self.diameter = float(diameter)
            self.resolved = True
            self._explicit_coords = True
        else:
            self.center = (0.0, 0.0)
            self.diameter = float(diameter or 0.0)
            self.target = target_or_center

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
            if not self._explicit_diameter:
                self.diameter = 0.0
            self._attach_to_view(view)

    def _attach_to_view(self, view: SheetView) -> None:
        self.view = view
        if not self.resolved and self.target is not None:
            if not isinstance(self.target, (tuple, list)):
                self.target = ensure_anchor(self.target, view.shape) or self.target
            prev_center = self.center
            prev_diameter = self.diameter
            try:
                paper_c, paper_r, _ = resolve_circle_or_cylinder_to_sheet(self.target, view)
                self.center = paper_c
                if self.diameter == 0.0:
                    self.diameter = 2.0 * paper_r
            except TypeError:
                self.center = resolve_point_to_sheet(self.target, view)

            if isinstance(self.target, Anchor) and self.target.is_stale:
                self.anchor_stale = True
                self.stale_reason = self.target.stale_reason
                if prev_center != (0.0, 0.0):
                    self.center = prev_center
                if prev_diameter > 0.0:
                    self.diameter = prev_diameter
            else:
                self.anchor_stale = False
                self.stale_reason = None
            self.resolved = True
        elif not self.resolved and self.target is None:
            self.center = (view.center_x, view.center_y)
            self.resolved = True

        self._register(view)

    def to_svg(self) -> str:
        cx, cy = self.center
        radius = self.diameter / 2.0
        elements: list[str] = [
            f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{radius:.2f}" class="line-center" fill="none" stroke-dasharray="8,2,2,2" stroke-width="{self.stroke_width:.2f}"/>'
        ]

        if self.num_holes > 0:
            step_deg = 360.0 / self.num_holes
            for i in range(self.num_holes):
                angle_rad = math.radians(self.start_angle_deg + i * step_deg)
                hx = cx + radius * math.cos(angle_rad)
                hy = cy - radius * math.sin(angle_rad)
                half = self.hole_cross_size / 2.0
                elements.append(
                    f'<line x1="{hx - half:.2f}" y1="{hy:.2f}" x2="{hx + half:.2f}" y2="{hy:.2f}" class="line-center" stroke-width="{self.stroke_width:.2f}"/>'
                )
                elements.append(
                    f'<line x1="{hx:.2f}" y1="{hy - half:.2f}" x2="{hx:.2f}" y2="{hy + half:.2f}" class="line-center" stroke-width="{self.stroke_width:.2f}"/>'
                )

        return f'<g class="bolt-circle">{"".join(elements)}</g>'
