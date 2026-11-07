"""Callouts, leader notes, hole/thread callouts, BOM balloons, and section cutting lines."""

from __future__ import annotations

import html
import math
from typing import Any

from build123d_techdraw.annotations.dimensions import format_dimension_value, render_arrowhead
from build123d_techdraw.annotations.resolver import (
    is_topological_entity,
    resolve_circle_or_cylinder_to_sheet,
    resolve_point_to_sheet,
)
from build123d_techdraw.context import CURRENT_SHEET, CURRENT_VIEW


class LeaderNote:
    """Multi-line leader note with pointer dot/arrow, elbow, and horizontal shelf."""

    def __init__(
        self,
        target_or_text: Any = None,
        lines_or_target: Any = None,
        elbow_offset: tuple[float, float] = (15.0, -12.0),
        shelf_length: float = 20.0,
        align_right: bool | None = None,
        terminator: str = "dot",
        font_size: float = 2.8,
        target: Any = None,
        lines: list[str] | str | None = None,
        text: str | list[str] | None = None,
        leader_offset: tuple[float, float] | None = None,
        view: Any | None = None,
    ):
        self.shelf_length = shelf_length
        self.align_right = align_right
        self.terminator = terminator
        self.font_size = font_size
        self.elbow_offset = leader_offset or elbow_offset
        self.view = view
        self.raw_target: Any = None
        self.target: tuple[float, float] = (0.0, 0.0)
        self.resolved = False

        # Parse text / lines
        raw_text = text or lines
        if raw_text is None:
            if isinstance(target_or_text, (str, list)):
                raw_text = target_or_text
                self.raw_target = target or lines_or_target
            elif isinstance(lines_or_target, (str, list)):
                raw_text = lines_or_target
                self.raw_target = target or target_or_text
        else:
            self.raw_target = target or target_or_text

        if isinstance(raw_text, str):
            self.lines = [raw_text]
        elif isinstance(raw_text, list):
            self.lines = list(raw_text)
        else:
            self.lines = ["NOTE"]

        if (
            self.raw_target is not None
            and not is_topological_entity(self.raw_target)
            and isinstance(self.raw_target, (tuple, list))
            and len(self.raw_target) == 2
        ):
            self.target = (float(self.raw_target[0]), float(self.raw_target[1]))
            self.resolved = True

        target_view = view or CURRENT_VIEW.get()
        if target_view is not None:
            self._attach_to_view(target_view)
        else:
            target_sheet = CURRENT_SHEET.get()
            if target_sheet is not None and self not in target_sheet.annotations:
                target_sheet.annotations.append(self)

    @property
    def text(self) -> str:
        return " ".join(self.lines)

    def _attach_to_view(self, view: Any) -> None:
        self.view = view
        if not self.resolved and self.raw_target is not None:
            self.target = resolve_point_to_sheet(self.raw_target, view)
            self.resolved = True
        elif not self.resolved and self.raw_target is None:
            self.target = (view.center_x, view.center_y)
            self.resolved = True

        if hasattr(view, "annotations") and self not in view.annotations:
            view.annotations.append(self)
        t_sheet = getattr(view, "sheet", None) or CURRENT_SHEET.get()
        if t_sheet is not None and self not in t_sheet.annotations:
            t_sheet.annotations.append(self)

    def to_svg(self) -> str:
        tx, ty = self.target
        ex = tx + self.elbow_offset[0]
        ey = ty + self.elbow_offset[1]

        going_left = (self.elbow_offset[0] < 0) if self.align_right is None else self.align_right
        sx = ex + (-self.shelf_length if going_left else self.shelf_length)

        elements: list[str] = []

        # Terminator at target
        if self.terminator == "arrow":
            dir_x = tx - ex
            dir_y = ty - ey
            elements.append(render_arrowhead(tx, ty, dir_x, dir_y, length=2.2, half_width=0.6))
        else:
            elements.append(
                f'<circle cx="{tx:.2f}" cy="{ty:.2f}" r="0.6" class="leader-dot" fill="currentColor"/>'
            )

        # Leader polyline
        elements.append(
            f'<polyline points="{tx:.2f},{ty:.2f} {ex:.2f},{ey:.2f} {sx:.2f},{ey:.2f}" class="dim-line" stroke-width="0.3" fill="none"/>'
        )

        # Text lines
        text_x = ex - 2.0 if going_left else ex + 2.0
        text_anchor = "end" if going_left else "start"

        for i, line in enumerate(self.lines):
            ly = ey - 1.2 - ((len(self.lines) - 1 - i) * 3.4)
            elements.append(
                f'<text x="{text_x:.2f}" y="{ly:.2f}" class="leader-text" text-anchor="{text_anchor}" font-size="{self.font_size:.2f}">{html.escape(line)}</text>'
            )

        return f'<g class="leader-note">{"".join(elements)}</g>'


class HoleCallout(LeaderNote):
    """Standard ISO / ASME hole and countersink/counterbore callout helper."""

    def __init__(
        self,
        target_or_center: Any = None,
        diameter: float | None = None,
        depth: float | str | None = None,
        countersink_dia: float | None = None,
        countersink_angle: float = 90.0,
        counterbore_dia: float | None = None,
        counterbore_depth: float | None = None,
        quantity: int | None = None,
        tolerance: str | None = None,
        center: Any = None,
        target: Any = None,
        elbow_offset: tuple[float, float] = (15.0, -12.0),
        leader_offset: tuple[float, float] | None = None,
        shelf_length: float = 20.0,
        align_right: bool | None = None,
        terminator: str = "arrow",
        font_size: float = 2.8,
        view: Any | None = None,
    ):
        self.diameter = diameter
        self.depth = depth
        self.countersink_dia = countersink_dia
        self.countersink_angle = countersink_angle
        self.counterbore_dia = counterbore_dia
        self.counterbore_depth = counterbore_depth
        self.quantity = quantity
        self.tolerance = str(tolerance) if tolerance is not None else None

        effective_target = (
            center if center is not None else (target if target is not None else target_or_center)
        )
        callout_text = self._format_callout(self.diameter)

        super().__init__(
            target=effective_target,
            lines=[callout_text],
            elbow_offset=elbow_offset,
            leader_offset=leader_offset,
            shelf_length=shelf_length,
            align_right=align_right,
            terminator=terminator,
            font_size=font_size,
            view=view,
        )

    def _format_callout(self, dia: float | None) -> str:
        qty_prefix = f"{self.quantity}x " if (self.quantity and self.quantity > 1) else ""
        dia_str = f"⌀{format_dimension_value(float(dia))}" if dia is not None else "⌀"
        tol_str = f" {self.tolerance}" if self.tolerance else ""

        if self.depth is None or (isinstance(self.depth, str) and self.depth.upper() == "THRU"):
            depth_str = " THRU"
        elif isinstance(self.depth, (int, float)):
            depth_str = f" ↧{format_dimension_value(float(self.depth))}"
        elif isinstance(self.depth, str) and self.depth.strip():
            depth_str = f" {self.depth.strip()}"
        else:
            depth_str = ""

        csink_str = ""
        if self.countersink_dia is not None:
            csink_str = (
                f" ⌵ ⌀{format_dimension_value(float(self.countersink_dia))}"
                f" × {format_dimension_value(float(self.countersink_angle))}°"
            )

        cbore_str = ""
        if self.counterbore_dia is not None:
            cbore_str = f" ⌴ ⌀{format_dimension_value(float(self.counterbore_dia))}"
            if self.counterbore_depth is not None:
                cbore_str += f" ↧{format_dimension_value(float(self.counterbore_depth))}"

        return f"{qty_prefix}{dia_str}{tol_str}{depth_str}{csink_str}{cbore_str}"

    def _attach_to_view(self, view: Any) -> None:
        if self.diameter is None and self.raw_target is not None:
            try:
                _, _, model_r = resolve_circle_or_cylinder_to_sheet(self.raw_target, view)
                self.diameter = 2.0 * model_r
                self.lines = [self._format_callout(self.diameter)]
            except TypeError:
                pass
        super()._attach_to_view(view)


class ThreadCallout(LeaderNote):
    """Standard ISO / ASME tapped thread callout helper."""

    def __init__(
        self,
        target_or_center: Any = None,
        thread: str = "M5x0.8",
        depth: float | str | None = "THRU",
        class_fit: str | None = None,
        quantity: int | None = None,
        countersink_dia: float | None = None,
        countersink_angle: float = 90.0,
        center: Any = None,
        target: Any = None,
        elbow_offset: tuple[float, float] = (15.0, -12.0),
        leader_offset: tuple[float, float] | None = None,
        shelf_length: float = 20.0,
        align_right: bool | None = None,
        terminator: str = "arrow",
        font_size: float = 2.8,
        view: Any | None = None,
    ):
        self.thread = thread
        self.depth = depth
        self.class_fit = class_fit
        self.quantity = quantity
        self.countersink_dia = countersink_dia
        self.countersink_angle = countersink_angle

        effective_target = (
            center if center is not None else (target if target is not None else target_or_center)
        )
        callout_text = self._format_callout()

        super().__init__(
            target=effective_target,
            lines=[callout_text],
            elbow_offset=elbow_offset,
            leader_offset=leader_offset,
            shelf_length=shelf_length,
            align_right=align_right,
            terminator=terminator,
            font_size=font_size,
            view=view,
        )

    def _format_callout(self) -> str:
        qty_prefix = f"{self.quantity}x " if (self.quantity and self.quantity > 1) else ""
        fit_str = f" - {self.class_fit}" if self.class_fit else ""

        if self.depth is None or (isinstance(self.depth, str) and self.depth.upper() == "THRU"):
            depth_str = " THRU"
        elif isinstance(self.depth, (int, float)):
            depth_str = f" ↧{format_dimension_value(float(self.depth))}"
        elif isinstance(self.depth, str) and self.depth.strip():
            depth_str = f" {self.depth.strip()}"
        else:
            depth_str = ""

        csink_str = ""
        if self.countersink_dia is not None:
            csink_str = (
                f" ⌵ ⌀{format_dimension_value(float(self.countersink_dia))}"
                f" × {format_dimension_value(float(self.countersink_angle))}°"
            )

        return f"{qty_prefix}{self.thread}{fit_str}{depth_str}{csink_str}"


class BOMBalloon:
    """ISO BOM balloon callout circle connected to component via leader."""

    def __init__(
        self,
        item_no: int | None = None,
        target: Any = None,
        balloon_center: tuple[float, float] | None = None,
        radius: float = 4.5,
        terminator: str = "dot",
        leader_offset: tuple[float, float] = (15.0, -15.0),
        view: Any | None = None,
        *,
        balloon_no: int | None = None,
        position: tuple[float, float] | None = None,
    ):
        resolved_no = (
            item_no if item_no is not None else (balloon_no if balloon_no is not None else 1)
        )
        self.item_no = int(resolved_no)
        self.radius = radius
        self.terminator = terminator
        self.leader_offset = leader_offset
        self.view = view

        effective_target = target if target is not None else position
        self.raw_target = effective_target
        self.target: tuple[float, float] = (0.0, 0.0)
        self.resolved = False

        if balloon_center is not None:
            self.balloon_center = (float(balloon_center[0]), float(balloon_center[1]))
            self.center_explicit = True
        elif target is not None and position is not None:
            self.balloon_center = (float(position[0]), float(position[1]))
            self.center_explicit = True
        else:
            self.balloon_center = (0.0, 0.0)
            self.center_explicit = False

        if (
            effective_target is not None
            and not is_topological_entity(effective_target)
            and isinstance(effective_target, (tuple, list))
            and len(effective_target) == 2
        ):
            self.target = (float(effective_target[0]), float(effective_target[1]))
            if not self.center_explicit:
                self.balloon_center = (
                    self.target[0] + self.leader_offset[0],
                    self.target[1] + self.leader_offset[1],
                )
            self.resolved = True

        target_view = view or CURRENT_VIEW.get()
        if target_view is not None:
            self._attach_to_view(target_view)
        else:
            target_sheet = CURRENT_SHEET.get()
            if target_sheet is not None and self not in target_sheet.annotations:
                target_sheet.annotations.append(self)

    @property
    def balloon_no(self) -> int:
        return self.item_no

    @balloon_no.setter
    def balloon_no(self, val: int) -> None:
        self.item_no = int(val)

    @property
    def position(self) -> tuple[float, float]:
        return self.target

    def _attach_to_view(self, view: Any) -> None:
        self.view = view
        if not self.resolved and self.raw_target is not None:
            self.target = resolve_point_to_sheet(self.raw_target, view)
            if not self.center_explicit:
                self.balloon_center = (
                    self.target[0] + self.leader_offset[0],
                    self.target[1] + self.leader_offset[1],
                )
            self.resolved = True
        elif not self.resolved and self.raw_target is None:
            self.target = (view.center_x, view.center_y)
            if not self.center_explicit:
                self.balloon_center = (
                    self.target[0] + self.leader_offset[0],
                    self.target[1] + self.leader_offset[1],
                )
            self.resolved = True

        if hasattr(view, "annotations") and self not in view.annotations:
            view.annotations.append(self)
        t_sheet = getattr(view, "sheet", None) or CURRENT_SHEET.get()
        if t_sheet is not None and self not in t_sheet.annotations:
            t_sheet.annotations.append(self)

    def to_svg(self) -> str:
        tx, ty = self.target
        bx, by = self.balloon_center
        r = self.radius

        # Calculate intersection with balloon perimeter
        dx = bx - tx
        dy = by - ty
        dist = math.hypot(dx, dy)
        if dist > r:
            end_x = bx - (dx / dist) * r
            end_y = by - (dy / dist) * r
        else:
            end_x, end_y = bx, by

        if self.terminator == "arrow" and dist > 0.0:
            term_elem = render_arrowhead(tx, ty, -dx, -dy, length=2.5, half_width=0.7)
        else:
            term_elem = f'<circle cx="{tx:.2f}" cy="{ty:.2f}" r="0.8" class="balloon-dot"/>'

        return f"""
        <g class="bom-balloon">
            {term_elem}
            <line x1="{tx:.2f}" y1="{ty:.2f}" x2="{end_x:.2f}" y2="{end_y:.2f}" class="balloon-leader" stroke-width="0.3"/>
            <circle cx="{bx:.2f}" cy="{by:.2f}" r="{r:.2f}" class="balloon-circle" stroke-width="0.4"/>
            <text x="{bx:.2f}" y="{by + 1.2:.2f}" class="balloon-text" text-anchor="middle" font-size="3.6" font-weight="bold">{self.item_no}</text>
        </g>
        """


class SectionCuttingLine:
    """ISO cutting plane line with thick termination segments and viewing direction arrows."""

    def __init__(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        label: str = "A",
        arrow_dir: str = "up",
        thick_length: float = 8.0,
        arrow_length: float = 6.0,
        view: Any | None = None,
    ):
        self.start = (float(start[0]), float(start[1]))
        self.end = (float(end[0]), float(end[1]))
        self.label = label
        self.arrow_dir = arrow_dir
        self.thick_length = thick_length
        self.arrow_length = arrow_length
        self.view = view

        target_view = view or CURRENT_VIEW.get()
        if target_view is not None:
            if hasattr(target_view, "annotations") and self not in target_view.annotations:
                target_view.annotations.append(self)
            t_sheet = getattr(target_view, "sheet", None) or CURRENT_SHEET.get()
            if t_sheet is not None and self not in t_sheet.annotations:
                t_sheet.annotations.append(self)
        else:
            target_sheet = CURRENT_SHEET.get()
            if target_sheet is not None and self not in target_sheet.annotations:
                target_sheet.annotations.append(self)

    def to_svg(self) -> str:
        x1, y1 = self.start
        x2, y2 = self.end
        dx = x2 - x1
        dy = y2 - y1
        length = math.hypot(dx, dy)
        if length == 0.0:
            return ""

        ux = dx / length
        uy = dy / length

        if self.arrow_dir == "up":
            nx, ny = -uy, ux
        elif self.arrow_dir == "down":
            nx, ny = uy, -ux
        elif self.arrow_dir == "left":
            nx, ny = -uy, ux
        else:
            nx, ny = uy, -ux

        tl = min(self.thick_length, length / 3.0)
        al = self.arrow_length

        elements = [
            # Central long-dash dot-dash line
            f'<line x1="{x1 + ux * tl:.2f}" y1="{y1 + uy * tl:.2f}" x2="{x2 - ux * tl:.2f}" y2="{y2 - uy * tl:.2f}" class="line-center" stroke-dasharray="10,2,2,2" stroke-width="0.35"/>',
            # Thick end segment 1
            f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x1 + ux * tl:.2f}" y2="{y1 + uy * tl:.2f}" class="line-section-thick" stroke-width="0.8"/>',
            # Thick end segment 2
            f'<line x1="{x2 - ux * tl:.2f}" y1="{y2 - uy * tl:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" class="line-section-thick" stroke-width="0.8"/>',
            # Arrow 1
            f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x1 + nx * al:.2f}" y2="{y1 + ny * al:.2f}" class="line-section-thick" stroke-width="0.6"/>',
            render_arrowhead(x1 + nx * al, y1 + ny * al, nx, ny, length=3.0, half_width=0.9),
            f'<text x="{x1 + nx * (al + 3.5):.2f}" y="{y1 + ny * (al + 3.5) + 1.5:.2f}" class="section-label-text" font-size="5.0" font-weight="bold" text-anchor="middle">{html.escape(self.label)}</text>',
            # Arrow 2
            f'<line x1="{x2:.2f}" y1="{y2:.2f}" x2="{x2 + nx * al:.2f}" y2="{y2 + ny * al:.2f}" class="line-section-thick" stroke-width="0.6"/>',
            render_arrowhead(x2 + nx * al, y2 + ny * al, nx, ny, length=3.0, half_width=0.9),
            f'<text x="{x2 + nx * (al + 3.5):.2f}" y="{y2 + ny * (al + 3.5) + 1.5:.2f}" class="section-label-text" font-size="5.0" font-weight="bold" text-anchor="middle">{html.escape(self.label)}</text>',
        ]
        return f'<g class="section-cutting-plane">{"".join(elements)}</g>'
