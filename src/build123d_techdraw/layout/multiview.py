"""High-level declarative multiview layout helpers and automated assembly BOM tools."""

from __future__ import annotations

import atexit
import io
import math
import multiprocessing as mp
import os
import threading
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import build123d as bd
from build123d.exporters import Drawing
from build123d.topology import Shape
from OCP.BinTools import BinTools, BinTools_FormatVersion
from OCP.TopoDS import TopoDS_Shape

from build123d_techdraw.annotations.callouts import BOMBalloon
from build123d_techdraw.core.directive import SheetDirective
from build123d_techdraw.core.part_info import get_part_info
from build123d_techdraw.core.scale import (
    find_closest_iso_scale,
    format_iso_ratio,
    format_iso_scale,
)
from build123d_techdraw.core.title_block import BOMItem
from build123d_techdraw.core.view import (
    VIEW_VECTORS,
    SheetView,
    ViewType,
    _build_sheet_view_from_payload,
    _compute_view_projection,
    _ProjectedViewPayload,
)

FIRST_ANGLE = "first_angle"
"""ISO 128-3 / ISO 5456-2 default projection method (ISO "method E")."""
THIRD_ANGLE = "third_angle"
"""ASME Y14.3 / US default projection method (ISO "method A")."""

# OCCT HLRBRep_Algo (via cadquery-ocp) holds the Python GIL during projection (so
# ThreadPoolExecutor yields ~1.0x speedup and risks OCCT shared-state contention), and
# importing build123d initializes native TBB/OpenBLAS thread pools (making POSIX fork()
# unsafe). We therefore use a process-isolated forkserver/spawn worker pool with lossless
# OCP.BinTools binary B-Rep serialization (~0.2-0.6 ms round-trip).
_PARALLEL_WARM_FACE_THRESHOLD = 16
_PARALLEL_COLD_FACE_THRESHOLD = 120

_POOL_LOCK = threading.Lock()
_PROJECTION_POOL: ProcessPoolExecutor | None = None
_PROJECTION_POOL_WORKERS: int = 0
_ATEXIT_REGISTERED: bool = False


def shutdown_projection_pool() -> None:
    """Shut down the cached parallel multi-view projection worker pool, if running."""
    global _PROJECTION_POOL, _PROJECTION_POOL_WORKERS
    with _POOL_LOCK:
        pool = _PROJECTION_POOL
        _PROJECTION_POOL = None
        _PROJECTION_POOL_WORKERS = 0
    if pool is not None:
        pool.shutdown(wait=True, cancel_futures=True)


def _get_projection_pool(requested_workers: int) -> ProcessPoolExecutor:
    """Return a lazily initialized, process-isolated worker pool for HLR projection."""
    global _PROJECTION_POOL, _PROJECTION_POOL_WORKERS, _ATEXIT_REGISTERED
    cpu_cap = max(1, os.cpu_count() or 4)
    target = max(1, min(requested_workers, cpu_cap))
    with _POOL_LOCK:
        if _PROJECTION_POOL is not None and target <= _PROJECTION_POOL_WORKERS:
            return _PROJECTION_POOL
        old_pool = _PROJECTION_POOL
        methods = mp.get_all_start_methods()
        # Never use "fork": build123d/OCCT/OpenBLAS spawns native threads on import.
        start_method = "forkserver" if "forkserver" in methods else "spawn"
        ctx = mp.get_context(start_method)
        pool_size = max(target, min(4, cpu_cap))
        pool = ProcessPoolExecutor(max_workers=pool_size, mp_context=ctx)
        _PROJECTION_POOL = pool
        _PROJECTION_POOL_WORKERS = pool_size
        if not _ATEXIT_REGISTERED:
            atexit.register(shutdown_projection_pool)
            _ATEXIT_REGISTERED = True
    if old_pool is not None:
        old_pool.shutdown(wait=False)
    return pool


def _serialize_shape_brep(shape: Any) -> bytes | None:
    """Serialize a build123d Shape to lossless IEEE-754 binary B-Rep bytes via OCP.BinTools."""
    if not isinstance(shape, Shape) or shape.wrapped is None:
        return None
    buf = io.BytesIO()
    BinTools.Write_s(
        shape.wrapped,
        buf,
        False,
        False,
        BinTools_FormatVersion.BinTools_FormatVersion_CURRENT,
    )
    return buf.getvalue()


def _project_view_worker(args: tuple[bytes, ViewType, bool]) -> _ProjectedViewPayload:
    """Top-level worker entry point for process-isolated OCCT view projection."""
    brep_bytes, view_type, show_hidden = args
    topo = TopoDS_Shape()
    BinTools.Read_s(topo, io.BytesIO(brep_bytes))
    shape = Shape.cast(topo)
    return _compute_view_projection(shape=shape, view_type=view_type, show_hidden=show_hidden)


def _should_project_in_parallel(
    shape: Any,
    num_views: int,
    parallel: bool | Literal["auto"],
) -> bool:
    """Decide whether to fan out multi-view HLR projection across worker processes."""
    if num_views <= 1 or parallel is False:
        return False
    if parallel is True:
        return True
    if parallel != "auto":
        raise ValueError(f"Expected parallel to be True, False, or 'auto'; got {parallel!r}.")

    env_val = os.environ.get("BUILD123D_TECHDRAW_PARALLEL", "").strip().lower()
    if env_val in ("0", "false", "off", "no"):
        return False
    if env_val in ("1", "true", "on", "yes"):
        return True

    if not isinstance(shape, Shape):
        return False
    face_count = len(shape.faces())
    threshold = (
        _PARALLEL_WARM_FACE_THRESHOLD
        if _PROJECTION_POOL is not None
        else _PARALLEL_COLD_FACE_THRESHOLD
    )
    return face_count >= threshold


def _project_views_payloads(
    shape: Any,
    view_specs: list[tuple[ViewType, bool]],
    *,
    parallel: bool | Literal["auto"],
    max_workers: int | None,
) -> list[_ProjectedViewPayload]:
    """Compute view projection payloads in deterministic ``view_specs`` order."""
    if _should_project_in_parallel(shape, len(view_specs), parallel):
        brep_bytes = _serialize_shape_brep(shape)
        if brep_bytes is not None:
            workers = (
                min(len(view_specs), max_workers)
                if max_workers is not None
                else min(len(view_specs), os.cpu_count() or 4)
            )
            try:
                pool = _get_projection_pool(workers)
                tasks = [(brep_bytes, v_type, v_hidden) for v_type, v_hidden in view_specs]
                return list(pool.map(_project_view_worker, tasks))
            except (OSError, PermissionError, RuntimeError):
                # Clean sequential fallback in restricted/sandboxed subprocess environments.
                pass

    return [
        _compute_view_projection(shape=shape, view_type=v_type, show_hidden=v_hidden)
        for v_type, v_hidden in view_specs
    ]


if TYPE_CHECKING:
    from build123d_techdraw.core.sheet import DrawingSheet


@dataclass
class MultiviewLayout(SheetDirective):
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

    def _apply_to_sheet(self, sheet: DrawingSheet) -> None:
        for v in self.views.values():
            sheet.add_view(v)

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
                target_shape = v.shape
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
        local_shape = frame.to_local_coords(shape)
        if not isinstance(local_shape, Shape):
            raise TypeError(f"Unexpected to_local_coords result {type(local_shape).__name__}")
        local_bb = local_shape.bounding_box()
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
    layout: str = FIRST_ANGLE,
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
    parallel: bool | Literal["auto"] = "auto",
    max_workers: int | None = None,
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
        Projection method used to arrange the views (ISO 5456-2):

        * ``'first_angle'`` (default) -- ISO 128-3 / ISO 5456-2 default
          ("method E"): Top view **below** the Front view, Right view to the
          **left** of it, Left view to its right.
        * ``'third_angle'`` -- ASME Y14.3 / US default ("method A"): Top view
          **above** the Front view, Right view to its **right**, Left view to
          its left.

        The Isometric view is placed in the free corner of the grid. The
        projection symbol in the title block (``sheet.title_info.third_angle``)
        is set to match the chosen method.
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
    parallel:
        Multi-view projection execution mode: ``'auto'`` (default, uses a
        process-isolated worker pool when the shape is complex enough to offset
        worker startup/IPC overhead, or when ``BUILD123D_TECHDRAW_PARALLEL=1``),
        ``True`` (always project views in parallel across worker processes when
        more than one view is requested), or ``False`` (always sequential).
    max_workers:
        Optional upper bound on worker processes when projecting in parallel.
    """
    layout_key = layout.lower()
    if layout_key not in (FIRST_ANGLE, THIRD_ANGLE):
        raise ValueError(
            f"Unknown projection layout {layout!r}; use {FIRST_ANGLE!r} or {THIRD_ANGLE!r}."
        )
    is_third_angle = layout_key == THIRD_ANGLE

    if views is None:
        views = [ViewType.FRONT, ViewType.TOP, ViewType.RIGHT, ViewType.ISOMETRIC]

    # Exact unscaled 2D extent (width, height) of every requested view, measured in
    # the same projection frame that SheetView uses when drawing it.
    extents = {v: projected_view_extent(shape, v) for v in views}

    # Standard arrangement grid (ISO 5456-2 / ASME Y14.3).
    #
    # Columns (width = widest projected view in the column):
    #   "main": Front / Top
    #   "side": Right view + Isometric
    #   "far":  Left view, on the opposite side of the main column
    # Rows (height = tallest projected view in the row):
    #   "elev": Front / Right / Left / Rear elevations
    #   "plan": Top view + Isometric
    #
    # Third-angle places each view on the side it is seen from:
    #   columns [far, main, side], rows [plan, elev] -> Top above, Right on the right.
    # First-angle places each view on the opposite side:
    #   columns [side, main, far], rows [elev, plan] -> Top below, Right on the left.
    # In both, the Isometric view fills the otherwise empty corner of the grid.
    col_members: dict[str, tuple[ViewType, ...]] = {
        "main": (ViewType.FRONT, ViewType.TOP, ViewType.BOTTOM, ViewType.REAR),
        "side": (ViewType.RIGHT, ViewType.ISOMETRIC),
        "far": (ViewType.LEFT,),
    }
    row_members: dict[str, tuple[ViewType, ...]] = {
        "elev": (ViewType.FRONT, ViewType.RIGHT, ViewType.LEFT, ViewType.REAR),
        "plan": (ViewType.TOP, ViewType.ISOMETRIC),
    }
    col_widths: dict[str, float] = {
        col: max(extents[v][0] for v in views if v in members)
        for col, members in col_members.items()
        if any(v in members for v in views)
    }
    row_heights: dict[str, float] = {
        row: max(extents[v][1] for v in views if v in members)
        for row, members in row_members.items()
        if any(v in members for v in views)
    }

    col_order = ["far", "main", "side"] if is_third_angle else ["side", "main", "far"]
    row_order = ["plan", "elev"] if is_third_angle else ["elev", "plan"]
    cols = [c for c in col_order if c in col_widths]
    rows = [r for r in row_order if r in row_heights]

    num_cols = len(cols)
    num_rows = len(rows)

    total_unscaled_w = sum(col_widths.values())
    total_unscaled_h = sum(row_heights.values())

    # Exclusion zone at bottom-right for title block and optional stacked BOM table
    has_bom = bool(sheet.bom_items) or any(c.label for c in extract_assembly_components(shape))
    bom_h = (5.5 + len(sheet.bom_items) * 5.2) if sheet.bom_items else 20.0
    bottom_excl = (
        sheet.bottom_margin
        + 36.0
        + (bom_h + 2.0 if has_bom and sheet.bom_position == "above_title_block" else 0.0)
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

    # Scaled grid geometry
    total_group_w = calculated_scale * total_unscaled_w + spacing * max(num_cols - 1, 0)
    total_group_h = calculated_scale * total_unscaled_h + spacing * max(num_rows - 1, 0)

    col_cx: dict[str, float] = {}
    cursor = center_x - total_group_w / 2.0
    for col in cols:
        w = col_widths[col] * calculated_scale
        col_cx[col] = cursor + w / 2.0
        cursor += w + spacing

    row_cy: dict[str, float] = {}
    cursor = center_y - total_group_h / 2.0
    for row in rows:
        h = row_heights[row] * calculated_scale
        row_cy[row] = cursor + h / 2.0
        cursor += h + spacing

    # Ensure bottom-row labels and dimensions do not encroach on bottom-right exclusion zone
    if rows:
        bottom_row = rows[-1]
        bottom_edge = row_cy[bottom_row] + row_heights[bottom_row] * calculated_scale / 2.0 + 16.0
        max_allowed_bottom = sheet.height_mm - bottom_excl + 10.0
        if bottom_edge > max_allowed_bottom:
            dy_shift = bottom_edge - max_allowed_bottom
            for row in rows:
                row_cy[row] -= dy_shift

    layout_obj = MultiviewLayout(
        scale=calculated_scale,
        scale_str=scale_str,
        sheet=sheet,
    )

    sheet.title_info.third_angle = is_third_angle
    sheet.title_info.scale = format_iso_ratio(calculated_scale)

    grid_cells: dict[ViewType, tuple[str, str]] = {
        ViewType.FRONT: ("main", "elev"),
        ViewType.TOP: ("main", "plan"),
        ViewType.RIGHT: ("side", "elev"),
        ViewType.LEFT: ("far", "elev"),
        ViewType.ISOMETRIC: ("side", "plan"),
    }
    pos_map: dict[ViewType, tuple[float, float]] = {
        v: (col_cx[col], row_cy[row]) for v, (col, row) in grid_cells.items() if v in views
    }

    view_specs: list[tuple[ViewType, bool]] = [
        (v_type, show_hidden if v_type != ViewType.ISOMETRIC else False) for v_type in views
    ]
    payloads = _project_views_payloads(
        shape,
        view_specs,
        parallel=parallel,
        max_workers=max_workers,
    )

    for (v_type, v_hidden), payload in zip(view_specs, payloads, strict=True):
        v_center = pos_map.get(v_type, (center_x, center_y))
        v_label = v_type.value.upper() if show_labels else None
        built_view = _build_sheet_view_from_payload(
            shape=shape,
            view_type=v_type,
            payload=payload,
            center_x=v_center[0],
            center_y=v_center[1],
            scale=calculated_scale,
            show_hidden=v_hidden,
            view_label=v_label,
        )
        v_obj = sheet.add_view(built_view)
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
            circles = v_obj._visible_circles
            if circles is None:
                look_from, look_up = VIEW_VECTORS.get(v_type, ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)))
                circles = []
                try:
                    dwg_v = Drawing(shape, look_from=look_from, look_up=look_up)
                    for edge in dwg_v.visible_lines.edges():
                        if edge.geom_type == bd.GeomType.CIRCLE:
                            c = edge.arc_center
                            circles.append((float(c.X), float(c.Y), float(edge.radius)))
                except Exception:
                    circles = []
            for cx, cy, radius in circles:
                paper_r = radius * v_obj.scale
                # Centermark arms extend 2.0 mm beyond circle perimeter per ISO 128 / ASME Y14.2
                cm_size = (paper_r + 2.0) * 2.0
                v_obj.centermark(cx, cy, size=cm_size)

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


def extract_assembly_components(shape: Shape[Any]) -> list[Shape[Any]]:
    """Recursively extract leaf child parts of an assembly, or the solids of a plain shape."""
    if shape.children:
        parts: list[Shape[Any]] = []
        for child in shape.children:
            if child.children:
                parts.extend(extract_assembly_components(child))
            else:
                parts.append(child)
        return parts
    solids = shape.solids()
    return list(solids) if solids else [shape]


def _group_components_by_label(
    components: list[Shape[Any]],
) -> dict[str, list[Shape[Any]]]:
    """Group components by ``label`` (class name if unlabeled), preserving first-seen order."""
    grouped: dict[str, list[Shape[Any]]] = {}
    for comp in components:
        grouped.setdefault(comp.label or type(comp).__name__, []).append(comp)
    return grouped


def _bom_material(component: Shape[Any]) -> str:
    """BOM material text: PartInfo.material_spec, else the build123d material name."""
    info = get_part_info(component)
    if info is not None and info.material_spec:
        return info.material_spec
    material = component.material
    if material is not None:
        return material.name.replace("_", " ").upper()
    return "STANDARD"


def generate_assembly_bom(assembly: Shape[Any] | list[Shape[Any]]) -> list[BOMItem]:
    """Generate structured BOM table entries by grouping and counting assembly components.

    Parts are grouped by their ``label`` (or shape class name if unlabeled). Part number
    and material text come from :class:`PartInfo` (see :func:`set_part_info`); material
    falls back to the shape's build123d ``material``.
    """
    components = assembly if isinstance(assembly, list) else extract_assembly_components(assembly)

    bom_items: list[BOMItem] = []
    for item_idx, (name, items) in enumerate(
        _group_components_by_label(components).items(), start=1
    ):
        first = items[0]
        info = get_part_info(first)
        bom_items.append(
            BOMItem(
                item_no=item_idx,
                qty=len(items),
                part_no=(info.part_no if info is not None else "") or f"PART-{item_idx:03d}",
                description=name,
                material=_bom_material(first),
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

    grouped = _group_components_by_label(components)

    balloons: list[BOMBalloon] = []
    num_items = len(bom_items)

    for idx, item in enumerate(bom_items):
        part_list = grouped.get(item.description, [])
        if not part_list:
            continue
        rep_part = part_list[0]

        # Point at the centre of mass; other shape kinds use their bounding-box centre.
        if isinstance(rep_part, (bd.Solid, bd.Compound)):
            pt = rep_part.center()
        else:
            pt = rep_part.bounding_box().center()
        target_pt = target_view.to_sheet_3d(float(pt.X), float(pt.Y), float(pt.Z))

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
