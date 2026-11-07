"""1:1 DXF flat pattern and multi-layer full drawing sheet export using ezdxf."""

from __future__ import annotations

import contextlib
import io
import math
import os
from typing import TYPE_CHECKING, Any

import ezdxf
import svgelements as se
from ezdxf.document import Drawing

if TYPE_CHECKING:
    from build123d_techdraw.core.sheet import DrawingSheet


class DXFRenderer:
    """Exports 2D CAD views and complete drawing sheets to standard multi-layer DXF files."""

    @classmethod
    def export_shape_to_dxf(
        cls,
        shape: Any,
        output_path: str,
        dxfversion: str = "R2010",
    ) -> None:
        """Export 2D cross-section or projected shape to a 1:1 DXF file for CNC/laser cutting."""
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        doc: Drawing = ezdxf.new(dxfversion=dxfversion)  # type: ignore[attr-defined]
        msp = doc.modelspace()

        # Set up standard CAD layers
        doc.layers.new(name="VISIBLE", dxfattribs={"color": 7})  # White/Black
        doc.layers.new(name="HIDDEN", dxfattribs={"color": 8, "linetype": "DASHED"})  # Gray
        doc.layers.new(name="CENTER", dxfattribs={"color": 4, "linetype": "CENTER"})  # Cyan

        edges: list[Any] = shape.edges() if hasattr(shape, "edges") else []
        for edge in edges:
            try:
                gtype_str = str(getattr(edge, "geom_type", "")).upper()
                if "LINE" in gtype_str:
                    v0 = edge.start_point()
                    v1 = edge.end_point()
                    msp.add_line(
                        (float(v0.X), float(v0.Y)),
                        (float(v1.X), float(v1.Y)),
                        dxfattribs={"layer": "VISIBLE"},
                    )
                elif "CIRCLE" in gtype_str:
                    center = edge.arc_center
                    radius = float(edge.radius)
                    if edge.is_closed:
                        msp.add_circle(
                            (float(center.X), float(center.Y)),
                            radius,
                            dxfattribs={"layer": "VISIBLE"},
                        )
                    else:
                        msp.add_arc(
                            (float(center.X), float(center.Y)),
                            radius,
                            start_angle=float(edge.start_angle),
                            end_angle=float(edge.end_angle),
                            dxfattribs={"layer": "VISIBLE"},
                        )
                else:
                    pts = [edge.position_at(i / 20.0) for i in range(21)]
                    for i in range(len(pts) - 1):
                        msp.add_line(
                            (float(pts[i].X), float(pts[i].Y)),
                            (float(pts[i + 1].X), float(pts[i + 1].Y)),
                            dxfattribs={"layer": "VISIBLE"},
                        )
            except Exception:
                continue

        doc.saveas(output_path)

    @classmethod
    def export_sheet_to_dxf(
        cls,
        sheet: DrawingSheet,
        output_path: str,
        dxfversion: str = "R2010",
    ) -> None:
        """Export the complete composed drawing sheet with standard CAD layers to DXF."""
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        doc: Drawing = ezdxf.new(dxfversion=dxfversion)  # type: ignore[attr-defined]
        msp = doc.modelspace()

        # Define standard CAD layers and colors (AutoCAD ACI colors)
        layers_cfg = [
            ("BORDER", 7, "CONTINUOUS"),  # White/Black
            ("TITLE_BLOCK", 7, "CONTINUOUS"),  # White/Black
            ("VISIBLE", 7, "CONTINUOUS"),  # White/Black
            ("HIDDEN", 8, "DASHED"),  # Gray
            ("CENTER", 4, "CENTER"),  # Cyan
            ("DIMENSIONS", 2, "CONTINUOUS"),  # Yellow
            ("ANNOTATIONS", 3, "CONTINUOUS"),  # Green
            ("HATCH", 9, "CONTINUOUS"),  # Light Gray
        ]
        for name, color, ltype in layers_cfg:
            with contextlib.suppress(Exception):
                doc.layers.new(name=name, dxfattribs={"color": color, "linetype": ltype})

        svg_code = sheet.to_svg()
        h_mm = sheet.height_mm

        doc_svg = se.SVG.parse(io.BytesIO(svg_code.encode("utf-8")))

        for el in doc_svg.elements():
            cls_str = el.values.get("class", "")
            cls_list = cls_str.split() if cls_str else []

            # Skip background fills
            if "sheet-bg" in cls_list or "blueprint-grid" in cls_list:
                continue

            # Layer mapping
            is_hidden = "line-hidden" in cls_list or "layer_hidden" in cls_list
            if not is_hidden:
                parent = getattr(el, "parent", None)
                while parent:
                    p_id = getattr(parent, "id", "") or parent.values.get("id", "")
                    p_cls = getattr(parent, "class", "") or parent.values.get("class", "")
                    if "hidden" in p_id or "hidden" in p_cls:
                        is_hidden = True
                        break
                    parent = getattr(parent, "parent", None)

            if is_hidden:
                target_layer = "HIDDEN"
            elif any(
                c in cls_list for c in ["border-main", "border-sub", "grid-ticks", "zone-text"]
            ):
                target_layer = "BORDER"
            elif any(
                c in cls_list
                for c in [
                    "title-bg",
                    "tb-title",
                    "tb-field-label",
                    "tb-field-val",
                    "tb-field-val-bold",
                    "tb-field-val-sm",
                ]
            ):
                target_layer = "TITLE_BLOCK"
            elif any(c in cls_list for c in ["line-center"]):
                target_layer = "CENTER"
            elif any(
                c in cls_list
                for c in [
                    "dim-line",
                    "dim-arrow",
                    "dim-text",
                    "radial-dim",
                    "diameter-dim",
                    "angular-dim",
                    "ordinate-dim",
                ]
            ):
                target_layer = "DIMENSIONS"
            elif any(
                c in cls_list
                for c in [
                    "leader-note",
                    "leader-text",
                    "bom-balloon",
                    "balloon-text",
                    "gdt-fcf",
                    "datum-symbol",
                    "surface-finish-symbol",
                    "welding-symbol",
                    "notes-text",
                    "view-title",
                    "view-scale",
                ]
            ):
                target_layer = "ANNOTATIONS"
            elif any(c in cls_list for c in ["section-hatch-fill", "sectionHatch"]):
                target_layer = "HATCH"
            else:
                target_layer = "VISIBLE"

            if isinstance(el, se.Shape):
                p = se.Path(el)
                p.reify()
                p.approximate_arcs_with_cubics()

                curr_pt: tuple[float, float] | None = None
                for seg in p:
                    if isinstance(seg, se.Move):
                        curr_pt = (float(seg.end.x), float(h_mm - seg.end.y))
                    elif isinstance(seg, se.Line):
                        nxt_pt = (float(seg.end.x), float(h_mm - seg.end.y))
                        if curr_pt is not None:
                            msp.add_line(curr_pt, nxt_pt, dxfattribs={"layer": target_layer})
                        curr_pt = nxt_pt
                    elif isinstance(seg, (se.CubicBezier, se.QuadraticBezier)):
                        # Discretize bezier curve
                        curve_pts = [seg.point(t) for t in [i / 10.0 for i in range(11)]]
                        for i in range(len(curve_pts) - 1):
                            p_a = (float(curve_pts[i].x), float(h_mm - curve_pts[i].y))
                            p_b = (float(curve_pts[i + 1].x), float(h_mm - curve_pts[i + 1].y))
                            msp.add_line(p_a, p_b, dxfattribs={"layer": target_layer})
                        curr_pt = (float(seg.end.x), float(h_mm - seg.end.y))
                    elif isinstance(seg, se.Close):
                        curr_pt = None

            elif isinstance(el, se.SVGText):
                txt = el.text
                if txt:
                    pt = se.Point(el.x or 0, el.y or 0) * el.transform
                    x = float(pt.x)
                    y = float(h_mm - pt.y)
                    fs_mm = float(el.font_size or 2.8)

                    # Extract rotation
                    a, b = el.transform.a, el.transform.b
                    angle_deg = math.degrees(math.atan2(b, a))

                    msp.add_text(
                        txt,
                        dxfattribs={
                            "layer": target_layer,
                            "height": fs_mm,
                            "rotation": angle_deg,
                            "insert": (x, y),
                        },
                    )

        doc.saveas(output_path)
