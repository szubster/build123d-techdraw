"""ISO 1101 and ASME Y14.5 Geometric Dimensioning and Tolerancing (GD&T) annotations."""

import html
from dataclasses import dataclass
from enum import Enum


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


@dataclass
class FeatureControlFrame:
    """ISO 1101 Feature Control Frame ([ Symbol | Tolerance | Datum A | Datum B | Datum C ])."""

    position: tuple[float, float]
    characteristic: GDTCharacteristic
    tolerance_value: str
    datum_a: str | None = None
    datum_b: str | None = None
    datum_c: str | None = None
    frame_height: float = 6.0
    symbol_font_size: float = 3.6
    text_font_size: float = 3.0

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

        elements: list[str] = [
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


@dataclass
class DatumFeatureSymbol:
    """ISO 1101 Datum Feature Identifier tag (e.g. —▲— [A])."""

    target: tuple[float, float]
    label: str = "A"
    stem_length: float = 7.0
    box_size: float = 5.5
    direction: str = "up"  # "up", "down", "left", "right"

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
