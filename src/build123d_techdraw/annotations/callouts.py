"""Callouts, leader notes, BOM balloons, and section cutting lines."""

import html
import math
from dataclasses import dataclass

from build123d_techdraw.annotations.dimensions import render_arrowhead


@dataclass
class LeaderNote:
    """Multi-line leader note with pointer dot/arrow, elbow, and horizontal shelf."""

    target: tuple[float, float]
    lines: list[str]
    elbow_offset: tuple[float, float] = (15.0, -12.0)
    shelf_length: float = 20.0
    align_right: bool | None = None
    terminator: str = "dot"  # "dot" or "arrow"
    font_size: float = 2.8

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


@dataclass
class BOMBalloon:
    """ISO BOM balloon callout circle connected to component via leader."""

    item_no: int
    target: tuple[float, float]
    balloon_center: tuple[float, float]
    radius: float = 4.5

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

        return f"""
        <g class="bom-balloon">
            <circle cx="{tx:.2f}" cy="{ty:.2f}" r="0.8" class="balloon-dot"/>
            <line x1="{tx:.2f}" y1="{ty:.2f}" x2="{end_x:.2f}" y2="{end_y:.2f}" class="balloon-leader" stroke-width="0.3"/>
            <circle cx="{bx:.2f}" cy="{by:.2f}" r="{r:.2f}" class="balloon-circle" stroke-width="0.4"/>
            <text x="{bx:.2f}" y="{by + 1.2:.2f}" class="balloon-text" text-anchor="middle" font-size="3.6" font-weight="bold">{self.item_no}</text>
        </g>
        """


@dataclass
class SectionCuttingLine:
    """ISO cutting plane line with thick termination segments and viewing direction arrows."""

    start: tuple[float, float]
    end: tuple[float, float]
    label: str = "A"
    arrow_dir: str = "up"  # "up", "down", "left", "right"
    thick_length: float = 8.0
    arrow_length: float = 6.0

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
