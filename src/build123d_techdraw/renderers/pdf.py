"""High-precision multi-page vector PDF export using matplotlib and svgelements."""

import io
import math
import os
from typing import Any

import matplotlib

matplotlib.use("Agg")
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib.patches as patches
import matplotlib.path as mpath
import matplotlib.pyplot as plt
import svgelements as se
from matplotlib.backends.backend_pdf import PdfPages

from build123d_techdraw.renderers.theme import THEME_COLORS, Theme, ThemeColors

MM_TO_PT: float = 72.0 / 25.4
# Contours (ISO 128 Type A): 0.50mm -> 1.42pt
# Dimensions/Leaders (ISO 128 Type B): 0.25mm -> 0.71pt
# Borders: 0.70mm -> 1.98pt


class PDFRenderer:
    """Renders vector technical drawings to multi-page publication-ready PDF documents."""

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

        doc = se.SVG.parse(
            io.BytesIO(svg_code.encode("utf-8")),
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
                    is_hatch = (
                        "section-hatch-fill" in cls_list
                        or "sectionHatch" in str(getattr(el, "fill", ""))
                        or "sectionHatch" in str(el.values.get("fill", ""))
                    )

                    if is_hidden:
                        stroke = colors.line_hidden
                        lw = 0.20 * MM_TO_PT
                        ls = (0, (3, 2))
                    elif is_hatch:
                        fill = colors.table_header_bg
                        stroke = colors.line_visible
                        lw = 0.35 * MM_TO_PT
                        hatch = "//"
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
                    elif "border-sub" in cls_list:
                        stroke = colors.border_sub
                        lw = float(el.values.get("stroke-width", 0.35)) * MM_TO_PT
                    elif "title-bg" in cls_list:
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

                    patch = patches.PathPatch(
                        mpl_path,
                        facecolor=fill or "none",
                        edgecolor=stroke or "none",
                        linewidth=lw,
                        linestyle=ls,
                        hatch=hatch,
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

                    if "balloon-text" in cls_list:
                        color = colors.balloon_text
                    elif "dim-text" in cls_list:
                        color = colors.dim_text
                    elif "section-label-text" in cls_list:
                        color = colors.line_section
                    elif any(
                        c in cls_list
                        for c in ["zone-text", "view-scale", "tb-field-label", "tb-field-val-sm"]
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

    @classmethod
    def render_sheets_to_pdf(
        cls,
        sheets_data: list[tuple[float, float, str]],
        output_path: str,
        theme: Theme = Theme.DARK_BLUEPRINT,
    ) -> None:
        """Render multiple sheets to a multi-page PDF document."""
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        colors = THEME_COLORS[theme]

        with PdfPages(output_path) as pdf:
            for width_mm, height_mm, svg_code in sheets_data:
                w_in = width_mm / 25.4
                h_in = height_mm / 25.4

                fig = plt.figure(figsize=(w_in, h_in), dpi=150)
                ax = fig.add_axes((0.0, 0.0, 1.0, 1.0))
                fig.patch.set_facecolor(colors.bg)

                cls._render_svg_to_ax(ax, svg_code, width_mm, height_mm, colors)

                pdf.savefig(fig)
                plt.close(fig)
