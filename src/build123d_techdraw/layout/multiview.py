"""High-level declarative multiview layout helpers and automated assembly BOM tools."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import build123d as bd

from build123d_techdraw.annotations.callouts import BOMBalloon
from build123d_techdraw.core.scale import (
    find_closest_iso_scale,
    format_iso_ratio,
    format_iso_scale,
)
from build123d_techdraw.core.title_block import BOMItem
from build123d_techdraw.core.view import VIEW_VECTORS, SheetView, ViewType

if TYPE_CHECKING:
    from build123d_techdraw.core.sheet import DrawingSheet


@dataclass
class MultiviewLayout:
    """Container holding views created by add_standard_multiview."""

    scale: float
    scale_str: str
    sheet: DrawingSheet
    front: SheetView | None = None
    top: SheetView | None = None
    right: SheetView | None = None
    bottom: SheetView | None = None
    left: SheetView | None = None
    rear: SheetView | None = None
    isometric: SheetView | None = None
    views: dict[ViewType, SheetView] = field(default_factory=dict)

    def add_overall_dimensions(
        self,
        offset: float = 12.0,
        tolerance: str | None = None,
    ) -> list[Any]:
        """Automatically add overall envelope dimensions (Length, Height, Depth) to the views."""
        dims: list[Any] = []
        if self.front is not None:
            # Length (horizontal) and Height (vertical) on Front view
            dim_w = self.front.add_overall_horizontal(offset_y=offset, tolerance=tolerance)
            dim_h = self.front.add_overall_vertical(offset_x=-offset, tolerance=tolerance)
            dims.extend([dim_w, dim_h])

        if self.top is not None:
            # Depth on Top view (vertical axis in Top view)
            dim_d = self.top.add_overall_vertical(offset_x=offset, tolerance=tolerance)
            dims.append(dim_d)
        elif self.right is not None:
            # Depth on Right view (horizontal axis in Right view)
            dim_d = self.right.add_overall_horizontal(offset_y=offset, tolerance=tolerance)
            dims.append(dim_d)

        return dims

    def auto_dimension(
        self,
        shape: Any | None = None,
        tolerance: str | None = None,
    ) -> list[Any]:
        """Run smart collision-avoiding auto-dimensioning across all views in this layout."""
        from build123d_techdraw.layout.auto_dimension import auto_dimension_views

        target_shape = shape
        if target_shape is None:
            for v in self.views.values():
                target_shape = getattr(v, "shape", None)
                if target_shape is not None:
                    break
        if target_shape is None:
            return []
        return auto_dimension_views(
            sheet=self.sheet,
            shape=target_shape,
            views_map=self.views,
            tolerance=tolerance,
        )


def projected_view_extent(shape: Any, view_type: ViewType) -> tuple[float, float]:
    """Return the exact unscaled 2D (width, height) of ``shape`` as drawn in ``view_type``.

    The shape is expressed in the view's projection frame (the same ``look_from`` /
    ``look_up`` pair from ``VIEW_VECTORS`` that ``SheetView`` uses), so the X/Y
    extent of its bounding box equals the extent of the projected drawing.
    """
    look_from, look_up = VIEW_VECTORS.get(view_type, ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)))
    view_dir = bd.Vector(look_from).normalized()
    x_dir = bd.Vector(look_up).cross(view_dir).normalized()
    frame = bd.Plane(origin=(0.0, 0.0, 0.0), x_dir=x_dir, z_dir=view_dir)
    try:
        local_bb = frame.to_local_coords(shape).bounding_box()
        return (float(local_bb.size.X), float(local_bb.size.Y))
    except Exception:
        # Conservative fallback: project the corners of the axis-aligned bounding box
        bb = shape.bounding_box()
        y_dir = view_dir.cross(x_dir)
        us: list[float] = []
        vs: list[float] = []
        for cx in (bb.min.X, bb.max.X):
            for cy in (bb.min.Y, bb.max.Y):
                for cz in (bb.min.Z, bb.max.Z):
                    p = bd.Vector(cx, cy, cz)
                    us.append(p.dot(x_dir))
                    vs.append(p.dot(y_dir))
        return (max(us) - min(us), max(vs) - min(vs))


def add_standard_multiview(
    sheet: DrawingSheet,
    shape: bd.Solid | bd.Compound | Any,
    views: list[ViewType] | None = None,
    layout: str = "third_angle",
    scale: float | str = "auto",
    spacing: float = 25.0,
    center: tuple[float, float] | None = None,
    show_hidden: bool = False,
    show_labels: bool = True,
    show_scale_label: bool = False,
    auto_dimensions: bool | str = False,
    auto_centerlines: bool = False,
    enforce_iso_scale: bool = True,
    infer_threads: bool = False,
) -> MultiviewLayout:
    """Generate a standard orthographic multi-view layout on a drawing sheet.

    Parameters
    ----------
    sheet:
        The target DrawingSheet.
    shape:
        The 3D build123d part or assembly.
    views:
        List of ViewTypes to generate. Defaults to Front, Top, Right, Isometric.
    layout:
        'third_angle' (standard ISO / ANSI) or 'first_angle'.
    scale:
        Explicit float scale (e.g. 0.5 for 1:2) or 'auto' to fit available sheet area.
    spacing:
        Spacing between views in paper millimeters.
    center:
        Center of the multiview group in sheet coordinates. Defaults to sheet center.
    show_hidden:
        Whether hidden lines are enabled for orthographic views.
    show_scale_label:
        Whether view title labels display individual scale.
    auto_dimensions:
        Whether to add overall bounding envelope dimensions automatically.
    """
    if views is None:
        views = [ViewType.FRONT, ViewType.TOP, ViewType.RIGHT, ViewType.ISOMETRIC]

    is_third_angle = layout.lower() != "first_angle"

    # Exact unscaled 2D extent (width, height) of every requested view, measured in
    # the same projection frame that SheetView uses when drawing it.
    extents = {v: projected_view_extent(shape, v) for v in views}

    # Grid membership. Column 1 holds the principal views stacked over/under the
    # front view; column 2 holds the side view and the pictorial (isometric) view.
    col1_views = {ViewType.FRONT, ViewType.TOP, ViewType.BOTTOM, ViewType.REAR}
    col2_views = {ViewType.RIGHT, ViewType.LEFT, ViewType.ISOMETRIC}
    if is_third_angle:
        # Upper row: Top + Isometric; lower row: Front + Right (+ Left)
        upper_views = {ViewType.TOP, ViewType.ISOMETRIC}
        lower_views = {ViewType.FRONT, ViewType.RIGHT, ViewType.LEFT, ViewType.REAR}
    else:
        # Upper row: Front + Right (+ Left); lower row: Top + Isometric
        upper_views = {ViewType.FRONT, ViewType.RIGHT, ViewType.LEFT, ViewType.REAR}
        lower_views = {ViewType.TOP, ViewType.ISOMETRIC}

    def _max_extent(members: set[ViewType], axis: int) -> float:
        return max((extents[v][axis] for v in views if v in members), default=0.0)

    has_col1 = any(v in col1_views for v in views)
    has_col2 = any(v in col2_views for v in views)
    has_row1 = any(v in upper_views for v in views)
    has_row2 = any(v in lower_views for v in views)

    unscaled_w_col1 = _max_extent(col1_views, 0)
    unscaled_w_col2 = _max_extent(col2_views, 0)
    unscaled_h_row1 = _max_extent(upper_views, 1)
    unscaled_h_row2 = _max_extent(lower_views, 1)

    num_cols = (1 if has_col1 else 0) + (1 if has_col2 else 0)
    num_rows = (1 if has_row1 else 0) + (1 if has_row2 else 0)

    total_unscaled_w = unscaled_w_col1 + unscaled_w_col2
    total_unscaled_h = unscaled_h_row1 + unscaled_h_row2

    # Exclusion zone at bottom-right for title block and optional stacked BOM table
    has_bom = bool(getattr(sheet, "bom_items", None)) or any(
        getattr(c, "label", None) for c in extract_assembly_components(shape)
    )
    bom_h = (5.5 + len(sheet.bom_items) * 5.2) if getattr(sheet, "bom_items", None) else 20.0
    bottom_excl = (
        sheet.bottom_margin
        + 36.0
        + (
            bom_h + 2.0
            if has_bom
            and getattr(sheet, "bom_position", "above_title_block") == "above_title_block"
            else 0.0
        )
        + 16.0  # clearance for dimension lines and view labels
    )
    top_excl = sheet.top_margin + 14.0

    avail_w = sheet.width_mm - sheet.left_margin - sheet.right_margin
    avail_h = sheet.height_mm - bottom_excl - top_excl

    if center is None:
        center_x = sheet.left_margin + avail_w * 0.44
        center_y = top_excl + avail_h / 2.0
    else:
        center_x, center_y = center

    # Auto scale computation
    if isinstance(scale, str) and scale.lower() == "auto":
        net_w = avail_w * 0.85 - (num_cols - 1) * spacing
        net_h = avail_h - (num_rows - 1) * spacing
        raw_scale_w = net_w / total_unscaled_w if total_unscaled_w > 0 else 1.0
        raw_scale_h = net_h / total_unscaled_h if total_unscaled_h > 0 else 1.0
        raw_scale = min(raw_scale_w, raw_scale_h)
        calculated_scale = find_closest_iso_scale(raw_scale) if enforce_iso_scale else raw_scale
    else:
        calculated_scale = float(scale)

    scale_str = format_iso_scale(calculated_scale)

    # Scaled dimensions
    w_col1 = unscaled_w_col1 * calculated_scale
    w_col2 = unscaled_w_col2 * calculated_scale
    h_row1 = unscaled_h_row1 * calculated_scale
    h_row2 = unscaled_h_row2 * calculated_scale

    total_group_w = w_col1 + (spacing if num_cols > 1 else 0.0) + w_col2
    total_group_h = h_row1 + (spacing if num_rows > 1 else 0.0) + h_row2

    min_x = center_x - total_group_w / 2.0
    min_y = center_y - total_group_h / 2.0

    if has_col1 and has_col2:
        col1_cx = min_x + w_col1 / 2.0
        col2_cx = min_x + w_col1 + spacing + w_col2 / 2.0
    else:
        col1_cx = col2_cx = center_x

    if has_row1 and has_row2:
        row1_cy = min_y + h_row1 / 2.0
        row2_cy = min_y + h_row1 + spacing + h_row2 / 2.0
    else:
        row1_cy = row2_cy = center_y

    # Ensure lower-row labels and dimensions do not encroach on bottom-right exclusion zone
    lower_h = h_row2 if has_row2 else h_row1
    lower_bottom = row2_cy + lower_h / 2.0 + 16.0
    max_allowed_lower_bottom = sheet.height_mm - bottom_excl + 10.0
    if lower_bottom > max_allowed_lower_bottom:
        dy_shift = lower_bottom - max_allowed_lower_bottom
        row1_cy -= dy_shift
        row2_cy -= dy_shift

    layout_obj = MultiviewLayout(
        scale=calculated_scale,
        scale_str=scale_str,
        sheet=sheet,
    )

    sheet.title_info.third_angle = is_third_angle
    sheet.title_info.scale = format_iso_ratio(calculated_scale)

    # Positioning mapping (row1 = upper row, row2 = lower row)
    pos_map: dict[ViewType, tuple[float, float]] = {}

    if is_third_angle:
        if ViewType.TOP in views:
            pos_map[ViewType.TOP] = (col1_cx, row1_cy)
        if ViewType.FRONT in views:
            pos_map[ViewType.FRONT] = (col1_cx, row2_cy)
        if ViewType.RIGHT in views:
            pos_map[ViewType.RIGHT] = (col2_cx, row2_cy)
        if ViewType.LEFT in views:
            pos_map[ViewType.LEFT] = (
                col1_cx - w_col1 / 2.0 - spacing - w_col2 / 2.0,
                row2_cy,
            )
        if ViewType.ISOMETRIC in views:
            pos_map[ViewType.ISOMETRIC] = (col2_cx, row1_cy)
    else:
        if ViewType.FRONT in views:
            pos_map[ViewType.FRONT] = (col1_cx, row1_cy)
        if ViewType.TOP in views:
            pos_map[ViewType.TOP] = (col1_cx, row2_cy)
        if ViewType.RIGHT in views:
            pos_map[ViewType.RIGHT] = (col2_cx, row1_cy)
        if ViewType.ISOMETRIC in views:
            pos_map[ViewType.ISOMETRIC] = (col2_cx, row2_cy)

    for v_type in views:
        v_center = pos_map.get(v_type, (center_x, center_y))
        v_hidden = show_hidden if v_type != ViewType.ISOMETRIC else False
        v_label = v_type.value.upper() if show_labels else None
        v_obj = sheet.add_view(
            shape,
            view_type=v_type,
            center=v_center,
            scale=calculated_scale,
            show_hidden=v_hidden,
            view_label=v_label,
        )
        if show_scale_label:
            v_obj.scale_label = scale_str
        layout_obj.views[v_type] = v_obj
        if v_type == ViewType.FRONT:
            layout_obj.front = v_obj
        elif v_type == ViewType.TOP:
            layout_obj.top = v_obj
        elif v_type == ViewType.RIGHT:
            layout_obj.right = v_obj
        elif v_type == ViewType.LEFT:
            layout_obj.left = v_obj
        elif v_type == ViewType.BOTTOM:
            layout_obj.bottom = v_obj
        elif v_type == ViewType.REAR:
            layout_obj.rear = v_obj
        elif v_type == ViewType.ISOMETRIC:
            layout_obj.isometric = v_obj

    if auto_dimensions:
        from build123d_techdraw.layout.auto_dimension import auto_dimension_views

        auto_dimension_views(
            sheet=sheet,
            shape=shape,
            views_map=layout_obj.views,
            infer_threads=infer_threads,
        )

    if auto_centerlines:
        # 1. Add centermarks to circular projected holes/bosses on 2D views
        for v_type, v_obj in layout_obj.views.items():
            if v_type == ViewType.ISOMETRIC:
                continue
            look_from, look_up = VIEW_VECTORS.get(v_type, ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)))
            try:
                dwg_v = bd.Drawing(shape, look_from=look_from, look_up=look_up)
                for edge in dwg_v.visible_lines.edges():
                    if edge.geom_type == bd.GeomType.CIRCLE:
                        c = edge.arc_center
                        paper_r = float(edge.radius) * v_obj.scale
                        # Centermark arms extend 2.0 mm beyond circle perimeter per ISO 128 / ASME Y14.2
                        cm_size = (paper_r + 2.0) * 2.0
                        v_obj.centermark(float(c.X), float(c.Y), size=cm_size)
            except Exception:
                pass

        # 2. Add vertical centerlines to cylindrical features in Front and Right views
        try:
            cyl_faces = [f for f in shape.faces() if f.geom_type == bd.GeomType.CYLINDER]
            for f in cyl_faces:
                axis = f.axis_of_rotation
                if axis is None:
                    continue
                f_bb = f.bounding_box()
                if abs(axis.direction.Z) > 0.8:
                    if layout_obj.front is not None:
                        layout_obj.front.centerline_vertical(
                            model_x=float(axis.position.X),
                            model_y1=float(f_bb.min.Z),
                            model_y2=float(f_bb.max.Z),
                            overhang=4.0,
                        )
                    if layout_obj.right is not None:
                        layout_obj.right.centerline_vertical(
                            model_x=float(axis.position.Y),
                            model_y1=float(f_bb.min.Z),
                            model_y2=float(f_bb.max.Z),
                            overhang=4.0,
                        )
        except Exception:
            pass

    return layout_obj


def extract_assembly_components(shape: Any) -> list[Any]:
    """Recursively extract child parts or solids from a compound or assembly."""
    if hasattr(shape, "children") and shape.children:
        parts: list[Any] = []
        for child in shape.children:
            if hasattr(child, "children") and child.children:
                parts.extend(extract_assembly_components(child))
            else:
                parts.append(child)
        return parts
    elif hasattr(shape, "solids") and shape.solids():
        return list(shape.solids())
    return [shape]


def generate_assembly_bom(assembly: bd.Compound | list[Any]) -> list[BOMItem]:
    """Generate structured BOM table entries by grouping and counting assembly components.

    Parts are grouped by their .label (or shape class name if unlabeled).
    """
    components = (
        extract_assembly_components(assembly) if not isinstance(assembly, list) else assembly
    )

    # Group by label
    grouped: dict[str, list[Any]] = {}
    for comp in components:
        lbl = getattr(comp, "label", "") or getattr(comp, "name", "")
        if not lbl:
            lbl = type(comp).__name__
        grouped.setdefault(lbl, []).append(comp)

    bom_items: list[BOMItem] = []
    for item_idx, (name, items) in enumerate(grouped.items(), start=1):
        first = items[0]
        raw_mat = (
            getattr(first, "material_spec", None)
            or getattr(first, "material_name", None)
            or getattr(first, "material", None)
        )
        if raw_mat is not None:
            if hasattr(raw_mat, "name"):
                comp_mat = str(raw_mat.name).replace("_", " ").upper()
            else:
                comp_mat = str(raw_mat)
        else:
            comp_mat = "STANDARD"
        comp_part_no = getattr(first, "part_no", "") or f"PART-{item_idx:03d}"
        bom_items.append(
            BOMItem(
                item_no=item_idx,
                qty=len(items),
                part_no=comp_part_no,
                description=name,
                material=comp_mat,
            )
        )
    return bom_items


def add_assembly_bom_and_balloons(
    sheet: DrawingSheet,
    assembly: bd.Compound,
    target_view: SheetView,
    balloon_distance: float = 30.0,
    balloon_radius: float = 4.5,
    balloon_terminator: str = "dot",
) -> tuple[list[BOMItem], list[BOMBalloon]]:
    """Automatically populate sheet BOM items and place radiating balloon callouts on target view.

    Parameters
    ----------
    sheet:
        The target DrawingSheet.
    assembly:
        The Compound assembly containing child components.
    target_view:
        The SheetView (typically Isometric or Front) to place balloon callouts on.
    balloon_distance:
        Radial offset in paper mm from the component center to the balloon circle.
    balloon_radius:
        Radius of the balloon circle in paper mm.
    balloon_terminator:
        Terminator type at target: 'dot' (inside body) or 'arrow' (on edge).
    """
    components = extract_assembly_components(assembly)
    bom_items = generate_assembly_bom(assembly)
    sheet.bom_items = bom_items

    # Map item_no to the first representative component
    grouped: dict[str, list[Any]] = {}
    for comp in components:
        lbl = getattr(comp, "label", "") or getattr(comp, "name", "")
        if not lbl:
            lbl = type(comp).__name__
        grouped.setdefault(lbl, []).append(comp)

    balloons: list[BOMBalloon] = []
    num_items = len(bom_items)

    for idx, item in enumerate(bom_items):
        part_list = grouped.get(item.description, [])
        if not part_list:
            continue
        rep_part = part_list[0]

        try:
            pt = rep_part.center()
            target_pt = target_view.to_sheet_3d(float(pt.X), float(pt.Y), float(pt.Z))
        except Exception:
            # Fallback to bounding box center
            bbox = rep_part.bounding_box()
            cx = (float(bbox.min.X) + float(bbox.max.X)) / 2.0
            cy = (float(bbox.min.Y) + float(bbox.max.Y)) / 2.0
            cz = (float(bbox.min.Z) + float(bbox.max.Z)) / 2.0
            target_pt = target_view.to_sheet_3d(cx, cy, cz)

        # Distribute balloons evenly around the perimeter of target_view
        angle = (2.0 * math.pi * idx) / max(num_items, 1)
        bx = target_pt[0] + math.cos(angle) * balloon_distance
        by = target_pt[1] + math.sin(angle) * balloon_distance

        balloon = sheet.add_balloon(
            item_no=item.item_no,
            target=target_pt,
            balloon_center=(bx, by),
            radius=balloon_radius,
            terminator=balloon_terminator,
        )
        balloons.append(balloon)

    return bom_items, balloons
