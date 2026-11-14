"""High-precision multi-page vector PDF export using matplotlib and svgelements."""

import io
import math
import os
import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from typing import Any

# Set MPLCONFIGDIR before importing matplotlib so it does not probe ~/.config/matplotlib.
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib  # noqa: E402

matplotlib.use("Agg")

import matplotlib.patches as patches  # noqa: E402
import matplotlib.path as mpath  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import svgelements as se  # noqa: E402
from matplotlib.backends.backend_pdf import Name, PdfPages  # noqa: E402

from build123d_techdraw.renderers.theme import THEME_COLORS, Theme, ThemeColors  # noqa: E402

MM_TO_PT: float = 72.0 / 25.4
# Contours (ISO 128 Type A): 0.50mm -> 1.42pt
# Dimensions/Leaders (ISO 128 Type B): 0.25mm -> 0.71pt
# Borders: 0.70mm -> 1.98pt


class BookmarkedPdfPages(PdfPages):
    """PdfPages subclass supporting native PDF document outline bookmarks."""

    def __init__(
        self,
        filename: str,
        booklet_title: str = "ENGINEERING DRAWINGS",
        sheet_titles: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        super().__init__(filename, metadata=metadata)
        self.booklet_title = booklet_title
        self.sheet_titles = sheet_titles or []

    def close(self) -> None:
        if self._file is not None and self.sheet_titles:
            pdf = self._file
            n = len(pdf.pageList)
            if n > 0:
                outlines_obj = pdf.reserveObject("outlines")
                top_obj = pdf.reserveObject("outline_top")
                child_objs = [pdf.reserveObject(f"outline_child_{i}") for i in range(n)]

                for i in range(n):
                    title = self.sheet_titles[i] if i < len(self.sheet_titles) else f"Sheet {i + 1}"
                    child_dict: dict[str, Any] = {
                        "Title": title,
                        "Parent": top_obj,
                        "Dest": [pdf.pageList[i], Name("Fit")],  # type: ignore[no-untyped-call]
                    }
                    if i > 0:
                        child_dict["Prev"] = child_objs[i - 1]
                    if i < n - 1:
                        child_dict["Next"] = child_objs[i + 1]
                    pdf.writeObject(child_objs[i], child_dict)

                top_dict: dict[str, Any] = {
                    "Title": self.booklet_title,
                    "Parent": outlines_obj,
                    "Count": n,
                    "Dest": [pdf.pageList[0], Name("Fit")],  # type: ignore[no-untyped-call]
                }
                if child_objs:
                    top_dict["First"] = child_objs[0]
                    top_dict["Last"] = child_objs[-1]
                pdf.writeObject(top_obj, top_dict)

                outlines_dict: dict[str, Any] = {
                    "Type": Name("Outlines"),  # type: ignore[no-untyped-call]
                    "First": top_obj,
                    "Last": top_obj,
                    "Count": n + 1,
                }
                pdf.writeObject(outlines_obj, outlines_dict)

                catalog_dict: dict[str, Any] = {
                    "Type": Name("Catalog"),  # type: ignore[no-untyped-call]
                    "Pages": pdf.pagesObject,
                    "Outlines": outlines_obj,
                }
                pdf.writeObject(pdf.rootObject, catalog_dict)

        super().close()  # type: ignore[no-untyped-call]


class PDFRenderer:
    """Renders vector technical drawings to multi-page publication-ready PDF documents."""

    @staticmethod
    def _flatten_nested_svgs(svg_code: str) -> str:
        """Convert nested <svg> elements into <g transform=...> so svgelements preserves offsets."""
        if svg_code.lower().count("<svg") <= 1:
            return svg_code
        try:
            ET.register_namespace("", "http://www.w3.org/2000/svg")
            root = ET.fromstring(svg_code)
        except ET.ParseError:
            return svg_code

        def _local_name(tag: str) -> str:
            return tag.split("}", 1)[1] if "}" in tag else tag

        def _ns_prefix(tag: str) -> str:
            return f"{tag.split('}', 1)[0]}}}" if "}" in tag else ""

        def _walk(parent: ET.Element) -> None:
            for child in list(parent):
                if _local_name(child.tag).lower() == "svg":
                    x = float(child.attrib.pop("x", "0") or 0)
                    y = float(child.attrib.pop("y", "0") or 0)
                    w_raw = child.attrib.pop("width", None)
                    h_raw = child.attrib.pop("height", None)
                    vb_raw = child.attrib.pop("viewBox", None)
                    transforms: list[str] = []
                    if x != 0.0 or y != 0.0:
                        transforms.append(f"translate({x:g}, {y:g})")
                    if vb_raw and w_raw and h_raw:
                        parts = [float(p) for p in re.split(r"[\s,]+", vb_raw.strip()) if p]
                        w_val = PDFRenderer._parse_svg_length_mm(w_raw)
                        h_val = PDFRenderer._parse_svg_length_mm(h_raw)
                        if len(parts) == 4 and w_val and h_val and parts[2] > 0 and parts[3] > 0:
                            sx = w_val / parts[2]
                            sy = h_val / parts[3]
                            transforms.append(f"scale({sx:g}, {sy:g})")
                            if parts[0] != 0.0 or parts[1] != 0.0:
                                transforms.append(f"translate({-parts[0]:g}, {-parts[1]:g})")
                    existing = child.attrib.get("transform", "").strip()
                    if existing:
                        transforms.append(existing)
                    if transforms:
                        child.attrib["transform"] = " ".join(transforms)
                    child.tag = f"{_ns_prefix(child.tag)}g"
                _walk(child)

        _walk(root)
        return ET.tostring(root, encoding="unicode")

    @classmethod
    def _render_svg_to_ax(
        cls,
        ax: Any,
        svg_code: str,
        width_mm: float,
        height_mm: float,
        colors: ThemeColors,
    ) -> None:
        ax.set_xlim(0, width_mm)
        ax.set_ylim(0, height_mm)
        ax.axis("off")

        # Explicit background canvas
        ax.add_patch(
            patches.Rectangle((0, 0), width_mm, height_mm, facecolor=colors.bg, edgecolor="none")
        )

        normalized_svg = cls._flatten_nested_svgs(svg_code)
        doc = se.SVG.parse(
            io.BytesIO(normalized_svg.encode("utf-8")),
            ppi=25.4,
            width=width_mm,
            height=height_mm,
        )
        for el in doc.elements():
            cls_str = el.values.get("class", "")
            cls_list = cls_str.split() if cls_str else []

            # Background and grid pattern rects already handled by canvas
            if "sheet-bg" in cls_list or "blueprint-grid" in cls_list:
                continue

            if isinstance(el, se.Shape):
                p = se.Path(el)
                p.reify()
                p.approximate_arcs_with_cubics()
                verts: list[tuple[float, float]] = []
                codes: list[Any] = []
                for seg in p:
                    if isinstance(seg, se.Move):
                        verts.append((seg.end.x, height_mm - seg.end.y))
                        codes.append(mpath.Path.MOVETO)
                    elif isinstance(seg, se.Line):
                        verts.append((seg.end.x, height_mm - seg.end.y))
                        codes.append(mpath.Path.LINETO)
                    elif isinstance(seg, se.Close):
                        verts.append((0, 0))
                        codes.append(mpath.Path.CLOSEPOLY)
                    elif isinstance(seg, se.CubicBezier):
                        verts.extend(
                            [
                                (seg.control1.x, height_mm - seg.control1.y),
                                (seg.control2.x, height_mm - seg.control2.y),
                                (seg.end.x, height_mm - seg.end.y),
                            ]
                        )
                        codes.extend([mpath.Path.CURVE4, mpath.Path.CURVE4, mpath.Path.CURVE4])
                    elif isinstance(seg, se.QuadraticBezier):
                        verts.extend(
                            [
                                (seg.control.x, height_mm - seg.control.y),
                                (seg.end.x, height_mm - seg.end.y),
                            ]
                        )
                        codes.extend([mpath.Path.CURVE3, mpath.Path.CURVE3])

                if verts:
                    mpl_path = mpath.Path(verts, codes)
                    stroke: str | None = None
                    fill: str | None = None
                    lw: float = 0.35 * MM_TO_PT
                    ls: Any = "-"
                    hatch: str | None = None

                    raw_stroke = str(el.values.get("stroke", ""))

                    # Check if hidden line
                    is_hidden = (
                        "line-hidden" in cls_list
                        or "layer_hidden" in cls_list
                        or "line-hidden" in raw_stroke
                    )
                    if not is_hidden:
                        parent = getattr(el, "parent", None)
                        while parent:
                            p_id = getattr(parent, "id", "") or parent.values.get("id", "")
                            p_cls = getattr(parent, "class", "") or parent.values.get("class", "")
                            if "hidden" in p_id or "hidden" in p_cls:
                                is_hidden = True
                                break
                            parent = getattr(parent, "parent", None)

                    # Check for section hatch
                    fill_str = str(getattr(el, "fill", "")) + " " + str(el.values.get("fill", ""))
                    is_hatch = (
                        "section-hatch-fill" in cls_list
                        or "sectionHatch" in fill_str
                        or "hatch_" in fill_str
                    )

                    if is_hidden:
                        stroke = colors.line_hidden
                        lw = 0.20 * MM_TO_PT
                        ls = (0, (3, 2))
                    elif is_hatch:
                        fill = colors.table_header_bg
                        stroke = colors.line_visible
                        lw = 0.35 * MM_TO_PT
                        fill_lower = fill_str.lower()
                        if "aluminum" in fill_lower or "ansi38" in fill_lower:
                            hatch = "//"
                        elif "brass" in fill_lower or "ansi32" in fill_lower:
                            hatch = "/."
                        elif "plastic" in fill_lower:
                            hatch = "xx"
                        elif "rubber" in fill_lower:
                            hatch = ".."
                        else:
                            hatch = "/"
                    elif (
                        "stroke-visible" in cls_list
                        or "line-visible" in cls_list
                        or "line-visible" in raw_stroke
                    ):
                        stroke = colors.line_visible
                        lw = float(el.values.get("stroke-width", 0.50)) * MM_TO_PT
                    elif "line-center" in cls_list:
                        stroke = colors.line_center
                        lw = 0.25 * MM_TO_PT
                        ls = (0, (6, 2, 1, 2))
                    elif "line-section" in cls_list or "line-section-thick" in cls_list:
                        stroke = colors.line_section
                        lw = float(el.values.get("stroke-width", 0.6)) * MM_TO_PT
                    elif "section-arrow-head" in cls_list:
                        fill = colors.line_section
                    elif "border-main" in cls_list:
                        stroke = colors.border
                        lw = 0.70 * MM_TO_PT
                    elif "border-sub" in cls_list or "panel-sub" in cls_list:
                        stroke = colors.border_sub
                        lw = float(el.values.get("stroke-width", 0.35)) * MM_TO_PT
                    elif "panel-stroke" in cls_list:
                        stroke = colors.border
                        lw = float(el.values.get("stroke-width", 0.50)) * MM_TO_PT
                    elif "title-bg" in cls_list or "panel-bg" in cls_list:
                        fill = colors.title_bg
                        stroke = colors.border
                        lw = 0.6 * MM_TO_PT
                    elif "table-header-bg" in cls_list:
                        fill = colors.table_header_bg
                    elif "table-row-alt" in cls_list:
                        fill = colors.table_row_alt
                    elif "dim-line" in cls_list:
                        stroke = colors.dim_line
                        lw = 0.25 * MM_TO_PT
                    elif "dim-arrow" in cls_list or "leader-dot" in cls_list:
                        fill = colors.dim_line
                    elif "balloon-circle" in cls_list:
                        fill = colors.balloon_bg
                        stroke = colors.balloon_border
                        lw = 0.5 * MM_TO_PT
                    elif "balloon-dot" in cls_list:
                        fill = colors.balloon_border
                    elif "balloon-leader" in cls_list:
                        stroke = colors.balloon_border
                        lw = 0.25 * MM_TO_PT
                    else:
                        if (
                            not isinstance(el, (se.Line, se.SimpleLine, se.Polyline))
                            and el.fill
                            and el.fill.hex
                            and el.fill.hex not in ("#00000000", "#000000")
                        ):
                            fill = el.fill.hex
                        if el.stroke and el.stroke.hex and el.stroke.hex != "#00000000":
                            stroke = el.stroke.hex
                            lw = float(el.values.get("stroke-width", 0.35)) * MM_TO_PT
                        elif not fill:
                            stroke = colors.line_visible
                            lw = 0.35 * MM_TO_PT

                    raw_fill_opacity = el.values.get("fill-opacity")
                    alpha = float(raw_fill_opacity) if raw_fill_opacity is not None else None
                    patch = patches.PathPatch(
                        mpl_path,
                        facecolor=fill or "none",
                        edgecolor=stroke or "none",
                        linewidth=lw,
                        linestyle=ls,
                        hatch=hatch,
                        alpha=alpha,
                    )
                    ax.add_patch(patch)

            elif isinstance(el, se.SVGText):
                txt = el.text
                if txt:
                    # Extract rotation angle from transformation matrix
                    a, b = el.transform.a, el.transform.b
                    angle_deg = math.degrees(math.atan2(b, a))
                    rotation = -angle_deg if abs(angle_deg) > 0.01 else 0.0

                    pt = se.Point(el.x or 0, el.y or 0) * el.transform
                    x = pt.x
                    y = height_mm - pt.y
                    fs_mm = float(el.font_size or 3.0)
                    fs_pt = fs_mm * MM_TO_PT * 0.90
                    weight = "bold" if el.font_weight in ["bold", "700", "800"] else "normal"

                    raw_text_fill = el.values.get("fill")
                    if (
                        raw_text_fill
                        and raw_text_fill not in ("none", "currentColor")
                        and not str(raw_text_fill).strip().startswith("var(")
                    ):
                        color = str(raw_text_fill).strip()
                    elif "balloon-text" in cls_list:
                        color = colors.balloon_text
                    elif "dim-text" in cls_list:
                        color = colors.dim_text
                    elif "accent-text" in cls_list:
                        color = colors.text_accent
                    elif "section-label-text" in cls_list:
                        color = colors.line_section
                    elif any(
                        c in cls_list
                        for c in [
                            "zone-text",
                            "view-scale",
                            "tb-field-label",
                            "tb-field-val-sm",
                            "panel-muted",
                        ]
                    ):
                        color = colors.text_muted
                    elif any(
                        c in cls_list
                        for c in [
                            "view-title",
                            "tb-title",
                            "tb-field-val-bold",
                            "notes-text",
                            "leader-text",
                            "panel-text",
                        ]
                    ):
                        color = colors.text
                    else:
                        color = colors.text

                    anchor_map = {"start": "left", "middle": "center", "end": "right"}
                    ha = anchor_map.get(getattr(el, "anchor", "start"), "left")
                    va = "center" if rotation != 0 else "baseline"
                    bbox_props = (
                        {"boxstyle": "square,pad=0.15", "facecolor": colors.bg, "edgecolor": "none"}
                        if "dim-text" in cls_list
                        else None
                    )
                    ax.text(
                        x,
                        y,
                        txt,
                        color=color,
                        fontsize=fs_pt,
                        fontweight=weight,
                        ha=ha,
                        va=va,
                        rotation=rotation,
                        rotation_mode="anchor",
                        bbox=bbox_props,
                    )

    @staticmethod
    def _parse_svg_length_mm(raw: str) -> float | None:
        """Convert an SVG length attribute (mm/cm/in/pt/px/unitless) to mm, rejecting '%'."""
        m = re.match(r"^\s*([-+]?\d*\.?\d+)\s*([a-zA-Z%]*)\s*$", raw)
        if not m:
            return None
        val = float(m.group(1))
        unit = m.group(2).lower()
        if unit == "%":
            return None
        unit_scale = {
            "": 1.0,
            "mm": 1.0,
            "cm": 10.0,
            "in": 25.4,
            "pt": 25.4 / 72.0,
            "pc": 25.4 / 6.0,
            "px": 25.4 / 96.0,
        }
        factor = unit_scale.get(unit)
        return val * factor if factor is not None else None

    @classmethod
    def _parse_svg_dimensions_mm(cls, svg_code: str) -> tuple[float, float]:
        """Extract (width_mm, height_mm) from the root <svg> tag or viewBox, defaulting to A4."""
        root_m = re.search(r"<svg\b([^>]*)>", svg_code, re.IGNORECASE)
        if root_m:
            attrs = root_m.group(1)
            w_m = re.search(r'\bwidth\s*=\s*["\']([^"\']+)["\']', attrs, re.IGNORECASE)
            h_m = re.search(r'\bheight\s*=\s*["\']([^"\']+)["\']', attrs, re.IGNORECASE)
            if w_m and h_m:
                w_mm = cls._parse_svg_length_mm(w_m.group(1))
                h_mm = cls._parse_svg_length_mm(h_m.group(1))
                if w_mm is not None and h_mm is not None:
                    return w_mm, h_mm
            vb_m = re.search(
                r'\bviewBox\s*=\s*["\']\s*[-+]?\d*\.?\d+[\s,]+[-+]?\d*\.?\d+[\s,]+([-+]?\d*\.?\d+)[\s,]+([-+]?\d*\.?\d+)',
                attrs,
                re.IGNORECASE,
            )
            if vb_m:
                return float(vb_m.group(1)), float(vb_m.group(2))
        return (297.0, 210.0)

    @classmethod
    def _coerce_sheet_input(
        cls,
        item: tuple[float, float, str] | Any,
        theme: Theme,
    ) -> tuple[float, float, str]:
        """Normalize a tuple, DrawingSheet, SVG path, or SVG string into (width_mm, height_mm, svg_str)."""
        if isinstance(item, tuple) and len(item) == 3:
            return (float(item[0]), float(item[1]), str(item[2]))
        if hasattr(item, "to_svg") and hasattr(item, "width_mm") and hasattr(item, "height_mm"):
            return (float(item.width_mm), float(item.height_mm), str(item.to_svg(theme=theme)))
        if isinstance(item, (str, os.PathLike)):
            path_or_svg = os.fspath(item)
            if os.path.isfile(path_or_svg):
                with open(path_or_svg, encoding="utf-8") as f:
                    svg_code = f.read()
            else:
                svg_code = path_or_svg
            w_mm, h_mm = cls._parse_svg_dimensions_mm(svg_code)
            return (w_mm, h_mm, svg_code)
        raise TypeError(
            f"Expected (width_mm, height_mm, svg_str), DrawingSheet, or SVG path/string; got {type(item).__name__}"
        )

    @classmethod
    def render_sheets_to_pdf(
        cls,
        sheets_data: Sequence[tuple[float, float, str] | Any],
        output_path: str,
        theme: Theme = Theme.DARK_BLUEPRINT,
        bookmarks: bool = False,
        booklet_title: str = "ENGINEERING DRAWINGS",
        sheet_titles: list[str] | None = None,
    ) -> None:
        """Render multiple sheets (tuples, DrawingSheets, or SVG paths/strings) to a multi-page PDF."""
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        colors = THEME_COLORS[theme]

        pages_cls = BookmarkedPdfPages if bookmarks else PdfPages
        kwargs: dict[str, Any] = {}
        if bookmarks:
            kwargs = {"booklet_title": booklet_title, "sheet_titles": sheet_titles}

        with pages_cls(output_path, **kwargs) as pdf:
            for item in sheets_data:
                width_mm, height_mm, svg_code = cls._coerce_sheet_input(item, theme)
                w_in = width_mm / 25.4
                h_in = height_mm / 25.4

                fig = plt.figure(figsize=(w_in, h_in), dpi=150)
                ax = fig.add_axes((0.0, 0.0, 1.0, 1.0))
                fig.patch.set_facecolor(colors.bg)

                cls._render_svg_to_ax(ax, svg_code, width_mm, height_mm, colors)

                pdf.savefig(fig)
                plt.close(fig)
