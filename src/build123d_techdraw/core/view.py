"""SheetView representations, view projection mathematics, and coordinate transformations."""

import contextlib
import math
import os
import re
import tempfile
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional, Self

import build123d as bd

from build123d_techdraw.annotations.callouts import (
    BOMBalloon,
    ChamferCallout,
    HoleCallout,
    LeaderNote,
    ThreadCallout,
    WeldingSymbol,
    WeldType,
)
from build123d_techdraw.annotations.centerlines import BoltCircle, Centerline, CenterMark
from build123d_techdraw.annotations.dimensions import (
    AngularDimension,
    DiameterDimension,
    DimensionType,
    LinearDimension,
    OrdinateDimension,
    RadialDimension,
    Tolerance,
)
from build123d_techdraw.annotations.gdt import (
    DatumFeatureSymbol,
    FeatureControlFrame,
    GDTCharacteristic,
)
from build123d_techdraw.annotations.surface import MaterialRemoval, SurfaceFinishSymbol
from build123d_techdraw.context import CURRENT_VIEW
from build123d_techdraw.core.scale import format_iso_scale

if TYPE_CHECKING:
    from build123d_techdraw.core.sheet import DrawingSheet


@dataclass
class BreakInfo:
    """Represents a broken-out region exclusion along an axis."""

    interval: tuple[float, float]
    axis: str = "X"
    style: str = "zigzag"
    gap: float = 10.0


class ViewType(Enum):
    """Standard technical drawing view types and projections."""

    FRONT = "front"
    TOP = "top"
    RIGHT = "right"
    LEFT = "left"
    REAR = "rear"
    BOTTOM = "bottom"
    ISOMETRIC = "isometric"
    ISOMETRIC_LEFT = "isometric_left"
    ISOMETRIC_REAR = "isometric_rear"
    SECTION = "section"
    DETAIL = "detail"
    AUXILIARY = "auxiliary"


# Standard camera orientations: (look_from, look_up)
VIEW_VECTORS: dict[ViewType, tuple[tuple[float, float, float], tuple[float, float, float]]] = {
    ViewType.FRONT: ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    ViewType.TOP: ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    ViewType.BOTTOM: ((0.0, 0.0, -1.0), (0.0, -1.0, 0.0)),
    ViewType.RIGHT: ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    ViewType.LEFT: ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    ViewType.REAR: ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    ViewType.ISOMETRIC: ((1.2, 1.8, 1.2), (0.0, 0.0, 1.0)),
    ViewType.ISOMETRIC_LEFT: ((-1.2, 1.8, 1.2), (0.0, 0.0, 1.0)),
    ViewType.ISOMETRIC_REAR: ((1.2, -1.8, 1.2), (0.0, 0.0, 1.0)),
}


class SheetView:
    """Represents an orthographic, isometric, or section view placed on a DrawingSheet.

    Provides bidirectional coordinate transformations between 3D model space,
    2D view projection space, and 2D paper sheet space (mm).
    """

    def __init__(
        self,
        view_type: ViewType,
        center_x: float,
        center_y: float,
        scale: float,
        width: float,
        height: float,
        model_origin: tuple[float, float] = (0.0, 0.0),
        projection_origin: bd.Vector | None = None,
        proj_x: bd.Vector | None = None,
        proj_y: bd.Vector | None = None,
        orig_cx: float = 0.0,
        orig_cy: float = 0.0,
        svg_content: str = "",
        view_label: str | None = None,
        view_label_pos: tuple[float, float] | None = None,
        scale_label: str | None = None,
        sheet: Optional["DrawingSheet"] = None,
        shape: Any | None = None,
        enforce_iso_scale: bool = True,
    ):
        self.sheet = sheet
        self.shape = shape
        self.enforce_iso_scale = enforce_iso_scale
        self.view_type = view_type
        self.center_x = center_x
        self.center_y = center_y
        self.scale = scale
        self.width = width
        self.height = height
        self.unbroken_width = width
        self.unbroken_height = height
        self.breaks: list[BreakInfo] = []
        self.min_x = center_x - width / 2.0
        self.max_x = center_x + width / 2.0
        self.min_y = center_y - height / 2.0
        self.max_y = center_y + height / 2.0

        self.model_origin = model_origin
        self.origin_x = model_origin[0]
        self.origin_y = model_origin[1]

        self.projection_origin = projection_origin
        self.proj_x = proj_x
        self.proj_y = proj_y
        self.orig_cx = orig_cx
        self.orig_cy = orig_cy

        self.svg_content = svg_content
        self.inner_svg_content: str = ""
        self.rotation_angle_deg: float = 0.0
        self.view_label = view_label
        self.view_label_pos = view_label_pos
        self.scale_label = scale_label
        self.bottom_annotation_y: float = self.max_y
        self.annotations: list[Any] = []
        self._ctx_token: Any = None
        self.hatch: str | None = None
        self.hatch_pitch: float | None = None
        self.hatch_angle: float | None = None
        self.hatch_id: str | None = None
        self.parent_view: SheetView | None = None
        self.detail_shape: str | None = None

    def __enter__(self) -> Self:
        self._ctx_token = CURRENT_VIEW.set(self)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        if self._ctx_token is not None:
            CURRENT_VIEW.reset(self._ctx_token)
            self._ctx_token = None

    def __iadd__(self, item: Any) -> Self:
        return self.add(item)

    def __add__(self, item: Any) -> Self:
        return self.add(item)

    def add(self, item: Any) -> Self:
        """Attach an annotation or collection of annotations to this view."""
        if isinstance(item, (list, tuple, set)):
            for sub_item in item:
                self.add(sub_item)
            return self

        if hasattr(item, "_attach_to_view"):
            item._attach_to_view(self)

        if item not in self.annotations:
            self.annotations.append(item)

        if self.sheet is not None and item not in self.sheet.annotations:
            self.sheet.annotations.append(item)

        return self

    add_annotation = add

    def _update_transform(self) -> None:
        """Regenerate the view's SVG transform after moving or scaling."""
        if not self.inner_svg_content:
            return
        rot_str = (
            f" rotate({self.rotation_angle_deg:.1f}, {self.orig_cx:.2f}, {self.orig_cy:.2f})"
            if self.rotation_angle_deg != 0.0
            else ""
        )
        transform = (
            f"translate({self.center_x:.2f}, {self.center_y:.2f}) "
            f"scale({self.scale:.4f}, {self.scale:.4f}){rot_str} "
            f"translate({-self.orig_cx:.2f}, {-self.orig_cy:.2f})"
        )
        view_id = f"view_{self.view_type.value}_{int(self.center_x)}_{int(self.center_y)}"
        self.svg_content = f"""
    <!-- View: {self.view_type.value.upper()} at ({self.center_x}, {self.center_y}) scale={self.scale} -->
    <g id="{view_id}" class="sheet-view" transform="{transform}">
        {self.inner_svg_content}
    </g>
    """
        if self.breaks:
            self._apply_breaks()

    def add_break(
        self,
        interval: tuple[float, float],
        axis: str = "X",
        style: str = "zigzag",
        gap: float = 10.0,
    ) -> Self:
        """Add a break line to cut out a uniform span of the view."""
        b_info = BreakInfo(
            interval=(min(interval[0], interval[1]), max(interval[0], interval[1])),
            axis=axis.upper(),
            style=style.lower(),
            gap=gap,
        )
        self.breaks.append(b_info)
        self._apply_breaks()
        return self

    def _apply_breaks(self) -> None:
        """Regenerate SVG and geometry bounds with break cuts and symbols."""
        if not self.breaks or not self.inner_svg_content:
            return

        b = self.breaks[0]
        x0, x1 = min(b.interval), max(b.interval)
        model_span = x1 - x0
        paper_span = model_span * self.scale
        shift = paper_span - b.gap

        if b.axis == "X":
            self.width = max(b.gap + 10.0, self.unbroken_width - shift)
            self.min_x = self.center_x - self.width / 2.0
            self.max_x = self.center_x + self.width / 2.0

            m_min_x = self.origin_x - (self.unbroken_width / (2.0 * self.scale))
            l1 = max(5.0, (x0 - m_min_x) * self.scale)
            bx1 = self.min_x + l1
            bx2 = bx1 + b.gap

            view_id = f"view_{self.view_type.value}_{int(self.center_x)}_{int(self.center_y)}"
            clip1_id = f"clip1_{view_id}"
            clip2_id = f"clip2_{view_id}"

            rot_str = (
                f" rotate({self.rotation_angle_deg:.1f}, {self.orig_cx:.2f}, {self.orig_cy:.2f})"
                if self.rotation_angle_deg != 0.0
                else ""
            )

            break_lines = []
            for bx in [bx1, bx2]:
                y_top = self.min_y - 2.0
                y_bot = self.max_y + 2.0
                ym = (y_top + y_bot) / 2.0
                if b.style == "zigzag":
                    pts = (
                        f"{bx:.2f},{y_top:.2f} "
                        f"{bx:.2f},{ym - 4.0:.2f} "
                        f"{bx + 2.5:.2f},{ym - 1.5:.2f} "
                        f"{bx - 2.5:.2f},{ym + 1.5:.2f} "
                        f"{bx:.2f},{ym + 4.0:.2f} "
                        f"{bx:.2f},{y_bot:.2f}"
                    )
                    break_lines.append(
                        f'<polyline points="{pts}" class="break-line dim-line" stroke-width="0.35" fill="none"/>'
                    )
                else:
                    d = (
                        f"M {bx:.2f} {y_top:.2f} "
                        f"Q {bx + 3.0:.2f} {(y_top * 3.0 + y_bot) / 4.0:.2f} {bx:.2f} {ym:.2f} "
                        f"Q {bx - 3.0:.2f} {(y_top + y_bot * 3.0) / 4.0:.2f} {bx:.2f} {y_bot:.2f}"
                    )
                    break_lines.append(
                        f'<path d="{d}" class="break-line dim-line" stroke-width="0.35" fill="none"/>'
                    )

            t1 = (
                f"translate({self.center_x + shift / 2.0:.2f}, {self.center_y:.2f}) "
                f"scale({self.scale:.4f}, {self.scale:.4f}){rot_str} "
                f"translate({-self.orig_cx:.2f}, {-self.orig_cy:.2f})"
            )
            t2 = (
                f"translate({self.center_x - shift / 2.0:.2f}, {self.center_y:.2f}) "
                f"scale({self.scale:.4f}, {self.scale:.4f}){rot_str} "
                f"translate({-self.orig_cx:.2f}, {-self.orig_cy:.2f})"
            )

            self.svg_content = f"""
    <!-- View: {self.view_type.value.upper()} (Broken View) at ({self.center_x}, {self.center_y}) scale={self.scale} -->
    <defs>
        <clipPath id="{clip1_id}">
            <rect x="{self.min_x - 10:.2f}" y="{self.min_y - 10:.2f}" width="{l1 + 10:.2f}" height="{self.height + 20:.2f}"/>
        </clipPath>
        <clipPath id="{clip2_id}">
            <rect x="{bx2:.2f}" y="{self.min_y - 10:.2f}" width="{self.max_x - bx2 + 10:.2f}" height="{self.height + 20:.2f}"/>
        </clipPath>
    </defs>
    <g id="{view_id}" class="sheet-view broken-view">
        <g clip-path="url(#{clip1_id})">
            <g transform="{t1}">
                {self.inner_svg_content}
            </g>
        </g>
        <g clip-path="url(#{clip2_id})">
            <g transform="{t2}">
                {self.inner_svg_content}
            </g>
        </g>
        {"".join(break_lines)}
    </g>
    """

    def export_dxf(
        self,
        filepath: str,
        layers: dict[str, str] | None = None,
        scale: float = 1.0,
        dxfversion: str = "R2010",
    ) -> None:
        """Export 1:1 CAM manufacturing DXF from this 2D view (outer profile, holes, centerlines)."""
        import ezdxf

        from build123d_techdraw.recognizers.features import recognize_holes

        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        doc = ezdxf.new(dxfversion=dxfversion)  # type: ignore[attr-defined]
        msp = doc.modelspace()

        layer_map = {
            "outer_profile": "CUT_OUTER",
            "inner_holes": "CUT_INNER",
            "centerlines": "MARK",
            "countersinks": "ETCH",
        }
        if layers:
            layer_map.update(layers)

        layer_colors = {
            layer_map["outer_profile"]: 7,
            layer_map["inner_holes"]: 1,
            layer_map["centerlines"]: 4,
            layer_map["countersinks"]: 2,
        }
        for lname in set(layer_map.values()):
            color = layer_colors.get(lname, 7)
            with contextlib.suppress(Exception):
                doc.layers.new(name=lname, dxfattribs={"color": color})

        shape = self.shape
        if shape is None:
            doc.saveas(filepath)
            return

        look_from, look_up = VIEW_VECTORS.get(self.view_type, ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)))
        dwg = bd.Drawing(shape, look_from=look_from, look_up=look_up, with_hidden=False)
        edges = dwg.visible_lines.edges()

        # Identify holes/countersinks via feature recognizer
        rec_holes = recognize_holes(shape)
        csink_radii: list[float] = []
        for rh in rec_holes:
            if rh.countersink_dia:
                csink_radii.append(round(rh.countersink_dia / 2.0, 2))
            if rh.counterbore_dia:
                csink_radii.append(round(rh.counterbore_dia / 2.0, 2))

        # Combine edges into closed wires
        wires: list[Any] = []
        try:
            wires = bd.Wire.combine(edges)
        except Exception:
            wires = []

        if wires:
            wires.sort(
                key=lambda w: bd.Face(w).area if getattr(w, "is_closed", False) else 0.0,
                reverse=True,
            )
            outer_wire = wires[0]
            inner_wires = wires[1:]

            for edge in outer_wire.edges():
                self._add_edge_to_dxf(msp, edge, layer_map["outer_profile"], scale)

            # Detect concentric circular wires where outer step is countersink/counterbore
            concentric_csinks: set[int] = set()
            circle_wires: list[tuple[int, tuple[float, float], float]] = []
            for idx, iw in enumerate(inner_wires):
                cw_edges = iw.edges()
                if (
                    len(cw_edges) == 1
                    and getattr(cw_edges[0], "geom_type", None) == bd.GeomType.CIRCLE
                ):
                    c = cw_edges[0].arc_center
                    circle_wires.append((idx, (float(c.X), float(c.Y)), float(cw_edges[0].radius)))

            for i in range(len(circle_wires)):
                for j in range(i + 1, len(circle_wires)):
                    idx_i, center_i, r_i = circle_wires[i]
                    idx_j, center_j, r_j = circle_wires[j]
                    dist = (
                        (center_i[0] - center_j[0]) ** 2 + (center_i[1] - center_j[1]) ** 2
                    ) ** 0.5
                    if dist < 0.5:
                        if r_i > r_j:
                            concentric_csinks.add(idx_i)
                        else:
                            concentric_csinks.add(idx_j)

            for idx, iw in enumerate(inner_wires):
                is_csink = idx in concentric_csinks
                if not is_csink:
                    for edge in iw.edges():
                        if getattr(edge, "geom_type", None) == bd.GeomType.CIRCLE:
                            r = round(float(edge.radius), 2)
                            if any(abs(r - cr) < 0.5 for cr in csink_radii):
                                is_csink = True
                                break
                target_l = layer_map["countersinks"] if is_csink else layer_map["inner_holes"]
                for edge in iw.edges():
                    self._add_edge_to_dxf(msp, edge, target_l, scale)
        else:
            for edge in edges:
                self._add_edge_to_dxf(msp, edge, layer_map["outer_profile"], scale)

        center_layer = layer_map["centerlines"]
        has_ann_centerlines = False
        for ann in getattr(self, "annotations", []):
            if hasattr(ann, "line_segments"):
                has_ann_centerlines = True
                for p0, p1 in ann.line_segments:
                    msp.add_line(
                        (p0[0] * scale, p0[1] * scale),
                        (p1[0] * scale, p1[1] * scale),
                        dxfattribs={"layer": center_layer},
                    )
            elif hasattr(ann, "center"):
                has_ann_centerlines = True
                cx, cy = ann.center
                r = getattr(ann, "radius", 5.0)
                msp.add_line(
                    ((cx - r - 2.0) * scale, cy * scale),
                    ((cx + r + 2.0) * scale, cy * scale),
                    dxfattribs={"layer": center_layer},
                )
                msp.add_line(
                    (cx * scale, (cy - r - 2.0) * scale),
                    (cx * scale, (cy + r + 2.0) * scale),
                    dxfattribs={"layer": center_layer},
                )

        if not has_ann_centerlines and rec_holes:
            for rh in rec_holes:
                c3d = rh.center_3d
                if self.projection_origin and self.proj_x and self.proj_y:
                    p = bd.Vector(c3d[0], c3d[1], c3d[2]) - self.projection_origin
                    cx2d = float(p.dot(self.proj_x))
                    cy2d = float(p.dot(self.proj_y))
                else:
                    cx2d, cy2d = float(c3d[0]), float(c3d[1])
                r = float(rh.diameter) / 2.0
                msp.add_line(
                    ((cx2d - r - 2.0) * scale, cy2d * scale),
                    ((cx2d + r + 2.0) * scale, cy2d * scale),
                    dxfattribs={"layer": center_layer},
                )
                msp.add_line(
                    (cx2d * scale, (cy2d - r - 2.0) * scale),
                    (cx2d * scale, (cy2d + r + 2.0) * scale),
                    dxfattribs={"layer": center_layer},
                )

        doc.saveas(filepath)

    @staticmethod
    def _add_edge_to_dxf(msp: Any, edge: Any, layer: str, scale: float) -> None:
        from build123d_techdraw.renderers.dxf import add_edge_to_dxf

        add_edge_to_dxf(msp, edge, layer=layer, scale=scale)

    def locate(self, center: tuple[float, float]) -> Self:
        """Reposition the view center on the sheet (in paper mm)."""
        dx = float(center[0]) - self.center_x
        dy = float(center[1]) - self.center_y
        self.center_x = float(center[0])
        self.center_y = float(center[1])
        self.min_x = self.center_x - self.width / 2.0
        self.max_x = self.center_x + self.width / 2.0
        self.min_y = self.center_y - self.height / 2.0
        self.max_y = self.center_y + self.height / 2.0
        self.bottom_annotation_y = self.max_y
        if self.view_label_pos is not None:
            self.view_label_pos = (self.view_label_pos[0] + dx, self.view_label_pos[1] + dy)
        self._update_transform()
        return self

    def scale_to(self, scale: float) -> Self:
        """Change the view scale factor and update its bounding box."""
        if self.scale != 0.0:
            ratio = scale / self.scale
            self.width *= ratio
            self.height *= ratio
        self.scale = scale
        self.min_x = self.center_x - self.width / 2.0
        self.max_x = self.center_x + self.width / 2.0
        self.min_y = self.center_y - self.height / 2.0
        self.max_y = self.center_y + self.height / 2.0
        self.bottom_annotation_y = self.max_y
        self.scale_label = format_iso_scale(scale)
        self._update_transform()
        return self

    def get_visual_bounds(
        self,
        include_annotations: bool = True,
        include_label: bool = True,
    ) -> tuple[float, float, float, float]:
        """Compute the visual bounding box (min_x, min_y, max_x, max_y) of this view in paper mm."""
        min_x = self.min_x
        min_y = self.min_y
        max_x = self.max_x
        max_y = self.max_y

        if include_annotations and hasattr(self, "annotations"):
            from build123d_techdraw.layout.linter import get_annotation_bbox_2d

            for ann in self.annotations:
                bbox = get_annotation_bbox_2d(ann)
                if bbox is not None:
                    min_x = min(min_x, bbox[0])
                    min_y = min(min_y, bbox[1])
                    max_x = max(max_x, bbox[2])
                    max_y = max(max_y, bbox[3])

        if include_label and (self.view_label or self.scale_label):
            label_y = getattr(self, "bottom_annotation_y", self.max_y) + 8.0
            max_y = max(max_y, label_y + 4.0)

        return (min_x, min_y, max_x, max_y)

    def align_to(
        self,
        other: "SheetView | tuple[Any, ...]",
        direction: str = "right",
        spacing: float = 25.0,
        include_annotations: bool = False,
        include_label: bool = True,
    ) -> Self:
        if isinstance(other, (tuple, list, dict)):
            _apply_align_to(self, other)
            return self
        """Align this view relative to another view on the sheet."""
        d = direction.lower()
        if include_annotations:
            other_min_x, other_min_y, other_max_x, other_max_y = other.get_visual_bounds(
                include_annotations=True, include_label=include_label
            )
            self_min_x, self_min_y, self_max_x, self_max_y = self.get_visual_bounds(
                include_annotations=True, include_label=include_label
            )
            self_left_extent = self.center_x - self_min_x
            self_right_extent = self_max_x - self.center_x
            self_top_extent = self.center_y - self_min_y
            self_bottom_extent = self_max_y - self.center_y

            if d in ("right", "east"):
                new_cx = other_max_x + spacing + self_left_extent
                new_cy = other.center_y
            elif d in ("left", "west"):
                new_cx = other_min_x - spacing - self_right_extent
                new_cy = other.center_y
            elif d in ("above", "top", "north"):
                new_cx = other.center_x
                new_cy = other_min_y - spacing - self_bottom_extent
            elif d in ("below", "bottom", "south"):
                new_cx = other.center_x
                new_cy = other_max_y + spacing + self_top_extent
            else:
                raise ValueError(
                    f"Unknown alignment direction '{direction}'. Use 'above', 'below', 'right', or 'left'."
                )
        else:
            if d in ("right", "east"):
                new_cx = other.max_x + spacing + self.width / 2.0
                new_cy = other.center_y
            elif d in ("left", "west"):
                new_cx = other.min_x - spacing - self.width / 2.0
                new_cy = other.center_y
            elif d in ("above", "top", "north"):
                new_cx = other.center_x
                new_cy = other.min_y - spacing - self.height / 2.0
            elif d in ("below", "bottom", "south"):
                new_cx = other.center_x
                new_cy = other.max_y + spacing + self.height / 2.0
            else:
                raise ValueError(
                    f"Unknown alignment direction '{direction}'. Use 'above', 'below', 'right', or 'left'."
                )
        return self.locate((new_cx, new_cy))

    @property
    def center(self) -> tuple[float, float]:
        """View center on the drawing sheet in paper mm."""
        return (self.center_x, self.center_y)

    def __getitem__(self, key: str) -> Any:
        """Provide dictionary-style access for backward compatibility."""
        attrs = {
            "view_type": self.view_type,
            "center_x": self.center_x,
            "center_y": self.center_y,
            "scale": self.scale,
            "width": self.width,
            "height": self.height,
            "min_x": self.min_x,
            "max_x": self.max_x,
            "min_y": self.min_y,
            "max_y": self.max_y,
            "origin_x": self.origin_x,
            "origin_y": self.origin_y,
        }
        if key in attrs:
            return attrs[key]
        raise KeyError(key)

    def to_sheet_x(self, model_x: float) -> float:
        """Transform 2D model X coordinate to sheet paper coordinate (mm)."""
        raw_x = self.center_x + ((model_x - self.origin_x) * self.scale)
        if self.breaks:
            for b in self.breaks:
                if b.axis == "X":
                    x0, x1 = min(b.interval), max(b.interval)
                    shift = (x1 - x0) * self.scale - b.gap
                    if model_x <= x0:
                        raw_x += shift / 2.0
                    elif model_x >= x1:
                        raw_x -= shift / 2.0
        return raw_x

    def to_sheet_y(self, model_y: float) -> float:
        """Transform 2D model Y coordinate to sheet paper coordinate (mm)."""
        raw_y = self.center_y - ((model_y - self.origin_y) * self.scale)
        if self.breaks:
            for b in self.breaks:
                if b.axis == "Y":
                    y0, y1 = min(b.interval), max(b.interval)
                    shift = (y1 - y0) * self.scale - b.gap
                    if model_y <= y0:
                        raw_y += shift / 2.0
                    elif model_y >= y1:
                        raw_y -= shift / 2.0
        return raw_y

    def to_sheet_coords(self, model_x: float, model_y: float) -> tuple[float, float]:
        """Transform 2D model (X, Y) to sheet paper (X, Y) coordinates (mm)."""
        return (self.to_sheet_x(model_x), self.to_sheet_y(model_y))

    def to_sheet_3d(self, x: float, y: float, z: float) -> tuple[float, float]:
        """Project a 3D part point (X, Y, Z) to 2D sheet paper coordinate space (mm)."""
        if self.projection_origin is None or self.proj_x is None or self.proj_y is None:
            return self.to_sheet_coords(x, y)
        p = bd.Vector(x, y, z) - self.projection_origin
        x2d = p.dot(self.proj_x)
        y2d = p.dot(self.proj_y)
        svg_x = x2d
        svg_y = -y2d
        sx = self.center_x + (svg_x - self.orig_cx) * self.scale
        sy = self.center_y + (svg_y - self.orig_cy) * self.scale
        if self.breaks:
            for b in self.breaks:
                if b.axis == "X":
                    x0, x1 = min(b.interval), max(b.interval)
                    shift = (x1 - x0) * self.scale - b.gap
                    m_x = x if abs(self.proj_x.X) > 0.9 else (y if abs(self.proj_x.Y) > 0.9 else z)
                    if m_x <= x0:
                        sx += shift / 2.0
                    elif m_x >= x1:
                        sx -= shift / 2.0
                elif b.axis == "Y":
                    y0, y1 = min(b.interval), max(b.interval)
                    shift = (y1 - y0) * self.scale - b.gap
                    m_y = z if abs(self.proj_y.Z) > 0.9 else (y if abs(self.proj_y.Y) > 0.9 else x)
                    if m_y <= y0:
                        sy += shift / 2.0
                    elif m_y >= y1:
                        sy -= shift / 2.0
        return (sx, sy)

    def dimension(
        self,
        target: Any,
        offset: float = -8.0,
        direction: str | None = None,
        text: str | None = None,
        tolerance: str | Tolerance | None = None,
        prefix: str = "",
        suffix: str = "",
    ) -> LinearDimension:
        """Add a dimension for a topological Edge, Face, or pair of points in this view.

        Automatically determines dimension direction and length from the model entity.
        """
        dim = LinearDimension(
            target_or_start=target,
            offset=offset,
            direction=direction,
            text=text,
            tolerance=tolerance,
            prefix=prefix,
            suffix=suffix,
            view=self,
        )
        self.add(dim)
        return dim

    def dimension_between(
        self,
        target1: Any,
        target2: Any,
        direction: str = "horizontal",
        offset: float = -8.0,
        text: str | None = None,
        tolerance: str | Tolerance | None = None,
        prefix: str = "",
        suffix: str = "",
    ) -> LinearDimension:
        """Add a linear dimension between two model vertices, edges, or points in this view."""
        from build123d_techdraw.annotations.resolver import is_topological_entity

        if (
            isinstance(target1, (tuple, list))
            and isinstance(target2, (tuple, list))
            and not is_topological_entity(target1)
            and not is_topological_entity(target2)
        ):
            if len(target1) == 2 and len(target2) == 2:
                s1 = self.to_sheet_coords(float(target1[0]), float(target1[1]))
                s2 = self.to_sheet_coords(float(target2[0]), float(target2[1]))
                d_lower = direction.lower() if direction else "horizontal"
                if text is None:
                    from build123d_techdraw.annotations.dimensions import format_dimension_value

                    if d_lower == "horizontal":
                        m_dist = abs(float(target2[0]) - float(target1[0]))
                    elif d_lower == "vertical":
                        m_dist = abs(float(target2[1]) - float(target1[1]))
                    else:
                        m_dist = math.hypot(
                            float(target2[0]) - float(target1[0]),
                            float(target2[1]) - float(target1[1]),
                        )
                    text = format_dimension_value(m_dist)
                dim = LinearDimension(
                    start=s1,
                    end=s2,
                    direction=direction,
                    offset=offset,
                    text=text,
                    tolerance=tolerance,
                    prefix=prefix,
                    suffix=suffix,
                    view=self,
                )
                self.add(dim)
                return dim
            elif len(target1) == 3 and len(target2) == 3:
                s1 = self.to_sheet_3d(float(target1[0]), float(target1[1]), float(target1[2]))
                s2 = self.to_sheet_3d(float(target2[0]), float(target2[1]), float(target2[2]))
                if text is None:
                    from build123d_techdraw.annotations.dimensions import format_dimension_value

                    dx = float(target2[0]) - float(target1[0])
                    dy = float(target2[1]) - float(target1[1])
                    dz = float(target2[2]) - float(target1[2])
                    d_lower = direction.lower() if direction else "horizontal"
                    if d_lower == "horizontal":
                        m_dist = abs(dx)
                    elif d_lower == "vertical":
                        m_dist = abs(dz if abs(dz) > 1e-4 else dy)
                    else:
                        m_dist = math.hypot(dx, dy, dz)
                    text = format_dimension_value(m_dist)
                dim = LinearDimension(
                    start=s1,
                    end=s2,
                    direction=direction,
                    offset=offset,
                    text=text,
                    tolerance=tolerance,
                    prefix=prefix,
                    suffix=suffix,
                    view=self,
                )
                self.add(dim)
                return dim

        dim = LinearDimension(
            target_or_start=target1,
            target2_or_end=target2,
            direction=direction,
            offset=offset,
            text=text,
            tolerance=tolerance,
            prefix=prefix,
            suffix=suffix,
            view=self,
        )
        self.add(dim)
        return dim

    def dim_horizontal(
        self,
        model_x1: float,
        model_x2: float,
        model_y: float,
        offset_y: float = -8.0,
        text: str | None = None,
        tolerance: str | None = None,
        prefix: str = "",
        suffix: str = "",
    ) -> LinearDimension:
        """Add a horizontal dimension between model_x1 and model_x2 at model_y."""
        sx1 = self.to_sheet_x(model_x1)
        sx2 = self.to_sheet_x(model_x2)
        sy = self.to_sheet_y(model_y)
        dim = LinearDimension(
            start=(sx1, sy),
            end=(sx2, sy),
            offset=offset_y,
            dim_type=DimensionType.HORIZONTAL,
            text=text,
            tolerance=tolerance,
            prefix=prefix,
            suffix=suffix,
            view=self,
        )
        self.add(dim)
        if offset_y > 0:
            self.bottom_annotation_y = max(self.bottom_annotation_y, sy + offset_y + 4.0)
        return dim

    def dim_vertical(
        self,
        model_y1: float,
        model_y2: float,
        model_x: float,
        offset_x: float = -10.0,
        text: str | None = None,
        tolerance: str | None = None,
        prefix: str = "",
        suffix: str = "",
    ) -> LinearDimension:
        """Add a vertical dimension between model_y1 and model_y2 at model_x."""
        sy1 = self.to_sheet_y(model_y1)
        sy2 = self.to_sheet_y(model_y2)
        sx = self.to_sheet_x(model_x)
        dim = LinearDimension(
            start=(sx, sy1),
            end=(sx, sy2),
            offset=offset_x,
            dim_type=DimensionType.VERTICAL,
            text=text,
            tolerance=tolerance,
            prefix=prefix,
            suffix=suffix,
            view=self,
        )
        self.add(dim)
        return dim

    def dim_aligned(
        self,
        p1_model: tuple[float, float],
        p2_model: tuple[float, float],
        offset: float = 8.0,
        text: str | None = None,
        tolerance: str | None = None,
        prefix: str = "",
        suffix: str = "",
    ) -> LinearDimension:
        """Add an aligned dimension between two model points."""
        s1 = self.to_sheet_coords(p1_model[0], p1_model[1])
        s2 = self.to_sheet_coords(p2_model[0], p2_model[1])
        dim = LinearDimension(
            start=s1,
            end=s2,
            offset=offset,
            dim_type=DimensionType.ALIGNED,
            text=text,
            tolerance=tolerance,
            prefix=prefix,
            suffix=suffix,
            view=self,
        )
        self.add(dim)
        return dim

    def add_overall_horizontal(
        self,
        offset_y: float = -8.0,
        text: str | None = None,
        tolerance: str | None = None,
        prefix: str = "",
        suffix: str = "",
    ) -> LinearDimension:
        """Add an overall horizontal dimension spanning view's bounding box."""
        y_ref = self.min_y if offset_y < 0 else self.max_y
        dim = LinearDimension(
            start=(self.min_x, y_ref),
            end=(self.max_x, y_ref),
            offset=offset_y,
            dim_type=DimensionType.HORIZONTAL,
            text=text,
            tolerance=tolerance,
            prefix=prefix,
            suffix=suffix,
            view=self,
        )
        self.add(dim)
        if offset_y > 0:
            self.bottom_annotation_y = max(self.bottom_annotation_y, y_ref + offset_y + 4.0)
        return dim

    def add_overall_vertical(
        self,
        offset_x: float = -10.0,
        text: str | None = None,
        tolerance: str | None = None,
        prefix: str = "",
        suffix: str = "",
    ) -> LinearDimension:
        """Add an overall vertical dimension spanning view's bounding box."""
        x_ref = self.min_x if offset_x < 0 else self.max_x
        dim = LinearDimension(
            start=(x_ref, self.min_y),
            end=(x_ref, self.max_y),
            offset=offset_x,
            dim_type=DimensionType.VERTICAL,
            text=text,
            tolerance=tolerance,
            prefix=prefix,
            suffix=suffix,
            view=self,
        )
        self.add(dim)
        return dim

    def dim_radial(
        self,
        center_model: tuple[float, float],
        radius_model: float,
        angle_deg: float = 45.0,
        text: str | None = None,
        tolerance: str | None = None,
    ) -> RadialDimension:
        """Add a radial dimension pointing to an arc defined in model space."""
        cx, cy = self.to_sheet_coords(center_model[0], center_model[1])
        dim = RadialDimension(
            center=(cx, cy),
            radius=radius_model * self.scale,
            angle_deg=angle_deg,
            text=text,
            tolerance=tolerance,
            view=self,
        )
        self.add(dim)
        return dim

    def dim_diameter(
        self,
        center_model: tuple[float, float],
        diameter_model: float,
        angle_deg: float = 45.0,
        text: str | None = None,
        tolerance: str | None = None,
    ) -> DiameterDimension:
        """Add a diameter dimension across a hole/cylinder defined in model space."""
        cx, cy = self.to_sheet_coords(center_model[0], center_model[1])
        dim = DiameterDimension(
            center=(cx, cy),
            diameter=diameter_model * self.scale,
            angle_deg=angle_deg,
            text=text,
            tolerance=tolerance,
            view=self,
        )
        self.add(dim)
        return dim

    def dim_angular(
        self,
        target1: Any,
        target2: Any,
        radius: float = 20.0,
        text: str | None = None,
        tolerance: str | None = None,
        prefix: str = "",
        suffix: str = "°",
    ) -> AngularDimension:
        """Add an angular dimension between two edges, rays, or faces in this view."""
        dim = AngularDimension(
            target1=target1,
            target2=target2,
            radius=radius,
            text=text,
            tolerance=tolerance,
            prefix=prefix,
            suffix=suffix,
            view=self,
        )
        return dim

    def dim_ordinate(
        self,
        target: Any,
        datum_coord: float = 0.0,
        direction: str = "horizontal",
        jog: float = 0.0,
        extension_length: float = 12.0,
        prefix: str = "",
        suffix: str = "",
    ) -> OrdinateDimension:
        """Add an ordinate dimension point in this view."""
        dim = OrdinateDimension(
            target=target,
            datum_coord=datum_coord,
            direction=direction,
            jog=jog,
            extension_length=extension_length,
            prefix=prefix,
            suffix=suffix,
            view=self,
        )
        return dim

    def chamfer_callout(
        self,
        target: Any,
        width: float | None = None,
        angle_deg: float = 45.0,
        style: str = "iso",
        quantity: int | None = None,
        elbow_dx: float = 15.0,
        elbow_dy: float = -12.0,
    ) -> ChamferCallout:
        """Add an ISO/ASME chamfer callout leader note."""
        cc = ChamferCallout(
            target_or_edge=target,
            width=width,
            angle_deg=angle_deg,
            style=style,
            quantity=quantity,
            elbow_offset=(elbow_dx, elbow_dy),
            view=self,
        )
        return cc

    def weld_symbol(
        self,
        target: Any,
        weld_type: WeldType | str = WeldType.FILLET,
        size: str | float | None = "a5",
        length: str | float | None = None,
        pitch: str | float | None = None,
        all_around: bool = False,
        field_weld: bool = False,
        tail_text: str | None = None,
        arrow_side: bool = True,
        elbow_dx: float = 16.0,
        elbow_dy: float = -12.0,
    ) -> WeldingSymbol:
        """Add an ISO 2553 welding annotation."""
        ws = WeldingSymbol(
            target=target,
            weld_type=weld_type,
            size=size,
            length=length,
            pitch=pitch,
            all_around=all_around,
            field_weld=field_weld,
            tail_text=tail_text,
            arrow_side=arrow_side,
            elbow_offset=(elbow_dx, elbow_dy),
            view=self,
        )
        return ws

    def centermark(
        self,
        model_x: float,
        model_y: float,
        size: float = 6.0,
    ) -> CenterMark:
        """Add a centermark crosshair at (model_x, model_y)."""
        sc = self.to_sheet_coords(model_x, model_y)
        cm = CenterMark(center=sc, size=size, view=self)
        self.add(cm)
        return cm

    def centerline_horizontal(
        self,
        model_y: float = 0.0,
        model_x1: float | None = None,
        model_x2: float | None = None,
        overhang: float = 4.0,
    ) -> Centerline:
        """Draw an ISO centerline across the horizontal axis at model_y."""
        sy = self.to_sheet_y(model_y)
        sx1 = (
            self.to_sheet_x(model_x1) - overhang if model_x1 is not None else self.min_x - overhang
        )
        sx2 = (
            self.to_sheet_x(model_x2) + overhang if model_x2 is not None else self.max_x + overhang
        )
        cl = Centerline(start=(sx1, sy), end=(sx2, sy), view=self)
        self.add(cl)
        return cl

    def centerline_vertical(
        self,
        model_x: float = 0.0,
        model_y1: float | None = None,
        model_y2: float | None = None,
        overhang: float = 4.0,
    ) -> Centerline:
        """Draw an ISO centerline across the vertical axis at model_x."""
        sx = self.to_sheet_x(model_x)
        sy1 = (
            self.to_sheet_y(model_y1) + overhang if model_y1 is not None else self.max_y + overhang
        )
        sy2 = (
            self.to_sheet_y(model_y2) - overhang if model_y2 is not None else self.min_y - overhang
        )
        cl = Centerline(start=(sx, min(sy1, sy2)), end=(sx, max(sy1, sy2)), view=self)
        self.add(cl)
        return cl

    def bolt_circle(
        self,
        center_model: tuple[float, float] = (0.0, 0.0),
        diameter_model: float = 50.0,
        num_holes: int = 4,
        start_angle_deg: float = 0.0,
    ) -> BoltCircle:
        """Add a bolt pitch circle diameter (PCD) annotation in model space."""
        sc = self.to_sheet_coords(center_model[0], center_model[1])
        bc = BoltCircle(
            center=sc,
            diameter=diameter_model * self.scale,
            num_holes=num_holes,
            start_angle_deg=start_angle_deg,
            view=self,
        )
        self.add(bc)
        return bc

    def leader(
        self,
        model_x: float,
        model_y: float,
        lines: list[str] | str,
        elbow_dx: float = 15.0,
        elbow_dy: float = -12.0,
        shelf_length: float = 20.0,
        align_right: bool | None = None,
        terminator: str = "dot",
    ) -> LeaderNote:
        """Add a leader note pointing to (model_x, model_y) on the part."""
        target = self.to_sheet_coords(model_x, model_y)
        line_list = [lines] if isinstance(lines, str) else list(lines)
        note = LeaderNote(
            target=target,
            lines=line_list,
            elbow_offset=(elbow_dx, elbow_dy),
            shelf_length=shelf_length,
            align_right=align_right,
            terminator=terminator,
            view=self,
        )
        self.add(note)
        return note

    def leader_3d(
        self,
        x: float,
        y: float,
        z: float,
        lines: list[str] | str,
        elbow_dx: float = 15.0,
        elbow_dy: float = -12.0,
        shelf_length: float = 20.0,
        align_right: bool | None = None,
        terminator: str = "dot",
    ) -> LeaderNote:
        """Add a leader note pointing to a 3D part point (x, y, z)."""
        target = self.to_sheet_3d(x, y, z)
        line_list = [lines] if isinstance(lines, str) else list(lines)
        note = LeaderNote(
            target=target,
            lines=line_list,
            elbow_offset=(elbow_dx, elbow_dy),
            shelf_length=shelf_length,
            align_right=align_right,
            terminator=terminator,
            view=self,
        )
        self.add(note)
        return note

    def balloon(
        self,
        item_no: int,
        model_x: float,
        model_y: float,
        balloon_x: float,
        balloon_y: float,
    ) -> BOMBalloon:
        """Add a BOM balloon callout pointing to (model_x, model_y)."""
        target = self.to_sheet_coords(model_x, model_y)
        balloon = BOMBalloon(
            item_no=item_no,
            target=target,
            balloon_center=(balloon_x, balloon_y),
            view=self,
        )
        self.add(balloon)
        return balloon

    def add_gdt_frame(
        self,
        model_x: float,
        model_y: float,
        characteristic: GDTCharacteristic,
        tolerance_value: str,
        datum_a: str | None = None,
        datum_b: str | None = None,
        datum_c: str | None = None,
        leader_offset: tuple[float, float] | None = None,
    ) -> FeatureControlFrame:
        """Add a GD&T Feature Control Frame near (model_x, model_y), optionally with a leader line."""
        target_pos = self.to_sheet_coords(model_x, model_y)
        if leader_offset is not None:
            pos = (target_pos[0] + leader_offset[0], target_pos[1] + leader_offset[1])
            leader_target: tuple[float, float] | None = target_pos
        else:
            pos = target_pos
            leader_target = None

        fcf = FeatureControlFrame(
            position=pos,
            characteristic=characteristic,
            tolerance_value=tolerance_value,
            datum_a=datum_a,
            datum_b=datum_b,
            datum_c=datum_c,
            leader_target=leader_target,
            view=self,
        )
        self.add(fcf)
        return fcf

    def add_datum_symbol(
        self,
        model_x: float,
        model_y: float,
        label: str = "A",
        direction: str = "up",
    ) -> DatumFeatureSymbol:
        """Add an ISO datum feature identifier symbol at (model_x, model_y)."""
        target = self.to_sheet_coords(model_x, model_y)
        sym = DatumFeatureSymbol(target=target, label=label, direction=direction, view=self)
        self.add(sym)
        return sym

    def add_surface_finish(
        self,
        model_x: float,
        model_y: float,
        ra_value: str = "Ra 3.2",
        material_removal: MaterialRemoval = MaterialRemoval.REQUIRED,
        process: str | None = None,
    ) -> SurfaceFinishSymbol:
        """Add an ISO 1302 surface roughness symbol at (model_x, model_y)."""
        pos = self.to_sheet_coords(model_x, model_y)
        sym = SurfaceFinishSymbol(
            position=pos,
            ra_value=ra_value,
            material_removal=material_removal,
            process=process,
            view=self,
        )
        self.add(sym)
        return sym

    def hole_callout(
        self,
        model_x: float,
        model_y: float,
        diameter: float,
        depth: float | str | None = None,
        countersink_dia: float | None = None,
        countersink_angle: float = 90.0,
        counterbore_dia: float | None = None,
        counterbore_depth: float | None = None,
        quantity: int | None = None,
        tolerance: str | Tolerance | None = None,
        elbow_dx: float = 15.0,
        elbow_dy: float = -12.0,
    ) -> HoleCallout:
        """Add a standard engineering hole callout at (model_x, model_y)."""
        target = self.to_sheet_coords(model_x, model_y)
        hc = HoleCallout(
            center=target,
            diameter=diameter,
            depth=depth,
            countersink_dia=countersink_dia,
            countersink_angle=countersink_angle,
            counterbore_dia=counterbore_dia,
            counterbore_depth=counterbore_depth,
            quantity=quantity,
            tolerance=str(tolerance) if tolerance is not None else None,
            elbow_offset=(elbow_dx, elbow_dy),
            view=self,
        )
        return hc

    def thread_callout(
        self,
        model_x: float,
        model_y: float,
        thread: str = "M5x0.8",
        depth: float | str | None = "THRU",
        class_fit: str | None = None,
        quantity: int | None = None,
        elbow_dx: float = 15.0,
        elbow_dy: float = -12.0,
    ) -> ThreadCallout:
        """Add a standard engineering thread callout at (model_x, model_y)."""
        target = self.to_sheet_coords(model_x, model_y)
        tc = ThreadCallout(
            center=target,
            thread=thread,
            depth=depth,
            class_fit=class_fit,
            quantity=quantity,
            elbow_offset=(elbow_dx, elbow_dy),
            view=self,
        )
        return tc


def _apply_align_to(view: Any, align_spec: Any) -> Any:
    """Apply an align_to specification (tuple, dict, or SheetView) to a view."""
    if align_spec is None:
        return view
    if isinstance(align_spec, SheetView):
        return view.align_to(align_spec)
    if isinstance(align_spec, (tuple, list)):
        if len(align_spec) == 1:
            return view.align_to(align_spec[0])
        if len(align_spec) == 2:
            return view.align_to(align_spec[0], direction=str(align_spec[1]))
        if len(align_spec) >= 3:
            return view.align_to(
                align_spec[0], direction=str(align_spec[1]), spacing=float(align_spec[2])
            )
    if isinstance(align_spec, dict):
        return view.align_to(
            align_spec["other"],
            direction=str(align_spec.get("direction", "right")),
            spacing=float(align_spec.get("spacing", 25.0)),
        )
    raise TypeError(
        "Invalid align_to specification. Expected (other_view, direction[, spacing]) or SheetView."
    )


def project_shape_to_view(
    shape: bd.Solid | bd.Compound | Any,
    view_type: ViewType,
    center_x: float,
    center_y: float,
    scale: float = 1.0,
    show_hidden: bool = True,
    view_label: str | None = None,
    rotation_angle_deg: float = 0.0,
    view_label_y: float | None = None,
    view_label_offset_y: float = 7.0,
    model_origin: tuple[float, float] | None = None,
) -> SheetView:
    """Project a 3D build123d shape into a 2D orthographic or isometric SheetView."""
    look_from, look_up = VIEW_VECTORS.get(view_type, ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)))

    # Use build123d.Drawing to project B-Rep edges
    dwg = bd.Drawing(shape, look_from=look_from, look_up=look_up, with_hidden=show_hidden)

    exporter = bd.ExportSVG(unit=bd.Unit.MM)

    # Filter out hidden lines that coincide with visible edges and render hidden layer first
    if show_hidden and len(dwg.hidden_lines.edges()) > 0:
        v_edges = dwg.visible_lines.edges()
        clean_hidden_edges: list[Any] = []
        for h_edge in dwg.hidden_lines.edges():
            h_bb = h_edge.bounding_box()
            coincides = False
            for v_edge in v_edges:
                v_bb = v_edge.bounding_box()
                if (
                    abs(h_bb.min.X - v_bb.min.X) < 1e-2
                    and abs(h_bb.max.X - v_bb.max.X) < 1e-2
                    and abs(h_bb.min.Y - v_bb.min.Y) < 1e-2
                    and abs(h_bb.max.Y - v_bb.max.Y) < 1e-2
                ):
                    coincides = True
                    break
            if not coincides:
                clean_hidden_edges.append(h_edge)

        if clean_hidden_edges:
            clean_hidden_compound = (
                clean_hidden_edges[0]
                if len(clean_hidden_edges) == 1
                else bd.Compound(children=clean_hidden_edges)
            )
            exporter.add_layer(
                "hidden", line_color="gray", line_weight=0.18, line_type=bd.LineType.DASHED
            )
            exporter.add_shape(clean_hidden_compound, layer="hidden")

    # Visible lines added AFTER hidden lines so visible lines render ON TOP in SVG DOM
    exporter.add_layer("visible", line_color="black", line_weight=0.35)
    exporter.add_shape(dwg.visible_lines, layer="visible")

    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as tf:
        tmp_path = tf.name

    try:
        exporter.write(tmp_path)
        with open(tmp_path, encoding="utf-8") as f:
            raw_svg = f.read()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    vb_m = re.search(r'viewBox="([^"]+)"', raw_svg)
    viewbox_str = vb_m.group(1) if vb_m else "0 0 100 100"
    vb_parts = [float(p) for p in viewbox_str.split()]
    vb_min_x, vb_min_y, vb_w, vb_h = vb_parts

    orig_cx = vb_min_x + vb_w / 2.0
    orig_cy = vb_min_y + vb_h / 2.0

    view_id = f"view_{view_type.value}_{int(center_x)}_{int(center_y)}"
    inner_m = re.search(r"<svg[^>]*>(.*)</svg>", raw_svg, flags=re.DOTALL)
    inner_content = inner_m.group(1).strip() if inner_m else raw_svg

    # Clean XML namespaces
    inner_content = re.sub(
        r"</?[a-zA-Z0-9_]+:",
        lambda m: "</" if m.group(0).startswith("</") else "<",
        inner_content,
    )
    inner_content = re.sub(r'\s+xmlns(?::[a-zA-Z0-9_]+)?="[^"]*"', "", inner_content)

    # Substitute styling with CSS custom properties
    inner_content = inner_content.replace('stroke="rgb(0,0,0)"', 'stroke="var(--line-visible)"')
    inner_content = inner_content.replace(
        'stroke="rgb(128,128,128)"', 'stroke="var(--line-hidden)"'
    )
    inner_content = inner_content.replace('id="visible"', 'id="layer_visible" class="line-visible"')
    inner_content = inner_content.replace('id="hidden"', 'id="layer_hidden" class="line-hidden"')

    # Compute exact geometric bounds from projected 2D geometry (pure CAD coordinates, no stroke inflation)
    geom_shapes: list[Any] = [dwg.visible_lines]
    if show_hidden and len(dwg.hidden_lines.edges()) > 0:
        geom_shapes.append(dwg.hidden_lines)

    try:
        combined_geom = (
            geom_shapes[0] if len(geom_shapes) == 1 else bd.Compound(children=geom_shapes)
        )
        geom_bb = combined_geom.bounding_box()
        exact_w = float(geom_bb.size.X)
        exact_h = float(geom_bb.size.Y)
    except Exception:
        exact_w = max(0.0, vb_w - 0.35)
        exact_h = max(0.0, vb_h - 0.35)

    rot_str = f" rotate({rotation_angle_deg})" if rotation_angle_deg != 0 else ""

    transform = (
        f"translate({center_x:.2f}, {center_y:.2f}) "
        f"scale({scale:.4f}, {scale:.4f}){rot_str} "
        f"translate({-orig_cx:.2f}, {-orig_cy:.2f})"
    )

    svg_view_group = f"""
    <!-- View: {view_type.value.upper()} at ({center_x}, {center_y}) scale={scale} -->
    <g id="{view_id}" class="sheet-view" transform="{transform}">
        {inner_content}
    </g>
    """

    lbl_pos = None
    scale_str = None
    if view_label:
        lbl_y = (
            view_label_y
            if view_label_y is not None
            else (center_y + (vb_h * scale / 2.0) + view_label_offset_y)
        )
        scale_str = format_iso_scale(scale)
        lbl_pos = (center_x, lbl_y)

    if model_origin is not None:
        orig_u, orig_v = model_origin
    else:
        try:
            bb = shape.bounding_box()
            bcx = (bb.min.X + bb.max.X) / 2.0
            bcy = (bb.min.Y + bb.max.Y) / 2.0
            bcz = (bb.min.Z + bb.max.Z) / 2.0
            if view_type == ViewType.FRONT:
                orig_u, orig_v = bcx, bcz
            elif view_type == ViewType.TOP:
                orig_u, orig_v = bcx, bcy
            elif view_type == ViewType.RIGHT:
                orig_u, orig_v = bcy, bcz
            elif view_type == ViewType.LEFT:
                orig_u, orig_v = -bcy, bcz
            elif view_type == ViewType.BOTTOM:
                orig_u, orig_v = bcx, -bcy
            elif view_type == ViewType.REAR:
                orig_u, orig_v = -bcx, bcz
            else:
                orig_u, orig_v = 0.0, 0.0
        except Exception:
            orig_u, orig_v = 0.0, 0.0

    try:
        proj_origin = shape.center()
        p_dir = bd.Vector(look_from).normalized()
        p_x = bd.Vector(look_up).normalized().cross(p_dir)
        p_y = p_dir.cross(p_x)
    except Exception:
        proj_origin = None
        p_x = None
        p_y = None

    view_res = SheetView(
        view_type=view_type,
        center_x=center_x,
        center_y=center_y,
        scale=scale,
        width=exact_w * scale,
        height=exact_h * scale,
        model_origin=(orig_u, orig_v),
        projection_origin=proj_origin,
        proj_x=p_x,
        proj_y=p_y,
        orig_cx=orig_cx,
        orig_cy=orig_cy,
        svg_content=svg_view_group,
        view_label=view_label,
        view_label_pos=lbl_pos,
        scale_label=scale_str,
        shape=shape,
    )
    view_res.inner_svg_content = inner_content
    view_res.rotation_angle_deg = rotation_angle_deg
    return view_res


def project_shape_to_svg_fragment(
    shape: bd.Solid | bd.Compound | Any,
    view_type: ViewType = ViewType.ISOMETRIC,
    center: tuple[float, float] = (0.0, 0.0),
    scale: float = 1.0,
    show_hidden: bool = False,
    rotation_angle_deg: float = 0.0,
) -> str:
    """Project a 3D shape into a standalone SVG ``<g>`` fragment without an outer ``<svg>`` wrapper."""
    view = project_shape_to_view(
        shape=shape,
        view_type=view_type,
        center_x=center[0],
        center_y=center[1],
        scale=scale,
        show_hidden=show_hidden,
        rotation_angle_deg=rotation_angle_deg,
    )
    return view.svg_content.strip()


def project_section_to_view(
    shape: bd.Solid | bd.Compound | Any,
    cutting_plane: bd.Plane,
    center_x: float,
    center_y: float,
    scale: float = 1.0,
    view_label: str = "SECTION A-A",
    view_label_y: float | None = None,
    view_label_offset_y: float = 7.0,
    model_origin: tuple[float, float] | None = None,
    show_background: bool = True,
    hatch: str = "steel",
    hatch_pitch: float = 4.0,
    hatch_angle: float = 45.0,
) -> SheetView:
    """Project a 2D section slice through 3D shape with ISO/ANSI material hatching and background visible lines."""
    sec = bd.section(shape, section_by=cutting_plane)
    sec_2d = cutting_plane.to_local_coords(sec)

    exporter = bd.ExportSVG(unit=bd.Unit.MM)

    # Optional background visible geometry behind the section plane
    if show_background:
        try:
            look_from = cutting_plane.z_dir
            look_up = cutting_plane.y_dir
            split_half = bd.split(shape, bisect_by=cutting_plane, keep=bd.Keep.BOTTOM)
            dwg_bg = bd.Drawing(split_half, look_from=look_from, look_up=look_up, with_hidden=False)
            if len(dwg_bg.visible_lines.edges()) > 0:
                exporter.add_layer("visible_bg", line_color="gray", line_weight=0.25)
                exporter.add_shape(dwg_bg.visible_lines, layer="visible_bg")
        except Exception:
            pass

    exporter.add_layer("visible", line_color="black", line_weight=0.5, fill_color=None)
    sec_faces = sorted(
        sec_2d.faces(),
        key=lambda f: (round(f.center().X, 4), round(f.center().Y, 4), round(f.area, 4)),
    )
    exporter.add_shape(bd.Compound(sec_faces) if sec_faces else sec_2d, layer="visible")

    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as tf:
        tmp_path = tf.name

    try:
        exporter.write(tmp_path)
        with open(tmp_path, encoding="utf-8") as f:
            raw_svg = f.read()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    vb_m = re.search(r'viewBox="([^"]+)"', raw_svg)
    viewbox_str = vb_m.group(1) if vb_m else "0 0 100 100"
    vb_parts = [float(p) for p in viewbox_str.split()]
    vb_min_x, vb_min_y, vb_w, vb_h = vb_parts

    orig_cx = vb_min_x + vb_w / 2.0
    orig_cy = vb_min_y + vb_h / 2.0

    view_id = f"view_section_{int(center_x)}_{int(center_y)}"
    inner_m = re.search(r"<svg[^>]*>(.*)</svg>", raw_svg, flags=re.DOTALL)
    inner_content = inner_m.group(1).strip() if inner_m else raw_svg

    inner_content = re.sub(
        r"</?[a-zA-Z0-9_]+:",
        lambda m: "</" if m.group(0).startswith("</") else "<",
        inner_content,
    )
    inner_content = re.sub(r'\s+xmlns(?::[a-zA-Z0-9_]+)?="[^"]*"', "", inner_content)
    inner_content = inner_content.replace('stroke="rgb(0,0,0)"', 'stroke="var(--line-visible)"')
    inner_content = inner_content.replace(
        'stroke="rgb(128,128,128)"', 'stroke="var(--line-visible)"'
    )

    h_mat = hatch.lower().strip()
    if hatch_pitch == 4.0 and hatch_angle == 45.0 and h_mat in ("steel", "ansi31"):
        hatch_id = "sectionHatch"
    else:
        hatch_id = f"hatch_{h_mat}_{int(hatch_pitch)}_{int(hatch_angle)}"
    inner_content = inner_content.replace(
        'fill="none"', f'fill="url(#{hatch_id})" class="section-hatch-fill"'
    )

    try:
        geom_bb = sec_2d.bounding_box()
        exact_w = float(geom_bb.size.X)
        exact_h = float(geom_bb.size.Y)
    except Exception:
        exact_w = max(0.0, vb_w - 0.35)
        exact_h = max(0.0, vb_h - 0.35)

    transform = (
        f"translate({center_x:.2f}, {center_y:.2f}) "
        f"scale({scale:.4f}, {scale:.4f}) "
        f"translate({-orig_cx:.2f}, {-orig_cy:.2f})"
    )

    svg_view_group = f"""
    <!-- View: SECTION at ({center_x}, {center_y}) scale={scale} -->
    <g id="{view_id}" class="sheet-view" transform="{transform}">
        {inner_content}
    </g>
    """

    lbl_y = (
        view_label_y
        if view_label_y is not None
        else (center_y + (vb_h * scale / 2.0) + view_label_offset_y)
    )
    scale_str = format_iso_scale(scale)

    orig_u, orig_v = model_origin if model_origin is not None else (0.0, 0.0)

    view_res = SheetView(
        view_type=ViewType.SECTION,
        center_x=center_x,
        center_y=center_y,
        scale=scale,
        width=exact_w * scale,
        height=exact_h * scale,
        model_origin=(orig_u, orig_v),
        orig_cx=orig_cx,
        orig_cy=orig_cy,
        svg_content=svg_view_group,
        view_label=view_label,
        view_label_pos=(center_x, lbl_y),
        scale_label=scale_str,
        shape=shape,
    )
    view_res.inner_svg_content = inner_content
    view_res.hatch = h_mat
    view_res.hatch_pitch = hatch_pitch
    view_res.hatch_angle = hatch_angle
    view_res.hatch_id = hatch_id
    return view_res


def project_detail_to_view(
    parent_view: SheetView,
    focus_target: Any = None,
    radius: float = 12.0,
    scale: float = 2.0,
    center_x: float = 150.0,
    center_y: float = 150.0,
    view_label: str | None = None,
    label_letter: str = "A",
    view_label_y: float | None = None,
    view_label_offset_y: float = 7.0,
    shape: str = "circle",
    region_center: Any = None,
    label: str | None = None,
) -> SheetView:
    """Project a magnified detail view of a circular or rectangular region from parent view."""
    import html

    from build123d_techdraw.annotations.resolver import resolve_point_to_sheet

    actual_target = region_center if region_center is not None else focus_target
    if actual_target is None:
        actual_target = (parent_view.center_x, parent_view.center_y)

    focus_sheet = resolve_point_to_sheet(actual_target, parent_view)

    effective_letter = label if label is not None else label_letter
    effective_label = view_label if view_label is not None else f"DETAIL {effective_letter}"
    shape_type = str(shape).lower()

    # Detail callout indicator on parent view
    parent_callout_r = radius * parent_view.scale
    if shape_type == "rect":
        callout_tag_x = focus_sheet[0] + parent_callout_r
        callout_tag_y = focus_sheet[1] - parent_callout_r
        rw = parent_callout_r * 2.0
        rh = parent_callout_r * 2.0
        rx = focus_sheet[0] - parent_callout_r
        ry = focus_sheet[1] - parent_callout_r
        callout_shape_svg = f'<rect x="{rx:.2f}" y="{ry:.2f}" width="{rw:.2f}" height="{rh:.2f}" class="dim-line detail-callout-circle" stroke-width="0.3" stroke-dasharray="3,2" fill="none"/>'
    else:
        callout_tag_x = focus_sheet[0] + parent_callout_r * 0.707
        callout_tag_y = focus_sheet[1] - parent_callout_r * 0.707
        callout_shape_svg = f'<circle cx="{focus_sheet[0]:.2f}" cy="{focus_sheet[1]:.2f}" r="{parent_callout_r:.2f}" class="dim-line detail-callout-circle" stroke-width="0.3" stroke-dasharray="3,2" fill="none"/>'

    callout_svg = f"""<g class="detail-callout">
        {callout_shape_svg}
        <line x1="{callout_tag_x:.2f}" y1="{callout_tag_y:.2f}" x2="{callout_tag_x + 5.0:.2f}" y2="{callout_tag_y - 5.0:.2f}" class="dim-line" stroke-width="0.3"/>
        <circle cx="{callout_tag_x + 8.0:.2f}" cy="{callout_tag_y - 8.0:.2f}" r="3.0" class="border-sub" fill="none"/>
        <text x="{callout_tag_x + 8.0:.2f}" y="{callout_tag_y - 6.8:.2f}" class="tb-title" text-anchor="middle" font-size="3.2">{html.escape(effective_letter)}</text>
    </g>"""
    parent_view.svg_content += f"\n    {callout_svg}"

    paper_r = radius * scale
    view_id = f"detail_{effective_letter}_{int(center_x)}_{int(center_y)}"
    clip_id = f"clip_{view_id}"

    focus_svg_x = (focus_sheet[0] - parent_view.center_x) / parent_view.scale + parent_view.orig_cx
    focus_svg_y = (focus_sheet[1] - parent_view.center_y) / parent_view.scale + parent_view.orig_cy

    inner_content = parent_view.inner_svg_content

    transform = (
        f"translate({center_x:.2f}, {center_y:.2f}) "
        f"scale({scale:.4f}, {scale:.4f}) "
        f"translate({-focus_svg_x:.2f}, {-focus_svg_y:.2f})"
    )

    if shape_type == "rect":
        clip_def = f'<rect x="{center_x - paper_r:.2f}" y="{center_y - paper_r:.2f}" width="{paper_r * 2.0:.2f}" height="{paper_r * 2.0:.2f}"/>'
        boundary_svg = f'<rect x="{center_x - paper_r:.2f}" y="{center_y - paper_r:.2f}" width="{paper_r * 2.0:.2f}" height="{paper_r * 2.0:.2f}" class="border-main" stroke-width="0.5" fill="none"/>'
    else:
        clip_def = f'<circle cx="{center_x:.2f}" cy="{center_y:.2f}" r="{paper_r:.2f}"/>'
        boundary_svg = f'<circle cx="{center_x:.2f}" cy="{center_y:.2f}" r="{paper_r:.2f}" class="border-main" stroke-width="0.5" fill="none"/>'

    svg_view_group = f"""
    <!-- Detail View: {effective_letter} at ({center_x}, {center_y}) scale={scale} -->
    <defs>
        <clipPath id="{clip_id}">
            {clip_def}
        </clipPath>
    </defs>
    <g id="{view_id}" class="sheet-view detail-view" clip-path="url(#{clip_id})">
        <g transform="{transform}">
            {inner_content}
        </g>
    </g>
    {boundary_svg}
    """

    lbl_y = view_label_y if view_label_y is not None else (center_y + paper_r + view_label_offset_y)
    scale_str = format_iso_scale(scale)

    view_res = SheetView(
        view_type=ViewType.DETAIL,
        center_x=center_x,
        center_y=center_y,
        scale=scale,
        width=paper_r * 2.0,
        height=paper_r * 2.0,
        model_origin=parent_view.model_origin,
        projection_origin=parent_view.projection_origin,
        proj_x=parent_view.proj_x,
        proj_y=parent_view.proj_y,
        orig_cx=focus_svg_x,
        orig_cy=focus_svg_y,
        svg_content=svg_view_group,
        view_label=effective_label,
        view_label_pos=(center_x, lbl_y),
        scale_label=scale_str,
        shape=parent_view.shape,
    )
    view_res.inner_svg_content = inner_content
    view_res.parent_view = parent_view
    view_res.detail_shape = shape_type
    return view_res


def project_auxiliary_view(
    shape: bd.Solid | bd.Compound | Any,
    face: bd.Face | tuple[float, float, float] | Any,
    center_x: float,
    center_y: float,
    scale: float = 1.0,
    show_hidden: bool = True,
    view_label: str = "AUXILIARY VIEW",
    view_label_y: float | None = None,
    view_label_offset_y: float = 7.0,
) -> SheetView:
    """Project an auxiliary view normal to an inclined face."""
    import build123d as bd

    if isinstance(face, bd.Face):
        n = face.normal_at()
        look_from = (-float(n.X), -float(n.Y), -float(n.Z))
    elif isinstance(face, (tuple, list)) and len(face) == 3:
        look_from = (-float(face[0]), -float(face[1]), -float(face[2]))
    else:
        look_from = (0.0, -1.0, 0.0)

    lf_vec = bd.Vector(look_from).normalized()
    ref_up = bd.Vector(0, 0, 1) if abs(lf_vec.Z) < 0.9 else bd.Vector(0, 1, 0)
    look_up_vec = ref_up - lf_vec * (ref_up.dot(lf_vec))
    look_up_vec = bd.Vector(0, 1, 0) if look_up_vec.length < 1e-4 else look_up_vec.normalized()
    look_up = (float(look_up_vec.X), float(look_up_vec.Y), float(look_up_vec.Z))

    dwg = bd.Drawing(shape, look_from=look_from, look_up=look_up, with_hidden=show_hidden)
    exporter = bd.ExportSVG(unit=bd.Unit.MM)
    if show_hidden and len(dwg.hidden_lines.edges()) > 0:
        exporter.add_layer(
            "hidden", line_color="gray", line_weight=0.18, line_type=bd.LineType.DASHED
        )
        exporter.add_shape(dwg.hidden_lines, layer="hidden")
    exporter.add_layer("visible", line_color="black", line_weight=0.35)
    exporter.add_shape(dwg.visible_lines, layer="visible")

    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as tf:
        tmp_path = tf.name
    try:
        exporter.write(tmp_path)
        with open(tmp_path, encoding="utf-8") as f:
            raw_svg = f.read()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    vb_m = re.search(r'viewBox="([^"]+)"', raw_svg)
    viewbox_str = vb_m.group(1) if vb_m else "0 0 100 100"
    vb_parts = [float(p) for p in viewbox_str.split()]
    vb_min_x, vb_min_y, vb_w, vb_h = vb_parts

    orig_cx = vb_min_x + vb_w / 2.0
    orig_cy = vb_min_y + vb_h / 2.0
    view_id = f"view_auxiliary_{int(center_x)}_{int(center_y)}"

    inner_m = re.search(r"<svg[^>]*>(.*)</svg>", raw_svg, flags=re.DOTALL)
    inner_content = inner_m.group(1).strip() if inner_m else raw_svg
    inner_content = inner_content.replace('stroke="rgb(0,0,0)"', 'stroke="var(--line-visible)"')
    inner_content = inner_content.replace(
        'stroke="rgb(128,128,128)"', 'stroke="var(--line-hidden)"'
    )

    transform = (
        f"translate({center_x:.2f}, {center_y:.2f}) "
        f"scale({scale:.4f}, {scale:.4f}) "
        f"translate({-orig_cx:.2f}, {-orig_cy:.2f})"
    )

    svg_view_group = f"""
    <!-- View: AUXILIARY at ({center_x}, {center_y}) scale={scale} -->
    <g id="{view_id}" class="sheet-view auxiliary-view" transform="{transform}">
        {inner_content}
    </g>
    """

    lbl_y = (
        view_label_y
        if view_label_y is not None
        else (center_y + (vb_h * scale / 2.0) + view_label_offset_y)
    )
    scale_str = format_iso_scale(scale)

    view_res = SheetView(
        view_type=ViewType.AUXILIARY,
        center_x=center_x,
        center_y=center_y,
        scale=scale,
        width=vb_w * scale,
        height=vb_h * scale,
        model_origin=(0.0, 0.0),
        orig_cx=orig_cx,
        orig_cy=orig_cy,
        svg_content=svg_view_group,
        view_label=view_label,
        view_label_pos=(center_x, lbl_y),
        scale_label=scale_str,
        shape=shape,
    )
    view_res.inner_svg_content = inner_content
    return view_res
