"""ISO 1302 surface roughness and texture symbols."""

import html
from dataclasses import dataclass
from enum import Enum


class MaterialRemoval(Enum):
    ANY = "any"  # Basic symbol (open checkmark)
    REQUIRED = "required"  # Triangle closed (machining required)
    PROHIBITED = "prohibited"  # Circle in vertex (no material removal)


@dataclass
class SurfaceFinishSymbol:
    """ISO 1302 surface texture / roughness symbol."""

    position: tuple[float, float]
    ra_value: str = "Ra 3.2"
    material_removal: MaterialRemoval = MaterialRemoval.REQUIRED
    process: str | None = None
    scale: float = 1.0

    def to_svg(self) -> str:
        x, y = self.position
        s = self.scale

        # Standard ISO 1302 60-degree tick geometry
        p_vertex = (x, y)
        p_left = (x - 2.5 * s, y - 4.33 * s)
        p_right = (x + 5.0 * s, y - 8.66 * s)
        shelf_len = max(12.0 * s, len(self.ra_value) * 2.2 * s + 4.0 * s)
        p_shelf_end = (p_right[0] + shelf_len, p_right[1])

        elements = [
            # Short left leg
            f'<line x1="{p_vertex[0]:.2f}" y1="{p_vertex[1]:.2f}" x2="{p_left[0]:.2f}" y2="{p_left[1]:.2f}" class="dim-line" stroke-width="0.35"/>',
            # Long right leg
            f'<line x1="{p_vertex[0]:.2f}" y1="{p_vertex[1]:.2f}" x2="{p_right[0]:.2f}" y2="{p_right[1]:.2f}" class="dim-line" stroke-width="0.35"/>',
            # Horizontal shelf bar
            f'<line x1="{p_right[0]:.2f}" y1="{p_right[1]:.2f}" x2="{p_shelf_end[0]:.2f}" y2="{p_shelf_end[1]:.2f}" class="dim-line" stroke-width="0.35"/>',
        ]

        if self.material_removal == MaterialRemoval.REQUIRED:
            # Closing horizontal bar across tick
            elements.append(
                f'<line x1="{p_left[0]:.2f}" y1="{p_left[1]:.2f}" x2="{x + 2.5 * s:.2f}" y2="{y - 4.33 * s:.2f}" class="dim-line" stroke-width="0.35"/>'
            )
        elif self.material_removal == MaterialRemoval.PROHIBITED:
            # Circle centered in checkmark
            elements.append(
                f'<circle cx="{x + 1.2 * s:.2f}" cy="{y - 3.2 * s:.2f}" r="{1.5 * s:.2f}" fill="none" class="dim-line" stroke-width="0.3"/>'
            )

        # Ra text above the shelf bar
        text_x = p_right[0] + 1.5 * s
        text_y = p_right[1] - 1.2 * s
        elements.append(
            f'<text x="{text_x:.2f}" y="{text_y:.2f}" class="dim-text" font-size="{2.8 * s:.2f}">{html.escape(self.ra_value)}</text>'
        )

        if self.process:
            proc_y = p_right[1] + 3.2 * s
            elements.append(
                f'<text x="{text_x:.2f}" y="{proc_y:.2f}" class="notes-text" font-size="{2.2 * s:.2f}">{html.escape(self.process)}</text>'
            )

        return f'<g class="surface-finish-symbol">{"".join(elements)}</g>'
