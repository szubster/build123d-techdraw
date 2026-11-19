"""1:1 DXF flat pattern and multi-layer full drawing sheet export using ezdxf."""

from __future__ import annotations

import contextlib
import io
import math
import os
import warnings
from typing import TYPE_CHECKING, Any

import build123d as bd
import ezdxf.path
import svgelements as se
from build123d.exporters import ColorIndex
from build123d.topology import Shape
from ezdxf.document import Drawing
from ezdxf.filemanagement import new as new_dxf_document

from build123d_techdraw.renderers.svg_elements import has_hidden_ancestor

if TYPE_CHECKING:
    from build123d_techdraw.core.sheet import DrawingSheet


def _detect_primary_flat_face(shape: Shape[Any]) -> tuple[Shape[Any], bd.Plane | None]:
    """Extract the largest planar face and its datum Plane when given a 3D solid/part."""
    if not shape.solids():
        return shape, None

    planar_faces = [f for f in shape.faces() if f.geom_type == bd.GeomType.PLANE]
    if not planar_faces:
        return shape, None

    primary = max(planar_faces, key=lambda f: float(f.area))
    return primary, bd.Plane(primary)


class DXFRenderer:
    """Exports 2D CAD views and complete drawing sheets to standard multi-layer DXF files."""

    @classmethod
    def export_shape_to_dxf(
        cls,
        shape: Shape[Any],
        output_path: str,
        dxfversion: str = "R2010",
        plane: bd.Plane | None = None,
        tolerance: float = 0.05,  # noqa: ARG003
        strict: bool = False,
    ) -> None:
        """Export 2D cross-section, flat pattern, or 3D plate primary face to a 1:1 DXF file."""
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        exporter = bd.ExportDXF(version=dxfversion, unit=bd.Unit.MM)
        exporter.add_layer("VISIBLE", color=ColorIndex.BLACK)
        exporter.add_layer("HIDDEN", color=ColorIndex.GRAY, line_type=bd.LineType.DASHED)
        exporter.add_layer("CENTER", color=ColorIndex.CYAN, line_type=bd.LineType.CENTER)

        target_shape = shape
        active_plane = plane
        if active_plane is None:
            target_shape, active_plane = _detect_primary_flat_face(shape)

        edges = target_shape.edges()
        for idx, edge in enumerate(edges):
            try:
                projected = active_plane.to_local_coords(edge) if active_plane is not None else edge
                if not isinstance(projected, Shape):
                    raise TypeError(f"Expected a projected Shape, got {type(projected).__name__}")
                exporter.add_shape(projected, layer="VISIBLE")
            except Exception as exc:
                if strict:
                    raise
                warnings.warn(
                    f"Failed to export edge {idx} to DXF: {exc}",
                    RuntimeWarning,
                    stacklevel=2,
                )

        exporter.write(output_path)

    @classmethod
    def export_sheet_to_dxf(
        cls,
        sheet: DrawingSheet,
        output_path: str,
        dxfversion: str = "R2010",
    ) -> None:
        """Export the complete composed drawing sheet with standard CAD layers to DXF."""
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        doc: Drawing = new_dxf_document(dxfversion=dxfversion)
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
            ("THREADS", 6, "CONTINUOUS"),  # Magenta (ISO 6410-1 cosmetic threads)
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
                is_hidden = has_hidden_ancestor(el)

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
            elif any(c in cls_list for c in ["line-thread", "line-thread-dashed"]):
                target_layer = "THREADS"
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

                subpaths: list[ezdxf.path.Path] = []
                dxf_path: ezdxf.path.Path | None = None
                for seg in p:
                    if isinstance(seg, se.Move):
                        if dxf_path is not None and len(dxf_path):
                            subpaths.append(dxf_path)
                        dxf_path = ezdxf.path.Path(
                            start=(float(seg.end.x), float(h_mm - seg.end.y))
                        )
                    elif isinstance(seg, se.Line):
                        if dxf_path is None:
                            dxf_path = ezdxf.path.Path(
                                start=(float(seg.start.x), float(h_mm - seg.start.y))
                            )
                        dxf_path.line_to((float(seg.end.x), float(h_mm - seg.end.y)))
                    elif isinstance(seg, se.CubicBezier):
                        if dxf_path is None:
                            dxf_path = ezdxf.path.Path(
                                start=(float(seg.start.x), float(h_mm - seg.start.y))
                            )
                        dxf_path.curve4_to(
                            (float(seg.end.x), float(h_mm - seg.end.y)),
                            (float(seg.control1.x), float(h_mm - seg.control1.y)),
                            (float(seg.control2.x), float(h_mm - seg.control2.y)),
                        )
                    elif isinstance(seg, se.QuadraticBezier):
                        if dxf_path is None:
                            dxf_path = ezdxf.path.Path(
                                start=(float(seg.start.x), float(h_mm - seg.start.y))
                            )
                        dxf_path.curve3_to(
                            (float(seg.end.x), float(h_mm - seg.end.y)),
                            (float(seg.control.x), float(h_mm - seg.control.y)),
                        )
                    elif isinstance(seg, se.Close) and dxf_path is not None:
                        dxf_path.close()
                if dxf_path is not None and len(dxf_path):
                    subpaths.append(dxf_path)
                if subpaths:
                    ezdxf.path.render_splines_and_polylines(
                        msp, subpaths, dxfattribs={"layer": target_layer}
                    )

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
