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
        size: tuple[float, float] | None = None,
        bbox: tuple[float, float, float, float] | None = None,
        view: Any | None = None,
        sheet: Any | None = None,
    ):
        self.content = content
        self.position = (float(position[0]), float(position[1]))
        self.scale = float(scale)
        self.rotation = float(rotation)
        self.size = (float(size[0]), float(size[1])) if size is not None else None
        self.bbox = (
            (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
            if bbox is not None
            else None
        )
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

    def bounding_box_2d(self) -> tuple[float, float, float, float] | None:
        """Return 2D paper-coordinate bounding box (min_x, min_y, max_x, max_y) for linting."""
        if self.bbox is not None:
            return self.bbox

        px, py = self.position
        if self.size is not None:
            w, h = self.size
            return (px, py, px + w * self.scale, py + h * self.scale)

        # Infer bounds from <rect> elements or <svg viewBox/width/height>
        rect_pattern = re.compile(
            r"<rect\b([^>]*)/?>",
            re.IGNORECASE,
        )

        def _attr_num(attrs: str, name: str) -> float | None:
            match = re.search(rf'\b{name}\s*=\s*["\']\s*([-+]?\d*\.?\d+)', attrs, re.IGNORECASE)
            return float(match.group(1)) if match else None

        min_x = float("inf")
        min_y = float("inf")
        max_x = float("-inf")
        max_y = float("-inf")
        found = False
        for m in rect_pattern.finditer(self.content):
            attrs = m.group(1)
            rw = _attr_num(attrs, "width")
            rh = _attr_num(attrs, "height")
            if rw is not None and rh is not None and rw > 0 and rh > 0:
                rx = _attr_num(attrs, "x") or 0.0
                ry = _attr_num(attrs, "y") or 0.0
                min_x = min(min_x, rx)
                min_y = min(min_y, ry)
                max_x = max(max_x, rx + rw)
                max_y = max(max_y, ry + rh)
                found = True

        if found:
            return (
                px + min_x * self.scale,
                py + min_y * self.scale,
                px + max_x * self.scale,
                py + max_y * self.scale,
            )

        vb_match = re.search(
            r'\bviewBox\s*=\s*["\']\s*([-+]?\d*\.?\d+)[\s,]+([-+]?\d*\.?\d+)[\s,]+([-+]?\d*\.?\d+)[\s,]+([-+]?\d*\.?\d+)',
            self.content,
            re.IGNORECASE,
        )
        if vb_match:
            vx, vy, vw, vh = (float(vb_match.group(i)) for i in range(1, 5))
            if vw > 0 and vh > 0:
                return (
                    px + vx * self.scale,
                    py + vy * self.scale,
                    px + (vx + vw) * self.scale,
                    py + (vy + vh) * self.scale,
                )

        return None

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


def wrap_text_to_width(text: str, max_width_mm: float, font_size: float = 2.6) -> list[str]:
    """Wrap a string into lines that fit within max_width_mm in paper coordinates."""
    if not text:
        return [""]
    char_w = max(0.5, font_size * 0.58)
    max_chars = max(4, int(max_width_mm / char_w))

    words = str(text).split(" ")
    lines: list[str] = []
    current_line = ""

    for w in words:
        if not current_line:
            if len(w) > max_chars:
                for k in range(0, len(w), max_chars):
                    lines.append(w[k : k + max_chars])
            else:
                current_line = w
        elif len(current_line) + 1 + len(w) <= max_chars:
            current_line += " " + w
        else:
            lines.append(current_line)
            if len(w) > max_chars:
                for k in range(0, len(w), max_chars):
                    lines.append(w[k : k + max_chars])
                current_line = ""
            else:
                current_line = w

    if current_line:
        lines.append(current_line)

    return lines or [str(text)]


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
        column_alignments: list[str] | str | None = None,
        align: list[str] | str | None = None,
        row_height: float = 5.2,
        header_height: float = 5.5,
        title_height: float = 6.0,
        font_size: float = 2.6,
        header_font_size: float = 2.1,
        title_font_size: float = 2.8,
        cell_padding: float = 2.5,
        margin: float | None = None,
        zebra: bool = True,
        row_alt_opacity: float | None = None,
        col_styles: list[dict[str, Any] | None] | None = None,
        cell_styles: dict[tuple[int, int], dict[str, Any]] | None = None,
        word_wrap: bool = True,
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
        self.zebra = zebra
        self.row_alt_opacity = float(row_alt_opacity) if row_alt_opacity is not None else None
        self.col_styles: list[dict[str, Any] | None] = list(col_styles) if col_styles else []
        self.cell_styles: dict[tuple[int, int], dict[str, Any]] = (
            dict(cell_styles) if cell_styles else {}
        )
        self.word_wrap = word_wrap
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

        raw_align = (
            column_alignments
            if column_alignments is not None
            else (alignments if alignments is not None else align)
        )
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

    def _compute_row_layout(self) -> tuple[list[list[list[str]]], list[float]]:
        """Calculate wrapped cell lines and dynamic height for each row."""
        row_cell_lines: list[list[list[str]]] = []
        row_heights: list[float] = []

        for r in self.rows:
            cell_lines_in_row = []
            max_lines = 1
            for c_idx, cell_text in enumerate(r):
                c_width = self.col_widths[c_idx] if c_idx < len(self.col_widths) else 25.0
                if self.word_wrap:
                    avail_w = max(4.0, c_width - 2.0 * self.cell_padding)
                    wrapped = wrap_text_to_width(cell_text, avail_w, self.font_size)
                else:
                    wrapped = [cell_text]
                cell_lines_in_row.append(wrapped)
                max_lines = max(max_lines, len(wrapped))
            row_cell_lines.append(cell_lines_in_row)
            rh = max(self.row_height, max_lines * (self.font_size * 1.35) + 1.8)
            row_heights.append(rh)

        return row_cell_lines, row_heights

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
        _, row_heights = self._compute_row_layout()
        h += sum(row_heights)
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
        row_cell_lines, row_heights = self._compute_row_layout()
        total_h = (
            (self.title_height if self.title else 0.0)
            + (self.header_height if self.headers else 0.0)
            + sum(row_heights)
        )
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

        for r_idx, (row, rh) in enumerate(zip(row_cell_lines, row_heights, strict=False)):
            ry = curr_y
            if self.zebra and r_idx % 2 == 1:
                opacity_attr = (
                    f' fill-opacity="{self.row_alt_opacity:.2f}"'
                    if self.row_alt_opacity is not None
                    else ""
                )
                elements.append(
                    f'<rect x="0" y="{ry:.2f}" width="{total_w:.2f}" height="{rh:.2f}" class="table-row-alt"{opacity_attr}/>'
                )
            if r_idx < len(row_cell_lines) - 1:
                elements.append(
                    f'<line x1="0" y1="{ry + rh:.2f}" x2="{total_w:.2f}" y2="{ry + rh:.2f}" class="border-sub" stroke-width="0.2"/>'
                )
            for c_idx, lines in enumerate(row):
                if c_idx >= len(self.col_widths):
                    break
                style: dict[str, Any] = {}
                if c_idx < len(self.col_styles) and self.col_styles[c_idx]:
                    style.update(self.col_styles[c_idx] or {})
                if (r_idx, c_idx) in self.cell_styles:
                    style.update(self.cell_styles[(r_idx, c_idx)])
                if "bg" in style and style["bg"]:
                    elements.append(
                        f'<rect x="{col_starts[c_idx]:.2f}" y="{ry:.2f}" width="{self.col_widths[c_idx]:.2f}" height="{rh:.2f}" fill="{html.escape(str(style["bg"]))}"/>'
                    )
                css_cls = str(style.get("css_class") or "tb-field-val-sm")
                fill_attr = (
                    f' fill="{html.escape(str(style["color"]))}"' if style.get("color") else ""
                )
                weight_attr = (
                    f' font-weight="{html.escape(str(style["font_weight"]))}"'
                    if style.get("font_weight")
                    else ""
                )
                cx, anchor = self._cell_x_and_anchor(
                    col_starts[c_idx], self.col_widths[c_idx], self.alignments[c_idx]
                )
                for l_idx, line in enumerate(lines):
                    line_y = ry + self.font_size * 1.1 + l_idx * (self.font_size * 1.35)
                    elements.append(
                        f'<text x="{cx:.2f}" y="{line_y:.2f}" class="{html.escape(css_cls)}"{fill_attr}{weight_attr} text-anchor="{anchor}" font-size="{self.font_size:.2f}">{html.escape(line)}</text>'
                    )
            curr_y += rh

        for cx in col_starts[1:]:
            elements.append(
                f'<line x1="{cx:.2f}" y1="{grid_top_y:.2f}" x2="{cx:.2f}" y2="{total_h:.2f}" class="border-sub" stroke-width="0.25"/>'
            )

        return f'<g class="schedule-table" transform="translate({px:.2f}, {py:.2f})">{"".join(elements)}</g>'


class Card:
    """First-class specification card / SOP callout box with optional header, background, and bullets."""

    def __init__(
        self,
        title: str | None = None,
        body: str | list[str] | None = None,
        position: tuple[float, float] = (0.0, 0.0),
        width: float = 65.0,
        height: float | None = None,
        font_size: float = 2.5,
        title_font_size: float = 3.0,
        padding: float = 3.0,
        border: bool = True,
        background: bool = True,
        bullet: str = "• ",
        view: Any | None = None,
        sheet: Any | None = None,
    ):
        self.title = title
        if body is None:
            self.lines: list[str] = []
        elif isinstance(body, str):
            self.lines = [body]
        else:
            self.lines = list(body)

        self.position = (float(position[0]), float(position[1]))
        self.width_val = float(width)
        self.height_override = float(height) if height is not None else None
        self.font_size = float(font_size)
        self.title_font_size = float(title_font_size)
        self.padding = float(padding)
        self.border = border
        self.background = background
        self.bullet = bullet
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

    @property
    def width(self) -> float:
        return self.width_val

    @property
    def height(self) -> float:
        if self.height_override is not None:
            return self.height_override
        h = self.padding * 2.0
        if self.title:
            h += self.title_font_size * 1.5 + 2.0
        avail_w = max(
            10.0,
            self.width_val
            - 2.0 * self.padding
            - (len(self.bullet) * self.font_size * 0.6 if len(self.lines) > 1 else 0.0),
        )
        total_wrapped_lines = 0
        for line_str in self.lines:
            wrapped = wrap_text_to_width(line_str, avail_w, self.font_size)
            total_wrapped_lines += len(wrapped)
        h += total_wrapped_lines * (self.font_size * 1.35)
        return max(15.0, h)

    def bounding_box_2d(self) -> tuple[float, float, float, float]:
        px, py = self.position
        return (px, py, px + self.width, py + self.height)

    def to_svg(self) -> str:
        px, py = self.position
        w = self.width
        h = self.height
        elements: list[str] = []

        if self.background:
            elements.append(
                f'<rect x="0" y="0" width="{w:.2f}" height="{h:.2f}" class="title-bg" stroke-width="{"0.4" if self.border else "0"}"/>'
            )
        elif self.border:
            elements.append(
                f'<rect x="0" y="0" width="{w:.2f}" height="{h:.2f}" fill="none" class="border-sub" stroke-width="0.4"/>'
            )

        curr_y = self.padding
        if self.title:
            th = self.title_font_size * 1.5
            elements.append(
                f'<rect x="0" y="0" width="{w:.2f}" height="{th + self.padding:.2f}" class="table-header-bg"/>'
            )
            elements.append(
                f'<line x1="0" y1="{th + self.padding:.2f}" x2="{w:.2f}" y2="{th + self.padding:.2f}" class="border-sub" stroke-width="0.35"/>'
            )
            elements.append(
                f'<text x="{self.padding:.2f}" y="{self.padding + self.title_font_size * 0.85:.2f}" class="tb-title" font-size="{self.title_font_size:.2f}">{html.escape(self.title)}</text>'
            )
            curr_y += th + 3.0

        avail_w = max(
            10.0,
            w
            - 2.0 * self.padding
            - (len(self.bullet) * self.font_size * 0.6 if len(self.lines) > 1 else 0.0),
        )
        for raw_line in self.lines:
            prefix = self.bullet if len(self.lines) > 1 else ""
            wrapped = wrap_text_to_width(raw_line, avail_w, self.font_size)
            for w_idx, wl in enumerate(wrapped):
                bullet_str = prefix if w_idx == 0 else " " * len(prefix)
                text_y = curr_y + self.font_size * 0.9
                elements.append(
                    f'<text x="{self.padding:.2f}" y="{text_y:.2f}" class="tb-field-val-sm" font-size="{self.font_size:.2f}">{html.escape(bullet_str + wl)}</text>'
                )
                curr_y += self.font_size * 1.35
            curr_y += 1.0

        return f'<g class="specification-card" transform="translate({px:.2f}, {py:.2f})">{"".join(elements)}</g>'


TextBox = Card
Schedule = Table
