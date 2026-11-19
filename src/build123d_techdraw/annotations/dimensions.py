"""ISO 129 compliant engineering dimensioning in true 2D paper space."""

from __future__ import annotations

import html
import math
import warnings
from enum import Enum
from typing import TYPE_CHECKING, Any

from build123d_techdraw.annotations.anchors import Anchor
from build123d_techdraw.annotations.base import Annotation, BBox2D
from build123d_techdraw.annotations.resolver import (
    ensure_anchor,
    is_topological_entity,
    resolve_point_3d,
    resolve_point_to_sheet,
    stale_point_to_sheet,
    stale_radius_to_paper,
    stale_reason_of,
)
from build123d_techdraw.context import CURRENT_SHEET, CURRENT_VIEW

if TYPE_CHECKING:
    from build123d_techdraw.core.view import SheetView


class DimensionType(Enum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    ALIGNED = "aligned"
    RADIAL = "radial"
    DIAMETER = "diameter"
    ANGULAR = "angular"
    ORDINATE = "ordinate"


def format_dimension_value(value: float, precision: int = 2) -> str:
    """Format dimension numerical value cleanly without redundant trailing zeros."""
    formatted = f"{value:.{precision}f}"
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    return formatted


_TEXT_CHAR_WIDTH_FACTOR = 0.6
"""Approximate advance width of one dimension-text character, as a fraction of font size."""

_JOG_HALF_WIDTH_MM = 3.5
"""Half the length of the break jog drawn in a dimension line across a view break."""


def _estimate_text_width(text: str, font_size: float) -> float:
    """Approximate paper width (mm) of a single line of dimension text."""
    return len(text) * font_size * _TEXT_CHAR_WIDTH_FACTOR


def _text_centre_clear_of_jogs(
    lo: float, hi: float, jogs: list[float], text_len: float, reserve: float
) -> float:
    """Centre position for text on a dimension line ``lo``..``hi`` that avoids break jogs.

    Keeps the text centred on the line when that is clear of every jog; otherwise centres it on
    the longest jog-free piece of the line (``reserve`` mm at each end is kept for arrowheads).
    """
    centre = (lo + hi) / 2.0
    clearance = _JOG_HALF_WIDTH_MM + 1.0
    if all(abs(centre - j) >= clearance + text_len / 2.0 for j in jogs):
        return centre
    cuts = [lo + reserve]
    for j in sorted(jogs):
        cuts.extend((j - clearance, j + clearance))
    cuts.append(hi - reserve)
    pieces = [(cuts[i], cuts[i + 1]) for i in range(0, len(cuts), 2)]
    a, b = max(pieces, key=lambda piece: piece[1] - piece[0])
    return (a + b) / 2.0


def _view_axis_delta(
    view: Any,
    p0: tuple[float, float, float],
    p1: tuple[float, float, float],
    horizontal: bool,
) -> float:
    """Model-space distance between two 3D points along a view's horizontal or vertical axis.

    Uses the view's own projection axes (``proj_x``/``proj_y``) when available, otherwise the
    axes derived from ``VIEW_VECTORS`` for its view type. Unlike ``to_sheet_3d`` this ignores
    break-line shifts, so the value is the true model length.
    """
    import build123d as bd

    from build123d_techdraw.core.view import VIEW_VECTORS, ViewType

    axis = getattr(view, "proj_x" if horizontal else "proj_y", None)
    if not isinstance(axis, bd.Vector):
        view_type = getattr(view, "view_type", ViewType.FRONT)
        look_from, look_up = VIEW_VECTORS.get(view_type, VIEW_VECTORS[ViewType.FRONT])
        p_dir = bd.Vector(look_from).normalized()
        p_x = bd.Vector(look_up).normalized().cross(p_dir)
        axis = p_x if horizontal else p_dir.cross(p_x)
    delta = bd.Vector(p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
    return abs(float(delta.dot(axis)))


class Tolerance(str):
    """Engineering dimension tolerance specification (bilateral, symmetric, unilateral, ISO fit, or limits)."""

    kind: str
    upper_val: float | None
    lower_val: float | None
    fit_class: str | None

    def __new__(
        cls,
        text: str,
        *,
        kind: str = "custom",
        upper: float | None = None,
        lower: float | None = None,
        fit_class: str | None = None,
    ) -> Tolerance:
        instance = super().__new__(cls, text)
        instance.kind = kind
        instance.upper_val = upper
        instance.lower_val = lower
        instance.fit_class = fit_class
        return instance

    @classmethod
    def bilateral(
        cls,
        upper: float,
        lower: float | None = None,
        precision: int = 2,
    ) -> Tolerance:
        """Format a bilateral tolerance (e.g. Tolerance.bilateral(+0.2, -0.1) -> '+0.2 / -0.1')."""
        if lower is None:
            val_str = format_dimension_value(abs(upper), precision)
            return cls(f"±{val_str}", kind="symmetric", upper=abs(upper), lower=-abs(upper))
        u_str = (
            f"+{format_dimension_value(upper, precision)}"
            if upper > 0
            else (f"-{format_dimension_value(abs(upper), precision)}" if upper < 0 else "0")
        )
        l_str = (
            f"+{format_dimension_value(lower, precision)}"
            if lower > 0
            else (f"-{format_dimension_value(abs(lower), precision)}" if lower < 0 else "0")
        )
        return cls(f"{u_str} / {l_str}", kind="bilateral", upper=upper, lower=lower)

    @classmethod
    def symmetric(cls, value: float, precision: int = 2) -> Tolerance:
        """Format a symmetric plus-minus tolerance (e.g. '±0.1')."""
        val_str = format_dimension_value(abs(value), precision)
        return cls(f"±{val_str}", kind="symmetric", upper=abs(value), lower=-abs(value))

    @classmethod
    def unilateral(cls, upper: float, lower: float = 0.0, precision: int = 2) -> Tolerance:
        """Format a unilateral tolerance where one limit is typically zero."""
        return cls.bilateral(upper, lower, precision=precision)

    @classmethod
    def fit(cls, fit_class: str) -> Tolerance:
        """Format an ISO 286 hole/shaft tolerance fit class (e.g. 'H7', 'g6', 'H7/g6')."""
        clean = fit_class.strip()
        return cls(clean, kind="fit", fit_class=clean)

    @classmethod
    def limits(cls, upper_limit: float, lower_limit: float, precision: int = 2) -> Tolerance:
        """Format explicit upper and lower limit dimensions."""
        u_str = format_dimension_value(upper_limit, precision)
        l_str = format_dimension_value(lower_limit, precision)
        return cls(f"{u_str} / {l_str}", kind="limits", upper=upper_limit, lower=lower_limit)


def render_arrowhead(
    tip_x: float,
    tip_y: float,
    dir_x: float,
    dir_y: float,
    length: float = 2.5,
    half_width: float = 0.7,
) -> str:
    """Render a closed filled 30-degree ISO arrowhead pointing in direction (dir_x, dir_y)."""
    dist = math.hypot(dir_x, dir_y)
    if dist == 0.0:
        return ""
    ux = dir_x / dist
    uy = dir_y / dist
    # Perpendicular unit vector
    nx = -uy
    ny = ux

    base_x = tip_x - ux * length
    base_y = tip_y - uy * length

    p1_x = base_x + nx * half_width
    p1_y = base_y + ny * half_width
    p2_x = base_x - nx * half_width
    p2_y = base_y - ny * half_width

    return f'<polygon points="{tip_x:.2f},{tip_y:.2f} {p1_x:.2f},{p1_y:.2f} {p2_x:.2f},{p2_y:.2f}" class="dim-arrow"/>'


class LinearDimension(Annotation):
    """Linear dimension (Horizontal, Vertical, or Aligned) in true 2D paper space."""

    def __init__(
        self,
        target_or_start: Any = None,
        target2_or_end: Any = None,
        offset: float = 8.0,
        dim_type: DimensionType | str = DimensionType.ALIGNED,
        text: str | None = None,
        tolerance: str | Tolerance | None = None,
        prefix: str = "",
        suffix: str = "",
        font_size: float = 3.2,
        arrow_size: float = 2.5,
        start: tuple[float, float] | None = None,
        end: tuple[float, float] | None = None,
        direction: str | None = None,
        nominal: float | None = None,
        view: SheetView | None = None,
        *,
        anchor: Any = None,
        anchor1: Any = None,
        anchor2: Any = None,
    ):
        if target_or_start is None:
            target_or_start = anchor1 if anchor1 is not None else anchor
        if target2_or_end is None and anchor2 is not None:
            target2_or_end = anchor2

        self.offset = offset
        self.dim_type = (
            dim_type if isinstance(dim_type, DimensionType) else DimensionType(dim_type.lower())
        )
        self.nominal = nominal
        self._explicit_text = text is not None or nominal is not None
        self._last_auto_text: str | None = None
        if text is None and nominal is not None:
            self.text: str | None = format_dimension_value(float(nominal))
        else:
            self.text = text
        self.tolerance = str(tolerance) if tolerance is not None else None
        self.prefix = prefix
        self.suffix = suffix
        self.font_size = font_size
        self.arrow_size = arrow_size
        self.direction = direction
        self.view = view
        self.target: Any = None
        self.target2: Any = None
        self.resolved = False
        self._explicit_coords = False
        self.has_break = False
        self.anchor_stale = False
        self.stale_reason: str | None = None

        if start is not None and end is not None:
            self.start = (float(start[0]), float(start[1]))
            self.end = (float(end[0]), float(end[1]))
            self.resolved = True
            self._explicit_coords = True
        elif (
            target_or_start is not None
            and target2_or_end is not None
            and not is_topological_entity(target_or_start)
            and not is_topological_entity(target2_or_end)
            and isinstance(target_or_start, (tuple, list))
            and isinstance(target2_or_end, (tuple, list))
        ):
            self.start = (float(target_or_start[0]), float(target_or_start[1]))
            self.end = (float(target2_or_end[0]), float(target2_or_end[1]))
            self.resolved = True
            self._explicit_coords = True
        else:
            self.start = (0.0, 0.0)
            self.end = (0.0, 0.0)
            self.target = target_or_start
            self.target2 = target2_or_end

        target_view = view or CURRENT_VIEW.get()
        if target_view is not None:
            self._attach_to_view(target_view)
        else:
            target_sheet = CURRENT_SHEET.get()
            if target_sheet is not None and self not in target_sheet.annotations:
                target_sheet.annotations.append(self)

    def _rebind_to_view(self, view: SheetView) -> None:
        """Re-resolve semantic anchors or topological entities against an updated view."""
        if not self._explicit_coords:
            self.resolved = False
            if not self._explicit_text:
                self._last_auto_text = self.text
                self.text = None
            self._attach_to_view(view)

    def _attach_to_view(self, view: SheetView) -> None:
        self.view = view
        if self.direction:
            d_str = self.direction.lower()
            if d_str == "horizontal":
                self.dim_type = DimensionType.HORIZONTAL
            elif d_str == "vertical":
                self.dim_type = DimensionType.VERTICAL
            elif d_str == "aligned":
                self.dim_type = DimensionType.ALIGNED

        if not self.resolved and self.target is not None:
            from build123d_techdraw.annotations.resolver import resolve_edge_to_sheet

            v_shape = view.shape
            self.target = ensure_anchor(self.target, v_shape) or self.target
            self.target2 = ensure_anchor(self.target2, v_shape) or self.target2

            prev_start = self.start
            prev_end = self.end
            prev_text = self._last_auto_text

            if self.target2 is None:
                has_prev = prev_start != (0.0, 0.0) or prev_end != (0.0, 0.0)
                edge_len: float | None
                unresolved_reason: str | None = None
                try:
                    s0, s1, edge_len = resolve_edge_to_sheet(self.target, view)
                except TypeError as exc:
                    # A single target must resolve to an edge. Never invent a second
                    # point (e.g. the view centre): keep the last good geometry if the
                    # anchor went stale, otherwise leave the dimension unresolved.
                    s0, s1, edge_len = prev_start, prev_end, None
                    unresolved_reason = str(exc)

                stale_anchor = (
                    self.target
                    if isinstance(self.target, Anchor) and self.target.is_stale
                    else None
                )
                stale = stale_anchor is not None
                if stale_anchor is not None:
                    self.anchor_stale = True
                    self.stale_reason = stale_anchor.stale_reason
                    if prev_text is not None and not self._explicit_text:
                        self.text = prev_text
                else:
                    self.anchor_stale = False
                    self.stale_reason = None

                if edge_len is None and not (stale and has_prev):
                    self.stale_reason = (
                        self.stale_reason
                        or "Single-target linear dimension requires an edge; "
                        f"target did not resolve to one ({unresolved_reason})."
                    )
                    if not stale:
                        warnings.warn(
                            f"LinearDimension target {type(self.target).__name__} does not "
                            "resolve to an edge; the dimension is left unresolved. Pass an "
                            "edge, or two points/vertices to measure between.",
                            UserWarning,
                            stacklevel=3,
                        )
                    self._register(view)
                    return

                self.start = s0
                self.end = s1
                if self.text is None and edge_len is not None:
                    self.text = format_dimension_value(edge_len)
                dx = abs(s1[0] - s0[0])
                dy = abs(s1[1] - s0[1])
                if self.dim_type == DimensionType.ALIGNED and self.direction is None:
                    if dy < 1e-2:
                        self.dim_type = DimensionType.HORIZONTAL
                    elif dx < 1e-2:
                        self.dim_type = DimensionType.VERTICAL
            else:
                p0_3d = resolve_point_3d(self.target, view)
                p1_3d = resolve_point_3d(self.target2, view)
                # Reuse the resolved 3D points; only 2D tuples need resolve_point_to_sheet
                s0 = (
                    view.to_sheet_3d(*p0_3d)
                    if p0_3d is not None
                    else resolve_point_to_sheet(self.target, view)
                )
                s1 = (
                    view.to_sheet_3d(*p1_3d)
                    if p1_3d is not None
                    else resolve_point_to_sheet(self.target2, view)
                )

                stale1 = isinstance(self.target, Anchor) and self.target.is_stale
                stale2 = isinstance(self.target2, Anchor) and self.target2.is_stale
                if stale1 or stale2:
                    self.anchor_stale = True
                    self.stale_reason = stale_reason_of(self.target, self.target2)
                    s0 = stale_point_to_sheet(self.target, view) or s0
                    s1 = stale_point_to_sheet(self.target2, view) or s1
                    if prev_text is not None and not self._explicit_text:
                        self.text = prev_text
                else:
                    self.anchor_stale = False
                    self.stale_reason = None

                self.start = s0
                self.end = s1
                if self.text is None:
                    if self.dim_type in (DimensionType.HORIZONTAL, DimensionType.VERTICAL):
                        horizontal = self.dim_type == DimensionType.HORIZONTAL
                        if p0_3d is not None and p1_3d is not None:
                            # Measure in model space along the view's projection axis so
                            # breaks (which shorten the paper span) do not leak in.
                            model_dist = _view_axis_delta(view, p0_3d, p1_3d, horizontal)
                        else:
                            paper_span = abs(s1[0] - s0[0]) if horizontal else abs(s1[1] - s0[1])
                            model_dist = paper_span / view.scale if view.scale else paper_span
                    elif p0_3d is not None and p1_3d is not None:
                        model_dist = math.dist(p0_3d, p1_3d)
                    else:
                        paper_dist = math.hypot(s1[0] - s0[0], s1[1] - s0[1])
                        model_dist = paper_dist / view.scale if view.scale else paper_dist
                    self.text = format_dimension_value(float(model_dist))
            self.resolved = True
        elif not self.resolved and self.target is None:
            unbroken_w = view.unbroken_width
            unbroken_h = view.unbroken_height
            if self.dim_type == DimensionType.VERTICAL:
                x_ref = view.min_x if self.offset < 0 else view.max_x
                self.start = (x_ref, view.min_y)
                self.end = (x_ref, view.max_y)
                if self.text is None:
                    model_h = unbroken_h / view.scale if view.scale else unbroken_h
                    if self.nominal is None:
                        self.nominal = float(model_h)
                    self.text = format_dimension_value(float(model_h))
            else:
                self.dim_type = DimensionType.HORIZONTAL
                y_ref = view.min_y if self.offset < 0 else view.max_y
                self.start = (view.min_x, y_ref)
                self.end = (view.max_x, y_ref)
                if self.text is None:
                    model_w = unbroken_w / view.scale if view.scale else view.width
                    if self.nominal is None:
                        self.nominal = float(model_w)
                    self.text = format_dimension_value(float(model_w))
                if self.offset > 0:
                    view.bottom_annotation_y = max(
                        view.bottom_annotation_y, y_ref + self.offset + 4.0
                    )
            self.resolved = True

        if self.resolved:
            self._update_break_flag(view)
        self._register(view)

    def _update_break_flag(self, view: SheetView) -> None:
        """Flag a horizontal/vertical dimension whose span crosses a break of ``view``."""
        if self.dim_type == DimensionType.HORIZONTAL:
            lo, hi = sorted((self.start[0], self.end[0]))
            gaps = view.break_gap_centers("X")
        elif self.dim_type == DimensionType.VERTICAL:
            lo, hi = sorted((self.start[1], self.end[1]))
            gaps = view.break_gap_centers("Y")
        else:
            self.has_break = False
            return
        self.has_break = any(lo < g < hi for g in gaps)

    def _jog_positions(self, lo: float, hi: float, axis: str) -> list[float]:
        """Positions of break jogs on a dimension line spanning ``lo``..``hi`` along ``axis``.

        Jogs sit where the view is actually broken; without view information (or if no break
        gap falls on the line) a single jog is drawn at the middle.
        """
        if not self.has_break:
            return []
        margin = _JOG_HALF_WIDTH_MM + self.arrow_size
        jogs: list[float] = []
        if self.view is not None:
            jogs = [g for g in self.view.break_gap_centers(axis) if lo + margin < g < hi - margin]
        return jogs or [(lo + hi) / 2.0]

    def bounding_box_2d(self) -> BBox2D:
        x1, y1 = self.start
        x2, y2 = self.end
        if self.dim_type == DimensionType.HORIZONTAL:
            dim_y = (y1 + y2) / 2.0 + self.offset
            return (min(x1, x2), dim_y - 3.5, max(x1, x2), dim_y + 2.0)
        if self.dim_type == DimensionType.VERTICAL:
            dim_x = (x1 + x2) / 2.0 + self.offset
            return (dim_x - 3.5, min(y1, y2), dim_x + 2.0, max(y1, y2))
        return (min(x1, x2) - 2.0, min(y1, y2) - 2.0, max(x1, x2) + 2.0, max(y1, y2) + 2.0)

    def to_svg(self) -> str:
        if not self.resolved and self.target is not None:
            # Target could not be resolved to geometry: draw nothing rather than garbage.
            return ""
        x1, y1 = self.start
        x2, y2 = self.end
        ext_gap = 1.5
        ext_overshoot = 2.0

        if self.dim_type == DimensionType.HORIZONTAL:
            # Horizontal dimension across x1, x2
            left_x = min(x1, x2)
            right_x = max(x1, x2)
            base_y = (y1 + y2) / 2.0
            dim_y = base_y + self.offset
            dist = right_x - left_x

            disp_val = self.text if self.text is not None else format_dimension_value(dist)
            full_str = f"{self.prefix}{disp_val}{self.suffix}"
            if self.tolerance:
                full_str += f" {self.tolerance}"

            gap_dir = 1.0 if self.offset >= 0 else -1.0
            ext_y_start = base_y + gap_dir * ext_gap
            ext_y_end = dim_y + gap_dir * ext_overshoot

            is_narrow = dist < (2.0 * self.arrow_size + 4.0)
            if is_narrow:
                arrow_elems = [
                    f'<line x1="{left_x - 5.0:.2f}" y1="{dim_y:.2f}" x2="{left_x:.2f}" y2="{dim_y:.2f}" class="dim-line" stroke-width="0.3"/>',
                    render_arrowhead(left_x, dim_y, 1.0, 0.0, length=self.arrow_size),
                    f'<line x1="{right_x:.2f}" y1="{dim_y:.2f}" x2="{right_x + 5.0:.2f}" y2="{dim_y:.2f}" class="dim-line" stroke-width="0.3"/>',
                    render_arrowhead(right_x, dim_y, -1.0, 0.0, length=self.arrow_size),
                ]
                text_x = right_x + 6.0 if dist < 8.0 else (left_x + right_x) / 2.0
                text_anchor = "start" if dist < 8.0 else "middle"
                text_elem = f'<text x="{text_x:.2f}" y="{dim_y - 1.2:.2f}" class="dim-text" text-anchor="{text_anchor}" font-size="{self.font_size:.2f}">{html.escape(full_str)}</text>'
                jogs: list[float] = []
            else:
                jogs = self._jog_positions(left_x, right_x, "X")
                arrow_elems = [
                    render_arrowhead(left_x, dim_y, -1.0, 0.0, length=self.arrow_size),
                    render_arrowhead(right_x, dim_y, 1.0, 0.0, length=self.arrow_size),
                ]
                text_w = _estimate_text_width(full_str, self.font_size)
                text_x = _text_centre_clear_of_jogs(left_x, right_x, jogs, text_w, self.arrow_size)
                text_elem = f'<text x="{text_x:.2f}" y="{dim_y - 1.2:.2f}" class="dim-text" text-anchor="middle" font-size="{self.font_size:.2f}">{html.escape(full_str)}</text>'

            # Dimension line
            if jogs:
                jw, jh = _JOG_HALF_WIDTH_MM, 2.5
                parts: list[str] = []
                seg_start = left_x
                for jog_x in jogs:
                    parts.append(
                        f'<line x1="{seg_start:.2f}" y1="{dim_y:.2f}" x2="{jog_x - jw:.2f}" y2="{dim_y:.2f}" class="dim-line" stroke-width="0.3"/>'
                        f'<polyline points="{jog_x - jw:.2f},{dim_y:.2f} {jog_x - jw / 3.0:.2f},{dim_y - jh:.2f} {jog_x + jw / 3.0:.2f},{dim_y + jh:.2f} {jog_x + jw:.2f},{dim_y:.2f}" class="dim-line break-jog" stroke-width="0.3" fill="none"/>'
                    )
                    seg_start = jog_x + jw
                parts.append(
                    f'<line x1="{seg_start:.2f}" y1="{dim_y:.2f}" x2="{right_x:.2f}" y2="{dim_y:.2f}" class="dim-line" stroke-width="0.3"/>'
                )
                dim_line_elem = "".join(parts)
            else:
                dim_line_elem = f'<line x1="{left_x:.2f}" y1="{dim_y:.2f}" x2="{right_x:.2f}" y2="{dim_y:.2f}" class="dim-line" stroke-width="0.3"/>'

            elements = [
                # Extension lines
                f'<line x1="{left_x:.2f}" y1="{ext_y_start:.2f}" x2="{left_x:.2f}" y2="{ext_y_end:.2f}" class="dim-line" stroke-width="0.25"/>',
                f'<line x1="{right_x:.2f}" y1="{ext_y_start:.2f}" x2="{right_x:.2f}" y2="{ext_y_end:.2f}" class="dim-line" stroke-width="0.25"/>',
                # Dimension line
                dim_line_elem,
                *arrow_elems,
                text_elem,
            ]
            return "".join(elements)

        elif self.dim_type == DimensionType.VERTICAL:
            # Vertical dimension across y1, y2
            top_y = min(y1, y2)
            bottom_y = max(y1, y2)
            base_x = (x1 + x2) / 2.0
            dim_x = base_x + self.offset
            dist = bottom_y - top_y

            disp_val = self.text if self.text is not None else format_dimension_value(dist)
            full_str = f"{self.prefix}{disp_val}{self.suffix}"
            if self.tolerance:
                full_str += f" {self.tolerance}"

            gap_dir = 1.0 if self.offset >= 0 else -1.0
            ext_x_start = base_x + gap_dir * ext_gap
            ext_x_end = dim_x + gap_dir * ext_overshoot

            mid_y = (top_y + bottom_y) / 2.0
            is_narrow = dist < (2.0 * self.arrow_size + 4.0)
            if is_narrow:
                arrow_elems = [
                    f'<line x1="{dim_x:.2f}" y1="{top_y - 5.0:.2f}" x2="{dim_x:.2f}" y2="{top_y:.2f}" class="dim-line" stroke-width="0.3"/>',
                    render_arrowhead(dim_x, top_y, 0.0, 1.0, length=self.arrow_size),
                    f'<line x1="{dim_x:.2f}" y1="{bottom_y:.2f}" x2="{dim_x:.2f}" y2="{bottom_y + 5.0:.2f}" class="dim-line" stroke-width="0.3"/>',
                    render_arrowhead(dim_x, bottom_y, 0.0, -1.0, length=self.arrow_size),
                ]
            else:
                arrow_elems = [
                    render_arrowhead(dim_x, top_y, 0.0, -1.0, length=self.arrow_size),
                    render_arrowhead(dim_x, bottom_y, 0.0, 1.0, length=self.arrow_size),
                ]

            jogs = [] if is_narrow else self._jog_positions(top_y, bottom_y, "Y")
            if jogs:
                mid_y = _text_centre_clear_of_jogs(
                    top_y,
                    bottom_y,
                    jogs,
                    _estimate_text_width(full_str, self.font_size),
                    self.arrow_size,
                )
                jw, jh = 2.5, _JOG_HALF_WIDTH_MM
                v_parts: list[str] = []
                seg_start = top_y
                for jog_y in jogs:
                    v_parts.append(
                        f'<line x1="{dim_x:.2f}" y1="{seg_start:.2f}" x2="{dim_x:.2f}" y2="{jog_y - jh:.2f}" class="dim-line" stroke-width="0.3"/>'
                        f'<polyline points="{dim_x:.2f},{jog_y - jh:.2f} {dim_x - jw:.2f},{jog_y - jh / 3.0:.2f} {dim_x + jw:.2f},{jog_y + jh / 3.0:.2f} {dim_x:.2f},{jog_y + jh:.2f}" class="dim-line break-jog" stroke-width="0.3" fill="none"/>'
                    )
                    seg_start = jog_y + jh
                v_parts.append(
                    f'<line x1="{dim_x:.2f}" y1="{seg_start:.2f}" x2="{dim_x:.2f}" y2="{bottom_y:.2f}" class="dim-line" stroke-width="0.3"/>'
                )
                dim_line_elem = "".join(v_parts)
            else:
                dim_line_elem = f'<line x1="{dim_x:.2f}" y1="{top_y:.2f}" x2="{dim_x:.2f}" y2="{bottom_y:.2f}" class="dim-line" stroke-width="0.3"/>'

            elements = [
                # Extension lines
                f'<line x1="{ext_x_start:.2f}" y1="{top_y:.2f}" x2="{ext_x_end:.2f}" y2="{top_y:.2f}" class="dim-line" stroke-width="0.25"/>',
                f'<line x1="{ext_x_start:.2f}" y1="{bottom_y:.2f}" x2="{ext_x_end:.2f}" y2="{bottom_y:.2f}" class="dim-line" stroke-width="0.25"/>',
                # Dimension line
                dim_line_elem,
                *arrow_elems,
                # Rotated dimension text
                f'<text x="{dim_x - 1.4:.2f}" y="{mid_y:.2f}" transform="rotate(-90 {dim_x - 1.4:.2f} {mid_y:.2f})" class="dim-text" text-anchor="middle" font-size="{self.font_size:.2f}">{html.escape(full_str)}</text>',
            ]
            return "".join(elements)

        else:
            # General aligned dimension along arbitrary vector
            dx = x2 - x1
            dy = y2 - y1
            length = math.hypot(dx, dy)
            if length == 0.0:
                return ""
            ux = dx / length
            uy = dy / length
            # Perpendicular normal
            nx = -uy
            ny = ux

            dim_x1 = x1 + nx * self.offset
            dim_y1 = y1 + ny * self.offset
            dim_x2 = x2 + nx * self.offset
            dim_y2 = y2 + ny * self.offset

            disp_val = self.text if self.text is not None else format_dimension_value(length)
            full_str = f"{self.prefix}{disp_val}{self.suffix}"
            if self.tolerance:
                full_str += f" {self.tolerance}"

            gap_dir = 1.0 if self.offset >= 0 else -1.0
            ext1_start_x = x1 + nx * gap_dir * ext_gap
            ext1_start_y = y1 + ny * gap_dir * ext_gap
            ext1_end_x = dim_x1 + nx * gap_dir * ext_overshoot
            ext1_end_y = dim_y1 + ny * gap_dir * ext_overshoot

            ext2_start_x = x2 + nx * gap_dir * ext_gap
            ext2_start_y = y2 + ny * gap_dir * ext_gap
            ext2_end_x = dim_x2 + nx * gap_dir * ext_overshoot
            ext2_end_y = dim_y2 + ny * gap_dir * ext_overshoot

            mid_x = (dim_x1 + dim_x2) / 2.0
            mid_y = (dim_y1 + dim_y2) / 2.0
            angle_deg = math.degrees(math.atan2(dy, dx))
            # Keep text readable (never upside down)
            if angle_deg > 90.0 or angle_deg <= -90.0:
                angle_deg += 180.0

            text_offset_dist = 1.5
            text_x = mid_x + nx * text_offset_dist
            text_y = mid_y + ny * text_offset_dist

            elements = [
                # Extension lines
                f'<line x1="{ext1_start_x:.2f}" y1="{ext1_start_y:.2f}" x2="{ext1_end_x:.2f}" y2="{ext1_end_y:.2f}" class="dim-line" stroke-width="0.25"/>',
                f'<line x1="{ext2_start_x:.2f}" y1="{ext2_start_y:.2f}" x2="{ext2_end_x:.2f}" y2="{ext2_end_y:.2f}" class="dim-line" stroke-width="0.25"/>',
                # Dimension line
                f'<line x1="{dim_x1:.2f}" y1="{dim_y1:.2f}" x2="{dim_x2:.2f}" y2="{dim_y2:.2f}" class="dim-line" stroke-width="0.3"/>',
                # Arrowheads
                render_arrowhead(dim_x1, dim_y1, -ux, -uy, length=self.arrow_size),
                render_arrowhead(dim_x2, dim_y2, ux, uy, length=self.arrow_size),
                # Aligned text
                f'<text x="{text_x:.2f}" y="{text_y:.2f}" transform="rotate({angle_deg:.1f} {text_x:.2f} {text_y:.2f})" class="dim-text" text-anchor="middle" font-size="{self.font_size:.2f}">{html.escape(full_str)}</text>',
            ]
            return "".join(elements)


class RadialDimension(Annotation):
    """Radius dimension (R) pointing to circular arc."""

    def __init__(
        self,
        target_or_center: Any = None,
        radius: float | None = None,
        angle_deg: float = 45.0,
        prefix: str = "",
        suffix: str = "",
        text: str | None = None,
        tolerance: str | Tolerance | None = None,
        shelf_length: float = 12.0,
        arrow_size: float = 2.5,
        font_size: float = 3.2,
        center: tuple[float, float] | None = None,
        nominal: float | None = None,
        view: SheetView | None = None,
        *,
        anchor: Any = None,
    ):
        if target_or_center is None and anchor is not None:
            target_or_center = anchor

        self.angle_deg = angle_deg
        self.prefix = prefix
        self.suffix = suffix
        self.nominal = nominal
        self._explicit_text = text is not None or nominal is not None
        self._last_auto_text: str | None = None
        if text is None and nominal is not None:
            self.text: str | None = f"R{format_dimension_value(float(nominal))}"
        else:
            self.text = text
        self.tolerance = str(tolerance) if tolerance is not None else None
        self.shelf_length = shelf_length
        self.arrow_size = arrow_size
        self.font_size = font_size
        self.view = view
        self.target: Any = None
        self.resolved = False
        self._explicit_coords = False
        self.anchor_stale = False
        self.stale_reason: str | None = None

        if center is not None and radius is not None:
            self.center = (float(center[0]), float(center[1]))
            self.radius = float(radius)
            self.resolved = True
            self._explicit_coords = True
        elif (
            target_or_center is not None
            and radius is not None
            and not is_topological_entity(target_or_center)
            and isinstance(target_or_center, (tuple, list))
        ):
            self.center = (float(target_or_center[0]), float(target_or_center[1]))
            self.radius = float(radius)
            self.resolved = True
            self._explicit_coords = True
        else:
            self.center = (0.0, 0.0)
            self.radius = float(radius) if radius is not None else 0.0
            self.target = target_or_center

        target_view = view or CURRENT_VIEW.get()
        if target_view is not None:
            self._attach_to_view(target_view)
        else:
            target_sheet = CURRENT_SHEET.get()
            if target_sheet is not None and self not in target_sheet.annotations:
                target_sheet.annotations.append(self)

    def _rebind_to_view(self, view: SheetView) -> None:
        if not self._explicit_coords:
            self.resolved = False
            if not self._explicit_text:
                self._last_auto_text = self.text
                self.text = None
            self._attach_to_view(view)

    def _attach_to_view(self, view: SheetView) -> None:
        self.view = view
        if not self.resolved and self.target is not None:
            from build123d_techdraw.annotations.resolver import resolve_circle_or_cylinder_to_sheet

            if not isinstance(self.target, (tuple, list)):
                self.target = ensure_anchor(self.target, view.shape) or self.target
            prev_text = self._last_auto_text

            try:
                paper_c, paper_r, model_r = resolve_circle_or_cylinder_to_sheet(self.target, view)
                self.center = paper_c
                self.radius = paper_r
                if self.text is None:
                    self.text = f"R{format_dimension_value(model_r)}"
            except TypeError:
                self.center = resolve_point_to_sheet(self.target, view)

            if isinstance(self.target, Anchor) and self.target.is_stale:
                self.anchor_stale = True
                self.stale_reason = self.target.stale_reason
                self.center = stale_point_to_sheet(self.target, view) or self.center
                stale_r = stale_radius_to_paper(self.target, view)
                if stale_r is not None:
                    self.radius = stale_r
                if prev_text is not None and not self._explicit_text:
                    self.text = prev_text
            else:
                self.anchor_stale = False
                self.stale_reason = None
            self.resolved = True
        elif not self.resolved and self.target is None:
            self.center = (view.center_x, view.center_y)
            self.resolved = True

        self._register(view)

    def to_svg(self) -> str:
        cx, cy = self.center
        rad = math.radians(self.angle_deg)
        ux = math.cos(rad)
        uy = -math.sin(rad)  # SVG y is inverted

        arc_x = cx + ux * self.radius
        arc_y = cy + uy * self.radius

        # Leader extension beyond arc
        leader_len = 10.0
        elbow_x = arc_x + ux * leader_len
        elbow_y = arc_y + uy * leader_len

        shelf_dir = 1.0 if ux >= 0 else -1.0
        shelf_x = elbow_x + shelf_dir * self.shelf_length

        disp_val = self.text if self.text is not None else f"R{format_dimension_value(self.radius)}"
        full_str = f"{self.prefix}{disp_val}{self.suffix}"
        if self.tolerance:
            full_str += f" {self.tolerance}"

        text_anchor = "start" if shelf_dir > 0 else "end"
        text_x = elbow_x + (2.0 if shelf_dir > 0 else -2.0)
        text_y = elbow_y - 1.2

        elements = [
            # Leader line from arc to elbow and shelf
            f'<polyline points="{arc_x:.2f},{arc_y:.2f} {elbow_x:.2f},{elbow_y:.2f} {shelf_x:.2f},{elbow_y:.2f}" class="dim-line" stroke-width="0.3" fill="none"/>',
            # Arrowhead touching the arc
            render_arrowhead(arc_x, arc_y, -ux, -uy, length=self.arrow_size),
            # Text
            f'<text x="{text_x:.2f}" y="{text_y:.2f}" class="dim-text" text-anchor="{text_anchor}" font-size="{self.font_size:.2f}">{html.escape(full_str)}</text>',
        ]
        return f'<g class="radial-dim">{"".join(elements)}</g>'


class DiameterDimension(Annotation):
    """Diameter dimension (⌀) across full circle or with leader."""

    def __init__(
        self,
        target_or_center: Any = None,
        diameter: float | None = None,
        angle_deg: float = 45.0,
        prefix: str = "",
        suffix: str = "",
        text: str | None = None,
        tolerance: str | Tolerance | None = None,
        shelf_length: float = 12.0,
        arrow_size: float = 2.5,
        font_size: float = 3.2,
        center: tuple[float, float] | None = None,
        nominal: float | None = None,
        view: SheetView | None = None,
        *,
        anchor: Any = None,
    ):
        if target_or_center is None and anchor is not None:
            target_or_center = anchor

        self.angle_deg = angle_deg
        self.prefix = prefix
        self.suffix = suffix
        self.nominal = nominal
        self._explicit_text = text is not None or nominal is not None
        self._last_auto_text: str | None = None
        if text is None and nominal is not None:
            self.text: str | None = f"⌀{format_dimension_value(float(nominal))}"
        else:
            self.text = text
        self.tolerance = str(tolerance) if tolerance is not None else None
        self.shelf_length = shelf_length
        self.arrow_size = arrow_size
        self.font_size = font_size
        self.view = view
        self.target: Any = None
        self.resolved = False
        self._explicit_coords = False
        self.anchor_stale = False
        self.stale_reason: str | None = None

        if center is not None and diameter is not None:
            self.center = (float(center[0]), float(center[1]))
            self.diameter = float(diameter)
            self.resolved = True
            self._explicit_coords = True
        elif (
            target_or_center is not None
            and diameter is not None
            and not is_topological_entity(target_or_center)
            and isinstance(target_or_center, (tuple, list))
        ):
            self.center = (float(target_or_center[0]), float(target_or_center[1]))
            self.diameter = float(diameter)
            self.resolved = True
            self._explicit_coords = True
        else:
            self.center = (0.0, 0.0)
            self.diameter = float(diameter) if diameter is not None else 0.0
            self.target = target_or_center

        target_view = view or CURRENT_VIEW.get()
        if target_view is not None:
            self._attach_to_view(target_view)
        else:
            target_sheet = CURRENT_SHEET.get()
            if target_sheet is not None and self not in target_sheet.annotations:
                target_sheet.annotations.append(self)

    def _rebind_to_view(self, view: SheetView) -> None:
        if not self._explicit_coords:
            self.resolved = False
            if not self._explicit_text:
                self._last_auto_text = self.text
                self.text = None
            self._attach_to_view(view)

    def _attach_to_view(self, view: SheetView) -> None:
        self.view = view
        if not self.resolved and self.target is not None:
            from build123d_techdraw.annotations.resolver import resolve_circle_or_cylinder_to_sheet

            if not isinstance(self.target, (tuple, list)):
                self.target = ensure_anchor(self.target, view.shape) or self.target
            prev_text = self._last_auto_text

            try:
                paper_c, paper_r, model_r = resolve_circle_or_cylinder_to_sheet(self.target, view)
                self.center = paper_c
                self.diameter = 2.0 * paper_r
                if self.text is None:
                    self.text = f"⌀{format_dimension_value(2.0 * model_r)}"
            except TypeError:
                self.center = resolve_point_to_sheet(self.target, view)

            if isinstance(self.target, Anchor) and self.target.is_stale:
                self.anchor_stale = True
                self.stale_reason = self.target.stale_reason
                self.center = stale_point_to_sheet(self.target, view) or self.center
                stale_r = stale_radius_to_paper(self.target, view)
                if stale_r is not None:
                    self.diameter = 2.0 * stale_r
                if prev_text is not None and not self._explicit_text:
                    self.text = prev_text
            else:
                self.anchor_stale = False
                self.stale_reason = None
            self.resolved = True
        elif not self.resolved and self.target is None:
            self.center = (view.center_x, view.center_y)
            self.resolved = True

        self._register(view)

    def to_svg(self) -> str:
        cx, cy = self.center
        radius = self.diameter / 2.0
        rad = math.radians(self.angle_deg)
        ux = math.cos(rad)
        uy = -math.sin(rad)

        p1_x = cx - ux * radius
        p1_y = cy - uy * radius
        p2_x = cx + ux * radius
        p2_y = cy + uy * radius

        leader_len = 10.0
        elbow_x = p2_x + ux * leader_len
        elbow_y = p2_y + uy * leader_len

        shelf_dir = 1.0 if ux >= 0 else -1.0
        shelf_x = elbow_x + shelf_dir * self.shelf_length

        disp_val = (
            self.text if self.text is not None else f"⌀{format_dimension_value(self.diameter)}"
        )
        full_str = f"{self.prefix}{disp_val}{self.suffix}"
        if self.tolerance:
            full_str += f" {self.tolerance}"

        text_anchor = "start" if shelf_dir > 0 else "end"
        text_x = elbow_x + (2.0 if shelf_dir > 0 else -2.0)
        text_y = elbow_y - 1.2

        elements = [
            # Diametral line through circle
            f'<line x1="{p1_x:.2f}" y1="{p1_y:.2f}" x2="{p2_x:.2f}" y2="{p2_y:.2f}" class="dim-line" stroke-width="0.3"/>',
            # Leader to shelf
            f'<polyline points="{p2_x:.2f},{p2_y:.2f} {elbow_x:.2f},{elbow_y:.2f} {shelf_x:.2f},{elbow_y:.2f}" class="dim-line" stroke-width="0.3" fill="none"/>',
            # Dual arrowheads inside circle
            render_arrowhead(p1_x, p1_y, -ux, -uy, length=self.arrow_size),
            render_arrowhead(p2_x, p2_y, ux, uy, length=self.arrow_size),
            # Text
            f'<text x="{text_x:.2f}" y="{text_y:.2f}" class="dim-text" text-anchor="{text_anchor}" font-size="{self.font_size:.2f}">{html.escape(full_str)}</text>',
        ]
        return f'<g class="diameter-dim">{"".join(elements)}</g>'


class AngularDimension(Annotation):
    """ISO 129 compliant angular dimension measuring angles between edges, faces, or rays."""

    def __init__(
        self,
        target1: Any = None,
        target2: Any = None,
        radius: float = 20.0,
        text: str | None = None,
        tolerance: str | Tolerance | None = None,
        prefix: str = "",
        suffix: str = "°",
        arrow_size: float = 2.5,
        font_size: float = 3.2,
        apex: tuple[float, float] | None = None,
        start_angle_deg: float | None = None,
        end_angle_deg: float | None = None,
        nominal: float | None = None,
        view: SheetView | None = None,
        *,
        anchor1: Any = None,
        anchor2: Any = None,
    ):
        if target1 is None and anchor1 is not None:
            target1 = anchor1
        if target2 is None and anchor2 is not None:
            target2 = anchor2

        self.radius = radius
        self.prefix = prefix
        self.suffix = suffix
        self.arrow_size = arrow_size
        self.font_size = font_size
        self.tolerance = str(tolerance) if tolerance is not None else None
        self.view = view
        self.target1 = target1
        self.target2 = target2
        self._explicit_text = text is not None or nominal is not None
        self._last_auto_text: str | None = None
        self._explicit_coords = False
        self.anchor_stale = False
        self.stale_reason: str | None = None

        if text is not None:
            self.text: str | None = text
        elif nominal is not None:
            self.text = f"{format_dimension_value(float(nominal))}{suffix}"
        else:
            self.text = None

        if apex is not None and start_angle_deg is not None and end_angle_deg is not None:
            self.apex = (float(apex[0]), float(apex[1]))
            self.start_angle_deg = float(start_angle_deg)
            self.end_angle_deg = float(end_angle_deg)
            self.resolved = True
            self._explicit_coords = True
        else:
            self.apex = (0.0, 0.0)
            self.start_angle_deg = 0.0
            self.end_angle_deg = 90.0
            self.resolved = False

        target_view = view or CURRENT_VIEW.get()
        if target_view is not None:
            self._attach_to_view(target_view)
        else:
            target_sheet = CURRENT_SHEET.get()
            if target_sheet is not None and self not in target_sheet.annotations:
                target_sheet.annotations.append(self)

    def _rebind_to_view(self, view: SheetView) -> None:
        if not self._explicit_coords:
            self.resolved = False
            if not self._explicit_text:
                self._last_auto_text = self.text
                self.text = None
            self._attach_to_view(view)

    def _attach_to_view(self, view: SheetView) -> None:
        self.view = view
        if not self.resolved and self.target1 is not None and self.target2 is not None:
            from build123d_techdraw.annotations.resolver import resolve_angle_to_sheet

            v_shape = view.shape
            self.target1 = ensure_anchor(self.target1, v_shape) or self.target1
            self.target2 = ensure_anchor(self.target2, v_shape) or self.target2

            prev_text = self._last_auto_text

            ap, a1, a2, angle_val = resolve_angle_to_sheet(self.target1, self.target2, view)

            stale1 = isinstance(self.target1, Anchor) and self.target1.is_stale
            stale2 = isinstance(self.target2, Anchor) and self.target2.is_stale
            if stale1 or stale2:
                self.anchor_stale = True
                self.stale_reason = stale_reason_of(self.target1, self.target2)
                if prev_text is not None and not self._explicit_text:
                    self.text = prev_text
            else:
                self.anchor_stale = False
                self.stale_reason = None

            self.apex = ap
            self.start_angle_deg = a1
            self.end_angle_deg = a2
            if self.text is None:
                self.text = f"{format_dimension_value(angle_val)}{self.suffix}"
            self.resolved = True
        elif not self.resolved:
            self.apex = (view.center_x, view.center_y)
            self.resolved = True

        self._register(view)

    def to_svg(self) -> str:
        cx, cy = self.apex
        r = self.radius
        a1 = self.start_angle_deg % 360.0
        a2 = self.end_angle_deg % 360.0
        diff = (a2 - a1) % 360.0

        rad1 = math.radians(a1)
        rad2 = math.radians(a2)

        # SVG y is downwards, math angles go CCW
        p1_x = cx + r * math.cos(rad1)
        p1_y = cy - r * math.sin(rad1)
        p2_x = cx + r * math.cos(rad2)
        p2_y = cy - r * math.sin(rad2)

        large_arc = 1 if diff > 180.0 else 0
        # In SVG y-down, moving CCW uses sweep-flag = 0
        arc_d = f"M {p1_x:.2f} {p1_y:.2f} A {r:.2f} {r:.2f} 0 {large_arc} 0 {p2_x:.2f} {p2_y:.2f}"

        # Tangent vectors
        # Tangent along CCW direction at a1: (-sin(a1), -cos(a1) in svg)
        t1_x = -math.sin(rad1)
        t1_y = -math.cos(rad1)
        # Tangent along CW direction at a2 (pointing into the arc from p2): (sin(a2), cos(a2) in svg)
        t2_x = math.sin(rad2)
        t2_y = math.cos(rad2)

        # Extension lines from apex out to arc
        ext1_x = cx + (r + 2.0) * math.cos(rad1)
        ext1_y = cy - (r + 2.0) * math.sin(rad1)
        ext2_x = cx + (r + 2.0) * math.cos(rad2)
        ext2_y = cy - (r + 2.0) * math.sin(rad2)

        # Mid angle for label
        mid_rad = math.radians((a1 + diff / 2.0) % 360.0)
        text_r = r + 3.0
        text_x = cx + text_r * math.cos(mid_rad)
        text_y = cy - text_r * math.sin(mid_rad)

        disp_val = (
            self.text if self.text is not None else f"{format_dimension_value(diff)}{self.suffix}"
        )
        full_str = f"{self.prefix}{disp_val}"
        if self.tolerance:
            full_str += f" {self.tolerance}"

        elements = [
            f'<line x1="{cx:.2f}" y1="{cy:.2f}" x2="{ext1_x:.2f}" y2="{ext1_y:.2f}" class="dim-line" stroke-width="0.25"/>',
            f'<line x1="{cx:.2f}" y1="{cy:.2f}" x2="{ext2_x:.2f}" y2="{ext2_y:.2f}" class="dim-line" stroke-width="0.25"/>',
            f'<path d="{arc_d}" class="dim-line" stroke-width="0.3" fill="none"/>',
            render_arrowhead(p1_x, p1_y, -t1_x, -t1_y, length=self.arrow_size),
            render_arrowhead(p2_x, p2_y, -t2_x, -t2_y, length=self.arrow_size),
            f'<text x="{text_x:.2f}" y="{text_y:.2f}" class="dim-text" text-anchor="middle" font-size="{self.font_size:.2f}">{html.escape(full_str)}</text>',
        ]
        return f'<g class="angular-dim">{"".join(elements)}</g>'


class OrdinateDimension(Annotation):
    """Single ordinate dimension point with extension line, jog, and coordinate text."""

    def __init__(
        self,
        target: Any = None,
        datum_coord: float = 0.0,
        direction: str = "horizontal",
        jog: float = 0.0,
        extension_length: float = 12.0,
        font_size: float = 3.2,
        prefix: str = "",
        suffix: str = "",
        view: SheetView | None = None,
        *,
        anchor: Any = None,
        group: OrdinateDimensionGroup | None = None,
    ):
        if target is None and anchor is not None:
            target = anchor

        self.datum_coord = datum_coord
        # When owned by a group, ``datum_coord`` is re-derived from the group's datum on every
        # attach/rebind so it always matches the view's current placement and scale.
        self.group = group
        self.direction = direction.lower()
        self.jog = jog
        self.extension_length = extension_length
        self.font_size = font_size
        self.prefix = prefix
        self.suffix = suffix
        self.view = view
        self.target = target
        self.point = (0.0, 0.0)
        self.resolved = False
        self._explicit_coords = False
        self.anchor_stale = False
        self.stale_reason: str | None = None

        if (
            isinstance(target, (tuple, list))
            and len(target) == 2
            and not is_topological_entity(target)
        ):
            self.point = (float(target[0]), float(target[1]))
            self.resolved = True
            self._explicit_coords = True

        target_view = view or CURRENT_VIEW.get()
        if target_view is not None:
            self._attach_to_view(target_view)
        else:
            target_sheet = CURRENT_SHEET.get()
            if target_sheet is not None and self not in target_sheet.annotations:
                target_sheet.annotations.append(self)

    def _rebind_to_view(self, view: SheetView) -> None:
        if not self._explicit_coords:
            self.resolved = False
            self._attach_to_view(view)

    def _attach_to_view(self, view: SheetView) -> None:
        self.view = view
        if self.group is not None:
            self.datum_coord = self.group._datum_coord(view)
        if not self.resolved and self.target is None and self.group is not None:
            # Group's own datum (0) ordinate when the datum is the view's lower-left corner.
            self.point = self.group.datum_point
            self.resolved = True
        if not self.resolved and self.target is not None:
            if not isinstance(self.target, (tuple, list)):
                self.target = ensure_anchor(self.target, view.shape) or self.target
            self.point = resolve_point_to_sheet(self.target, view)
            if isinstance(self.target, Anchor) and self.target.is_stale:
                self.anchor_stale = True
                self.stale_reason = self.target.stale_reason
                self.point = stale_point_to_sheet(self.target, view) or self.point
            else:
                self.anchor_stale = False
                self.stale_reason = None
            self.resolved = True

        self._register(view)

    def to_svg(self) -> str:
        px, py = self.point
        if self.direction == "horizontal":
            # Measures X coordinate relative to datum
            coord_val = abs(px - self.datum_coord) / (
                self.view.scale if self.view and self.view.scale else 1.0
            )
            disp_str = f"{self.prefix}{format_dimension_value(coord_val)}{self.suffix}"
            end_y = py - self.extension_length
            if self.jog != 0.0:
                mid_y = (py + end_y) / 2.0
                pts = f"{px:.2f},{py:.2f} {px:.2f},{mid_y:.2f} {px + self.jog:.2f},{mid_y:.2f} {px + self.jog:.2f},{end_y:.2f}"
                text_x = px + self.jog
            else:
                pts = f"{px:.2f},{py:.2f} {px:.2f},{end_y:.2f}"
                text_x = px
            text_y = end_y - 1.2
            return f"""<g class="ordinate-dim">
                <polyline points="{pts}" class="dim-line" stroke-width="0.25" fill="none"/>
                <text x="{text_x:.2f}" y="{text_y:.2f}" class="dim-text" text-anchor="middle" font-size="{self.font_size:.2f}">{html.escape(disp_str)}</text>
            </g>"""
        else:
            # Measures Y coordinate relative to datum
            coord_val = abs(py - self.datum_coord) / (
                self.view.scale if self.view and self.view.scale else 1.0
            )
            disp_str = f"{self.prefix}{format_dimension_value(coord_val)}{self.suffix}"
            end_x = px - self.extension_length
            if self.jog != 0.0:
                mid_x = (px + end_x) / 2.0
                pts = f"{px:.2f},{py:.2f} {mid_x:.2f},{py:.2f} {mid_x:.2f},{py + self.jog:.2f} {end_x:.2f},{py + self.jog:.2f}"
                text_y = py + self.jog
            else:
                pts = f"{px:.2f},{py:.2f} {end_x:.2f},{py:.2f}"
                text_y = py
            text_x = end_x - 1.5
            return f"""<g class="ordinate-dim">
                <polyline points="{pts}" class="dim-line" stroke-width="0.25" fill="none"/>
                <text x="{text_x:.2f}" y="{text_y + 1.0:.2f}" class="dim-text" text-anchor="end" font-size="{self.font_size:.2f}">{html.escape(disp_str)}</text>
            </g>"""


class OrdinateDimensionGroup(Annotation):
    """Collection of ordinate dimensions sharing a common datum origin (0)."""

    def __init__(
        self,
        datum_target: Any = None,
        direction: str = "horizontal",
        extension_length: float = 14.0,
        view: SheetView | None = None,
        *,
        anchor: Any = None,
    ):
        if datum_target is None and anchor is not None:
            datum_target = anchor
        self.datum_target = datum_target
        self.direction = direction.lower()
        self.extension_length = extension_length
        self.view = view
        self.ordinates: list[OrdinateDimension] = []
        self.datum_point = (0.0, 0.0)

        target_view = view or CURRENT_VIEW.get()
        if target_view is not None:
            self._attach_to_view(target_view)

    def _resolve_datum(self, view: SheetView) -> tuple[float, float]:
        """Re-resolve the datum against ``view`` as it is now and cache it in ``datum_point``."""
        self.view = view
        if self.datum_target is not None:
            if not isinstance(self.datum_target, (tuple, list)):
                self.datum_target = (
                    ensure_anchor(self.datum_target, view.shape) or self.datum_target
                )
            self.datum_point = resolve_point_to_sheet(self.datum_target, view)
        else:
            self.datum_point = (view.min_x, view.min_y)
        return self.datum_point

    def _datum_coord(self, view: SheetView) -> float:
        """Sheet-space datum coordinate along this group's measuring axis for ``view``."""
        dp = self._resolve_datum(view)
        return dp[0] if self.direction == "horizontal" else dp[1]

    def _rebind_to_view(self, view: SheetView) -> None:
        # The group itself is not registered on the view; its ordinates are, and each one
        # re-derives its datum from this group when the view re-binds it. This method only
        # exists for callers that re-bind a detached group explicitly.
        for ord_dim in self.ordinates:
            ord_dim._rebind_to_view(view)

    def _attach_to_view(self, view: SheetView) -> None:
        self._resolve_datum(view)
        # Add datum 0 ordinate (target None -> follows the view's lower-left corner)
        self.add_ordinate(self.datum_target)

    def add_ordinate(self, target: Any, jog: float = 0.0) -> OrdinateDimension:
        datum_c = self.datum_point[0] if self.direction == "horizontal" else self.datum_point[1]
        dim = OrdinateDimension(
            target=target,
            datum_coord=datum_c,
            direction=self.direction,
            jog=jog,
            extension_length=self.extension_length,
            view=self.view,
            group=self,
        )
        self.ordinates.append(dim)
        return dim

    def to_svg(self) -> str:
        return "".join(o.to_svg() for o in self.ordinates)


class OrdinateDimensionSet(Annotation):
    """A set of datum ordinate dimensions along an axis (X or Y) with auto-jogging for tightly-spaced values."""

    def __init__(
        self,
        datum_origin: tuple[float, float] | Any = (0.0, 0.0),
        direction: str = "X",
        coordinates: list[float] | list[Any] | None = None,
        baseline_offset: float = -16.0,
        jog: bool = True,
        font_size: float = 3.2,
        prefix: str = "",
        suffix: str = "",
        view: SheetView | None = None,
        *,
        anchor: Any = None,
    ):
        if anchor is not None:
            datum_origin = anchor
        self.datum_origin = datum_origin
        self.direction = direction.upper()
        self.coordinates = list(coordinates) if coordinates is not None else [0.0]
        self.baseline_offset = baseline_offset
        self.jog = jog
        self.font_size = font_size
        self.prefix = prefix
        self.suffix = suffix
        self.view = view
        self.datum_sheet: tuple[float, float] = (0.0, 0.0)
        self.resolved = False

        target_view = view or CURRENT_VIEW.get()
        if target_view is not None:
            self._attach_to_view(target_view)
        else:
            target_sheet = CURRENT_SHEET.get()
            if target_sheet is not None and self not in target_sheet.annotations:
                target_sheet.annotations.append(self)

    def _rebind_to_view(self, view: SheetView) -> None:
        self.resolved = False
        self._attach_to_view(view)

    def _attach_to_view(self, view: SheetView) -> None:
        self.view = view
        if is_topological_entity(self.datum_origin):
            self.datum_origin = ensure_anchor(self.datum_origin, view.shape) or self.datum_origin
            self.datum_sheet = resolve_point_to_sheet(self.datum_origin, view)
        elif isinstance(self.datum_origin, (tuple, list)):
            if len(self.datum_origin) == 3:
                self.datum_sheet = view.to_sheet_3d(*self.datum_origin)
            else:
                self.datum_sheet = view.to_sheet_coords(
                    float(self.datum_origin[0]), float(self.datum_origin[1])
                )
        else:
            self.datum_sheet = (view.min_x, view.min_y)

        self._register(view)
        self.resolved = True

    def to_svg(self) -> str:
        if not self.view and not self.datum_sheet:
            return ""

        scale = self.view.scale if self.view and self.view.scale else 1.0
        is_x = self.direction in ("X", "HORIZONTAL")
        items_svg = []

        # Sort coordinates numerically
        sorted_coords = sorted(
            self.coordinates,
            key=lambda c: float(c) if isinstance(c, (int, float)) else 0.0,
        )

        # Compute paper locations and auto-jogging
        # A coordinate datum is mapped through the view (honours view breaks). A topological
        # datum (``Anchor``) only has a resolved sheet point, so offset linearly from that.
        view = self.view
        datum = self.datum_origin
        model_datum = datum if isinstance(datum, (tuple, list)) else None
        paper_locs: list[float] = []
        for c in sorted_coords:
            val = float(c) if isinstance(c, (int, float)) else 0.0
            if is_x:
                p_coord = (
                    view.to_sheet_x(float(model_datum[0]) + val)
                    if view and model_datum is not None
                    else (self.datum_sheet[0] + val * scale)
                )
            else:
                p_coord = (
                    view.to_sheet_y(float(model_datum[1]) + val)
                    if view and model_datum is not None
                    else (self.datum_sheet[1] - val * scale)
                )
            paper_locs.append(p_coord)

        jog_offsets: list[float] = [0.0] * len(sorted_coords)
        if self.jog:
            for i in range(1, len(sorted_coords)):
                gap = abs(paper_locs[i] - paper_locs[i - 1])
                if gap < 5.0:
                    jog_dir = 1.0 if (i % 2 == 1) else -1.0
                    jog_offsets[i] = jog_dir * max(3.5, 6.0 - gap)

        feature_baseline = (
            (self.view.min_y if self.baseline_offset < 0 else self.view.max_y)
            if (self.view and is_x)
            else (
                (self.view.min_x if self.baseline_offset < 0 else self.view.max_x)
                if (self.view and not is_x)
                else (self.datum_sheet[1] if is_x else self.datum_sheet[0])
            )
        )
        baseline_pos = feature_baseline + self.baseline_offset

        for i, c in enumerate(sorted_coords):
            val = float(c) if isinstance(c, (int, float)) else 0.0
            p_loc = paper_locs[i]
            jog = jog_offsets[i]

            disp_str = "0" if abs(val) < 1e-4 else format_dimension_value(val)
            full_str = f"{self.prefix}{disp_str}{self.suffix}"

            if is_x:
                feat_y = feature_baseline
                end_y = baseline_pos
                if jog != 0.0:
                    mid_y = (feat_y + end_y) / 2.0
                    pts = (
                        f"{p_loc:.2f},{feat_y:.2f} "
                        f"{p_loc:.2f},{mid_y:.2f} "
                        f"{p_loc + jog:.2f},{mid_y:.2f} "
                        f"{p_loc + jog:.2f},{end_y:.2f}"
                    )
                    text_x = p_loc + jog
                else:
                    pts = f"{p_loc:.2f},{feat_y:.2f} {p_loc:.2f},{end_y:.2f}"
                    text_x = p_loc
                text_y = end_y - 1.2 if self.baseline_offset < 0 else end_y + 3.0
                items_svg.append(f"""
                <g class="ordinate-dim">
                    <polyline points="{pts}" class="dim-line" stroke-width="0.25" fill="none"/>
                    <text x="{text_x:.2f}" y="{text_y:.2f}" class="dim-text" text-anchor="middle" font-size="{self.font_size:.2f}">{html.escape(full_str)}</text>
                </g>""")
            else:
                feat_x = feature_baseline
                end_x = baseline_pos
                if jog != 0.0:
                    mid_x = (feat_x + end_x) / 2.0
                    pts = (
                        f"{feat_x:.2f},{p_loc:.2f} "
                        f"{mid_x:.2f},{p_loc:.2f} "
                        f"{mid_x:.2f},{p_loc + jog:.2f} "
                        f"{end_x:.2f},{p_loc + jog:.2f}"
                    )
                    text_y = p_loc + jog
                else:
                    pts = f"{feat_x:.2f},{p_loc:.2f} {end_x:.2f},{p_loc:.2f}"
                    text_y = p_loc
                text_x = end_x - 1.5 if self.baseline_offset < 0 else end_x + 1.5
                anchor = "end" if self.baseline_offset < 0 else "start"
                items_svg.append(f"""
                <g class="ordinate-dim">
                    <polyline points="{pts}" class="dim-line" stroke-width="0.25" fill="none"/>
                    <text x="{text_x:.2f}" y="{text_y + 1.0:.2f}" class="dim-text" text-anchor="{anchor}" font-size="{self.font_size:.2f}">{html.escape(full_str)}</text>
                </g>""")

        return f'<g class="ordinate-dim-set">{"".join(items_svg)}</g>'


class BaselineDimensionGroup(Annotation):
    """Group of linear dimensions stacked progressively from a single reference baseline."""

    def __init__(
        self,
        base_target: Any,
        targets: list[Any],
        direction: str = "horizontal",
        first_offset: float = 10.0,
        step_offset: float = 7.0,
        view: SheetView | None = None,
    ):
        self.base_target = base_target
        self.targets = targets
        self.direction = direction
        self.first_offset = first_offset
        self.step_offset = step_offset
        self.view = view
        self.dimensions: list[LinearDimension] = []

        sign = 1.0 if first_offset >= 0 else -1.0
        for i, target in enumerate(targets):
            offset = first_offset + sign * (i * step_offset)
            dim = LinearDimension(
                target_or_start=base_target,
                target2_or_end=target,
                offset=offset,
                direction=direction,
                view=view,
            )
            self.dimensions.append(dim)

    def _rebind_to_view(self, view: SheetView) -> None:
        self.view = view
        for d in self.dimensions:
            d._rebind_to_view(view)

    def to_svg(self) -> str:
        return "".join(d.to_svg() for d in self.dimensions)


class ChainDimensionGroup(Annotation):
    """Group of consecutive linear dimensions aligned in a single continuous chain."""

    def __init__(
        self,
        targets: list[Any],
        direction: str = "horizontal",
        offset: float = 8.0,
        view: SheetView | None = None,
    ):
        self.targets = targets
        self.direction = direction
        self.offset = offset
        self.view = view
        self.dimensions: list[LinearDimension] = []

        if len(targets) >= 2:
            for i in range(len(targets) - 1):
                dim = LinearDimension(
                    target_or_start=targets[i],
                    target2_or_end=targets[i + 1],
                    offset=offset,
                    direction=direction,
                    view=view,
                )
                self.dimensions.append(dim)

    def _rebind_to_view(self, view: SheetView) -> None:
        self.view = view
        for d in self.dimensions:
            d._rebind_to_view(view)

    def to_svg(self) -> str:
        return "".join(d.to_svg() for d in self.dimensions)
