"""SheetView representations, view projection mathematics, and coordinate transformations."""

from __future__ import annotations

import contextlib
import io
import math
import os
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Self, TypeAlias

import build123d as bd
from build123d.exporters import Drawing
from build123d.topology import Shape

from build123d_techdraw.annotations.base import Annotation
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
from build123d_techdraw.annotations.threads import CosmeticThread, auto_cosmetic_threads
from build123d_techdraw.context import CURRENT_VIEW
from build123d_techdraw.core.scale import format_iso_scale

if TYPE_CHECKING:
    from build123d_techdraw.core.sheet import DrawingSheet


@dataclass
class BreakInfo:
    """A span removed from a view by a pair of break lines (ISO 128-3 interrupted view).

    ``interval`` is in absolute model coordinates along the view's horizontal (``"X"``) or
    vertical (``"Y"``) axis, the same frame as :meth:`SheetView.to_sheet_x` /
    :meth:`SheetView.to_sheet_y`. ``gap`` is the paper-space distance (mm) left between the two
    break lines.
    """

    interval: tuple[float, float]
    axis: str = "X"
    style: str = "zigzag"
    gap: float = 10.0


def _break_line_svg(pos: float, a: float, b: float, *, style: str, horizontal: bool) -> str:
    """One break line across the view at ``pos`` along the break axis, spanning ``a``..``b``.

    ``horizontal`` means the view is broken along X, so the line itself runs vertically.
    """
    m = (a + b) / 2.0

    def pt(along: float, across: float) -> str:
        x, y = (pos + along, across) if horizontal else (across, pos + along)
        return f"{x:.2f},{y:.2f}" if style == "zigzag" else f"{x:.2f} {y:.2f}"

    if style == "zigzag":
        pts = " ".join(
            (pt(0.0, a), pt(0.0, m - 4.0), pt(2.5, m - 1.5), pt(-2.5, m + 1.5), pt(0.0, m + 4.0))
        )
        pts += " " + pt(0.0, b)
        return f'<polyline points="{pts}" class="break-line dim-line" stroke-width="0.35" fill="none"/>'
    d = (
        f"M {pt(0.0, a)} Q {pt(3.0, (a * 3.0 + b) / 4.0)} {pt(0.0, m)} "
        f"Q {pt(-3.0, (a + b * 3.0) / 4.0)} {pt(0.0, b)}"
    )
    return f'<path d="{d}" class="break-line dim-line" stroke-width="0.35" fill="none"/>'


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
        sheet: DrawingSheet | None = None,
        shape: Any | None = None,
        enforce_iso_scale: bool = True,
        show_hidden: bool = True,
    ):
        self.sheet = sheet
        self.shape = shape
        self.enforce_iso_scale = enforce_iso_scale
        self.show_hidden = show_hidden
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
        self.annotations: list[Annotation] = []
        self._ctx_token: Any = None
        self.hatch: str | None = None
        self.hatch_pitch: float | None = None
        self.hatch_angle: float | None = None
        self.hatch_id: str | None = None
        self.parent_view: SheetView | None = None
        self.detail_shape: str | None = None
        # Factory + build parameters used by ``update_shape`` to regenerate this view.
        # Placement (center, scale, rotation) is taken from the live view at regeneration
        # time so that ``locate``/``align_to``/``scale_to`` are honoured.
        self._regen_factory: Callable[..., SheetView] | None = None
        self._regen_kwargs: dict[str, Any] = {}
        # Callout SVG a detail view appended to its parent (removed before re-projection).
        self._parent_callout_svg: str | None = None
        # Projected 2D visible circular edges (cx, cy, radius) cached from HLR projection
        # so auto_centerlines does not need to re-run OCCT Drawing a second time.
        self._visible_circles: list[tuple[float, float, float]] | None = None

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

    def __iadd__(self, item: Annotation | Iterable[Annotation]) -> Self:
        return self.add(item)

    def __add__(self, item: Annotation | Iterable[Annotation]) -> Self:
        return self.add(item)

    def add(self, item: Annotation | Iterable[Annotation]) -> Self:
        """Attach an annotation or collection of annotations to this view."""
        if not isinstance(item, Annotation):
            if not isinstance(item, Iterable) or isinstance(item, str):
                raise TypeError(f"Cannot add {type(item).__name__} to a SheetView.")
            for sub_item in item:
                self.add(sub_item)
            return self

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
        """Cut a uniform span out of the view (ISO 128-3 interrupted view).

        Args:
            interval: Start and end of the removed span in absolute model coordinates along
                ``axis`` (the frame used by ``to_sheet_x`` / ``to_sheet_y``). Both ends must lie
                strictly inside the part so material remains on both sides.
            axis: ``"X"`` (horizontal break) or ``"Y"`` (vertical break). All breaks of one
                view must use the same axis.
            style: ``"zigzag"`` or ``"wavy"`` break lines.
            gap: Paper distance in mm between the two break lines.

        Dimensions keep reporting true model lengths; only the drawn span is shortened.
        """
        axis_u = axis.upper()
        style_l = style.lower()
        if axis_u not in ("X", "Y"):
            raise ValueError(f"Break axis must be 'X' or 'Y', got {axis!r}.")
        if style_l not in ("zigzag", "wavy"):
            raise ValueError(f"Break style must be 'zigzag' or 'wavy', got {style!r}.")
        if gap <= 0.0:
            raise ValueError(f"Break gap must be positive, got {gap}.")
        lo, hi = min(interval[0], interval[1]), max(interval[0], interval[1])
        half = self._unbroken_paper_length(axis_u) / (2.0 * self.scale) if self.scale else 0.0
        origin = self.origin_x if axis_u == "X" else self.origin_y
        part_min, part_max = origin - half, origin + half
        if not (part_min < lo < hi < part_max):
            raise ValueError(
                f"Break interval {interval} must lie strictly inside the part "
                f"({part_min:g} < start < end < {part_max:g} along {axis_u}, model coordinates)."
            )
        if (hi - lo) * self.scale <= gap:
            raise ValueError(
                f"Break interval {interval} is shorter on paper than the gap ({gap} mm)."
            )
        for other in self.breaks:
            if other.axis != axis_u:
                raise ValueError("All breaks of a view must use the same axis.")
            if lo < other.interval[1] and other.interval[0] < hi:
                raise ValueError(f"Break interval {interval} overlaps {other.interval}.")
        self.breaks.append(BreakInfo(interval=(lo, hi), axis=axis_u, style=style_l, gap=gap))
        self.breaks.sort(key=lambda b: b.interval[0])
        self._apply_breaks()
        return self

    def _unbroken_paper_length(self, axis: str) -> float:
        return self.unbroken_width if axis == "X" else self.unbroken_height

    def _break_cuts(self, axis: str) -> list[tuple[float, float, BreakInfo]]:
        """Unbroken paper-space spans removed by the breaks along ``axis``, sorted ascending."""
        cuts: list[tuple[float, float, BreakInfo]] = []
        for b in self.breaks:
            if b.axis != axis:
                continue
            lo, hi = b.interval
            if axis == "X":
                x_lo = self.center_x + (lo - self.origin_x) * self.scale
                x_hi = self.center_x + (hi - self.origin_x) * self.scale
                cuts.append((x_lo, x_hi, b))
            else:
                # Paper Y grows downward, so the model's upper bound maps to the smaller paper Y.
                y_top = self.center_y - (hi - self.origin_y) * self.scale
                y_bottom = self.center_y - (lo - self.origin_y) * self.scale
                cuts.append((y_top, y_bottom, b))
        cuts.sort(key=lambda c: c[0])
        return cuts

    def _removed_length(self, axis: str) -> float:
        return sum(c1 - c0 - b.gap for c0, c1, b in self._break_cuts(axis))

    def _map_through_breaks(self, raw: float, axis: str) -> float:
        """Map an unbroken paper coordinate along ``axis`` to its position in the broken view.

        The broken view stays centred on the view centre. Points inside a removed span are
        spread proportionally across the gap between the break lines.
        """
        cuts = self._break_cuts(axis)
        if not cuts:
            return raw
        pos = raw + sum(c1 - c0 - b.gap for c0, c1, b in cuts) / 2.0
        for c0, c1, b in cuts:
            if raw >= c1:
                pos -= c1 - c0 - b.gap
            elif raw > c0:
                inside = raw - c0
                pos -= inside - inside / (c1 - c0) * b.gap
                break
            else:
                break
        return pos

    def break_gap_centers(self, axis: str) -> list[float]:
        """Broken-view paper positions (mm) of the middle of each break gap along ``axis``."""
        return [
            self._map_through_breaks((c0 + c1) / 2.0, axis) for c0, c1, _ in self._break_cuts(axis)
        ]

    def _refresh_bounds(self) -> None:
        """Recompute ``width``/``height`` and the min/max extents from the unbroken size."""
        self.width = self.unbroken_width - self._removed_length("X")
        self.height = self.unbroken_height - self._removed_length("Y")
        self.min_x = self.center_x - self.width / 2.0
        self.max_x = self.center_x + self.width / 2.0
        self.min_y = self.center_y - self.height / 2.0
        self.max_y = self.center_y + self.height / 2.0

    def _apply_breaks(self) -> None:
        """Regenerate bounds and SVG: one clipped, shifted copy of the view per kept segment."""
        if not self.breaks:
            return
        self._refresh_bounds()
        if not self.inner_svg_content:
            return

        axis = self.breaks[0].axis
        cuts = self._break_cuts(axis)
        horizontal = axis == "X"
        centre = self.center_x if horizontal else self.center_y
        half = self._unbroken_paper_length(axis) / 2.0

        # Kept segments in unbroken paper coordinates along the break axis.
        edges = [centre - half]
        for c0, c1, _ in cuts:
            edges.extend((c0, c1))
        edges.append(centre + half)
        segments = [(edges[i], edges[i + 1]) for i in range(0, len(edges), 2)]

        view_id = f"view_{self.view_type.value}_{int(self.center_x)}_{int(self.center_y)}"
        rot_str = (
            f" rotate({self.rotation_angle_deg:.1f}, {self.orig_cx:.2f}, {self.orig_cy:.2f})"
            if self.rotation_angle_deg != 0.0
            else ""
        )
        pad = 10.0
        clips: list[str] = []
        groups: list[str] = []
        break_positions: list[tuple[float, BreakInfo]] = []
        for i, (a, b) in enumerate(segments):
            mid = (a + b) / 2.0
            shift = self._map_through_breaks(mid, axis) - mid
            new_a, new_b = a + shift, b + shift
            lo = new_a - (pad if i == 0 else 0.0)
            hi = new_b + (pad if i == len(segments) - 1 else 0.0)
            clip_id = f"clip{i + 1}_{view_id}"
            if horizontal:
                rect = (lo, self.min_y - pad, hi - lo, self.height + 2.0 * pad)
                tx, ty = self.center_x + shift, self.center_y
            else:
                rect = (self.min_x - pad, lo, self.width + 2.0 * pad, hi - lo)
                tx, ty = self.center_x, self.center_y + shift
            clips.append(
                f'<clipPath id="{clip_id}"><rect x="{rect[0]:.2f}" y="{rect[1]:.2f}" '
                f'width="{rect[2]:.2f}" height="{rect[3]:.2f}"/></clipPath>'
            )
            transform = (
                f"translate({tx:.2f}, {ty:.2f}) "
                f"scale({self.scale:.4f}, {self.scale:.4f}){rot_str} "
                f"translate({-self.orig_cx:.2f}, {-self.orig_cy:.2f})"
            )
            groups.append(
                f'<g clip-path="url(#{clip_id})"><g transform="{transform}">'
                f"{self.inner_svg_content}</g></g>"
            )
            if i > 0:
                break_positions.append((new_a, cuts[i - 1][2]))
            if i < len(cuts):
                break_positions.append((new_b, cuts[i][2]))

        break_lines = [
            _break_line_svg(
                pos,
                (self.min_y if horizontal else self.min_x) - 2.0,
                (self.max_y if horizontal else self.max_x) + 2.0,
                style=b.style,
                horizontal=horizontal,
            )
            for pos, b in break_positions
        ]

        self.svg_content = f"""
    <!-- View: {self.view_type.value.upper()} (Broken View) at ({self.center_x}, {self.center_y}) scale={self.scale} -->
    <defs>{"".join(clips)}</defs>
    <g id="{view_id}" class="sheet-view broken-view">
        {"".join(groups)}
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
        from build123d_techdraw.recognizers.features import recognize_holes

        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        exporter = bd.ExportDXF(version=dxfversion, unit=bd.Unit.MM)
        doc = exporter._document
        msp = exporter._modelspace

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
            exporter.write(filepath)
            return

        def _export_edge(edge: Any, layer: str) -> None:
            exporter.add_shape(edge.scale(scale) if scale != 1.0 else edge, layer=layer)

        look_from, look_up = VIEW_VECTORS.get(self.view_type, ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)))
        dwg = Drawing(shape, look_from=look_from, look_up=look_up, with_hidden=False)
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
        wires: list[bd.Wire] = []
        try:
            wires = bd.Wire.combine(edges)
        except Exception:
            wires = []

        if wires:
            wires.sort(
                key=lambda w: bd.Face(w).area if w.is_closed else 0.0,
                reverse=True,
            )
            outer_wire = wires[0]
            inner_wires = wires[1:]

            for edge in outer_wire.edges():
                _export_edge(edge, layer_map["outer_profile"])

            # Detect concentric circular wires where outer step is countersink/counterbore
            concentric_csinks: set[int] = set()
            circle_wires: list[tuple[int, tuple[float, float], float]] = []
            for idx, iw in enumerate(inner_wires):
                cw_edges = iw.edges()
                if len(cw_edges) == 1 and cw_edges[0].geom_type == bd.GeomType.CIRCLE:
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
                        if edge.geom_type == bd.GeomType.CIRCLE:
                            r = round(float(edge.radius), 2)
                            if any(abs(r - cr) < 0.5 for cr in csink_radii):
                                is_csink = True
                                break
                target_l = layer_map["countersinks"] if is_csink else layer_map["inner_holes"]
                for edge in iw.edges():
                    _export_edge(edge, target_l)
        else:
            for edge in edges:
                _export_edge(edge, layer_map["outer_profile"])

        center_layer = layer_map["centerlines"]
        has_ann_centerlines = False
        for ann in self.annotations:
            if isinstance(ann, (CenterMark, BoltCircle, DiameterDimension, RadialDimension)):
                has_ann_centerlines = True
                cx, cy = ann.center
                r = ann.radius if isinstance(ann, RadialDimension) else 5.0
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

        exporter.write(filepath)

    def _rebind_annotations(self) -> None:
        """Re-resolve all anchored/topological annotations attached to this view."""
        for ann in list(self.annotations):
            ann._rebind_to_view(self)

    def _copy_projection_from(self, reproj: SheetView) -> None:
        """Adopt the layout fields of a fresh projection of the same view (``update_shape``)."""
        self.width = reproj.width
        self.height = reproj.height
        self.unbroken_width = reproj.unbroken_width
        self.unbroken_height = reproj.unbroken_height
        self.min_x = reproj.min_x
        self.max_x = reproj.max_x
        self.min_y = reproj.min_y
        self.max_y = reproj.max_y
        self.bottom_annotation_y = reproj.bottom_annotation_y
        self.model_origin = reproj.model_origin
        self.origin_x = reproj.origin_x
        self.origin_y = reproj.origin_y
        self.projection_origin = reproj.projection_origin
        self.proj_x = reproj.proj_x
        self.proj_y = reproj.proj_y
        self.orig_cx = reproj.orig_cx
        self.orig_cy = reproj.orig_cy
        self.svg_content = reproj.svg_content
        self.inner_svg_content = reproj.inner_svg_content
        self.view_label_pos = reproj.view_label_pos
        self._visible_circles = reproj._visible_circles

    def can_regenerate(self) -> bool:
        """Return True if ``update_shape`` is able to re-project this view's geometry."""
        if self.view_type == ViewType.DETAIL:
            return self._regen_factory is not None and self.parent_view is not None
        if self.view_type in (ViewType.SECTION, ViewType.AUXILIARY):
            return self._regen_factory is not None
        return True

    def _regen_label_y(self) -> float | None:
        """Explicit label Y to reuse on regeneration (tracks moves), or None for auto."""
        if self._regen_kwargs.get("view_label_y") is not None and self.view_label_pos is not None:
            return self.view_label_pos[1]
        return None

    def _reproject(self, new_shape: Any) -> SheetView:
        """Re-run the factory this view was built with against ``new_shape``."""
        kwargs = dict(self._regen_kwargs)
        kwargs["view_label_y"] = self._regen_label_y()

        if self.view_type == ViewType.DETAIL:
            parent = self.parent_view
            assert parent is not None and self._regen_factory is not None
            if parent.shape is not new_shape:
                parent.update_shape(new_shape)
            if self._parent_callout_svg and self._parent_callout_svg in parent.svg_content:
                parent.svg_content = parent.svg_content.replace(self._parent_callout_svg, "", 1)
            return self._regen_factory(
                parent_view=parent,
                center_x=self.center_x,
                center_y=self.center_y,
                scale=self.scale,
                **kwargs,
            )

        if self._regen_factory is None:
            # Standard view built without a recorded spec (e.g. a hand-made SheetView).
            return project_shape_to_view(
                new_shape,
                self.view_type,
                self.center_x,
                self.center_y,
                scale=self.scale,
                show_hidden=self.show_hidden,
                view_label=self.view_label,
                rotation_angle_deg=self.rotation_angle_deg,
                view_label_y=kwargs["view_label_y"],
            )

        if self._regen_factory is project_shape_to_view:
            kwargs["rotation_angle_deg"] = self.rotation_angle_deg
        return self._regen_factory(
            new_shape,
            center_x=self.center_x,
            center_y=self.center_y,
            scale=self.scale,
            **kwargs,
        )

    def update_shape(self, new_shape: Any) -> Self:
        """Update the view's 3D shape, re-project geometry, and re-bind all anchored annotations.

        Every view type is regenerated from the parameters it was built with (cut plane,
        section path, detail region, auxiliary direction, explicit ``model_origin``,
        label placement). Detail views follow their parent view: the parent is updated to
        ``new_shape`` first if needed, then the detail is re-cut from it. Space reserved
        below the view by dimensions (``bottom_annotation_y``) is kept, shifted with the
        view's new bottom edge.

        Raises:
            ValueError: if this view has no recorded build parameters and cannot be
                regenerated (section/detail/auxiliary views constructed by hand).
        """
        if not self.can_regenerate():
            raise ValueError(
                f"Cannot update shape of {self.view_type.value} view "
                f"{self.view_label or ''!r}: it was not created by a projection factory, "
                "so its build parameters are unknown. Recreate the view instead."
            )

        old_max_y = self.max_y
        old_bottom = self.bottom_annotation_y
        reproj = self._reproject(new_shape)

        self.shape = reproj.shape if self.view_type == ViewType.DETAIL else new_shape
        self._copy_projection_from(reproj)
        if reproj.hatch_id is not None:
            self.hatch = reproj.hatch
            self.hatch_pitch = reproj.hatch_pitch
            self.hatch_angle = reproj.hatch_angle
            self.hatch_id = reproj.hatch_id
        if reproj.parent_view is not None:
            self.parent_view = reproj.parent_view
        self._parent_callout_svg = reproj._parent_callout_svg
        if self.breaks:
            self._apply_breaks()

        # Keep annotation space reserved below the view, moved with the view's bottom edge.
        self.bottom_annotation_y = max(self.max_y, old_bottom + (self.max_y - old_max_y))

        self._rebind_annotations()
        return self

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
        if self.breaks:
            self._refresh_bounds()
        self.bottom_annotation_y = self.max_y
        if self.view_label_pos is not None:
            self.view_label_pos = (self.view_label_pos[0] + dx, self.view_label_pos[1] + dy)
        self._update_transform()
        self._rebind_annotations()
        return self

    def scale_to(self, scale: float) -> Self:
        """Change the view scale factor and update its bounding box."""
        if self.scale != 0.0:
            ratio = scale / self.scale
            self.width *= ratio
            self.height *= ratio
            self.unbroken_width *= ratio
            self.unbroken_height *= ratio
        self.scale = scale
        self.min_x = self.center_x - self.width / 2.0
        self.max_x = self.center_x + self.width / 2.0
        self.min_y = self.center_y - self.height / 2.0
        self.max_y = self.center_y + self.height / 2.0
        if self.breaks:
            self._refresh_bounds()
        self.bottom_annotation_y = self.max_y
        self.scale_label = format_iso_scale(scale)
        self._update_transform()
        self._rebind_annotations()
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

        if include_annotations:
            for ann in self.annotations:
                bbox = ann.bounding_box_2d()
                if bbox is not None:
                    min_x = min(min_x, bbox[0])
                    min_y = min(min_y, bbox[1])
                    max_x = max(max_x, bbox[2])
                    max_y = max(max_y, bbox[3])

        if include_label and (self.view_label or self.scale_label):
            label_y = self.bottom_annotation_y + 8.0
            max_y = max(max_y, label_y + 4.0)

        return (min_x, min_y, max_x, max_y)

    def align_to(
        self,
        other: AlignSpec,
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
        """Transform 2D model X coordinate to sheet paper coordinate (mm), honouring breaks."""
        raw_x = self.center_x + ((model_x - self.origin_x) * self.scale)
        return self._map_through_breaks(raw_x, "X")

    def to_sheet_y(self, model_y: float) -> float:
        """Transform 2D model Y coordinate to sheet paper coordinate (mm), honouring breaks."""
        raw_y = self.center_y - ((model_y - self.origin_y) * self.scale)
        return self._map_through_breaks(raw_y, "Y")

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
        sx = self.center_x + (x2d - self.orig_cx) * self.scale
        sy = self.center_y + (-y2d - self.orig_cy) * self.scale
        return (self._map_through_breaks(sx, "X"), self._map_through_breaks(sy, "Y"))

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
            nominal=None if text is not None else abs(model_x2 - model_x1),
            tolerance=tolerance,
            prefix=prefix,
            suffix=suffix,
            view=self,
        )
        self.add(dim)
        if dim.offset > 0:
            self.bottom_annotation_y = max(self.bottom_annotation_y, sy + dim.offset + 4.0)
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
            nominal=None if text is not None else abs(model_y2 - model_y1),
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
            nominal=None if text is not None else math.dist(p1_model, p2_model),
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
            nominal=None
            if text is not None or not self.scale
            else self.unbroken_width / self.scale,
            tolerance=tolerance,
            prefix=prefix,
            suffix=suffix,
            view=self,
        )
        self.add(dim)
        if dim.offset > 0:
            self.bottom_annotation_y = max(self.bottom_annotation_y, y_ref + dim.offset + 4.0)
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
            nominal=None
            if text is not None or not self.scale
            else self.unbroken_height / self.scale,
            tolerance=tolerance,
            prefix=prefix,
            suffix=suffix,
            view=self,
        )
        self.add(dim)
        return dim

    def auto_dimension(self, tolerance: str | None = None) -> list[Any]:
        """Automatically dimension this view using collision-avoiding placement."""
        from build123d_techdraw.layout.auto_dimension import auto_dimension_view

        return auto_dimension_view(self, tolerance=tolerance)

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
        model_x: float = 0.0,
        model_y: float = 0.0,
        characteristic: GDTCharacteristic = GDTCharacteristic.POSITION,
        tolerance_value: str = "0.05",
        datum_a: str | None = None,
        datum_b: str | None = None,
        datum_c: str | None = None,
        leader_offset: tuple[float, float] | None = None,
        *,
        anchor: Any = None,
    ) -> FeatureControlFrame:
        """Add a GD&T Feature Control Frame near (model_x, model_y) or an anchor, optionally with a leader line."""
        if anchor is not None:
            fcf = FeatureControlFrame(
                characteristic=characteristic,
                tolerance_value=tolerance_value,
                datum_a=datum_a,
                datum_b=datum_b,
                datum_c=datum_c,
                leader_offset=leader_offset or (12.0, -10.0),
                view=self,
                anchor=anchor,
            )
            self.add(fcf)
            return fcf

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
        model_x: float = 0.0,
        model_y: float = 0.0,
        label: str = "A",
        direction: str = "up",
        *,
        anchor: Any = None,
    ) -> DatumFeatureSymbol:
        """Add an ISO datum feature identifier symbol at (model_x, model_y) or an anchor."""
        target = anchor if anchor is not None else self.to_sheet_coords(model_x, model_y)
        sym = DatumFeatureSymbol(target=target, label=label, direction=direction, view=self)
        self.add(sym)
        return sym

    def add_surface_finish(
        self,
        model_x: float = 0.0,
        model_y: float = 0.0,
        ra_value: str = "Ra 3.2",
        material_removal: MaterialRemoval = MaterialRemoval.REQUIRED,
        process: str | None = None,
        *,
        anchor: Any = None,
    ) -> SurfaceFinishSymbol:
        """Add an ISO 1302 surface roughness symbol at (model_x, model_y) or an anchor."""
        sym = SurfaceFinishSymbol(
            position=None if anchor is not None else self.to_sheet_coords(model_x, model_y),
            ra_value=ra_value,
            material_removal=material_removal,
            process=process,
            view=self,
            anchor=anchor,
        )
        self.add(sym)
        return sym

    def hole_callout(
        self,
        model_x: float = 0.0,
        model_y: float = 0.0,
        diameter: float | None = None,
        depth: float | str | None = None,
        countersink_dia: float | None = None,
        countersink_angle: float = 90.0,
        counterbore_dia: float | None = None,
        counterbore_depth: float | None = None,
        quantity: int | None = None,
        tolerance: str | Tolerance | None = None,
        clearance_for: str | None = None,
        fit: str = "medium",
        elbow_dx: float = 15.0,
        elbow_dy: float = -12.0,
        *,
        anchor: Any = None,
    ) -> HoleCallout:
        """Add a standard engineering hole callout at (model_x, model_y) or an anchor."""
        target = anchor if anchor is not None else self.to_sheet_coords(model_x, model_y)
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
            clearance_for=clearance_for,
            fit=fit,
            elbow_offset=(elbow_dx, elbow_dy),
            view=self,
        )
        return hc

    def thread_callout(
        self,
        model_x: float = 0.0,
        model_y: float = 0.0,
        thread: str = "M5x0.8",
        depth: float | str | None = "THRU",
        class_fit: str | None = None,
        quantity: int | None = None,
        show_tap_drill: bool = False,
        elbow_dx: float = 15.0,
        elbow_dy: float = -12.0,
        *,
        anchor: Any = None,
    ) -> ThreadCallout:
        """Add a standard engineering thread callout at (model_x, model_y) or an anchor."""
        target = anchor if anchor is not None else self.to_sheet_coords(model_x, model_y)
        tc = ThreadCallout(
            center=target,
            thread=thread,
            depth=depth,
            class_fit=class_fit,
            quantity=quantity,
            show_tap_drill=show_tap_drill,
            elbow_offset=(elbow_dx, elbow_dy),
            view=self,
        )
        return tc

    def cosmetic_thread(
        self,
        target: Any = None,
        thread: str | Any | None = None,
        nominal_diameter: float | None = None,
        minor_diameter: float | None = None,
        thread_type: str = "internal",
        depth: float | None = None,
        model_x: float | None = None,
        model_y: float | None = None,
        view_mode: str | None = None,
    ) -> CosmeticThread:
        """Add an ISO 6410-1 cosmetic thread representation to this view."""
        center_pt = (
            self.to_sheet_coords(model_x, model_y)
            if model_x is not None and model_y is not None
            else None
        )
        return CosmeticThread(
            target=target,
            thread=thread,
            nominal_diameter=nominal_diameter,
            minor_diameter=minor_diameter,
            thread_type=thread_type,
            depth=depth,
            center=center_pt,
            view_mode=view_mode,
            view=self,
        )

    def auto_cosmetic_threads(
        self,
        *,
        threaded_diameters: dict[float, str] | None = None,
        infer_threads: bool = False,
        tolerance: float = 0.08,
    ) -> list[CosmeticThread]:
        """Automatically generate ISO 6410-1 CosmeticThread annotations from recognized holes."""
        return auto_cosmetic_threads(
            self,
            threaded_diameters=threaded_diameters,
            infer_threads=infer_threads,
            tolerance=tolerance,
        )


AlignSpec: TypeAlias = SheetView | tuple[Any, ...] | list[Any] | dict[str, Any]
"""``align_to`` argument: another view, ``(view, direction[, spacing])``, or a mapping with
``other`` / ``direction`` / ``spacing`` keys."""


def _apply_align_to(view: SheetView, align_spec: AlignSpec | None) -> SheetView:
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


def _export_svg_to_inner(exporter: bd.ExportSVG) -> tuple[float, float, float, float, str]:
    """Serialize an ExportSVG instance in memory and return (min_x, min_y, w, h, inner_svg)."""
    buf = io.BytesIO()
    exporter.write(buf)
    raw_svg = buf.getvalue().decode("utf-8")

    vb_m = re.search(r'viewBox="([^"]+)"', raw_svg)
    viewbox_str = vb_m.group(1) if vb_m else "0 0 100 100"
    vb_min_x, vb_min_y, vb_w, vb_h = (float(p) for p in viewbox_str.split())

    inner_m = re.search(r"<svg[^>]*>(.*)</svg>", raw_svg, flags=re.DOTALL)
    inner_content = inner_m.group(1).strip() if inner_m else raw_svg

    # Clean XML namespaces
    inner_content = re.sub(
        r"</?[a-zA-Z0-9_]+:",
        lambda m: "</" if m.group(0).startswith("</") else "<",
        inner_content,
    )
    inner_content = re.sub(r'\s+xmlns(?::[a-zA-Z0-9_]+)?="[^"]*"', "", inner_content)
    return vb_min_x, vb_min_y, vb_w, vb_h, inner_content


@dataclass(frozen=True)
class _ProjectedViewPayload:
    """Pickleable output of OCCT HLR projection and SVG export for a single view."""

    inner_content: str
    vb_min_x: float
    vb_min_y: float
    vb_w: float
    vb_h: float
    exact_w: float
    exact_h: float
    visible_circles: tuple[tuple[float, float, float], ...]


def _filter_coincident_hidden_edges(
    hidden_edges: Sequence[Any],
    visible_edges: Sequence[Any],
    tol: float = 1e-2,
) -> list[Any]:
    """Return hidden edges whose 2D bounding box does not coincide with any visible edge.

    Uses a 4D spatial-hash grid over precomputed visible-edge bounding boxes
    ``(min_x, max_x, min_y, max_y)`` with bucket-boundary neighbor checks so the result is
    mathematically identical to the brute-force ``O(H * V)`` loop while running in
    ``O(H + V)`` expected time and computing each edge's bounding box only once.
    """
    if not hidden_edges:
        return []
    if not visible_edges or tol <= 0.0:
        return list(hidden_edges)

    # Cell size >= 4 * tol guarantees [x - pad, x + pad] spans at most 2 cells per axis,
    # and choosing 0.25 for default tol=1e-2 gives exact binary-power scaling (inv_cell=4.0).
    cell_size = max(0.25, tol * 4.0)
    inv_cell = 1.0 / cell_size
    pad = tol * (1.0 + 1e-6) + 1e-12
    floor = math.floor

    buckets: dict[tuple[int, int, int, int], list[tuple[float, float, float, float]]] = {}
    for v_edge in visible_edges:
        v_bb = v_edge.bounding_box()
        vx0 = float(v_bb.min.X)
        vx1 = float(v_bb.max.X)
        vy0 = float(v_bb.min.Y)
        vy1 = float(v_bb.max.Y)
        if not (
            math.isfinite(vx0) and math.isfinite(vx1) and math.isfinite(vy0) and math.isfinite(vy1)
        ):
            continue
        key = (
            floor(vx0 * inv_cell),
            floor(vx1 * inv_cell),
            floor(vy0 * inv_cell),
            floor(vy1 * inv_cell),
        )
        bucket = buckets.get(key)
        if bucket is None:
            buckets[key] = [(vx0, vx1, vy0, vy1)]
        else:
            bucket.append((vx0, vx1, vy0, vy1))

    clean_hidden_edges: list[Any] = []
    for h_edge in hidden_edges:
        h_bb = h_edge.bounding_box()
        hx0 = float(h_bb.min.X)
        hx1 = float(h_bb.max.X)
        hy0 = float(h_bb.min.Y)
        hy1 = float(h_bb.max.Y)
        if not (
            math.isfinite(hx0) and math.isfinite(hx1) and math.isfinite(hy0) and math.isfinite(hy1)
        ):
            clean_hidden_edges.append(h_edge)
            continue

        ix0_lo = floor((hx0 - pad) * inv_cell)
        ix0_hi = floor((hx0 + pad) * inv_cell)
        ix1_lo = floor((hx1 - pad) * inv_cell)
        ix1_hi = floor((hx1 + pad) * inv_cell)
        iy0_lo = floor((hy0 - pad) * inv_cell)
        iy0_hi = floor((hy0 + pad) * inv_cell)
        iy1_lo = floor((hy1 - pad) * inv_cell)
        iy1_hi = floor((hy1 + pad) * inv_cell)

        coincides = False
        if ix0_lo == ix0_hi and ix1_lo == ix1_hi and iy0_lo == iy0_hi and iy1_lo == iy1_hi:
            candidates = buckets.get((ix0_lo, ix1_lo, iy0_lo, iy1_lo))
            if candidates is not None:
                for vx0, vx1, vy0, vy1 in candidates:
                    if (
                        abs(hx0 - vx0) < tol
                        and abs(hx1 - vx1) < tol
                        and abs(hy0 - vy0) < tol
                        and abs(hy1 - vy1) < tol
                    ):
                        coincides = True
                        break
        else:
            for bx0 in range(ix0_lo, ix0_hi + 1):
                for bx1 in range(ix1_lo, ix1_hi + 1):
                    for by0 in range(iy0_lo, iy0_hi + 1):
                        for by1 in range(iy1_lo, iy1_hi + 1):
                            candidates = buckets.get((bx0, bx1, by0, by1))
                            if candidates is None:
                                continue
                            for vx0, vx1, vy0, vy1 in candidates:
                                if (
                                    abs(hx0 - vx0) < tol
                                    and abs(hx1 - vx1) < tol
                                    and abs(hy0 - vy0) < tol
                                    and abs(hy1 - vy1) < tol
                                ):
                                    coincides = True
                                    break
                            if coincides:
                                break
                        if coincides:
                            break
                    if coincides:
                        break
                if coincides:
                    break

        if not coincides:
            clean_hidden_edges.append(h_edge)

    return clean_hidden_edges


def _compute_view_projection(
    shape: bd.Solid | bd.Compound | Any,
    view_type: ViewType,
    show_hidden: bool = True,
) -> _ProjectedViewPayload:
    """Run OCCT HLR projection, hidden-edge deduplication, and SVG export for one view."""
    look_from, look_up = VIEW_VECTORS.get(view_type, ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)))

    # Use build123d.Drawing to project B-Rep edges
    dwg = Drawing(shape, look_from=look_from, look_up=look_up, with_hidden=show_hidden)

    exporter = bd.ExportSVG(unit=bd.Unit.MM)

    v_edges = dwg.visible_lines.edges()
    hidden_edges = dwg.hidden_lines.edges() if show_hidden else []

    # Cache projected visible circles for auto_centerlines (avoids a second Drawing() pass)
    visible_circles: list[tuple[float, float, float]] = []
    for v_edge in v_edges:
        if v_edge.geom_type == bd.GeomType.CIRCLE:
            with contextlib.suppress(Exception):
                c = v_edge.arc_center
                visible_circles.append((float(c.X), float(c.Y), float(v_edge.radius)))

    # Filter out hidden lines that coincide with visible edges and render hidden layer first
    if show_hidden and len(hidden_edges) > 0:
        clean_hidden_edges = _filter_coincident_hidden_edges(hidden_edges, v_edges)

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

    vb_min_x, vb_min_y, vb_w, vb_h, inner_content = _export_svg_to_inner(exporter)

    # Substitute styling with CSS custom properties
    inner_content = inner_content.replace('stroke="rgb(0,0,0)"', 'stroke="var(--line-visible)"')
    inner_content = inner_content.replace(
        'stroke="rgb(128,128,128)"', 'stroke="var(--line-hidden)"'
    )
    inner_content = inner_content.replace('id="visible"', 'id="layer_visible" class="line-visible"')
    inner_content = inner_content.replace('id="hidden"', 'id="layer_hidden" class="line-hidden"')

    # Compute exact geometric bounds from projected 2D geometry (pure CAD coordinates, no stroke inflation)
    geom_shapes: list[Any] = [dwg.visible_lines]
    if show_hidden and len(hidden_edges) > 0:
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

    return _ProjectedViewPayload(
        inner_content=inner_content,
        vb_min_x=vb_min_x,
        vb_min_y=vb_min_y,
        vb_w=vb_w,
        vb_h=vb_h,
        exact_w=exact_w,
        exact_h=exact_h,
        visible_circles=tuple(visible_circles),
    )


def _build_sheet_view_from_payload(
    shape: bd.Solid | bd.Compound | Any,
    view_type: ViewType,
    payload: _ProjectedViewPayload,
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
    """Assemble a positioned SheetView from a precomputed ``_ProjectedViewPayload``."""
    look_from, look_up = VIEW_VECTORS.get(view_type, ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)))

    orig_cx = payload.vb_min_x + payload.vb_w / 2.0
    orig_cy = payload.vb_min_y + payload.vb_h / 2.0

    view_id = f"view_{view_type.value}_{int(center_x)}_{int(center_y)}"
    rot_str = f" rotate({rotation_angle_deg})" if rotation_angle_deg != 0 else ""

    transform = (
        f"translate({center_x:.2f}, {center_y:.2f}) "
        f"scale({scale:.4f}, {scale:.4f}){rot_str} "
        f"translate({-orig_cx:.2f}, {-orig_cy:.2f})"
    )

    svg_view_group = f"""
    <!-- View: {view_type.value.upper()} at ({center_x}, {center_y}) scale={scale} -->
    <g id="{view_id}" class="sheet-view" transform="{transform}">
        {payload.inner_content}
    </g>
    """

    lbl_pos = None
    scale_str = None
    if view_label:
        lbl_y = (
            view_label_y
            if view_label_y is not None
            else (center_y + (payload.vb_h * scale / 2.0) + view_label_offset_y)
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
        width=payload.exact_w * scale,
        height=payload.exact_h * scale,
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
        show_hidden=show_hidden,
    )
    view_res.inner_svg_content = payload.inner_content
    view_res.rotation_angle_deg = rotation_angle_deg
    view_res._visible_circles = list(payload.visible_circles)
    view_res._regen_factory = project_shape_to_view
    view_res._regen_kwargs = {
        "view_type": view_type,
        "show_hidden": show_hidden,
        "view_label": view_label,
        "view_label_y": view_label_y,
        "view_label_offset_y": view_label_offset_y,
        "model_origin": model_origin,
    }
    return view_res


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
    payload = _compute_view_projection(shape=shape, view_type=view_type, show_hidden=show_hidden)
    return _build_sheet_view_from_payload(
        shape=shape,
        view_type=view_type,
        payload=payload,
        center_x=center_x,
        center_y=center_y,
        scale=scale,
        show_hidden=show_hidden,
        view_label=view_label,
        rotation_angle_deg=rotation_angle_deg,
        view_label_y=view_label_y,
        view_label_offset_y=view_label_offset_y,
        model_origin=model_origin,
    )


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


def _assemble_section_view(
    shape: bd.Solid | bd.Compound | Any,
    sec_faces: list[bd.Face],
    bg_edge_shapes: list[Any],
    center_x: float,
    center_y: float,
    scale: float,
    view_label: str,
    view_label_y: float | None,
    view_label_offset_y: float,
    model_origin: tuple[float, float] | None,
    hatch: str,
    hatch_pitch: float,
    hatch_angle: float,
    fallback_shape: Any | None = None,
    projection_origin: bd.Vector | None = None,
    proj_x: bd.Vector | None = None,
    proj_y: bd.Vector | None = None,
) -> SheetView:
    """Export 2D section faces and background edges into a hatched SheetView."""
    exporter = bd.ExportSVG(unit=bd.Unit.MM)

    if bg_edge_shapes:
        exporter.add_layer("visible_bg", line_color="gray", line_weight=0.25)
        for bg_edges in bg_edge_shapes:
            exporter.add_shape(bg_edges, layer="visible_bg")

    exporter.add_layer("visible", line_color="black", line_weight=0.5, fill_color=None)
    sorted_faces = sorted(
        sec_faces,
        key=lambda f: (round(f.center().X, 4), round(f.center().Y, 4), round(f.area, 4)),
    )
    section_compound: Any = (
        bd.Compound(sorted_faces) if sorted_faces else (fallback_shape or bd.Compound())
    )
    exporter.add_shape(section_compound, layer="visible")

    vb_min_x, vb_min_y, vb_w, vb_h, inner_content = _export_svg_to_inner(exporter)

    orig_cx = vb_min_x + vb_w / 2.0
    orig_cy = vb_min_y + vb_h / 2.0

    view_id = f"view_section_{int(center_x)}_{int(center_y)}"
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
        geom_bb = section_compound.bounding_box()
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
        projection_origin=projection_origin,
        proj_x=proj_x,
        proj_y=proj_y,
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


def _point_to_plane_ud(pt: Any, base_plane: bd.Plane) -> tuple[float, float]:
    """Convert a 2D (u, depth) tuple or 3D point/Vector/Vertex into base_plane (x_dir, z_dir)."""
    if isinstance(pt, (tuple, list)) and len(pt) == 2:
        return float(pt[0]), float(pt[1])
    if isinstance(pt, (tuple, list)) and len(pt) >= 3:
        vec = bd.Vector(float(pt[0]), float(pt[1]), float(pt[2]))
    elif isinstance(pt, bd.Vertex):
        vec = bd.Vector(float(pt.X), float(pt.Y), float(pt.Z))
    elif isinstance(pt, bd.Vector):
        vec = pt
    else:
        raise TypeError(f"Unsupported section path point type: {type(pt).__name__}")
    delta = vec - base_plane.origin
    return float(delta.dot(base_plane.x_dir)), float(delta.dot(base_plane.z_dir))


def _clip_2d_faces_x(
    faces: list[bd.Face], x_min: float, x_max: float, y_extent: float
) -> list[bd.Face]:
    """Clip 2D XY faces to the horizontal slab [x_min, x_max]."""
    if x_max - x_min <= 1e-6 or not faces:
        return []
    cx = (x_min + x_max) / 2.0
    clip_rect = bd.Pos(cx, 0.0, 0.0) * bd.Rectangle(x_max - x_min, y_extent * 2.0)
    clipped: list[bd.Face] = []
    for face in faces:
        try:
            inter = face.intersect(clip_rect)
            if inter is not None:
                for sub in inter.faces():
                    if isinstance(sub, bd.Face) and sub.area > 1e-6:
                        clipped.append(sub)
        except Exception:
            continue
    return clipped


def _unify_2d_faces(faces: list[bd.Face]) -> list[bd.Face]:
    """Fuse adjacent/touching coplanar 2D faces to remove internal jog seam lines."""
    if len(faces) <= 1:
        return faces
    try:
        fused = faces[0].fuse(*faces[1:])
        result = [f for f in fused.faces() if f.area > 1e-6]
        return result if result else faces
    except Exception:
        return faces


def _section_local(shape: Shape[Any], plane: bd.Plane) -> Shape[Any]:
    """Slice ``shape`` with ``plane`` and return the cut in the plane's local 2D frame."""
    # build123d annotates ``section(obj: Part)``, but it slices any Solid / Compound.
    sec = bd.section(shape, section_by=plane)  # pyright: ignore[reportArgumentType]
    sec_local = plane.to_local_coords(sec)
    if not isinstance(sec_local, Shape):
        raise TypeError(f"Unexpected section result {type(sec_local).__name__}")
    return sec_local


def _split_keep_bottom(shape: Shape[Any], plane: bd.Plane) -> Shape[Any]:
    """Return the half of ``shape`` behind ``plane`` (the material seen in a section view)."""
    # build123d's ``SplitType`` omits Compound, but ``split`` handles compounds (multi-solid parts).
    half = bd.split(shape, bisect_by=plane, keep=bd.Keep.BOTTOM)  # pyright: ignore[reportArgumentType]
    if not isinstance(half, Shape):
        raise TypeError(f"Unexpected split result {type(half).__name__}")
    return half


def _intersect_to_compound(shape: Shape[Any], tool: Shape[Any]) -> bd.Compound | None:
    """Intersect two shapes and wrap the result in a single Compound.

    ``Shape.intersect`` returns a ``ShapeList`` (or None), which cannot be rotated or
    projected with ``Drawing`` directly.
    """
    result = shape.intersect(tool)
    return bd.Compound(list(result)) if result else None


def _make_plane_slab_box(
    base_plane: bd.Plane, u_min: float, u_max: float, extent: float
) -> bd.Solid:
    """Create a 3D bounding box aligned to base_plane spanning u in [u_min, u_max]."""
    w = u_max - u_min
    uc = (u_min + u_max) / 2.0
    box_local = bd.Pos(uc, 0.0, 0.0) * bd.Box(w, extent * 2.0, extent * 2.0)
    return base_plane.from_local_coords(box_local)  # type: ignore[no-any-return]


def _record_regen(view: SheetView, factory: Callable[..., SheetView], **kwargs: Any) -> SheetView:
    """Remember how ``view`` was built so ``SheetView.update_shape`` can regenerate it."""
    view._regen_factory = factory
    view._regen_kwargs = kwargs
    return view


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
    sec_2d = _section_local(shape, cutting_plane)

    bg_shapes: list[Any] = []
    if show_background:
        with contextlib.suppress(Exception):
            look_from = cutting_plane.z_dir
            look_up = cutting_plane.y_dir
            split_half = _split_keep_bottom(shape, cutting_plane)
            dwg_bg = Drawing(split_half, look_from=look_from, look_up=look_up, with_hidden=False)
            if len(dwg_bg.visible_lines.edges()) > 0:
                bg_shapes.append(dwg_bg.visible_lines)

    view = _assemble_section_view(
        shape=shape,
        sec_faces=list(sec_2d.faces()),
        bg_edge_shapes=bg_shapes,
        center_x=center_x,
        center_y=center_y,
        scale=scale,
        view_label=view_label,
        view_label_y=view_label_y,
        view_label_offset_y=view_label_offset_y,
        model_origin=model_origin,
        hatch=hatch,
        hatch_pitch=hatch_pitch,
        hatch_angle=hatch_angle,
        fallback_shape=sec_2d,
        projection_origin=cutting_plane.origin,
        proj_x=cutting_plane.x_dir,
        proj_y=cutting_plane.y_dir,
    )
    return _record_regen(
        view,
        project_section_to_view,
        cutting_plane=cutting_plane,
        view_label=view_label,
        view_label_y=view_label_y,
        view_label_offset_y=view_label_offset_y,
        model_origin=model_origin,
        show_background=show_background,
        hatch=hatch,
        hatch_pitch=hatch_pitch,
        hatch_angle=hatch_angle,
    )


def project_offset_section_to_view(
    shape: bd.Solid | bd.Compound | Any,
    base_plane: bd.Plane,
    points: Sequence[Any],
    center_x: float,
    center_y: float,
    scale: float = 1.0,
    view_label: str = "SECTION A-A",
    view_label_y: float | None = None,
    view_label_offset_y: float = 7.0,
    model_origin: tuple[float, float] | None = None,
    show_background: bool = True,
    show_jog_lines: bool = False,
    hatch: str = "steel",
    hatch_pitch: float = 4.0,
    hatch_angle: float = 45.0,
) -> SheetView:
    """Project a stepped/offset section view along a multi-jog cutting path."""
    if len(points) < 2:
        raise ValueError("Offset section requires at least 2 path points.")

    ud_pts = [_point_to_plane_ud(p, base_plane) for p in points]
    bb = shape.bounding_box()
    extent = max(float(bb.size.X), float(bb.size.Y), float(bb.size.Z), 10.0) * 2.0

    all_faces: list[bd.Face] = []
    bg_shapes: list[Any] = []

    for i in range(len(ud_pts) - 1):
        u1, d1 = ud_pts[i]
        u2, d2 = ud_pts[i + 1]
        if abs(u2 - u1) <= 1e-6:
            # 90-degree jog segment perpendicular to projection plane
            continue

        u_min, u_max = min(u1, u2), max(u1, u2)
        depth = 0.5 * (d1 + d2)
        plane_k = bd.Plane(
            origin=base_plane.origin + base_plane.z_dir * depth,
            x_dir=base_plane.x_dir,
            z_dir=base_plane.z_dir,
        )
        clipped_k = _clip_2d_faces_x(
            list(_section_local(shape, plane_k).faces()), u_min, u_max, extent
        )
        all_faces.extend(clipped_k)

        if show_background:
            with contextlib.suppress(Exception):
                split_half = _split_keep_bottom(shape, plane_k)
                slab_box = _make_plane_slab_box(base_plane, u_min, u_max, extent)
                slab_bg = _intersect_to_compound(split_half, slab_box)
                if slab_bg is not None:
                    dwg_bg = Drawing(
                        slab_bg,
                        look_from=base_plane.z_dir,
                        look_up=base_plane.y_dir,
                        with_hidden=False,
                    )
                    if len(dwg_bg.visible_lines.edges()) > 0:
                        bg_shapes.append(dwg_bg.visible_lines)

    if not show_jog_lines:
        all_faces = _unify_2d_faces(all_faces)

    view = _assemble_section_view(
        shape=shape,
        sec_faces=all_faces,
        bg_edge_shapes=bg_shapes,
        center_x=center_x,
        center_y=center_y,
        scale=scale,
        view_label=view_label,
        view_label_y=view_label_y,
        view_label_offset_y=view_label_offset_y,
        model_origin=model_origin,
        hatch=hatch,
        hatch_pitch=hatch_pitch,
        hatch_angle=hatch_angle,
    )
    return _record_regen(
        view,
        project_offset_section_to_view,
        base_plane=base_plane,
        points=list(points),
        show_jog_lines=show_jog_lines,
        view_label=view_label,
        view_label_y=view_label_y,
        view_label_offset_y=view_label_offset_y,
        model_origin=model_origin,
        show_background=show_background,
        hatch=hatch,
        hatch_pitch=hatch_pitch,
        hatch_angle=hatch_angle,
    )


def project_aligned_section_to_view(
    shape: bd.Solid | bd.Compound | Any,
    base_plane: bd.Plane,
    points: Sequence[Any],
    center_x: float,
    center_y: float,
    scale: float = 1.0,
    view_label: str = "SECTION A-A",
    view_label_y: float | None = None,
    view_label_offset_y: float = 7.0,
    model_origin: tuple[float, float] | None = None,
    show_background: bool = True,
    show_jog_lines: bool = False,
    hatch: str = "steel",
    hatch_pitch: float = 4.0,
    hatch_angle: float = 45.0,
) -> SheetView:
    """Project an aligned/revolved section view by unfolding angled cutting legs onto base_plane."""
    if len(points) < 2:
        raise ValueError("Aligned section requires at least 2 path points.")

    ud_pts = [_point_to_plane_ud(p, base_plane) for p in points]
    if len(ud_pts) == 2:
        u_piv, d_piv = 0.0, 0.0
        leg_targets = [ud_pts[0], ud_pts[1]]
    else:
        u_piv, d_piv = ud_pts[1]
        leg_targets = [ud_pts[0], ud_pts[2]]

    pivot_3d = base_plane.origin + base_plane.x_dir * u_piv + base_plane.z_dir * d_piv
    bb = shape.bounding_box()
    extent = max(float(bb.size.X), float(bb.size.Y), float(bb.size.Z), 10.0) * 2.0

    all_faces: list[bd.Face] = []
    bg_shapes: list[Any] = []

    for leg_idx, (u_end, d_end) in enumerate(leg_targets):
        du = u_end - u_piv
        dd = d_end - d_piv
        leg_len = math.hypot(du, dd)
        if leg_len <= 1e-6:
            continue

        r_dir = (base_plane.x_dir * du + base_plane.z_dir * dd).normalized()
        leg_z = r_dir.cross(base_plane.y_dir)
        leg_plane = bd.Plane(origin=pivot_3d, x_dir=r_dir, z_dir=leg_z)

        clipped_leg = _clip_2d_faces_x(
            list(_section_local(shape, leg_plane).faces()), 0.0, extent, extent
        )

        place_negative = (leg_idx == 0 and du <= 0.0) or (
            leg_idx == 1 and du < 0.0 and leg_targets[0][0] >= u_piv
        )
        for face in clipped_leg:
            oriented = face.mirror(bd.Plane.YZ) if place_negative else face
            if abs(u_piv) > 1e-6:
                oriented = bd.Pos(u_piv, 0.0, 0.0) * oriented
            all_faces.append(oriented)

        if show_background:
            with contextlib.suppress(Exception):
                split_half = _split_keep_bottom(shape, leg_plane)
                half_box = _make_plane_slab_box(leg_plane, 0.0, extent, extent)
                leg_bg = _intersect_to_compound(split_half, half_box)
                if leg_bg is not None:
                    target_dir = -base_plane.x_dir if place_negative else base_plane.x_dir
                    angle_rad = math.atan2(
                        float(r_dir.cross(target_dir).dot(base_plane.y_dir)),
                        float(r_dir.dot(target_dir)),
                    )
                    axis = bd.Axis(pivot_3d, base_plane.y_dir)
                    rotated_bg = leg_bg.rotate(axis, math.degrees(angle_rad))
                    dwg_bg = Drawing(
                        rotated_bg,
                        look_from=base_plane.z_dir,
                        look_up=base_plane.y_dir,
                        with_hidden=False,
                    )
                    if len(dwg_bg.visible_lines.edges()) > 0:
                        bg_shapes.append(dwg_bg.visible_lines)

    if not show_jog_lines:
        all_faces = _unify_2d_faces(all_faces)

    view = _assemble_section_view(
        shape=shape,
        sec_faces=all_faces,
        bg_edge_shapes=bg_shapes,
        center_x=center_x,
        center_y=center_y,
        scale=scale,
        view_label=view_label,
        view_label_y=view_label_y,
        view_label_offset_y=view_label_offset_y,
        model_origin=model_origin,
        hatch=hatch,
        hatch_pitch=hatch_pitch,
        hatch_angle=hatch_angle,
    )
    return _record_regen(
        view,
        project_aligned_section_to_view,
        base_plane=base_plane,
        points=list(points),
        show_jog_lines=show_jog_lines,
        view_label=view_label,
        view_label_y=view_label_y,
        view_label_offset_y=view_label_offset_y,
        model_origin=model_origin,
        show_background=show_background,
        hatch=hatch,
        hatch_pitch=hatch_pitch,
        hatch_angle=hatch_angle,
    )


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
    parent_callout_svg = f"\n    {callout_svg}"
    parent_view.svg_content += parent_callout_svg

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
    view_res._parent_callout_svg = parent_callout_svg
    return _record_regen(
        view_res,
        project_detail_to_view,
        focus_target=focus_target,
        radius=radius,
        view_label=view_label,
        label_letter=label_letter,
        view_label_y=view_label_y,
        view_label_offset_y=view_label_offset_y,
        shape=shape,
        region_center=region_center,
        label=label,
    )


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
    # Direction as a plain vector, so regeneration does not depend on the old Face.
    face_normal = (-look_from[0], -look_from[1], -look_from[2])

    lf_vec = bd.Vector(look_from).normalized()
    ref_up = bd.Vector(0, 0, 1) if abs(lf_vec.Z) < 0.9 else bd.Vector(0, 1, 0)
    look_up_vec = ref_up - lf_vec * (ref_up.dot(lf_vec))
    look_up_vec = bd.Vector(0, 1, 0) if look_up_vec.length < 1e-4 else look_up_vec.normalized()
    look_up = (float(look_up_vec.X), float(look_up_vec.Y), float(look_up_vec.Z))

    dwg = Drawing(shape, look_from=look_from, look_up=look_up, with_hidden=show_hidden)
    exporter = bd.ExportSVG(unit=bd.Unit.MM)
    if show_hidden and len(dwg.hidden_lines.edges()) > 0:
        exporter.add_layer(
            "hidden", line_color="gray", line_weight=0.18, line_type=bd.LineType.DASHED
        )
        exporter.add_shape(dwg.hidden_lines, layer="hidden")
    exporter.add_layer("visible", line_color="black", line_weight=0.35)
    exporter.add_shape(dwg.visible_lines, layer="visible")

    vb_min_x, vb_min_y, vb_w, vb_h, inner_content = _export_svg_to_inner(exporter)

    orig_cx = vb_min_x + vb_w / 2.0
    orig_cy = vb_min_y + vb_h / 2.0
    view_id = f"view_auxiliary_{int(center_x)}_{int(center_y)}"

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
    return _record_regen(
        view_res,
        project_auxiliary_view,
        face=face_normal,
        show_hidden=show_hidden,
        view_label=view_label,
        view_label_y=view_label_y,
        view_label_offset_y=view_label_offset_y,
    )
