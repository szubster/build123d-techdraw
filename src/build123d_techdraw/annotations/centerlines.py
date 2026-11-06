"""ISO centerlines, center crosshairs, and bolt pitch circles (PCD)."""

import math
from dataclasses import dataclass


@dataclass
class Centerline:
    """ISO 128 compliant long-dash dot-dash centerline."""

    start: tuple[float, float]
    end: tuple[float, float]
    stroke_width: float = 0.25

    def to_svg(self) -> str:
        x1, y1 = self.start
        x2, y2 = self.end
        return (
            f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
            f'class="line-center" stroke-dasharray="10,2,2,2" stroke-width="{self.stroke_width:.2f}"/>'
        )


@dataclass
class CenterMark:
    """ISO centermark crosshair with central intersection and overhang."""

    center: tuple[float, float]
    size: float = 6.0
    stroke_width: float = 0.25

    def to_svg(self) -> str:
        cx, cy = self.center
        half = self.size / 2.0
        return f"""
        <g class="centermark">
            <line x1="{cx - half:.2f}" y1="{cy:.2f}" x2="{cx + half:.2f}" y2="{cy:.2f}" class="line-center" stroke-width="{self.stroke_width:.2f}"/>
            <line x1="{cx:.2f}" y1="{cy - half:.2f}" x2="{cx:.2f}" y2="{cy + half:.2f}" class="line-center" stroke-width="{self.stroke_width:.2f}"/>
        </g>
        """


@dataclass
class BoltCircle:
    """Bolt Circle / Pitch Circle Diameter (PCD) annotation with radial hole crosshairs."""

    center: tuple[float, float]
    diameter: float
    num_holes: int = 4
    start_angle_deg: float = 0.0
    hole_cross_size: float = 4.0
    stroke_width: float = 0.25

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
