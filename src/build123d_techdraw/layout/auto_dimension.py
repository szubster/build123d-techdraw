"""Smart collision-avoiding auto-dimension placement and canonical view de-duplication.

Implements ISO 129-1 / ASME Y14.5 dimension stacking (10 mm first offset from
geometry contour, 7 mm step between parallel dimension lines) and assigns each
recognized 3D feature to a single canonical orthographic view to prevent
duplicate dimensions across views.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import build123d as bd

from build123d_techdraw.annotations.callouts import HoleCallout
from build123d_techdraw.annotations.dimensions import DimensionType, LinearDimension
from build123d_techdraw.core.view import ViewType
from build123d_techdraw.layout.linter import get_annotation_bbox_2d, get_title_block_bbox
from build123d_techdraw.recognizers.features import (
    recognize_bolt_circles,
    recognize_bosses,
    recognize_holes,
    recognize_steps,
)

if TYPE_CHECKING:
    from build123d_techdraw.core.sheet import DrawingSheet
    from build123d_techdraw.core.view import SheetView

FIRST_DIM_OFFSET_MM = 10.0
DIM_STACK_STEP_MM = 7.0
MAX_STACK_TIERS = 8


@dataclass
class PlannedLinearDim:
    """Intermediate representation of a linear dimension before collision-free stacking."""

    view: SheetView
    orientation: str  # "horizontal" or "vertical"
    p1_sheet: float  # X for horizontal, Y for vertical
    p2_sheet: float  # X for horizontal, Y for vertical
    span_mm: float
    prefix: str = ""
    tolerance: str | None = None
    preferred_side: str | None = None  # "top"|"bottom" for horizontal, "left"|"right" for vertical


def _bboxes_overlap(
    b1: tuple[float, float, float, float],
    b2: tuple[float, float, float, float],
    pad: float = 1.0,
) -> bool:
    """Return True if two 2D bounding boxes overlap (with optional padding)."""
    return not (
        b1[2] + pad <= b2[0] or b1[0] - pad >= b2[2] or b1[3] + pad <= b2[1] or b1[1] - pad >= b2[3]
    )


class SheetOccupancy:
    """Tracks occupied 2D regions on a DrawingSheet using linter-compatible bounding boxes."""

    def __init__(self, sheet: DrawingSheet) -> None:
        self.sheet = sheet
        self.bounds = (
            sheet.left_margin,
            sheet.top_margin,
            sheet.width_mm - sheet.right_margin,
            sheet.height_mm - sheet.bottom_margin,
        )
        self.tb_bbox: tuple[float, float, float, float] | None = (
            get_title_block_bbox(sheet) if getattr(sheet, "show_title_block", True) else None
        )
        self.view_bboxes: list[tuple[SheetView, tuple[float, float, float, float]]] = []
        self.label_bboxes: list[tuple[float, float, float, float]] = []
        for v in sheet.views:
            self.view_bboxes.append((v, (v.min_x, v.min_y, v.max_x, v.max_y)))
            if getattr(v, "view_label", None):
                self.label_bboxes.append(
                    (v.center_x - 20.0, v.max_y + 1.5, v.center_x + 20.0, v.max_y + 8.5)
                )

        self.ann_bboxes: list[tuple[float, float, float, float]] = []
        for ann in sheet.annotations:
            if type(ann).__name__ == "CenterMark":
                continue
            bb = get_annotation_bbox_2d(ann)
            if bb is not None:
                self.ann_bboxes.append(bb)

    def in_bounds(self, bbox: tuple[float, float, float, float]) -> bool:
        """Return True if bbox lies strictly inside sheet drawable margins."""
        return (
            bbox[0] >= self.bounds[0]
            and bbox[1] >= self.bounds[1]
            and bbox[2] <= self.bounds[2]
            and bbox[3] <= self.bounds[3]
        )

    def collides(
        self,
        bbox: tuple[float, float, float, float],
        *,
        ignore_view: SheetView | None = None,
        check_own_view_geom: bool = False,
    ) -> bool:
        """Return True if bbox collides with margins, title block, views, labels, or annotations."""
        if not self.in_bounds(bbox):
            return True
        if self.tb_bbox is not None and _bboxes_overlap(bbox, self.tb_bbox, pad=1.5):
            return True
        for v, v_bb in self.view_bboxes:
            if not check_own_view_geom and v is ignore_view:
                continue
            if _bboxes_overlap(bbox, v_bb, pad=1.5):
                return True
        for l_bb in self.label_bboxes:
            if _bboxes_overlap(bbox, l_bb, pad=1.0):
                return True
        return any(_bboxes_overlap(bbox, a_bb, pad=1.0) for a_bb in self.ann_bboxes)

    def register(self, ann: Any) -> None:
        """Register a newly placed annotation's 2D bounding box."""
        bb = get_annotation_bbox_2d(ann)
        if bb is not None:
            self.ann_bboxes.append(bb)


def _pick_view(
    views_map: dict[ViewType, SheetView],
    candidates: list[ViewType],
) -> SheetView | None:
    """Return the first available SheetView matching candidate ViewTypes."""
    for vt in candidates:
        if vt in views_map:
            return views_map[vt]
    return None


def _preferred_sides_for_view(
    view: SheetView,
    all_views: list[SheetView],
) -> dict[str, list[str]]:
    """Choose outer/unobstructed side order ('top'/'bottom', 'left'/'right') for a view."""
    # Check if other views sit above, below, left, or right of this view
    has_above = any(
        ov is not view
        and ov.center_y < view.center_y - 5.0
        and abs(ov.center_x - view.center_x) < 120.0
        for ov in all_views
    )
    has_below = any(
        ov is not view
        and ov.center_y > view.center_y + 5.0
        and abs(ov.center_x - view.center_x) < 120.0
        for ov in all_views
    )
    has_left = any(
        ov is not view
        and ov.center_x < view.center_x - 5.0
        and abs(ov.center_y - view.center_y) < 120.0
        for ov in all_views
    )
    has_right = any(
        ov is not view
        and ov.center_x > view.center_x + 5.0
        and abs(ov.center_y - view.center_y) < 120.0
        for ov in all_views
    )

    h_sides = ["bottom", "top"]
    if has_below and not has_above:
        h_sides = ["top", "bottom"]
    elif not has_below and has_above:
        h_sides = ["bottom", "top"]

    v_sides = ["left", "right"]
    if has_left and not has_right:
        v_sides = ["right", "left"]
    elif not has_left and has_right:
        v_sides = ["left", "right"]

    return {"horizontal": h_sides, "vertical": v_sides}


def _place_stacked_linear_dims(
    planned: list[PlannedLinearDim],
    occupancy: SheetOccupancy,
    all_views: list[SheetView],
) -> list[LinearDimension]:
    """Stack planned linear dimensions inner-to-outer (10 mm + 7 mm * k) avoiding collisions."""
    placed: list[LinearDimension] = []

    # Group by (id(view), orientation)
    groups: dict[tuple[int, str], list[PlannedLinearDim]] = {}
    for item in planned:
        if item.span_mm < 1e-3:
            continue
        key = (id(item.view), item.orientation)
        groups.setdefault(key, []).append(item)

    for (_, orientation), items in groups.items():
        view = items[0].view
        # Deduplicate identical spans on the same view & orientation
        deduped: list[PlannedLinearDim] = []
        seen_spans: set[tuple[float, float]] = set()
        for it in items:
            a, b = min(it.p1_sheet, it.p2_sheet), max(it.p1_sheet, it.p2_sheet)
            sig = (round(a, 2), round(b, 2))
            if sig not in seen_spans:
                seen_spans.add(sig)
                deduped.append(it)

        # Sort innermost (shortest span) to outermost (overall span)
        deduped.sort(key=lambda d: d.span_mm)

        side_prefs = _preferred_sides_for_view(view, all_views)[orientation]

        for it in deduped:
            sides_to_try = list(side_prefs)
            if it.preferred_side and it.preferred_side in sides_to_try:
                sides_to_try.remove(it.preferred_side)
                sides_to_try.insert(0, it.preferred_side)

            chosen: tuple[tuple[float, float], tuple[float, float], float] | None = None

            for side in sides_to_try:
                for tier in range(MAX_STACK_TIERS):
                    dist = FIRST_DIM_OFFSET_MM + tier * DIM_STACK_STEP_MM
                    if orientation == "horizontal":
                        y_ref = view.max_y if side == "bottom" else view.min_y
                        offset = dist if side == "bottom" else -dist
                        start = (it.p1_sheet, y_ref)
                        end = (it.p2_sheet, y_ref)
                        dim_y = y_ref + offset
                        cand_bbox = (
                            min(it.p1_sheet, it.p2_sheet),
                            dim_y - 3.5,
                            max(it.p1_sheet, it.p2_sheet),
                            dim_y + 2.0,
                        )
                    else:
                        x_ref = view.max_x if side == "right" else view.min_x
                        offset = dist if side == "right" else -dist
                        start = (x_ref, it.p1_sheet)
                        end = (x_ref, it.p2_sheet)
                        dim_x = x_ref + offset
                        cand_bbox = (
                            dim_x - 3.5,
                            min(it.p1_sheet, it.p2_sheet),
                            dim_x + 2.0,
                            max(it.p1_sheet, it.p2_sheet),
                        )

                    if not occupancy.collides(cand_bbox, ignore_view=view):
                        chosen = (start, end, offset)
                        break
                if chosen is not None:
                    break

            if chosen is None:
                # Fallback to first preferred side at tier 0 if sheet is extremely tight
                side = sides_to_try[0]
                if orientation == "horizontal":
                    y_ref = view.max_y if side == "bottom" else view.min_y
                    offset = FIRST_DIM_OFFSET_MM if side == "bottom" else -FIRST_DIM_OFFSET_MM
                    chosen = ((it.p1_sheet, y_ref), (it.p2_sheet, y_ref), offset)
                else:
                    x_ref = view.max_x if side == "right" else view.min_x
                    offset = FIRST_DIM_OFFSET_MM if side == "right" else -FIRST_DIM_OFFSET_MM
                    chosen = ((x_ref, it.p1_sheet), (x_ref, it.p2_sheet), offset)

            dim_type = (
                DimensionType.HORIZONTAL if orientation == "horizontal" else DimensionType.VERTICAL
            )
            dim = LinearDimension(
                start=chosen[0],
                end=chosen[1],
                offset=chosen[2],
                dim_type=dim_type,
                prefix=it.prefix,
                tolerance=it.tolerance,
                view=view,
            )
            occupancy.register(dim)
            placed.append(dim)

    return placed


def _place_collision_free_callout(
    view: SheetView,
    target_sheet: tuple[float, float],
    occupancy: SheetOccupancy,
    shelf_length: float = 20.0,
    **callout_kwargs: Any,
) -> HoleCallout:
    """Place a HoleCallout at the first collision-free outward leader position."""
    tx, ty = target_sheet
    # Radial outward direction from view center
    vx = tx - view.center_x
    vy = ty - view.center_y
    v_len = math.hypot(vx, vy)
    if v_len > 1e-3:
        ux, uy = vx / v_len, vy / v_len
    else:
        ux, uy = 0.707, -0.707

    base_dirs = [
        (ux, uy),
        (0.75, -0.65),
        (-0.75, -0.65),
        (0.75, 0.65),
        (-0.75, 0.65),
        (0.95, -0.35),
        (-0.95, -0.35),
        (0.95, 0.35),
        (-0.95, 0.35),
    ]

    best_offset: tuple[float, float] | None = None
    for dist in (18.0, 26.0, 34.0):
        for dx_u, dy_u in base_dirs:
            dx = dx_u * dist
            dy = dy_u * dist
            ex = tx + dx
            ey = ty + dy
            sx = ex + (shelf_length if dx >= 0 else -shelf_length)
            cand_bbox = (
                min(tx, ex, sx),
                min(ty, ey) - 4.0,
                max(tx, ex, sx),
                max(ty, ey) + 4.0,
            )
            # Check strictly outside view geometry first, then allow inside own view if needed
            if not occupancy.collides(cand_bbox, ignore_view=view, check_own_view_geom=True):
                best_offset = (dx, dy)
                break
        if best_offset is not None:
            break

    if best_offset is None:
        for dist in (18.0, 26.0):
            for dx_u, dy_u in base_dirs:
                dx = dx_u * dist
                dy = dy_u * dist
                ex = tx + dx
                ey = ty + dy
                sx = ex + (shelf_length if dx >= 0 else -shelf_length)
                cand_bbox = (
                    min(tx, ex, sx),
                    min(ty, ey) - 4.0,
                    max(tx, ex, sx),
                    max(ty, ey) + 4.0,
                )
                if not occupancy.collides(cand_bbox, ignore_view=view, check_own_view_geom=False):
                    best_offset = (dx, dy)
                    break
            if best_offset is not None:
                break

    if best_offset is None:
        best_offset = (20.0, -15.0)

    hc = HoleCallout(
        center=target_sheet,
        elbow_offset=best_offset,
        shelf_length=shelf_length,
        view=view,
        **callout_kwargs,
    )
    occupancy.register(hc)
    return hc


def auto_dimension_views(
    sheet: DrawingSheet,
    shape: bd.Solid | bd.Compound | Any,
    views_map: dict[ViewType, SheetView],
    tolerance: str | None = None,
    infer_threads: bool = False,
) -> list[Any]:
    """Automatically dimension a set of orthographic views with canonical view de-duplication
    and ISO 129-1 / ASME Y14.5 collision-avoiding stacking.
    """
    if not views_map:
        return []

    occupancy = SheetOccupancy(sheet)
    all_views = list(sheet.views)
    seen_features: set[tuple[Any, ...]] = set()
    planned_linear: list[PlannedLinearDim] = []
    created_annotations: list[Any] = []

    bbox = shape.bounding_box()
    min_x3, min_y3, min_z3 = float(bbox.min.X), float(bbox.min.Y), float(bbox.min.Z)
    max_x3, max_y3 = float(bbox.max.X), float(bbox.max.Y)

    front_v = _pick_view(views_map, [ViewType.FRONT, ViewType.REAR])
    top_v = _pick_view(views_map, [ViewType.TOP, ViewType.BOTTOM])

    # ------------------------------------------------------------------
    # 1. Overall Envelope Dimensions (X, Y, Z) - each assigned once
    # ------------------------------------------------------------------
    v_x = _pick_view(views_map, [ViewType.FRONT, ViewType.TOP, ViewType.BOTTOM, ViewType.REAR])
    if v_x is not None and ("overall", "X") not in seen_features:
        seen_features.add(("overall", "X"))
        planned_linear.append(
            PlannedLinearDim(
                view=v_x,
                orientation="horizontal",
                p1_sheet=v_x.min_x,
                p2_sheet=v_x.max_x,
                span_mm=abs(v_x.max_x - v_x.min_x),
                tolerance=tolerance,
            )
        )

    v_z = _pick_view(views_map, [ViewType.FRONT, ViewType.RIGHT, ViewType.LEFT, ViewType.REAR])
    if v_z is not None and ("overall", "Z") not in seen_features:
        seen_features.add(("overall", "Z"))
        planned_linear.append(
            PlannedLinearDim(
                view=v_z,
                orientation="vertical",
                p1_sheet=v_z.min_y,
                p2_sheet=v_z.max_y,
                span_mm=abs(v_z.max_y - v_z.min_y),
                tolerance=tolerance,
            )
        )

    v_y = _pick_view(views_map, [ViewType.TOP, ViewType.RIGHT, ViewType.LEFT, ViewType.BOTTOM])
    if v_y is not None and ("overall", "Y") not in seen_features:
        seen_features.add(("overall", "Y"))
        if v_y.view_type in (ViewType.TOP, ViewType.BOTTOM):
            planned_linear.append(
                PlannedLinearDim(
                    view=v_y,
                    orientation="vertical",
                    p1_sheet=v_y.min_y,
                    p2_sheet=v_y.max_y,
                    span_mm=abs(v_y.max_y - v_y.min_y),
                    tolerance=tolerance,
                )
            )
        else:
            planned_linear.append(
                PlannedLinearDim(
                    view=v_y,
                    orientation="horizontal",
                    p1_sheet=v_y.min_x,
                    p2_sheet=v_y.max_x,
                    span_mm=abs(v_y.max_x - v_y.min_x),
                    tolerance=tolerance,
                )
            )

    # ------------------------------------------------------------------
    # 2. Planar Steps / Shoulders - canonical view per step axis
    # ------------------------------------------------------------------
    for step in recognize_steps(shape):
        feat_key = ("step", step.axis, round(step.step_coord, 2))
        if feat_key in seen_features:
            continue

        if step.axis == "X":
            target_v = _pick_view(
                views_map, [ViewType.FRONT, ViewType.TOP, ViewType.BOTTOM, ViewType.REAR]
            )
            if target_v is not None:
                seen_features.add(feat_key)
                p1 = target_v.to_sheet_3d(min_x3, min_y3, min_z3)[0]
                p2 = target_v.to_sheet_3d(step.step_coord, min_y3, min_z3)[0]
                planned_linear.append(
                    PlannedLinearDim(
                        view=target_v,
                        orientation="horizontal",
                        p1_sheet=p1,
                        p2_sheet=p2,
                        span_mm=abs(p2 - p1),
                    )
                )
        elif step.axis == "Z":
            target_v = _pick_view(
                views_map, [ViewType.FRONT, ViewType.RIGHT, ViewType.LEFT, ViewType.REAR]
            )
            if target_v is not None:
                seen_features.add(feat_key)
                p1 = target_v.to_sheet_3d(min_x3, min_y3, min_z3)[1]
                p2 = target_v.to_sheet_3d(min_x3, min_y3, step.step_coord)[1]
                planned_linear.append(
                    PlannedLinearDim(
                        view=target_v,
                        orientation="vertical",
                        p1_sheet=p1,
                        p2_sheet=p2,
                        span_mm=abs(p2 - p1),
                    )
                )
        elif step.axis == "Y":
            target_v = _pick_view(
                views_map, [ViewType.TOP, ViewType.RIGHT, ViewType.LEFT, ViewType.BOTTOM]
            )
            if target_v is not None:
                seen_features.add(feat_key)
                if target_v.view_type in (ViewType.TOP, ViewType.BOTTOM):
                    p1 = target_v.to_sheet_3d(min_x3, min_y3, min_z3)[1]
                    p2 = target_v.to_sheet_3d(min_x3, step.step_coord, min_z3)[1]
                    planned_linear.append(
                        PlannedLinearDim(
                            view=target_v,
                            orientation="vertical",
                            p1_sheet=p1,
                            p2_sheet=p2,
                            span_mm=abs(p2 - p1),
                        )
                    )
                else:
                    p1 = target_v.to_sheet_3d(min_x3, min_y3, min_z3)[0]
                    p2 = target_v.to_sheet_3d(min_x3, step.step_coord, min_z3)[0]
                    planned_linear.append(
                        PlannedLinearDim(
                            view=target_v,
                            orientation="horizontal",
                            p1_sheet=p1,
                            p2_sheet=p2,
                            span_mm=abs(p2 - p1),
                        )
                    )

    # ------------------------------------------------------------------
    # 3. External Cylindrical Bosses / Shafts - canonical side view
    # ------------------------------------------------------------------
    for boss in recognize_bosses(shape):
        dx, dy, dz = boss.axis_dir
        axis_tag = "Z" if abs(dz) > 0.8 else ("Y" if abs(dy) > 0.8 else "X")
        boss_key = ("boss", axis_tag, round(boss.diameter, 2))
        if boss_key in seen_features:
            continue

        # Skip if diameter equals overall envelope width/depth already dimensioned
        if (
            axis_tag == "Z"
            and abs(boss.diameter - (max_x3 - min_x3)) < 0.2
            and abs(boss.diameter - (max_y3 - min_y3)) < 0.2
        ):
            # Turned part: still useful to show Ø prefix on Front if not duplicated, skip if box-like
            pass

        if axis_tag == "Z":
            target_v = _pick_view(views_map, [ViewType.FRONT, ViewType.RIGHT, ViewType.LEFT])
            if target_v is not None:
                seen_features.add(boss_key)
                r = boss.diameter / 2.0
                if target_v.view_type in (ViewType.FRONT, ViewType.REAR):
                    p1 = target_v.to_sheet_3d(
                        boss.center_3d[0] - r, boss.center_3d[1], boss.center_3d[2]
                    )[0]
                    p2 = target_v.to_sheet_3d(
                        boss.center_3d[0] + r, boss.center_3d[1], boss.center_3d[2]
                    )[0]
                else:
                    p1 = target_v.to_sheet_3d(
                        boss.center_3d[0], boss.center_3d[1] - r, boss.center_3d[2]
                    )[0]
                    p2 = target_v.to_sheet_3d(
                        boss.center_3d[0], boss.center_3d[1] + r, boss.center_3d[2]
                    )[0]
                planned_linear.append(
                    PlannedLinearDim(
                        view=target_v,
                        orientation="horizontal",
                        p1_sheet=p1,
                        p2_sheet=p2,
                        span_mm=abs(p2 - p1),
                        prefix="Ø",
                    )
                )

    # ------------------------------------------------------------------
    # 4. Holes & Bolt Circles - canonical circular view + datum locations
    # ------------------------------------------------------------------
    bcs = recognize_bolt_circles(shape)
    holes = recognize_holes(shape, infer_threads=infer_threads)

    # Track hole datum coordinates on canonical views (excluding bolt-circle holes)
    if not bcs:
        for h in holes:
            dx, dy, dz = h.axis_dir
            if abs(dz) > 0.8:
                v_choice = top_v or front_v
                if v_choice is not None and v_choice.view_type in (ViewType.TOP, ViewType.BOTTOM):
                    hx_key = ("hole_pos_x", round(h.center_3d[0], 2))
                    if hx_key not in seen_features and abs(h.center_3d[0] - min_x3) > 1.0:
                        seen_features.add(hx_key)
                        p1 = v_choice.to_sheet_3d(min_x3, min_y3, min_z3)[0]
                        p2 = v_choice.to_sheet_3d(*h.center_3d)[0]
                        planned_linear.append(
                            PlannedLinearDim(
                                view=v_choice,
                                orientation="horizontal",
                                p1_sheet=p1,
                                p2_sheet=p2,
                                span_mm=abs(p2 - p1),
                            )
                        )
                    hy_key = ("hole_pos_y", round(h.center_3d[1], 2))
                    if hy_key not in seen_features and abs(h.center_3d[1] - min_y3) > 1.0:
                        seen_features.add(hy_key)
                        p1 = v_choice.to_sheet_3d(min_x3, min_y3, min_z3)[1]
                        p2 = v_choice.to_sheet_3d(*h.center_3d)[1]
                        planned_linear.append(
                            PlannedLinearDim(
                                view=v_choice,
                                orientation="vertical",
                                p1_sheet=p1,
                                p2_sheet=p2,
                                span_mm=abs(p2 - p1),
                            )
                        )

    # Stack and place all linear dimensions FIRST so callouts can route around them!
    created_annotations.extend(_place_stacked_linear_dims(planned_linear, occupancy, all_views))

    # Now place Bolt Circles & Hole Callouts avoiding all views + stacked dimensions
    for bc in bcs:
        bc_key = (
            "bolt_circle",
            round(bc.diameter, 2),
            bc.num_holes,
            round(bc.hole_feature.diameter, 2),
        )
        if bc_key in seen_features:
            continue
        seen_features.add(bc_key)
        target_view = top_v or front_v
        if target_view is not None:
            bc_ann = target_view.bolt_circle(
                center_model=(bc.center_3d[0], bc.center_3d[1]),
                diameter_model=bc.diameter,
                num_holes=bc.num_holes,
            )
            created_annotations.append(bc_ann)
            t_sheet = target_view.to_sheet_3d(
                bc.center_3d[0] + bc.diameter / 2.0,
                bc.center_3d[1],
                bc.center_3d[2],
            )
            hc = _place_collision_free_callout(
                target_view,
                t_sheet,
                occupancy,
                diameter=bc.hole_feature.diameter,
                depth=bc.hole_feature.depth,
                counterbore_dia=bc.hole_feature.counterbore_dia,
                counterbore_depth=bc.hole_feature.counterbore_depth,
                countersink_dia=bc.hole_feature.countersink_dia,
                countersink_angle=bc.hole_feature.countersink_angle,
                quantity=bc.num_holes,
            )
            created_annotations.append(hc)

    if not bcs:
        for h in holes:
            sig = ("hole_callout", h.diameter, str(h.depth), h.counterbore_dia, h.countersink_dia)
            if sig in seen_features:
                continue
            seen_features.add(sig)

            dx, dy, dz = h.axis_dir
            if abs(dz) > 0.8:
                v_choice = _pick_view(
                    views_map, [ViewType.TOP, ViewType.BOTTOM, ViewType.FRONT, ViewType.RIGHT]
                )
            elif abs(dy) > 0.8:
                v_choice = _pick_view(
                    views_map, [ViewType.FRONT, ViewType.REAR, ViewType.TOP, ViewType.RIGHT]
                )
            else:
                v_choice = _pick_view(
                    views_map, [ViewType.RIGHT, ViewType.LEFT, ViewType.FRONT, ViewType.TOP]
                )

            if v_choice is not None:
                t_sheet = v_choice.to_sheet_3d(*h.center_3d)
                hc = _place_collision_free_callout(
                    v_choice,
                    t_sheet,
                    occupancy,
                    diameter=h.diameter,
                    depth=h.depth,
                    counterbore_dia=h.counterbore_dia,
                    counterbore_depth=h.counterbore_depth,
                    countersink_dia=h.countersink_dia,
                    countersink_angle=h.countersink_angle,
                    quantity=h.quantity,
                )
                created_annotations.append(hc)

    return created_annotations


def auto_dimension_view(
    view: SheetView,
    shape: bd.Solid | bd.Compound | Any | None = None,
    tolerance: str | None = None,
) -> list[Any]:
    """Automatically dimension a single SheetView with collision-avoiding stacking."""
    target_shape = shape if shape is not None else getattr(view, "shape", None)
    if target_shape is None or view.sheet is None:
        return []
    return auto_dimension_views(
        sheet=view.sheet,
        shape=target_shape,
        views_map={view.view_type: view},
        tolerance=tolerance,
    )
