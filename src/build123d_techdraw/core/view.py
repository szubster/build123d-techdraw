"""SheetView representations, view projection mathematics, and coordinate transformations."""

import os
import re
import tempfile
from enum import Enum
from typing import Any

import build123d as bd

from build123d_techdraw.core.scale import format_iso_scale


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
    ):
        self.view_type = view_type
        self.center_x = center_x
        self.center_y = center_y
        self.scale = scale
        self.width = width
        self.height = height
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
        self.view_label = view_label
        self.view_label_pos = view_label_pos
        self.scale_label = scale_label

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
        return self.center_x + ((model_x - self.origin_x) * self.scale)

    def to_sheet_y(self, model_y: float) -> float:
        """Transform 2D model Y coordinate to sheet paper coordinate (mm)."""
        return self.center_y - ((model_y - self.origin_y) * self.scale)

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
        return (sx, sy)


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
    exporter.add_layer("visible", line_color="black", line_weight=0.35)
    exporter.add_shape(dwg.visible_lines, layer="visible")

    if show_hidden and len(dwg.hidden_lines.edges()) > 0:
        exporter.add_layer(
            "hidden", line_color="gray", line_weight=0.18, line_type=bd.LineType.DASHED
        )
        exporter.add_shape(dwg.hidden_lines, layer="hidden")

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

    rot_str = f" rotate({rotation_angle_deg})" if rotation_angle_deg != 0 else ""
    transform = (
        f"translate({center_x:.2f}, {center_y:.2f}) "
        f"scale({scale:.4f}, {scale:.4f}){rot_str} "
        f"translate({-orig_cx:.2f}, {-orig_cy:.2f})"
    )

    svg_view_group = f"""
    <!-- View: {view_type.value.upper()} at ({center_x}, {center_y}) scale={scale} -->
    <g id="{view_id}" transform="{transform}">
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

    return SheetView(
        view_type=view_type,
        center_x=center_x,
        center_y=center_y,
        scale=scale,
        width=vb_w * scale,
        height=vb_h * scale,
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
    )


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
) -> SheetView:
    """Project a 2D section slice through 3D shape with 45-degree engineering cross-hatching."""
    sec = bd.section(shape, section_by=cutting_plane)
    sec_2d = cutting_plane.to_local_coords(sec)

    exporter = bd.ExportSVG(unit=bd.Unit.MM)
    exporter.add_layer("visible", line_color="black", line_weight=0.5, fill_color=None)
    exporter.add_shape(sec_2d, layer="visible")

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
    inner_content = inner_content.replace('fill="none"', 'fill="url(#sectionHatch)"')

    transform = (
        f"translate({center_x:.2f}, {center_y:.2f}) "
        f"scale({scale:.4f}, {scale:.4f}) "
        f"translate({-orig_cx:.2f}, {-orig_cy:.2f})"
    )

    svg_view_group = f"""
    <!-- View: SECTION at ({center_x}, {center_y}) scale={scale} -->
    <g id="{view_id}" transform="{transform}">
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

    return SheetView(
        view_type=ViewType.SECTION,
        center_x=center_x,
        center_y=center_y,
        scale=scale,
        width=vb_w * scale,
        height=vb_h * scale,
        model_origin=(orig_u, orig_v),
        orig_cx=orig_cx,
        orig_cy=orig_cy,
        svg_content=svg_view_group,
        view_label=view_label,
        view_label_pos=(center_x, lbl_y),
        scale_label=scale_str,
    )
