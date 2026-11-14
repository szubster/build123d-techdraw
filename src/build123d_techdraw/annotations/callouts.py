"""Callouts, leader notes, hole/thread callouts, BOM balloons, and section cutting lines."""

from __future__ import annotations

import html
import math
from enum import Enum
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
        item: int | None = None,
        elbow_offset: tuple[float, float] | None = None,
        balloon_no: int | None = None,
        position: tuple[float, float] | None = None,
    ):
        resolved_no = (
            item
            if item is not None
            else (item_no if item_no is not None else (balloon_no if balloon_no is not None else 1))
        )
        self.item_no = int(resolved_no)
        self.radius = radius
        self.terminator = terminator
        self.leader_offset = elbow_offset if elbow_offset is not None else leader_offset
        self.view = view

        self.raw_target = target
        self.raw_position = position
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

        if position is not None and target is None:
            self.target = (float(position[0]), float(position[1]))
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
            if (
                target is not None
                and not is_topological_entity(target)
                and isinstance(target, (tuple, list))
                and len(target) == 2
            ):
                self.target = (float(target[0]), float(target[1]))
                if not self.center_explicit:
                    self.balloon_center = (
                        self.target[0] + self.leader_offset[0],
                        self.target[1] + self.leader_offset[1],
                    )
                self.resolved = True
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
            if isinstance(self.raw_target, (tuple, list)):
                if len(self.raw_target) == 2:
                    self.target = view.to_sheet_coords(
                        float(self.raw_target[0]), float(self.raw_target[1])
                    )
                elif len(self.raw_target) == 3:
                    self.target = view.to_sheet_3d(
                        float(self.raw_target[0]),
                        float(self.raw_target[1]),
                        float(self.raw_target[2]),
                    )
                else:
                    self.target = (float(self.raw_target[0]), float(self.raw_target[1]))
            else:
                self.target = resolve_point_to_sheet(self.raw_target, view)

            if not self.center_explicit:
                self.balloon_center = (
                    self.target[0] + self.leader_offset[0],
                    self.target[1] + self.leader_offset[1],
                )
            self.resolved = True
        elif not self.resolved and self.raw_target is None and not self.resolved:
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


class ChamferCallout(LeaderNote):
    """ISO / ASME chamfer callout (e.g. '2 x 45°' or 'C2')."""

    def __init__(
        self,
        target_or_edge: Any = None,
        width: float | None = None,
        angle_deg: float = 45.0,
        style: str = "iso",
        quantity: int | None = None,
        elbow_offset: tuple[float, float] = (15.0, -12.0),
        shelf_length: float = 18.0,
        view: Any | None = None,
    ):
        self.width = width
        self.angle_deg = angle_deg
        self.style = style.lower()
        self.quantity = quantity

        callout_text = self._format_chamfer(self.width, self.angle_deg)
        super().__init__(
            target=target_or_edge,
            lines=[callout_text],
            elbow_offset=elbow_offset,
            shelf_length=shelf_length,
            terminator="arrow",
            view=view,
        )

    def _format_chamfer(self, w: float | None, a: float) -> str:
        qty_prefix = f"{self.quantity}x " if (self.quantity and self.quantity > 1) else ""
        w_val = format_dimension_value(float(w)) if w is not None else "2"
        if self.style in ("c", "compact"):
            return f"{qty_prefix}C{w_val}"
        a_val = format_dimension_value(float(a))
        return f"{qty_prefix}{w_val} × {a_val}°"

    def _attach_to_view(self, view: Any) -> None:
        if self.width is None and self.raw_target is not None:
            import build123d as bd

            if (
                isinstance(self.raw_target, bd.Edge)
                and self.raw_target.geom_type == bd.GeomType.CIRCLE
            ):
                self.width = float(self.raw_target.radius) * 0.1
                self.lines = [self._format_chamfer(self.width, self.angle_deg)]
        super()._attach_to_view(view)


class WeldType(Enum):
    """ISO 2553 elementary weld symbols."""

    FILLET = "△"
    SQUARE_BUTT = "||"
    V_BUTT = "V"
    BEVEL_BUTT = "Y"
    U_BUTT = "U"
    PLUG = "⌴"
    SEAM = "○"


class WeldingSymbol:
    """ISO 2553 welding annotation symbol with reference line, joint arrow, and process codes."""

    def __init__(
        self,
        target: Any = None,
        weld_type: WeldType | str = WeldType.FILLET,
        size: str | float | None = "a5",
        length: str | float | None = None,
        pitch: str | float | None = None,
        all_around: bool = False,
        field_weld: bool = False,
        tail_text: str | None = None,
        arrow_side: bool = True,
        elbow_offset: tuple[float, float] = (16.0, -12.0),
        shelf_length: float = 24.0,
        font_size: float = 2.8,
        view: Any | None = None,
    ):
        self.target_raw = target
        self.weld_type = (
            weld_type
            if isinstance(weld_type, WeldType)
            else WeldType[weld_type.upper().replace("-", "_")]
        )
        self.size = (
            f"a{format_dimension_value(float(size))}"
            if isinstance(size, (int, float))
            else (str(size) if size else "")
        )
        self.length = (
            format_dimension_value(float(length))
            if isinstance(length, (int, float))
            else (str(length) if length else "")
        )
        self.pitch = (
            format_dimension_value(float(pitch))
            if isinstance(pitch, (int, float))
            else (str(pitch) if pitch else "")
        )
        self.all_around = all_around
        self.field_weld = field_weld
        self.tail_text = tail_text
        self.arrow_side = arrow_side
        self.elbow_offset = elbow_offset
        self.shelf_length = shelf_length
        self.font_size = font_size
        self.view = view

        self.target: tuple[float, float] = (0.0, 0.0)
        self.resolved = False

        if (
            isinstance(target, (tuple, list))
            and len(target) == 2
            and not is_topological_entity(target)
        ):
            self.target = (float(target[0]), float(target[1]))
            self.resolved = True

        target_view = view or CURRENT_VIEW.get()
        if target_view is not None:
            self._attach_to_view(target_view)
        else:
            target_sheet = CURRENT_SHEET.get()
            if target_sheet is not None and self not in target_sheet.annotations:
                target_sheet.annotations.append(self)

    def _attach_to_view(self, view: Any) -> None:
        self.view = view
        if not self.resolved and self.target_raw is not None:
            self.target = resolve_point_to_sheet(self.target_raw, view)
            self.resolved = True
        elif not self.resolved:
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
        going_left = self.elbow_offset[0] < 0
        shelf_dir = -1.0 if going_left else 1.0
        sx = ex + shelf_dir * self.shelf_length

        elements: list[str] = []

        # Arrowhead touching joint
        dx = ex - tx
        dy = ey - ty
        elements.append(render_arrowhead(tx, ty, -dx, -dy, length=2.5, half_width=0.7))

        # Leader line to elbow
        elements.append(
            f'<line x1="{tx:.2f}" y1="{ty:.2f}" x2="{ex:.2f}" y2="{ey:.2f}" class="dim-line" stroke-width="0.3"/>'
        )

        # Horizontal reference line
        elements.append(
            f'<line x1="{ex:.2f}" y1="{ey:.2f}" x2="{sx:.2f}" y2="{ey:.2f}" class="dim-line" stroke-width="0.35"/>'
        )

        # All-around circle at elbow junction
        if self.all_around:
            elements.append(
                f'<circle cx="{ex:.2f}" cy="{ey:.2f}" r="2.2" class="dim-line weld-all-around" stroke-width="0.3" fill="none"/>'
            )

        # Field weld flag
        if self.field_weld:
            flag_dir = -1.0 if going_left else 1.0
            elements.append(
                f'<path d="M {ex:.2f} {ey:.2f} L {ex:.2f} {ey - 4.5:.2f} L {ex + flag_dir * 3.5:.2f} {ey - 3.2:.2f} L {ex:.2f} {ey - 2.0:.2f}" class="dim-line weld-flag" stroke-width="0.3" fill="currentColor"/>'
            )

        # Dashed identification line for other side (ISO 2553)
        if not self.arrow_side:
            dashed_y = ey + 2.0
            elements.append(
                f'<line x1="{ex:.2f}" y1="{dashed_y:.2f}" x2="{sx:.2f}" y2="{dashed_y:.2f}" class="dim-line weld-ref-dashed" stroke-width="0.25" stroke-dasharray="2,1.5"/>'
            )

        # Weld symbol and dimension text
        sym_y = ey - 1.2 if self.arrow_side else ey + 3.2
        text_y = ey - 1.2 if self.arrow_side else ey + 3.2
        mid_x = (ex + sx) / 2.0

        # Dimensions: size on left of symbol, length(pitch) on right
        size_str = self.size
        len_pitch_str = ""
        if self.length:
            len_pitch_str = f" {self.length}"
            if self.pitch:
                len_pitch_str += f"({self.pitch})"

        elements.append(
            f'<text x="{mid_x:.2f}" y="{sym_y:.2f}" class="tb-title" text-anchor="middle" font-size="{self.font_size * 1.1:.2f}">{self.weld_type.value}</text>'
        )
        if size_str:
            elements.append(
                f'<text x="{mid_x - 3.5:.2f}" y="{text_y:.2f}" class="dim-text" text-anchor="end" font-size="{self.font_size:.2f}">{html.escape(size_str)}</text>'
            )
        if len_pitch_str:
            elements.append(
                f'<text x="{mid_x + 3.5:.2f}" y="{text_y:.2f}" class="dim-text" text-anchor="start" font-size="{self.font_size:.2f}">{html.escape(len_pitch_str.strip())}</text>'
            )

        # Tail text (fork at end of reference line)
        if self.tail_text:
            fork_x = sx + shelf_dir * 3.0
            elements.append(
                f'<polyline points="{sx:.2f},{ey - 2.0:.2f} {fork_x:.2f},{ey:.2f} {sx:.2f},{ey + 2.0:.2f}" class="dim-line" stroke-width="0.3" fill="none"/>'
            )
            t_anchor = "end" if going_left else "start"
            elements.append(
                f'<text x="{fork_x + shelf_dir * 1.5:.2f}" y="{ey + 1.0:.2f}" class="tb-field-val" text-anchor="{t_anchor}" font-size="{self.font_size:.2f}">{html.escape(self.tail_text)}</text>'
            )

        return f'<g class="welding-symbol">{"".join(elements)}</g>'


class StepBadge:
    """Circular step number badge for assembly manuals and procedural drawings."""

    def __init__(
        self,
        step_number: int | str = 1,
        position: tuple[float, float] = (15.0, 15.0),
        radius: float = 6.0,
        label: str | None = None,
        font_size: float = 4.0,
        view: Any | None = None,
        sheet: Any | None = None,
    ):
        self.step_number = step_number
        self.position = (float(position[0]), float(position[1]))
        self.radius = float(radius)
        self.label = label
        self.font_size = float(font_size)
        self.view = view
        self.sheet = sheet

        target_view = view or CURRENT_VIEW.get()
        if target_view is not None:
            self._attach_to_view(target_view)
        else:
            target_sheet = sheet or CURRENT_SHEET.get()
            if target_sheet is not None and self not in target_sheet.annotations:
                target_sheet.annotations.append(self)
                self.sheet = target_sheet

    def _attach_to_view(self, view: Any) -> None:
        self.view = view
        if hasattr(view, "annotations") and self not in view.annotations:
            view.annotations.append(self)
        t_sheet = getattr(view, "sheet", None) or CURRENT_SHEET.get()
        if t_sheet is not None and self not in t_sheet.annotations:
            t_sheet.annotations.append(self)
            self.sheet = t_sheet

    def bounding_box_2d(self) -> tuple[float, float, float, float]:
        cx, cy = self.position
        r = self.radius
        return (cx - r, cy - r, cx + r, cy + r)

    def to_svg(self) -> str:
        cx, cy = self.position
        r = self.radius
        elements = [
            f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" class="balloon-circle" stroke-width="0.6" fill="currentColor" fill-opacity="0.12"/>',
            f'<text x="{cx:.2f}" y="{cy + self.font_size * 0.35:.2f}" class="balloon-text" text-anchor="middle" font-size="{self.font_size:.2f}" font-weight="bold">{html.escape(str(self.step_number))}</text>',
        ]
        if self.label:
            elements.append(
                f'<text x="{cx + r + 3.0:.2f}" y="{cy + 1.2:.2f}" class="tb-title" text-anchor="start" font-size="{self.font_size * 0.85:.2f}">{html.escape(self.label)}</text>'
            )
        return f'<g class="step-badge">{"".join(elements)}</g>'
