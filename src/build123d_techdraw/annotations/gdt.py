"""ISO 1101 and ASME Y14.5 Geometric Dimensioning and Tolerancing (GD&T) annotations."""

from __future__ import annotations

import html
import math
from enum import Enum
from typing import Any

from build123d_techdraw.annotations.dimensions import render_arrowhead
from build123d_techdraw.annotations.resolver import is_topological_entity, resolve_point_to_sheet
from build123d_techdraw.context import CURRENT_SHEET, CURRENT_VIEW


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


class FeatureControlFrame:
    """ISO 1101 Feature Control Frame ([ Symbol | Tolerance | Datum A | Datum B | Datum C ])."""

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
        view: Any | None = None,
    ):
        self.characteristic = characteristic
        self.tolerance_value = tolerance_value
        self.frame_height = frame_height
        self.symbol_font_size = symbol_font_size
        self.text_font_size = text_font_size
        self.leader_offset = leader_offset
        self.view = view
        self.target = target if target is not None else leader_target
        self.resolved = False

        if datum_refs:
            self.datum_a = datum_refs[0] if len(datum_refs) > 0 else None
            self.datum_b = datum_refs[1] if len(datum_refs) > 1 else None
            self.datum_c = datum_refs[2] if len(datum_refs) > 2 else None
        else:
            self.datum_a = datum_a
            self.datum_b = datum_b
            self.datum_c = datum_c

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
        else:
            self.leader_target = None

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
            target_pt = resolve_point_to_sheet(self.target, view)
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

        if hasattr(view, "annotations") and self not in view.annotations:
            view.annotations.append(self)
        t_sheet = getattr(view, "sheet", None) or CURRENT_SHEET.get()
        if t_sheet is not None and self not in t_sheet.annotations:
            t_sheet.annotations.append(self)

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


class DatumFeatureSymbol:
    """ISO 1101 Datum Feature Identifier tag (e.g. —▲— [A])."""

    def __init__(
        self,
        label: str = "A",
        target: Any = None,
        stem_length: float = 7.0,
        box_size: float = 5.5,
        direction: str = "up",
        view: Any | None = None,
    ):
        self.label = label
        self.stem_length = stem_length
        self.box_size = box_size
        self.direction = direction
        self.view = view
        self.raw_target = target
        self.target: tuple[float, float] = (0.0, 0.0)
        self.resolved = False

        if (
            target is not None
            and not is_topological_entity(target)
            and isinstance(target, (tuple, list))
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
        if not self.resolved and self.raw_target is not None:
            self.target = resolve_point_to_sheet(self.raw_target, view)
            self.resolved = True
        elif not self.resolved and self.raw_target is None:
            self.target = (view.center_x, view.min_y)
            self.resolved = True

        if hasattr(view, "annotations") and self not in view.annotations:
            view.annotations.append(self)
        t_sheet = getattr(view, "sheet", None) or CURRENT_SHEET.get()
        if t_sheet is not None and self not in t_sheet.annotations:
            t_sheet.annotations.append(self)

    def to_svg(self) -> str:
        tx, ty = self.target
        tri_size = 2.4

        if self.direction == "up":
            tri_pts = f"{tx},{ty} {tx - tri_size},{ty - tri_size * 1.5} {tx + tri_size},{ty - tri_size * 1.5}"
            stem_end_x = tx
            stem_end_y = ty - self.stem_length
            box_x = stem_end_x - self.box_size / 2.0
            box_y = stem_end_y - self.box_size
        elif self.direction == "down":
            tri_pts = f"{tx},{ty} {tx - tri_size},{ty + tri_size * 1.5} {tx + tri_size},{ty + tri_size * 1.5}"
            stem_end_x = tx
            stem_end_y = ty + self.stem_length
            box_x = stem_end_x - self.box_size / 2.0
            box_y = stem_end_y
        elif self.direction == "left":
            tri_pts = f"{tx},{ty} {tx - tri_size * 1.5},{ty - tri_size} {tx - tri_size * 1.5},{ty + tri_size}"
            stem_end_x = tx - self.stem_length
            stem_end_y = ty
            box_x = stem_end_x - self.box_size
            box_y = stem_end_y - self.box_size / 2.0
        else:
            tri_pts = f"{tx},{ty} {tx + tri_size * 1.5},{ty - tri_size} {tx + tri_size * 1.5},{ty + tri_size}"
            stem_end_x = tx + self.stem_length
            stem_end_y = ty
            box_x = stem_end_x
            box_y = stem_end_y - self.box_size / 2.0

        elements = [
            f'<polygon points="{tri_pts}" class="dim-arrow"/>',
            f'<line x1="{tx:.2f}" y1="{ty:.2f}" x2="{stem_end_x:.2f}" y2="{stem_end_y:.2f}" class="dim-line" stroke-width="0.35"/>',
            f'<rect x="{box_x:.2f}" y="{box_y:.2f}" width="{self.box_size:.2f}" height="{self.box_size:.2f}" class="title-bg" stroke-width="0.4"/>',
            f'<text x="{box_x + self.box_size / 2.0:.2f}" y="{box_y + self.box_size * 0.72:.2f}" class="tb-field-val-bold" text-anchor="middle" font-size="3.4">{html.escape(self.label)}</text>',
        ]

        return f'<g class="datum-symbol">{"".join(elements)}</g>'


# Familiar build123d-style alias
DatumFeature = DatumFeatureSymbol
