"""Smart collision-avoiding auto-dimension placement and canonical view de-duplication.

Implements ISO 129-1 / ASME Y14.5 dimension stacking (10 mm first offset from
geometry contour, 7 mm step between parallel dimension lines) and assigns each
recognized 3D feature to a single canonical orthographic view to prevent
duplicate dimensions across views.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import build123d as bd

from build123d_techdraw.annotations.base import Annotation, BBox2D
from build123d_techdraw.annotations.callouts import (
    BOMBalloon,
    HoleCallout,
    LeaderNote,
    leader_bbox_2d,
)
from build123d_techdraw.annotations.centerlines import CenterMark
from build123d_techdraw.annotations.dimensions import (
    DimensionType,
    LinearDimension,
    format_dimension_value,
)
from build123d_techdraw.core.view import ViewType
from build123d_techdraw.gpu.sdf import SheetDistanceField, compute_sheet_sdf
from build123d_techdraw.layout.linter import get_title_block_bbox
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
            get_title_block_bbox(sheet) if sheet.show_title_block else None
        )
        self.view_bboxes: list[tuple[SheetView, tuple[float, float, float, float]]] = []
        self.label_bboxes: list[tuple[float, float, float, float]] = []
        for v in sheet.views:
            self.view_bboxes.append((v, (v.min_x, v.min_y, v.max_x, v.max_y)))
            if v.view_label:
                self.label_bboxes.append(
                    (v.center_x - 20.0, v.max_y + 1.5, v.center_x + 20.0, v.max_y + 8.5)
                )

        self.ann_bboxes: list[tuple[float, float, float, float]] = []
        for ann in sheet.annotations:
            if isinstance(ann, CenterMark):
                continue
            bb = ann.bounding_box_2d()
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

    def register(self, ann: Annotation) -> None:
        """Register a newly placed annotation's 2D bounding box."""
        bb = ann.bounding_box_2d()
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


def _dim_tier_candidate(
    view: SheetView,
    item: PlannedLinearDim,
    orientation: str,
    side: str,
    tier: int,
) -> tuple[tuple[float, float], tuple[float, float], float, BBox2D]:
    """Compute ``(start, end, offset, cand_bbox)`` for a single stacked dimension tier."""
    dist = FIRST_DIM_OFFSET_MM + tier * DIM_STACK_STEP_MM
    if orientation == "horizontal":
        y_ref = view.max_y if side == "bottom" else view.min_y
        offset = dist if side == "bottom" else -dist
        dim_y = y_ref + offset
        bbox = (
            min(item.p1_sheet, item.p2_sheet),
            dim_y - 3.5,
            max(item.p1_sheet, item.p2_sheet),
            dim_y + 2.0,
        )
        return (item.p1_sheet, y_ref), (item.p2_sheet, y_ref), offset, bbox
    x_ref = view.max_x if side == "right" else view.min_x
    offset = dist if side == "right" else -dist
    dim_x = x_ref + offset
    bbox = (
        dim_x - 3.5,
        min(item.p1_sheet, item.p2_sheet),
        dim_x + 2.0,
        max(item.p1_sheet, item.p2_sheet),
    )
    return (x_ref, item.p1_sheet), (x_ref, item.p2_sheet), offset, bbox


def _bbox_is_linter_clean(
    bbox: BBox2D,
    occupancy: SheetOccupancy,
    reserved_boxes: Sequence[BBox2D] = (),
) -> bool:
    """Return True if ``bbox`` stays in bounds and avoids title block and overlap lints."""
    if not occupancy.in_bounds(bbox):
        return False
    if occupancy.tb_bbox is not None and _bboxes_overlap(bbox, occupancy.tb_bbox, pad=0.5):
        return False
    for existing in occupancy.ann_bboxes:
        ox = max(0.0, min(bbox[2], existing[2]) - max(bbox[0], existing[0]))
        oy = max(0.0, min(bbox[3], existing[3]) - max(bbox[1], existing[1]))
        if ox > 3.5 and oy > 2.5:
            return False
    for reserved in reserved_boxes:
        ox = max(0.0, min(bbox[2], reserved[2]) - max(bbox[0], reserved[0]))
        oy = max(0.0, min(bbox[3], reserved[3]) - max(bbox[1], reserved[1]))
        if ox > 3.5 and oy > 2.5:
            return False
    return True


def _peer_target_boxes(
    target_xy: tuple[float, float],
    peer_points: Sequence[tuple[float, float]],
) -> list[BBox2D]:
    """Build 8x8 mm protection boxes around peer leader targets so earlier leaders don't trap them."""
    return [
        (px - 4.0, py - 4.0, px + 4.0, py + 4.0)
        for px, py in peer_points
        if math.hypot(px - target_xy[0], py - target_xy[1]) > 0.5
    ]


def _place_stacked_linear_dims(
    planned: list[PlannedLinearDim],
    occupancy: SheetOccupancy,
    all_views: list[SheetView],
    *,
    sdf: SheetDistanceField | None = None,
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

        if sdf is not None and len(side_prefs) == 2 and deduped:
            # Evaluate how many stacked tiers fit cleanly on each side using the SDF
            # so dense dimension groups naturally gravitate to unobstructed sheet space.
            n_check = min(len(deduped), MAX_STACK_TIERS)
            side_scores: list[tuple[int, float]] = []
            for side in side_prefs:
                clear_tiers = 0
                clr_sum = 0.0
                for tier in range(n_check):
                    dist = FIRST_DIM_OFFSET_MM + tier * DIM_STACK_STEP_MM
                    if orientation == "horizontal":
                        y_ref = view.max_y if side == "bottom" else view.min_y
                        dim_y = y_ref + (dist if side == "bottom" else -dist)
                        probe = (view.min_x, dim_y - 3.5, view.max_x, dim_y + 2.0)
                    else:
                        x_ref = view.max_x if side == "right" else view.min_x
                        dim_x = x_ref + (dist if side == "right" else -dist)
                        probe = (dim_x - 3.5, view.min_y, dim_x + 2.0, view.max_y)
                    if not occupancy.collides(probe, ignore_view=view):
                        clr = sdf.box_min_clearance(*probe)
                        if clr >= 1.0:
                            clear_tiers += 1
                            clr_sum += min(clr, 40.0)
                side_scores.append((clear_tiers, clr_sum))
            if side_scores[1][0] > side_scores[0][0] or (
                side_scores[1][0] == side_scores[0][0]
                and side_scores[1][1] > side_scores[0][1] + 6.0
            ):
                side_prefs = [side_prefs[1], side_prefs[0]]

        for it in deduped:
            sides_to_try = list(side_prefs)
            if it.preferred_side and it.preferred_side in sides_to_try:
                sides_to_try.remove(it.preferred_side)
                sides_to_try.insert(0, it.preferred_side)

            chosen: tuple[tuple[float, float], tuple[float, float], float] | None = None

            if sdf is None:
                for side in sides_to_try:
                    for tier in range(MAX_STACK_TIERS):
                        start, end, offset, cand_bbox = _dim_tier_candidate(
                            view, it, orientation, side, tier
                        )
                        if not occupancy.collides(cand_bbox, ignore_view=view):
                            chosen = (start, end, offset)
                            break
                    if chosen is not None:
                        break
            else:
                # SDF-guided tier selection: preserve ISO 129-1 single-side stacking
                # while checking true 2D SDF clearance against tables, notes, and contours.
                best_choice: tuple[tuple[float, float], tuple[float, float], float] | None = None
                best_rank: tuple[int, int, int, float] | None = None

                for side_idx, side in enumerate(sides_to_try):
                    found_clean_on_side = False
                    for tier in range(MAX_STACK_TIERS):
                        start, end, offset, cand_bbox = _dim_tier_candidate(
                            view, it, orientation, side, tier
                        )
                        if occupancy.collides(cand_bbox, ignore_view=view):
                            continue
                        clr = sdf.box_min_clearance(*cand_bbox)
                        # ISO 129-1 7 mm stacking leaves 1.5 mm gap between adjacent 5.5 mm
                        # dim boxes; with 0.5 mm dim padding, adjacent clean tiers have >=0.5 mm.
                        bucket = 2 if clr >= 0.5 else (1 if clr > 0.0 else 0)
                        rank = (bucket, -side_idx, -tier, min(clr, 6.0))
                        if best_rank is None or rank > best_rank:
                            best_rank = rank
                            best_choice = (start, end, offset)
                        if bucket == 2:
                            found_clean_on_side = True
                            break
                    if found_clean_on_side:
                        break
                chosen = best_choice

            if chosen is None:
                start, end, offset, _ = _dim_tier_candidate(
                    view, it, orientation, sides_to_try[0], 0
                )
                chosen = (start, end, offset)

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
            if sdf is not None:
                dim_bb = dim.bounding_box_2d()
                if orientation == "horizontal" and abs(chosen[1][0] - chosen[0][0]) < 12.0:
                    dim_bb = (dim_bb[0] - 4.0, dim_bb[1], dim_bb[2] + 4.0, dim_bb[3])
                sdf.mark_occupied_box(dim_bb, pad_mm=0.5)
            placed.append(dim)

    return placed


def _is_linter_clean_leader(
    target_xy: tuple[float, float],
    elbow_offset: tuple[float, float],
    shelf_length: float,
    occupancy: SheetOccupancy,
    reserved_target_boxes: Sequence[BBox2D] = (),
) -> bool:
    """Return True if a leader note with ``elbow_offset`` produces zero sheet lint warnings."""
    return _bbox_is_linter_clean(
        leader_bbox_2d(target_xy, elbow_offset, shelf_length),
        occupancy,
        reserved_target_boxes,
    )


def _leader_shelf_bbox(
    target_xy: tuple[float, float],
    elbow_offset: tuple[float, float],
    shelf_length: float,
    text_height_mm: float = 8.0,
) -> BBox2D:
    """Return the visual text-shelf bounding box for a leader note or hole callout."""
    ex = target_xy[0] + elbow_offset[0]
    ey = target_xy[1] + elbow_offset[1]
    sx = ex + (shelf_length if elbow_offset[0] >= 0.0 else -shelf_length)
    return (min(ex, sx), ey - text_height_mm + 1.5, max(ex, sx), ey + 2.0)


def _estimate_hole_callout_shelf(callout_kwargs: dict[str, Any]) -> float:
    """Estimate horizontal shelf length (mm) for a ``HoleCallout`` without side effects."""
    qty = callout_kwargs.get("quantity")
    qty_prefix = f"{qty}x " if (isinstance(qty, int) and qty > 1) else ""
    dia = callout_kwargs.get("diameter")
    dia_str = f"⌀{format_dimension_value(float(dia))}" if dia is not None else "⌀"
    tol = callout_kwargs.get("tolerance")
    tol_str = f" {tol}" if tol is not None else ""
    depth = callout_kwargs.get("depth")
    if depth is None or (isinstance(depth, str) and depth.upper() == "THRU"):
        depth_str = " THRU"
    elif isinstance(depth, (int, float)):
        depth_str = f" ↧{format_dimension_value(float(depth))}"
    elif isinstance(depth, str) and depth.strip():
        depth_str = f" {depth.strip()}"
    else:
        depth_str = ""
    csink_dia = callout_kwargs.get("countersink_dia")
    csink_ang = callout_kwargs.get("countersink_angle", 90.0)
    csink_str = (
        f" ⌵ ⌀{format_dimension_value(float(csink_dia))} × {format_dimension_value(float(csink_ang))}°"
        if csink_dia is not None
        else ""
    )
    cbore_dia = callout_kwargs.get("counterbore_dia")
    cbore_depth = callout_kwargs.get("counterbore_depth")
    cbore_str = ""
    if cbore_dia is not None:
        cbore_str = f" ⌴ ⌀{format_dimension_value(float(cbore_dia))}"
        if cbore_depth is not None:
            cbore_str += f" ↧{format_dimension_value(float(cbore_depth))}"

    formatted = f"{qty_prefix}{dia_str}{tol_str}{depth_str}{csink_str}{cbore_str}"
    return max(18.0, len(formatted) * 1.48 + 3.0)


def _place_sdf_callout(
    view: SheetView,
    target_sheet: tuple[float, float],
    occupancy: SheetOccupancy,
    sdf: SheetDistanceField,
    shelf_length: float | None = None,
    reserved_target_boxes: Sequence[BBox2D] = (),
    **callout_kwargs: Any,
) -> HoleCallout:
    """Place a ``HoleCallout`` using the 2D SDF into a clear sheet or concave pocket."""
    est_shelf = (
        float(shelf_length)
        if shelf_length is not None
        else _estimate_hole_callout_shelf(callout_kwargs)
    )

    elbow_offset = sdf.find_best_leader_elbow(
        target_sheet,
        shelf_length_mm=est_shelf,
        text_height_mm=8.0,
        min_dist_mm=12.0,
        max_dist_mm=65.0,
        avoid_leader_crossings=True,
        validator=lambda off: _is_linter_clean_leader(
            target_sheet, off, est_shelf, occupancy, reserved_target_boxes
        ),
    )

    hc = HoleCallout(
        center=target_sheet,
        elbow_offset=elbow_offset,
        shelf_length=est_shelf,
        view=view,
        **callout_kwargs,
    )
    occupancy.register(hc)
    shelf_box = _leader_shelf_bbox(target_sheet, elbow_offset, est_shelf, text_height_mm=8.0)
    sdf.mark_occupied_box(shelf_box, pad_mm=1.5)
    sdf.register_leader_segment(
        target_sheet,
        (target_sheet[0] + elbow_offset[0], target_sheet[1] + elbow_offset[1]),
    )
    return hc


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
    *,
    use_sdf: bool = False,
    gpu: bool | None = None,
    sdf: SheetDistanceField | None = None,
) -> list[Annotation]:
    """Automatically dimension a set of orthographic views with canonical view de-duplication
    and ISO 129-1 / ASME Y14.5 collision-avoiding stacking.

    When ``use_sdf=True`` (opt-in), builds a 2D Euclidean :class:`SheetDistanceField`
    (on WebGPU via JFA+1 when available, or exact CPU SciPy EDT fallback) and uses it to
    place stacked dimensions and hole callouts into truly clear concave/sheet pockets
    while avoiding contour and leader crossings.
    """
    if not views_map:
        return []

    occupancy = SheetOccupancy(sheet)
    active_sdf: SheetDistanceField | None = (
        sdf if sdf is not None else (compute_sheet_sdf(sheet, gpu=gpu) if use_sdf else None)
    )
    all_views = list(sheet.views)
    seen_features: set[tuple[Any, ...]] = set()
    planned_linear: list[PlannedLinearDim] = []
    created_annotations: list[Annotation] = []

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
    created_annotations.extend(
        _place_stacked_linear_dims(planned_linear, occupancy, all_views, sdf=active_sdf)
    )

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
            bc_kwargs: dict[str, Any] = {
                "diameter": bc.hole_feature.diameter,
                "depth": bc.hole_feature.depth,
                "counterbore_dia": bc.hole_feature.counterbore_dia,
                "counterbore_depth": bc.hole_feature.counterbore_depth,
                "countersink_dia": bc.hole_feature.countersink_dia,
                "countersink_angle": bc.hole_feature.countersink_angle,
                "quantity": bc.num_holes,
            }
            if active_sdf is not None:
                hc = _place_sdf_callout(target_view, t_sheet, occupancy, active_sdf, **bc_kwargs)
            else:
                hc = _place_collision_free_callout(target_view, t_sheet, occupancy, **bc_kwargs)
            created_annotations.append(hc)

    if not bcs:
        pending_holes: list[tuple[SheetView, tuple[float, float], dict[str, Any]]] = []
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
                pending_holes.append(
                    (
                        v_choice,
                        t_sheet,
                        {
                            "diameter": h.diameter,
                            "depth": h.depth,
                            "counterbore_dia": h.counterbore_dia,
                            "counterbore_depth": h.counterbore_depth,
                            "countersink_dia": h.countersink_dia,
                            "countersink_angle": h.countersink_angle,
                            "quantity": h.quantity,
                        },
                    )
                )

        if active_sdf is not None:
            # Place widest / tightest hole callouts first while protecting peer targets
            # from being swallowed inside another callout's leader_bbox_2d.
            hole_order = sorted(
                range(len(pending_holes)),
                key=lambda idx: (
                    -_estimate_hole_callout_shelf(pending_holes[idx][2]),
                    _local_ring_clearance(active_sdf, pending_holes[idx][1]),
                    idx,
                ),
            )
            placed_holes: dict[int, HoleCallout] = {}
            remaining_indices = set(hole_order)
            for idx in hole_order:
                remaining_indices.discard(idx)
                v_choice, t_sheet, h_kwargs = pending_holes[idx]
                peer_boxes = _peer_target_boxes(
                    t_sheet, [pending_holes[j][1] for j in remaining_indices]
                )
                placed_holes[idx] = _place_sdf_callout(
                    v_choice,
                    t_sheet,
                    occupancy,
                    active_sdf,
                    reserved_target_boxes=peer_boxes,
                    **h_kwargs,
                )
            for idx in range(len(pending_holes)):
                created_annotations.append(placed_holes[idx])
        else:
            for v_choice, t_sheet, h_kwargs in pending_holes:
                hc = _place_collision_free_callout(
                    v_choice,
                    t_sheet,
                    occupancy,
                    **h_kwargs,
                )
                created_annotations.append(hc)

    return created_annotations


def auto_dimension_view(
    view: SheetView,
    shape: bd.Solid | bd.Compound | Any | None = None,
    tolerance: str | None = None,
    *,
    use_sdf: bool = False,
    gpu: bool | None = None,
    sdf: SheetDistanceField | None = None,
) -> list[Annotation]:
    """Automatically dimension a single SheetView with collision-avoiding stacking."""
    target_shape = shape if shape is not None else view.shape
    if target_shape is None or view.sheet is None:
        return []
    return auto_dimension_views(
        sheet=view.sheet,
        shape=target_shape,
        views_map={view.view_type: view},
        tolerance=tolerance,
        use_sdf=use_sdf,
        gpu=gpu,
        sdf=sdf,
    )


def auto_dimension_sheet(
    sheet: DrawingSheet,
    shape: bd.Solid | bd.Compound | Any | None = None,
    *,
    tolerance: str | None = None,
    infer_threads: bool = False,
    use_sdf: bool = False,
    gpu: bool | None = None,
    sdf: SheetDistanceField | None = None,
) -> list[Annotation]:
    """Automatically dimension all orthographic views currently placed on ``sheet``.

    Resolves ``shape`` from the first view with an attached 3D model when omitted.
    Set ``use_sdf=True`` to enable GPU/CPU Jump-Flooding SDF smart placement.
    """
    views_map: dict[ViewType, SheetView] = {}
    resolved_shape = shape
    for view in sheet.views:
        if view.view_type not in views_map:
            views_map[view.view_type] = view
        if resolved_shape is None and view.shape is not None:
            resolved_shape = view.shape
    if resolved_shape is None or not views_map:
        return []
    return auto_dimension_views(
        sheet=sheet,
        shape=resolved_shape,
        views_map=views_map,
        tolerance=tolerance,
        infer_threads=infer_threads,
        use_sdf=use_sdf,
        gpu=gpu,
        sdf=sdf,
    )


def _resolve_target_xy(
    raw_xy: tuple[float, float],
    view: SheetView | None,
    coord_space: Literal["sheet", "model"],
) -> tuple[float, float]:
    """Convert a 2D target point to sheet paper millimetres."""
    if coord_space == "model" and view is not None:
        return view.to_sheet_coords(float(raw_xy[0]), float(raw_xy[1]))
    return (float(raw_xy[0]), float(raw_xy[1]))


def _local_ring_clearance(
    sdf: SheetDistanceField, xy: tuple[float, float], radius_mm: float = 14.0
) -> float:
    """Mean SDF clearance on a small ring around ``xy`` (used to prioritize tight targets)."""
    tx, ty = xy
    total = 0.0
    n_samples = 12
    for i in range(n_samples):
        ang = (2.0 * math.pi * i) / n_samples
        total += sdf.query_clearance(tx + radius_mm * math.cos(ang), ty + radius_mm * math.sin(ang))
    return total / n_samples


def place_balloons_sdf(
    sheet: DrawingSheet,
    view: SheetView | None,
    targets: Sequence[tuple[int, tuple[float, float]]],
    *,
    gpu: bool | None = None,
    radius_mm: float = 4.0,
    terminator: str = "dot",
    min_dist_mm: float = 10.0,
    max_dist_mm: float = 55.0,
    coord_space: Literal["sheet", "model"] = "sheet",
    sdf: SheetDistanceField | None = None,
) -> list[BOMBalloon]:
    """Place dense BOM balloons around complex or concave parts using a 2D SDF.

    Args:
        sheet: Target :class:`DrawingSheet`.
        view: Optional :class:`SheetView` owning the targets (balloons are attached to it).
        targets: Sequence of ``(item_no, (x, y))`` pairs in sheet mm (or model 2D mm when
            ``coord_space="model"``).
        gpu: Optional GPU override (``True`` / ``False`` / ``None`` auto).
        radius_mm: Balloon circle radius in paper millimetres (default ``4.0``).
        terminator: Leader terminator style (``"dot"`` or ``"arrow"``).
        min_dist_mm: Minimum leader reach from target to balloon center (mm).
        max_dist_mm: Maximum initial search reach from target to balloon center (mm).
        coord_space: Coordinate system of target coordinates (``"sheet"`` or ``"model"``).
        sdf: Optional precomputed :class:`SheetDistanceField` updated in place.

    Returns:
        List of placed :class:`BOMBalloon` annotations in the same order as ``targets``.
    """
    if not targets:
        return []

    active_sdf = sdf if sdf is not None else compute_sheet_sdf(sheet, gpu=gpu)
    occupancy = SheetOccupancy(sheet)
    resolved: list[tuple[int, tuple[float, float]]] = [
        (int(item_no), _resolve_target_xy(xy, view, coord_space)) for item_no, xy in targets
    ]

    # Place tightest / most interior targets first so outer balloons don't steal their pockets.
    order = sorted(
        range(len(resolved)),
        key=lambda idx: (_local_ring_clearance(active_sdf, resolved[idx][1]), idx),
    )

    placed_by_idx: dict[int, BOMBalloon] = {}
    box_size = (2.0 * radius_mm, 2.0 * radius_mm)

    def _is_linter_clean_balloon(center_xy: tuple[float, float]) -> bool:
        bx, by = center_xy
        return _bbox_is_linter_clean(
            (bx - radius_mm, by - radius_mm, bx + radius_mm, by + radius_mm), occupancy
        )

    for idx in order:
        item_no, target_sheet = resolved[idx]
        center_xy = active_sdf.find_best_callout_position(
            target_sheet,
            box_size,
            min_dist_mm=min_dist_mm,
            max_dist_mm=max_dist_mm,
            avoid_leader_crossings=True,
            validator=_is_linter_clean_balloon,
        )
        balloon = sheet.add_balloon(
            item_no=item_no,
            target=target_sheet,
            balloon_center=center_xy,
            radius=radius_mm,
            terminator=terminator,
        )
        if view is not None:
            balloon.view = view
            if balloon not in view.annotations:
                view.annotations.append(balloon)

        occupancy.register(balloon)
        active_sdf.mark_occupied_box(balloon.bounding_box_2d(), pad_mm=1.5)
        active_sdf.register_leader_segment(target_sheet, center_xy)
        placed_by_idx[idx] = balloon

    return [placed_by_idx[i] for i in range(len(resolved))]


def place_leaders_sdf(
    sheet: DrawingSheet,
    view: SheetView | None,
    targets: Sequence[tuple[tuple[float, float], str | Sequence[str]]],
    *,
    gpu: bool | None = None,
    shelf_length: float | None = None,
    terminator: str = "arrow",
    font_size: float = 2.8,
    min_dist_mm: float = 12.0,
    max_dist_mm: float = 65.0,
    coord_space: Literal["sheet", "model"] = "sheet",
    sdf: SheetDistanceField | None = None,
) -> list[LeaderNote]:
    """Place multiple :class:`LeaderNote` callouts cleanly using a 2D SDF with zero lint warnings.

    Args:
        sheet: Target :class:`DrawingSheet`.
        view: Optional :class:`SheetView` owning the targets.
        targets: Sequence of ``((x, y), text_or_lines)`` pairs in sheet mm (or model 2D mm
            when ``coord_space="model"``).
        gpu: Optional GPU override (``True`` / ``False`` / ``None`` auto).
        shelf_length: Fixed horizontal shelf length (mm), or ``None`` to auto-size from text.
        terminator: Leader terminator style (``"arrow"`` or ``"dot"``).
        font_size: Leader text height in paper mm (default ``2.8``).
        min_dist_mm: Minimum leader reach from target to elbow (mm).
        max_dist_mm: Maximum initial search reach from target to elbow (mm).
        coord_space: Coordinate system of target coordinates (``"sheet"`` or ``"model"``).
        sdf: Optional precomputed :class:`SheetDistanceField` updated in place.

    Returns:
        List of placed :class:`LeaderNote` annotations in the same order as ``targets``.
    """
    if not targets:
        return []

    active_sdf = sdf if sdf is not None else compute_sheet_sdf(sheet, gpu=gpu)
    occupancy = SheetOccupancy(sheet)

    resolved: list[tuple[tuple[float, float], list[str]]] = []
    for raw_xy, raw_text in targets:
        lines = [raw_text] if isinstance(raw_text, str) else [str(s) for s in raw_text]
        resolved.append((_resolve_target_xy(raw_xy, view, coord_space), lines))

    order = sorted(
        range(len(resolved)),
        key=lambda idx: (_local_ring_clearance(active_sdf, resolved[idx][0]), idx),
    )

    placed_by_idx: dict[int, LeaderNote] = {}
    remaining = set(order)

    for idx in order:
        remaining.discard(idx)
        target_sheet, lines = resolved[idx]
        max_chars = max((len(line) for line in lines), default=6)
        eff_shelf = (
            float(shelf_length)
            if shelf_length is not None
            else max(18.0, max_chars * (font_size * 0.62) + 4.5)
        )
        text_h = max(8.0, 3.4 * len(lines) + 3.0)
        peer_boxes = _peer_target_boxes(target_sheet, [resolved[j][0] for j in remaining])

        def _validate_leader(
            off: tuple[float, float],
            *,
            ts: tuple[float, float] = target_sheet,
            sl: float = eff_shelf,
            pb: list[BBox2D] = peer_boxes,
        ) -> bool:
            return _is_linter_clean_leader(ts, off, sl, occupancy, pb)

        elbow_offset = active_sdf.find_best_leader_elbow(
            target_sheet,
            shelf_length_mm=eff_shelf,
            text_height_mm=text_h,
            min_dist_mm=min_dist_mm,
            max_dist_mm=max_dist_mm,
            avoid_leader_crossings=True,
            validator=_validate_leader,
        )

        note = sheet.add_leader_note(
            target=target_sheet,
            lines=lines,
            elbow_offset=elbow_offset,
            shelf_length=eff_shelf,
            terminator=terminator,
        )
        note.font_size = font_size
        if view is not None:
            note.view = view
            if note not in view.annotations:
                view.annotations.append(note)

        occupancy.register(note)
        shelf_box = _leader_shelf_bbox(target_sheet, elbow_offset, eff_shelf, text_height_mm=text_h)
        active_sdf.mark_occupied_box(shelf_box, pad_mm=1.5)
        active_sdf.register_leader_segment(
            target_sheet,
            (target_sheet[0] + elbow_offset[0], target_sheet[1] + elbow_offset[1]),
        )
        placed_by_idx[idx] = note

    return [placed_by_idx[i] for i in range(len(resolved))]
