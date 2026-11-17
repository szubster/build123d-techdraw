"""Drawing quality linter: checks for overlapping annotations, margin boundary violations, and missing features."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from build123d_techdraw.annotations.base import Annotation, BBox2D
from build123d_techdraw.annotations.centerlines import Centerline
from build123d_techdraw.annotations.dimensions import LinearDimension
from build123d_techdraw.annotations.tables import Table

if TYPE_CHECKING:
    from build123d_techdraw.core.sheet import DrawingSheet
    from build123d_techdraw.core.view import SheetView


@dataclass
class LintIssue:
    """Represents a potential drafting defect or layout warning found by the linter."""

    code: str
    severity: str  # "WARNING" or "ERROR"
    message: str
    element: Annotation | SheetView | None = None
    bbox: BBox2D | None = None


def get_title_block_bbox(sheet: DrawingSheet) -> tuple[float, float, float, float]:
    """Return the ISO 7200 title block exclusion bounding box (min_x, min_y, max_x, max_y)."""
    tb_w = 180.0
    tb_h = 36.0 + (
        len(sheet.title_info.extra_fields) * 3.5 if sheet.title_info.extra_fields else 0.0
    )
    return (
        sheet.width_mm - sheet.right_margin - tb_w,
        sheet.height_mm - sheet.bottom_margin - tb_h,
        sheet.width_mm - sheet.right_margin,
        sheet.height_mm - sheet.bottom_margin,
    )


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
    tb_bbox = get_title_block_bbox(sheet)

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
    ann_bboxes: list[tuple[Annotation, BBox2D]] = []

    for ann in sheet.annotations:
        bbox = ann.bounding_box_2d()
        if ann.anchor_stale:
            reason = ann.stale_reason or "Semantic anchor failed to resolve"
            issues.append(
                LintIssue(
                    code="STALE_ANCHOR",
                    severity="WARNING",
                    message=f"Stale semantic anchor on {type(ann).__name__}: {reason}",
                    element=ann,
                    bbox=bbox,
                )
            )
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
            sheet.show_title_block
            and not (
                bbox[2] < tb_bbox[0]
                or bbox[0] > tb_bbox[2]
                or bbox[3] < tb_bbox[1]
                or bbox[1] > tb_bbox[3]
            )
            and (not isinstance(ann, Table) or bbox[1] < tb_bbox[1] - 5.0)
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
        if isinstance(ann, (LinearDimension, Centerline)):
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
