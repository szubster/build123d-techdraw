"""1:1 DXF flat pattern and view export using ezdxf."""

import os
from typing import Any

import ezdxf
from ezdxf.document import Drawing


class DXFRenderer:
    """Exports 2D CAD views to standard 1:1 DXF files for laser/waterjet cutting and CNC machining."""

    @classmethod
    def export_shape_to_dxf(
        cls,
        shape: Any,
        output_path: str,
        dxfversion: str = "R2010",
    ) -> None:
        """Export 2D cross-section or projected shape to a 1:1 DXF file."""
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        doc: Drawing = ezdxf.new(dxfversion=dxfversion)  # type: ignore[attr-defined]
        msp = doc.modelspace()

        # Set up standard CAD layers
        doc.layers.new(name="VISIBLE", dxfattribs={"color": 7})  # White/Black
        doc.layers.new(name="HIDDEN", dxfattribs={"color": 8, "linetype": "DASHED"})  # Gray
        doc.layers.new(name="CENTER", dxfattribs={"color": 4, "linetype": "CENTER"})  # Cyan

        # Convert build123d edges to DXF entities
        edges: list[Any] = shape.edges() if hasattr(shape, "edges") else []
        for edge in edges:
            try:
                # Linear edge
                if edge.geom_type == "LINE":
                    v0 = edge.start_point()
                    v1 = edge.end_point()
                    msp.add_line(
                        (v0.X, v0.Y),
                        (v1.X, v1.Y),
                        dxfattribs={"layer": "VISIBLE"},
                    )
                # Circular arc or full circle
                elif edge.geom_type == "CIRCLE":
                    center = edge.arc_center
                    radius = edge.radius
                    if edge.is_closed:
                        msp.add_circle(
                            (center.X, center.Y),
                            radius,
                            dxfattribs={"layer": "VISIBLE"},
                        )
                    else:
                        start_angle = edge.start_angle
                        end_angle = edge.end_angle
                        msp.add_arc(
                            (center.X, center.Y),
                            radius,
                            start_angle=start_angle,
                            end_angle=end_angle,
                            dxfattribs={"layer": "VISIBLE"},
                        )
                # Fallback: discretize complex splines / curves
                else:
                    pts = edge.positions(count=30)
                    for i in range(len(pts) - 1):
                        msp.add_line(
                            (pts[i].X, pts[i].Y),
                            (pts[i + 1].X, pts[i + 1].Y),
                            dxfattribs={"layer": "VISIBLE"},
                        )
            except Exception:
                continue

        doc.saveas(output_path)
