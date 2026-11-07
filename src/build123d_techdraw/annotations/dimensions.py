"""ISO 129 compliant engineering dimensioning in true 2D paper space."""

import html
import math
from dataclasses import dataclass
from enum import Enum


class DimensionType(Enum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    ALIGNED = "aligned"
    RADIAL = "radial"
    DIAMETER = "diameter"


def format_dimension_value(value: float, precision: int = 2) -> str:
    """Format dimension numerical value cleanly without redundant trailing zeros."""
    formatted = f"{value:.{precision}f}"
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    return formatted


def render_arrowhead(
    tip_x: float,
    tip_y: float,
    dir_x: float,
    dir_y: float,
    length: float = 2.5,
    half_width: float = 0.7,
) -> str:
    """Render a closed filled 30-degree ISO arrowhead pointing in direction (dir_x, dir_y)."""
    dist = math.hypot(dir_x, dir_y)
    if dist == 0.0:
        return ""
    ux = dir_x / dist
    uy = dir_y / dist
    # Perpendicular unit vector
    nx = -uy
    ny = ux

    base_x = tip_x - ux * length
    base_y = tip_y - uy * length

    p1_x = base_x + nx * half_width
    p1_y = base_y + ny * half_width
    p2_x = base_x - nx * half_width
    p2_y = base_y - ny * half_width

    return f'<polygon points="{tip_x:.2f},{tip_y:.2f} {p1_x:.2f},{p1_y:.2f} {p2_x:.2f},{p2_y:.2f}" class="dim-arrow"/>'


@dataclass
class LinearDimension:
    """Linear dimension (Horizontal, Vertical, or Aligned) in true 2D paper space."""

    start: tuple[float, float]
    end: tuple[float, float]
    offset: float = 8.0
    dim_type: DimensionType = DimensionType.ALIGNED
    text: str | None = None
    tolerance: str | None = None
    prefix: str = ""
    suffix: str = ""
    font_size: float = 3.2
    arrow_size: float = 2.5

    def to_svg(self) -> str:
        x1, y1 = self.start
        x2, y2 = self.end
        ext_gap = 1.5
        ext_overshoot = 2.0

        if self.dim_type == DimensionType.HORIZONTAL:
            # Horizontal dimension across x1, x2
            left_x = min(x1, x2)
            right_x = max(x1, x2)
            base_y = (y1 + y2) / 2.0
            dim_y = base_y + self.offset
            dist = right_x - left_x

            disp_val = self.text if self.text is not None else format_dimension_value(dist)
            full_str = f"{self.prefix}{disp_val}{self.suffix}"
            if self.tolerance:
                full_str += f" {self.tolerance}"

            gap_dir = 1.0 if self.offset >= 0 else -1.0
            ext_y_start = base_y + gap_dir * ext_gap
            ext_y_end = dim_y + gap_dir * ext_overshoot

            elements = [
                # Extension lines
                f'<line x1="{left_x:.2f}" y1="{ext_y_start:.2f}" x2="{left_x:.2f}" y2="{ext_y_end:.2f}" class="dim-line" stroke-width="0.25"/>',
                f'<line x1="{right_x:.2f}" y1="{ext_y_start:.2f}" x2="{right_x:.2f}" y2="{ext_y_end:.2f}" class="dim-line" stroke-width="0.25"/>',
                # Dimension line
                f'<line x1="{left_x:.2f}" y1="{dim_y:.2f}" x2="{right_x:.2f}" y2="{dim_y:.2f}" class="dim-line" stroke-width="0.3"/>',
                # Arrowheads
                render_arrowhead(left_x, dim_y, -1.0, 0.0, length=self.arrow_size),
                render_arrowhead(right_x, dim_y, 1.0, 0.0, length=self.arrow_size),
                # Dimension text
                f'<text x="{(left_x + right_x) / 2.0:.2f}" y="{dim_y - 1.2:.2f}" class="dim-text" text-anchor="middle" font-size="{self.font_size:.2f}">{html.escape(full_str)}</text>',
            ]
            return "".join(elements)

        elif self.dim_type == DimensionType.VERTICAL:
            # Vertical dimension across y1, y2
            top_y = min(y1, y2)
            bottom_y = max(y1, y2)
            base_x = (x1 + x2) / 2.0
            dim_x = base_x + self.offset
            dist = bottom_y - top_y

            disp_val = self.text if self.text is not None else format_dimension_value(dist)
            full_str = f"{self.prefix}{disp_val}{self.suffix}"
            if self.tolerance:
                full_str += f" {self.tolerance}"

            gap_dir = 1.0 if self.offset >= 0 else -1.0
            ext_x_start = base_x + gap_dir * ext_gap
            ext_x_end = dim_x + gap_dir * ext_overshoot

            mid_y = (top_y + bottom_y) / 2.0
            elements = [
                # Extension lines
                f'<line x1="{ext_x_start:.2f}" y1="{top_y:.2f}" x2="{ext_x_end:.2f}" y2="{top_y:.2f}" class="dim-line" stroke-width="0.25"/>',
                f'<line x1="{ext_x_start:.2f}" y1="{bottom_y:.2f}" x2="{ext_x_end:.2f}" y2="{bottom_y:.2f}" class="dim-line" stroke-width="0.25"/>',
                # Dimension line
                f'<line x1="{dim_x:.2f}" y1="{top_y:.2f}" x2="{dim_x:.2f}" y2="{bottom_y:.2f}" class="dim-line" stroke-width="0.3"/>',
                # Arrowheads
                render_arrowhead(dim_x, top_y, 0.0, -1.0, length=self.arrow_size),
                render_arrowhead(dim_x, bottom_y, 0.0, 1.0, length=self.arrow_size),
                # Rotated dimension text
                f'<text x="{dim_x - 1.4:.2f}" y="{mid_y:.2f}" transform="rotate(-90 {dim_x - 1.4:.2f} {mid_y:.2f})" class="dim-text" text-anchor="middle" font-size="{self.font_size:.2f}">{html.escape(full_str)}</text>',
            ]
            return "".join(elements)

        else:
            # General aligned dimension along arbitrary vector
            dx = x2 - x1
            dy = y2 - y1
            length = math.hypot(dx, dy)
            if length == 0.0:
                return ""
            ux = dx / length
            uy = dy / length
            # Perpendicular normal
            nx = -uy
            ny = ux

            dim_x1 = x1 + nx * self.offset
            dim_y1 = y1 + ny * self.offset
            dim_x2 = x2 + nx * self.offset
            dim_y2 = y2 + ny * self.offset

            disp_val = self.text if self.text is not None else format_dimension_value(length)
            full_str = f"{self.prefix}{disp_val}{self.suffix}"
            if self.tolerance:
                full_str += f" {self.tolerance}"

            gap_dir = 1.0 if self.offset >= 0 else -1.0
            ext1_start_x = x1 + nx * gap_dir * ext_gap
            ext1_start_y = y1 + ny * gap_dir * ext_gap
            ext1_end_x = dim_x1 + nx * gap_dir * ext_overshoot
            ext1_end_y = dim_y1 + ny * gap_dir * ext_overshoot

            ext2_start_x = x2 + nx * gap_dir * ext_gap
            ext2_start_y = y2 + ny * gap_dir * ext_gap
            ext2_end_x = dim_x2 + nx * gap_dir * ext_overshoot
            ext2_end_y = dim_y2 + ny * gap_dir * ext_overshoot

            mid_x = (dim_x1 + dim_x2) / 2.0
            mid_y = (dim_y1 + dim_y2) / 2.0
            angle_deg = math.degrees(math.atan2(dy, dx))
            # Keep text readable (never upside down)
            if angle_deg > 90.0 or angle_deg <= -90.0:
                angle_deg += 180.0

            text_offset_dist = 1.5
            text_x = mid_x + nx * text_offset_dist
            text_y = mid_y + ny * text_offset_dist

            elements = [
                # Extension lines
                f'<line x1="{ext1_start_x:.2f}" y1="{ext1_start_y:.2f}" x2="{ext1_end_x:.2f}" y2="{ext1_end_y:.2f}" class="dim-line" stroke-width="0.25"/>',
                f'<line x1="{ext2_start_x:.2f}" y1="{ext2_start_y:.2f}" x2="{ext2_end_x:.2f}" y2="{ext2_end_y:.2f}" class="dim-line" stroke-width="0.25"/>',
                # Dimension line
                f'<line x1="{dim_x1:.2f}" y1="{dim_y1:.2f}" x2="{dim_x2:.2f}" y2="{dim_y2:.2f}" class="dim-line" stroke-width="0.3"/>',
                # Arrowheads
                render_arrowhead(dim_x1, dim_y1, -ux, -uy, length=self.arrow_size),
                render_arrowhead(dim_x2, dim_y2, ux, uy, length=self.arrow_size),
                # Aligned text
                f'<text x="{text_x:.2f}" y="{text_y:.2f}" transform="rotate({angle_deg:.1f} {text_x:.2f} {text_y:.2f})" class="dim-text" text-anchor="middle" font-size="{self.font_size:.2f}">{html.escape(full_str)}</text>',
            ]
            return "".join(elements)


@dataclass
class RadialDimension:
    """Radius dimension (R) pointing to circular arc."""

    center: tuple[float, float]
    radius: float
    angle_deg: float = 45.0
    prefix: str = ""
    suffix: str = ""
    text: str | None = None
    tolerance: str | None = None
    shelf_length: float = 12.0
    arrow_size: float = 2.5
    font_size: float = 3.2

    def to_svg(self) -> str:
        cx, cy = self.center
        rad = math.radians(self.angle_deg)
        ux = math.cos(rad)
        uy = -math.sin(rad)  # SVG y is inverted

        arc_x = cx + ux * self.radius
        arc_y = cy + uy * self.radius

        # Leader extension beyond arc
        leader_len = 10.0
        elbow_x = arc_x + ux * leader_len
        elbow_y = arc_y + uy * leader_len

        shelf_dir = 1.0 if ux >= 0 else -1.0
        shelf_x = elbow_x + shelf_dir * self.shelf_length

        disp_val = self.text if self.text is not None else f"R{format_dimension_value(self.radius)}"
        full_str = f"{self.prefix}{disp_val}{self.suffix}"
        if self.tolerance:
            full_str += f" {self.tolerance}"

        text_anchor = "start" if shelf_dir > 0 else "end"
        text_x = elbow_x + (2.0 if shelf_dir > 0 else -2.0)
        text_y = elbow_y - 1.2

        elements = [
            # Leader line from arc to elbow and shelf
            f'<polyline points="{arc_x:.2f},{arc_y:.2f} {elbow_x:.2f},{elbow_y:.2f} {shelf_x:.2f},{elbow_y:.2f}" class="dim-line" stroke-width="0.3" fill="none"/>',
            # Arrowhead touching the arc
            render_arrowhead(arc_x, arc_y, -ux, -uy, length=self.arrow_size),
            # Text
            f'<text x="{text_x:.2f}" y="{text_y:.2f}" class="dim-text" text-anchor="{text_anchor}" font-size="{self.font_size:.2f}">{html.escape(full_str)}</text>',
        ]
        return f'<g class="radial-dim">{"".join(elements)}</g>'


@dataclass
class DiameterDimension:
    """Diameter dimension (⌀) across full circle or with leader."""

    center: tuple[float, float]
    diameter: float
    angle_deg: float = 45.0
    prefix: str = ""
    suffix: str = ""
    text: str | None = None
    tolerance: str | None = None
    shelf_length: float = 12.0
    arrow_size: float = 2.5
    font_size: float = 3.2

    def to_svg(self) -> str:
        cx, cy = self.center
        radius = self.diameter / 2.0
        rad = math.radians(self.angle_deg)
        ux = math.cos(rad)
        uy = -math.sin(rad)

        p1_x = cx - ux * radius
        p1_y = cy - uy * radius
        p2_x = cx + ux * radius
        p2_y = cy + uy * radius

        leader_len = 10.0
        elbow_x = p2_x + ux * leader_len
        elbow_y = p2_y + uy * leader_len

        shelf_dir = 1.0 if ux >= 0 else -1.0
        shelf_x = elbow_x + shelf_dir * self.shelf_length

        disp_val = (
            self.text if self.text is not None else f"⌀{format_dimension_value(self.diameter)}"
        )
        full_str = f"{self.prefix}{disp_val}{self.suffix}"
        if self.tolerance:
            full_str += f" {self.tolerance}"

        text_anchor = "start" if shelf_dir > 0 else "end"
        text_x = elbow_x + (2.0 if shelf_dir > 0 else -2.0)
        text_y = elbow_y - 1.2

        elements = [
            # Diametral line through circle
            f'<line x1="{p1_x:.2f}" y1="{p1_y:.2f}" x2="{p2_x:.2f}" y2="{p2_y:.2f}" class="dim-line" stroke-width="0.3"/>',
            # Leader to shelf
            f'<polyline points="{p2_x:.2f},{p2_y:.2f} {elbow_x:.2f},{elbow_y:.2f} {shelf_x:.2f},{elbow_y:.2f}" class="dim-line" stroke-width="0.3" fill="none"/>',
            # Dual arrowheads inside circle
            render_arrowhead(p1_x, p1_y, -ux, -uy, length=self.arrow_size),
            render_arrowhead(p2_x, p2_y, ux, uy, length=self.arrow_size),
            # Text
            f'<text x="{text_x:.2f}" y="{text_y:.2f}" class="dim-text" text-anchor="{text_anchor}" font-size="{self.font_size:.2f}">{html.escape(full_str)}</text>',
        ]
        return f'<g class="diameter-dim">{"".join(elements)}</g>'
