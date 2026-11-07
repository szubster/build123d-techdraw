"""ISO 1302 surface roughness and texture symbols."""

from __future__ import annotations

import html
from enum import Enum
from typing import Any

from build123d_techdraw.annotations.resolver import is_topological_entity, resolve_point_to_sheet
from build123d_techdraw.context import CURRENT_SHEET, CURRENT_VIEW


class MaterialRemoval(Enum):
    ANY = "any"  # Basic symbol (open checkmark)
    REQUIRED = "required"  # Triangle closed (machining required)
    PROHIBITED = "prohibited"  # Circle in vertex (no material removal)


class SurfaceFinishSymbol:
    """ISO 1302 surface texture / roughness symbol."""

    def __init__(
        self,
        target_or_pos: Any = None,
        ra_value: str = "Ra 3.2",
        material_removal: MaterialRemoval = MaterialRemoval.REQUIRED,
        process: str | None = None,
        scale: float = 1.0,
        position: tuple[float, float] | None = None,
        target: Any = None,
        view: Any | None = None,
    ):
        self.ra_value = ra_value
        self.material_removal = material_removal
        self.process = process
        self.scale = scale
        self.view = view
        self.resolved = False

        effective_target = target if target is not None else target_or_pos

        if position is not None:
            self.position = (float(position[0]), float(position[1]))
            self.target = None
            self.resolved = True
        elif (
            effective_target is not None
            and not is_topological_entity(effective_target)
            and isinstance(effective_target, (tuple, list))
        ):
            self.position = (float(effective_target[0]), float(effective_target[1]))
            self.target = None
            self.resolved = True
        else:
            self.position = (0.0, 0.0)
            self.target = effective_target

        target_view = view or CURRENT_VIEW.get()
        if target_view is not None:
            self._attach_to_view(target_view)
        else:
            target_sheet = CURRENT_SHEET.get()
            if target_sheet is not None and self not in target_sheet.annotations:
                target_sheet.annotations.append(self)

    def _attach_to_view(self, view: Any) -> None:
        self.view = view
        if not self.resolved and self.target is not None:
            self.position = resolve_point_to_sheet(self.target, view)
            self.resolved = True
        elif not self.resolved and self.target is None:
            self.position = (view.center_x, view.min_y)
            self.resolved = True

        if hasattr(view, "annotations") and self not in view.annotations:
            view.annotations.append(self)
        t_sheet = getattr(view, "sheet", None) or CURRENT_SHEET.get()
        if t_sheet is not None and self not in t_sheet.annotations:
            t_sheet.annotations.append(self)

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

        # Optional process description above Ra
        if self.process:
            elements.append(
                f'<text x="{text_x:.2f}" y="{text_y - 3.2 * s:.2f}" class="tb-field-val" font-size="{2.2 * s:.2f}">{html.escape(self.process)}</text>'
            )

        return f'<g class="surface-finish-symbol">{"".join(elements)}</g>'


# Familiar build123d-style alias
SurfaceFinish = SurfaceFinishSymbol
