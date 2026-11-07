"""First-class RawSVG and Table / Schedule components for technical drawings."""

from __future__ import annotations

import html
import re
from typing import Any

from build123d_techdraw.context import CURRENT_SHEET, CURRENT_VIEW


class RawSVG:
    """First-class annotation element for embedding arbitrary SVG vector content."""

    def __init__(
        self,
        content: str,
        position: tuple[float, float] = (0.0, 0.0),
        scale: float = 1.0,
        rotation: float = 0.0,
        view: Any | None = None,
        sheet: Any | None = None,
    ):
        self.content = content
        self.position = (float(position[0]), float(position[1]))
        self.scale = float(scale)
        self.rotation = float(rotation)
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

    def to_svg(self) -> str:
        cleaned = re.sub(r"<\?xml[^>]*\?>", "", self.content).strip()
        x, y = self.position
        transforms: list[str] = []
        if x != 0.0 or y != 0.0:
            transforms.append(f"translate({x:.2f}, {y:.2f})")
        if self.scale != 1.0:
            transforms.append(f"scale({self.scale:.4f})")
        if self.rotation != 0.0:
            transforms.append(f"rotate({self.rotation:.2f})")
        transform_attr = f' transform="{" ".join(transforms)}"' if transforms else ""
        return f'<g class="raw-svg"{transform_attr}>{cleaned}</g>'


class Table:
    """First-class engineering schedule / data table formatted according to the active Theme."""

    def __init__(
        self,
        headers: list[str] | None = None,
        rows: list[list[Any]] | None = None,
        position: tuple[float, float] = (0.0, 0.0),
        col_widths: list[float] | None = None,
        title: str | None = None,
        alignments: list[str] | str | None = None,
        align: list[str] | str | None = None,
        row_height: float = 5.2,
        header_height: float = 5.5,
        title_height: float = 6.0,
        font_size: float = 2.6,
        header_font_size: float = 2.1,
        title_font_size: float = 2.8,
        cell_padding: float = 2.5,
        margin: float | None = None,
        view: Any | None = None,
        sheet: Any | None = None,
    ):
        self.headers: list[str] = [str(h) for h in headers] if headers else []
        self.rows: list[list[str]] = [[str(c) for c in r] for r in rows] if rows else []
        self.position = (float(position[0]), float(position[1]))
        self.title = title
        self.row_height = float(row_height)
        self.header_height = float(header_height)
        self.title_height = float(title_height)
        self.font_size = float(font_size)
        self.header_font_size = float(header_font_size)
        self.title_font_size = float(title_font_size)
        self.cell_padding = float(margin if margin is not None else cell_padding)
        self.view = view
        self.sheet = sheet

        num_cols = max(
            len(self.headers),
            max((len(r) for r in self.rows), default=0),
        )
        self.num_cols = num_cols

        if col_widths is not None:
            self.col_widths = [float(w) for w in col_widths]
            if len(self.col_widths) < num_cols:
                last_w = self.col_widths[-1] if self.col_widths else 25.0
                self.col_widths.extend([last_w] * (num_cols - len(self.col_widths)))
        else:
            computed: list[float] = []
            for c_idx in range(num_cols):
                max_chars = len(self.headers[c_idx]) if c_idx < len(self.headers) else 0
                for r in self.rows:
                    if c_idx < len(r):
                        max_chars = max(max_chars, len(r[c_idx]))
                col_w = max(12.0, max_chars * (self.font_size * 0.68) + 2.0 * self.cell_padding)
                computed.append(col_w)
            self.col_widths = computed

        raw_align = alignments if alignments is not None else align
        if isinstance(raw_align, str):
            self.alignments = [raw_align.lower()] * num_cols
        elif isinstance(raw_align, list):
            self.alignments = [str(a).lower() for a in raw_align]
            if len(self.alignments) < num_cols:
                self.alignments.extend(["left"] * (num_cols - len(self.alignments)))
        else:
            self.alignments = ["left"] * num_cols

        target_view = view or CURRENT_VIEW.get()
        if target_view is not None:
            self._attach_to_view(target_view)
        else:
            target_sheet = sheet or CURRENT_SHEET.get()
            if target_sheet is not None and self not in target_sheet.annotations:
                target_sheet.annotations.append(self)
                self.sheet = target_sheet

    @property
    def width(self) -> float:
        return sum(self.col_widths) if self.col_widths else 0.0

    @property
    def height(self) -> float:
        h = 0.0
        if self.title:
            h += self.title_height
        if self.headers:
            h += self.header_height
        h += len(self.rows) * self.row_height
        return h

    def _attach_to_view(self, view: Any) -> None:
        self.view = view
        if hasattr(view, "annotations") and self not in view.annotations:
            view.annotations.append(self)
        t_sheet = getattr(view, "sheet", None) or CURRENT_SHEET.get()
        if t_sheet is not None and self not in t_sheet.annotations:
            t_sheet.annotations.append(self)
            self.sheet = t_sheet

    def _cell_x_and_anchor(self, col_x: float, col_w: float, align_str: str) -> tuple[float, str]:
        a = align_str.strip().lower()
        if a in ("center", "c", "middle"):
            return col_x + col_w / 2.0, "middle"
        if a in ("right", "r", "end"):
            return col_x + col_w - self.cell_padding, "end"
        return col_x + self.cell_padding, "start"

    def to_svg(self) -> str:
        total_w = self.width
        total_h = self.height
        if total_w <= 0.0 or total_h <= 0.0:
            return ""

        px, py = self.position
        elements: list[str] = [
            f'<rect x="0" y="0" width="{total_w:.2f}" height="{total_h:.2f}" class="title-bg" stroke-width="0.5"/>'
        ]

        curr_y = 0.0

        if self.title:
            th = self.title_height
            elements.append(
                f'<rect x="0" y="0" width="{total_w:.2f}" height="{th:.2f}" class="table-header-bg"/>'
            )
            elements.append(
                f'<line x1="0" y1="{th:.2f}" x2="{total_w:.2f}" y2="{th:.2f}" class="border-sub" stroke-width="0.4"/>'
            )
            elements.append(
                f'<text x="{total_w / 2.0:.2f}" y="{th * 0.70:.2f}" class="tb-title" text-anchor="middle" font-size="{self.title_font_size:.2f}">{html.escape(self.title)}</text>'
            )
            curr_y += th

        col_starts: list[float] = []
        acc_x = 0.0
        for w in self.col_widths:
            col_starts.append(acc_x)
            acc_x += w

        grid_top_y = curr_y

        if self.headers:
            hh = self.header_height
            elements.append(
                f'<rect x="0" y="{curr_y:.2f}" width="{total_w:.2f}" height="{hh:.2f}" class="table-header-bg"/>'
            )
            elements.append(
                f'<line x1="0" y1="{curr_y + hh:.2f}" x2="{total_w:.2f}" y2="{curr_y + hh:.2f}" class="border-sub" stroke-width="0.4"/>'
            )
            text_y = curr_y + hh * 0.72
            for c_idx, h_text in enumerate(self.headers):
                if c_idx >= len(self.col_widths):
                    break
                cx, anchor = self._cell_x_and_anchor(
                    col_starts[c_idx], self.col_widths[c_idx], self.alignments[c_idx]
                )
                elements.append(
                    f'<text x="{cx:.2f}" y="{text_y:.2f}" class="tb-field-label" text-anchor="{anchor}" font-size="{self.header_font_size:.2f}">{html.escape(h_text)}</text>'
                )
            curr_y += hh

        rh = self.row_height
        for r_idx, row in enumerate(self.rows):
            ry = curr_y + r_idx * rh
            if r_idx % 2 == 1:
                elements.append(
                    f'<rect x="0" y="{ry:.2f}" width="{total_w:.2f}" height="{rh:.2f}" class="table-row-alt"/>'
                )
            if r_idx < len(self.rows) - 1:
                elements.append(
                    f'<line x1="0" y1="{ry + rh:.2f}" x2="{total_w:.2f}" y2="{ry + rh:.2f}" class="border-sub" stroke-width="0.2"/>'
                )
            text_y = ry + rh * 0.72
            for c_idx, cell_text in enumerate(row):
                if c_idx >= len(self.col_widths):
                    break
                cx, anchor = self._cell_x_and_anchor(
                    col_starts[c_idx], self.col_widths[c_idx], self.alignments[c_idx]
                )
                elements.append(
                    f'<text x="{cx:.2f}" y="{text_y:.2f}" class="tb-field-val-sm" text-anchor="{anchor}" font-size="{self.font_size:.2f}">{html.escape(cell_text)}</text>'
                )

        for cx in col_starts[1:]:
            elements.append(
                f'<line x1="{cx:.2f}" y1="{grid_top_y:.2f}" x2="{cx:.2f}" y2="{total_h:.2f}" class="border-sub" stroke-width="0.25"/>'
            )

        return f'<g class="schedule-table" transform="translate({px:.2f}, {py:.2f})">{"".join(elements)}</g>'


# Standard engineering alias
Schedule = Table
