"""Drawing quality linter: checks for overlapping annotations, margin boundary violations, and missing features."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from build123d_techdraw.core.sheet import DrawingSheet


@dataclass
class LintIssue:
    """Represents a potential drafting defect or layout warning found by the linter."""

    code: str
    severity: str  # "WARNING" or "ERROR"
    message: str
    element: Any = None
    bbox: tuple[float, float, float, float] | None = None


def get_annotation_bbox_2d(ann: Any) -> tuple[float, float, float, float] | None:
    """Extract approximate 2D bounding box (min_x, min_y, max_x, max_y) in paper millimeters."""
    # Check if element explicitly provides bounding_box_2d
    if hasattr(ann, "bounding_box_2d"):
        return ann.bounding_box_2d()  # type: ignore[no-any-return]

    # LinearDimension
    if hasattr(ann, "start") and hasattr(ann, "end") and hasattr(ann, "dim_type"):
        s = ann.start
        e = ann.end
        x1, y1 = s
        x2, y2 = e
        min_x = min(x1, x2)
        max_x = max(x1, x2)
        min_y = min(y1, y2)
        max_y = max(y1, y2)
        offset = getattr(ann, "offset", 8.0)
        dim_type = getattr(ann, "dim_type", None)
        if str(dim_type).endswith("HORIZONTAL"):
            dim_y = (y1 + y2) / 2.0 + offset
            return (min_x, min(min_y, dim_y - 2.0), max_x, max(max_y, dim_y + 2.0))
        elif str(dim_type).endswith("VERTICAL"):
            dim_x = (x1 + x2) / 2.0 + offset
            return (min(min_x, dim_x - 2.0), min_y, max(max_x, dim_x + 2.0), max_y)
        return (min_x - 2.0, min_y - 2.0, max_x + 2.0, max_y + 2.0)

    # LeaderNote / ChamferCallout / HoleCallout / ThreadCallout
    if hasattr(ann, "target") and hasattr(ann, "elbow_offset"):
        tx, ty = ann.target
        ex = tx + ann.elbow_offset[0]
        ey = ty + ann.elbow_offset[1]
        sl = getattr(ann, "shelf_length", 20.0)
        sx = ex + (sl if ann.elbow_offset[0] >= 0 else -sl)
        return (min(tx, ex, sx), min(ty, ey) - 4.0, max(tx, ex, sx), max(ty, ey) + 4.0)

    # BOMBalloon
    if hasattr(ann, "balloon_center") and hasattr(ann, "radius"):
        bx, by = ann.balloon_center
        r = ann.radius
        return (bx - r, by - r, bx + r, by + r)

    # FeatureControlFrame
    if hasattr(ann, "position") and hasattr(ann, "frame_height") and hasattr(ann, "characteristic"):
        x, y = ann.position
        h = ann.frame_height
        w = 35.0  # approximate width
        return (x, y, x + w, y + h)

    # Table
    if hasattr(ann, "position") and hasattr(ann, "width") and hasattr(ann, "height"):
        x, y = ann.position
        return (x, y, x + ann.width, y + ann.height)

    # CenterMark
    if hasattr(ann, "center") and hasattr(ann, "size"):
        cx, cy = ann.center
        s = ann.size / 2.0
        return (cx - s, cy - s, cx + s, cy + s)

    return None


def run_sheet_lint(sheet: DrawingSheet) -> list[LintIssue]:
    """Inspect drawing sheet for standards compliance, overlap defects, and boundary clipping."""
    issues: list[LintIssue] = []

    sheet_w = sheet.width_mm
    sheet_h = sheet.height_mm
    margin_l = sheet.left_margin
    margin_r = sheet.right_margin
    margin_t = sheet.top_margin
    margin_b = sheet.bottom_margin

    # 1. Title Block Exclusion Zone
    # Standard ISO 7200 title block is in bottom-right corner: 180mm x 36mm
    tb_w = 180.0
    tb_h = 36.0 + (
        len(sheet.title_info.extra_fields) * 3.5 if sheet.title_info.extra_fields else 0.0
    )
    tb_bbox = (
        sheet_w - margin_r - tb_w,
        sheet_h - margin_b - tb_h,
        sheet_w - margin_r,
        sheet_h - margin_b,
    )

    # Check views for title block collision
    for v in sheet.views:
        v_bbox = (v.min_x, v.min_y, v.max_x, v.max_y)
        # Check title block overlap
        if not (
            v_bbox[2] < tb_bbox[0]
            or v_bbox[0] > tb_bbox[2]
            or v_bbox[3] < tb_bbox[1]
            or v_bbox[1] > tb_bbox[3]
        ):
            issues.append(
                LintIssue(
                    code="COLLISION_TITLE_BLOCK",
                    severity="ERROR",
                    message=f"View '{v.view_type.value}' overlaps with the ISO 7200 title block.",
                    element=v,
                    bbox=v_bbox,
                )
            )

        # Check sheet margin clipping
        if (
            v.min_x < margin_l
            or v.max_x > sheet_w - margin_r
            or v.min_y < margin_t
            or v.max_y > sheet_h - margin_b
        ):
            issues.append(
                LintIssue(
                    code="COLLISION_MARGIN",
                    severity="WARNING",
                    message=f"View '{v.view_type.value}' extends beyond sheet drawable margins.",
                    element=v,
                    bbox=v_bbox,
                )
            )

    # 2. Check Annotations
    ann_bboxes: list[tuple[Any, tuple[float, float, float, float]]] = []

    for ann in sheet.annotations:
        bbox = get_annotation_bbox_2d(ann)
        if bbox is None:
            continue

        ann_bboxes.append((ann, bbox))

        # Check annotation margins
        if (
            bbox[0] < margin_l
            or bbox[2] > sheet_w - margin_r
            or bbox[1] < margin_t
            or bbox[3] > sheet_h - margin_b
        ):
            issues.append(
                LintIssue(
                    code="COLLISION_MARGIN",
                    severity="WARNING",
                    message=f"Annotation of type {type(ann).__name__} extends beyond sheet margins.",
                    element=ann,
                    bbox=bbox,
                )
            )

        # Check title block collision (only if title block is enabled on sheet)
        if (
            getattr(sheet, "show_title_block", True)
            and not (
                bbox[2] < tb_bbox[0]
                or bbox[0] > tb_bbox[2]
                or bbox[3] < tb_bbox[1]
                or bbox[1] > tb_bbox[3]
            )
            and (type(ann).__name__ != "Table" or bbox[1] < tb_bbox[1] - 5.0)
        ):
            issues.append(
                LintIssue(
                    code="COLLISION_TITLE_BLOCK",
                    severity="WARNING",
                    message=f"Annotation of type {type(ann).__name__} intrudes into title block exclusion zone.",
                    element=ann,
                    bbox=bbox,
                )
            )

        # Check zero-length linear dimensions
        if hasattr(ann, "start") and hasattr(ann, "end"):
            dist = math.hypot(ann.end[0] - ann.start[0], ann.end[1] - ann.start[1])
            if dist < 1e-3:
                issues.append(
                    LintIssue(
                        code="ZERO_LENGTH_DIMENSION",
                        severity="ERROR",
                        message="Linear dimension has zero length (start point equals end point).",
                        element=ann,
                        bbox=bbox,
                    )
                )

    # 3. Check Annotation Overlaps (pairwise)
    for i in range(len(ann_bboxes)):
        ann1, b1 = ann_bboxes[i]
        for j in range(i + 1, len(ann_bboxes)):
            ann2, b2 = ann_bboxes[j]
            # Discard if same instance compared against itself
            if ann1 is ann2:
                continue
            # Discard pairs if both are in a group or centermark on hole
            if type(ann1).__name__ == "CenterMark" or type(ann2).__name__ == "CenterMark":
                continue
            # Overlap test with small tolerance
            ox = max(0.0, min(b1[2], b2[2]) - max(b1[0], b2[0]))
            oy = max(0.0, min(b1[3], b2[3]) - max(b1[1], b2[1]))
            # Significant overlap area
            if ox > 4.0 and oy > 3.0:
                issues.append(
                    LintIssue(
                        code="OVERLAP_ANNOTATION",
                        severity="WARNING",
                        message=f"Annotation '{type(ann1).__name__}' overlaps with '{type(ann2).__name__}'.",
                        element=ann1,
                        bbox=b1,
                    )
                )

    return issues
