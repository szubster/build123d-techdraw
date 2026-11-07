"""High-level declarative multiview layout helpers and automated assembly BOM tools."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import build123d as bd

from build123d_techdraw.annotations.callouts import BOMBalloon
from build123d_techdraw.core.scale import (
    find_closest_iso_scale,
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
    auto_dimensions: bool = False,
    auto_centerlines: bool = False,
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

    bbox = shape.bounding_box()
    sx = float(bbox.size.X)
    sy = float(bbox.size.Y)
    sz = float(bbox.size.Z)

    # Diagonal approximation for isometric unscaled size
    iso_diag = math.sqrt(sx * sx + sy * sy + sz * sz) * 0.75

    # Determine unscaled column and row dimensions
    # In standard layouts:
    # Column 1: Front / Top / Bottom (width = sx)
    # Column 2: Right / Left / Isometric (width = max(sy, iso_diag))
    has_col1 = any(
        v in [ViewType.FRONT, ViewType.TOP, ViewType.BOTTOM, ViewType.REAR] for v in views
    )
    has_col2 = any(v in [ViewType.RIGHT, ViewType.LEFT, ViewType.ISOMETRIC] for v in views)

    has_row1 = any(v in [ViewType.TOP, ViewType.ISOMETRIC] for v in views)
    has_row2 = any(
        v in [ViewType.FRONT, ViewType.RIGHT, ViewType.LEFT, ViewType.REAR] for v in views
    )

    unscaled_w_col1 = sx if has_col1 else 0.0
    unscaled_w_col2 = (
        max(
            sy if any(v in [ViewType.RIGHT, ViewType.LEFT] for v in views) else 0.0,
            iso_diag if ViewType.ISOMETRIC in views else 0.0,
        )
        if has_col2
        else 0.0
    )

    unscaled_h_row1 = (
        max(sy if ViewType.TOP in views else 0.0, iso_diag if ViewType.ISOMETRIC in views else 0.0)
        if has_row1
        else 0.0
    )
    unscaled_h_row2 = (
        max(
            sz
            if any(
                v in [ViewType.FRONT, ViewType.RIGHT, ViewType.LEFT, ViewType.REAR] for v in views
            )
            else 0.0,
            0.0,
        )
        if has_row2
        else 0.0
    )

    num_cols = (1 if has_col1 else 0) + (1 if has_col2 else 0)
    num_rows = (1 if has_row1 else 0) + (1 if has_row2 else 0)

    total_unscaled_w = unscaled_w_col1 + unscaled_w_col2
    total_unscaled_h = unscaled_h_row1 + unscaled_h_row2

    # Available printable drawing area on sheet
    sheet_w = sheet.width_mm - sheet.left_margin - sheet.right_margin
    sheet_h = sheet.height_mm - sheet.top_margin - sheet.bottom_margin

    if center is None:
        # Default center offset slightly upwards/leftwards to leave room for title block at bottom-right
        center_x = sheet.left_margin + sheet_w * 0.46
        center_y = sheet.top_margin + sheet_h * 0.46
    else:
        center_x, center_y = center

    # Auto scale computation
    if isinstance(scale, str) and scale.lower() == "auto":
        avail_w = sheet_w * 0.85 - (num_cols - 1) * spacing
        avail_h = sheet_h * 0.80 - (num_rows - 1) * spacing
        raw_scale_w = avail_w / total_unscaled_w if total_unscaled_w > 0 else 1.0
        raw_scale_h = avail_h / total_unscaled_h if total_unscaled_h > 0 else 1.0
        calculated_scale = find_closest_iso_scale(min(raw_scale_w, raw_scale_h))
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

    col1_cx = min_x + w_col1 / 2.0
    col2_cx = min_x + w_col1 + spacing + w_col2 / 2.0 if num_cols > 1 else col1_cx

    row1_cy = min_y + h_row1 / 2.0
    row2_cy = min_y + h_row1 + spacing + h_row2 / 2.0 if num_rows > 1 else row1_cy

    layout_obj = MultiviewLayout(
        scale=calculated_scale,
        scale_str=scale_str,
        sheet=sheet,
    )

    is_third_angle = layout.lower() != "first_angle"
    sheet.title_info.third_angle = is_third_angle
    sheet.title_info.scale = scale_str

    # Positioning mapping
    pos_map: dict[ViewType, tuple[float, float]] = {}

    if is_third_angle:
        # Third Angle:
        # Row 1 (top): Top view (Col 1), Isometric (Col 2)
        # Row 2 (bottom): Front view (Col 1), Right view (Col 2)
        if ViewType.TOP in views:
            pos_map[ViewType.TOP] = (col1_cx, row1_cy)
        if ViewType.FRONT in views:
            pos_map[ViewType.FRONT] = (col1_cx, row2_cy if has_row1 else row1_cy)
        if ViewType.RIGHT in views:
            pos_map[ViewType.RIGHT] = (col2_cx, row2_cy if has_row1 else row1_cy)
        if ViewType.LEFT in views:
            pos_map[ViewType.LEFT] = (
                col1_cx - w_col1 / 2.0 - spacing - w_col2 / 2.0,
                row2_cy if has_row1 else row1_cy,
            )
        if ViewType.ISOMETRIC in views:
            pos_map[ViewType.ISOMETRIC] = (col2_cx, row1_cy)
    else:
        # First Angle:
        # Row 1 (top): Front view (Col 1), Isometric (Col 2)
        # Row 2 (bottom): Top view (Col 1), Right/Left view
        if ViewType.FRONT in views:
            pos_map[ViewType.FRONT] = (col1_cx, row1_cy)
        if ViewType.TOP in views:
            pos_map[ViewType.TOP] = (col1_cx, row2_cy if has_row2 else row1_cy)
        if ViewType.RIGHT in views:
            pos_map[ViewType.RIGHT] = (col2_cx, row1_cy)
        if ViewType.ISOMETRIC in views:
            pos_map[ViewType.ISOMETRIC] = (col2_cx, row2_cy if has_row2 else row1_cy)

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
        layout_obj.add_overall_dimensions()

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
                        r = float(edge.radius)
                        v_obj.centermark(float(c.X), float(c.Y), size=max(r * 1.6, 6.0))
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
