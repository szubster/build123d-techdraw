"""Builder Mode context managers and algebraic view classes for build123d-techdraw."""

from __future__ import annotations

import contextvars
import math
import types
from dataclasses import dataclass
from typing import Any

import build123d as bd

from build123d_techdraw.annotations.callouts import BOMBalloon
from build123d_techdraw.context import CURRENT_SHEET, CURRENT_VIEW
from build123d_techdraw.core.scale import calculate_auto_scale, find_closest_iso_scale
from build123d_techdraw.core.sheet import DrawingSheet, SheetSize
from build123d_techdraw.core.title_block import BOMItem
from build123d_techdraw.core.view import (
    SheetView,
    ViewType,
    _apply_align_to,
    project_auxiliary_view,
    project_detail_to_view,
    project_section_to_view,
    project_shape_to_view,
)
from build123d_techdraw.layout.multiview import (
    MultiviewLayout,
    add_assembly_bom_and_balloons,
    add_standard_multiview,
)


class BuildDrawing:
    """Context manager for constructing drawing sheets in build123d Builder mode."""

    def __init__(
        self,
        sheet_size: SheetSize | str = SheetSize.A3_LANDSCAPE,
        custom_size: tuple[float, float] | None = None,
        title: str | None = None,
        drawing_number: str | None = None,
        drawing_no: str | None = None,
        drawing_title: str | None = None,
        subtitle: str | None = None,
        rev: str | None = None,
        author: str | None = None,
        material: str | None = None,
        finish: str | None = None,
        date: str | None = None,
        fields: dict[str, str] | None = None,
        left_margin: float = 15.0,
        margin: float = 8.0,
        **kwargs: Any,
    ):
        style = kwargs.pop("style", "iso")
        show_border = kwargs.pop("show_border", True)
        show_title_block = kwargs.pop("show_title_block", True)
        show_grid = kwargs.pop("show_grid", True)
        show_notes = kwargs.pop("show_notes", True)
        borderless = kwargs.pop("borderless", False)
        self.sheet = DrawingSheet(
            sheet_size=sheet_size,
            custom_size=custom_size,
            left_margin=left_margin,
            margin=margin,
            style=style,
            show_border=show_border,
            show_title_block=show_title_block,
            show_grid=show_grid,
            show_notes=show_notes,
            borderless=borderless,
        )
        effective_title = title or drawing_title
        effective_dwg_no = drawing_number or drawing_no
        if (
            any(
                v is not None
                for v in [
                    effective_title,
                    effective_dwg_no,
                    subtitle,
                    rev,
                    author,
                    material,
                    finish,
                    date,
                    fields,
                ]
            )
            or kwargs
        ):
            self.sheet.set_title_block(
                title=effective_title,
                drawing_no=effective_dwg_no,
                subtitle=subtitle,
                rev=rev,
                author=author,
                material=material,
                finish=finish,
                date=date,
                fields=fields,
                **kwargs,
            )
        self._token: contextvars.Token[DrawingSheet | None] | None = None

    def __enter__(self) -> BuildDrawing:
        self._token = CURRENT_SHEET.set(self.sheet)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        if self._token is not None:
            CURRENT_SHEET.reset(self._token)
            self._token = None

    @classmethod
    def _get_context(cls) -> DrawingSheet | None:
        return CURRENT_SHEET.get()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.sheet, name)

    def __iadd__(self, item: Any) -> BuildDrawing:
        self.sheet.add(item)
        return self

    def __add__(self, item: Any) -> BuildDrawing:
        self.sheet.add(item)
        return self


def _resolve_view_scale(
    shape: Any,
    view_type: ViewType,
    scale: float | str,
    sheet: DrawingSheet | None,
    enforce_iso_scale: bool = True,
) -> float:
    """Resolve view scale factor, snapping automatic scales to ISO 5455 ratios when requested."""
    if isinstance(scale, str) and scale.lower() == "auto":
        target_sheet = sheet or CURRENT_SHEET.get()
        if target_sheet is not None:
            avail_w = target_sheet.width_mm - target_sheet.left_margin - target_sheet.right_margin
            avail_h = (
                target_sheet.height_mm - target_sheet.top_margin - target_sheet.bottom_margin - 36.0
            )
        else:
            avail_w, avail_h = 397.0, 245.0

        try:
            bbox = shape.bounding_box()
            sx, sy, sz = float(bbox.size.X), float(bbox.size.Y), float(bbox.size.Z)
            if view_type in (ViewType.FRONT, ViewType.REAR):
                m_size = (sx, sz)
            elif view_type in (ViewType.TOP, ViewType.BOTTOM):
                m_size = (sx, sy)
            elif view_type in (ViewType.RIGHT, ViewType.LEFT):
                m_size = (sy, sz)
            else:
                iso_d = math.sqrt(sx * sx + sy * sy + sz * sz) * 0.75
                m_size = (iso_d, iso_d)
        except Exception:
            m_size = (100.0, 100.0)

        return calculate_auto_scale(
            m_size,
            (avail_w, avail_h),
            enforce_iso_scale=enforce_iso_scale,
        )

    raw_val = float(scale)
    return find_closest_iso_scale(raw_val) if enforce_iso_scale else raw_val


class View(SheetView):
    """General view class for orthographic, isometric, or section views."""

    @classmethod
    def _get_context(cls) -> SheetView | None:
        return CURRENT_VIEW.get()

    def __new__(
        cls,
        shape: Any,
        view_type: ViewType = ViewType.FRONT,
        center: tuple[float, float] = (150.0, 150.0),
        scale: float | str = 1.0,
        show_hidden: bool = True,
        view_label: str | None = None,
        rotation_angle_deg: float = 0.0,
        view_label_y: float | None = None,
        model_origin: tuple[float, float] | None = None,
        sheet: DrawingSheet | None = None,
        align_to: Any = None,
        enforce_iso_scale: bool = True,
    ) -> View:
        calc_scale = _resolve_view_scale(
            shape=shape,
            view_type=view_type,
            scale=scale,
            sheet=sheet,
            enforce_iso_scale=enforce_iso_scale,
        )
        view = project_shape_to_view(
            shape=shape,
            view_type=view_type,
            center_x=center[0],
            center_y=center[1],
            scale=calc_scale,
            show_hidden=show_hidden,
            view_label=view_label,
            rotation_angle_deg=rotation_angle_deg,
            view_label_y=view_label_y,
            model_origin=model_origin,
        )
        view.__class__ = cls
        view.shape = shape
        view.rotation_angle_deg = rotation_angle_deg
        view.enforce_iso_scale = enforce_iso_scale

        if align_to is not None:
            _apply_align_to(view, align_to)

        target_sheet = sheet or CURRENT_SHEET.get()
        if target_sheet is not None:
            target_sheet.add_view(view)

        return view  # type: ignore[return-value]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Initialized via __new__
        pass


class BuildView(View):
    """Context manager and view builder for constructing a view in Builder mode."""

    def __new__(
        cls,
        shape: Any,
        view_type: ViewType = ViewType.FRONT,
        center: tuple[float, float] = (150.0, 150.0),
        scale: float | str = "auto",
        show_hidden: bool = True,
        view_label: str | None = None,
        rotation_angle_deg: float = 0.0,
        view_label_y: float | None = None,
        model_origin: tuple[float, float] | None = None,
        sheet: DrawingSheet | None = None,
        align_to: Any = None,
        enforce_iso_scale: bool = True,
    ) -> BuildView:
        return super().__new__(  # type: ignore[return-value]
            cls,
            shape=shape,
            view_type=view_type,
            center=center,
            scale=scale,
            show_hidden=show_hidden,
            view_label=view_label,
            rotation_angle_deg=rotation_angle_deg,
            view_label_y=view_label_y,
            model_origin=model_origin,
            sheet=sheet,
            align_to=align_to,
            enforce_iso_scale=enforce_iso_scale,
        )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass


class FrontView(View):
    """Front orthographic projection view."""

    def __new__(
        cls,
        shape: Any,
        center: tuple[float, float] = (150.0, 150.0),
        scale: float | str = 1.0,
        show_hidden: bool = True,
        view_label: str | None = None,
        rotation_angle_deg: float = 0.0,
        view_label_y: float | None = None,
        model_origin: tuple[float, float] | None = None,
        sheet: DrawingSheet | None = None,
        align_to: Any = None,
        enforce_iso_scale: bool = True,
    ) -> FrontView:
        return super().__new__(  # type: ignore[return-value]
            cls,
            shape=shape,
            view_type=ViewType.FRONT,
            center=center,
            scale=scale,
            show_hidden=show_hidden,
            view_label=view_label,
            rotation_angle_deg=rotation_angle_deg,
            view_label_y=view_label_y,
            model_origin=model_origin,
            sheet=sheet,
            align_to=align_to,
            enforce_iso_scale=enforce_iso_scale,
        )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass


class TopView(View):
    """Top orthographic projection view."""

    def __new__(
        cls,
        shape: Any,
        center: tuple[float, float] = (150.0, 150.0),
        scale: float | str = 1.0,
        show_hidden: bool = True,
        view_label: str | None = None,
        rotation_angle_deg: float = 0.0,
        view_label_y: float | None = None,
        model_origin: tuple[float, float] | None = None,
        sheet: DrawingSheet | None = None,
        align_to: Any = None,
        enforce_iso_scale: bool = True,
    ) -> TopView:
        return super().__new__(  # type: ignore[return-value]
            cls,
            shape=shape,
            view_type=ViewType.TOP,
            center=center,
            scale=scale,
            show_hidden=show_hidden,
            view_label=view_label,
            rotation_angle_deg=rotation_angle_deg,
            view_label_y=view_label_y,
            model_origin=model_origin,
            sheet=sheet,
            align_to=align_to,
            enforce_iso_scale=enforce_iso_scale,
        )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass


class RightView(View):
    """Right orthographic projection view."""

    def __new__(
        cls,
        shape: Any,
        center: tuple[float, float] = (150.0, 150.0),
        scale: float | str = 1.0,
        show_hidden: bool = True,
        view_label: str | None = None,
        rotation_angle_deg: float = 0.0,
        view_label_y: float | None = None,
        model_origin: tuple[float, float] | None = None,
        sheet: DrawingSheet | None = None,
        align_to: Any = None,
        enforce_iso_scale: bool = True,
    ) -> RightView:
        return super().__new__(  # type: ignore[return-value]
            cls,
            shape=shape,
            view_type=ViewType.RIGHT,
            center=center,
            scale=scale,
            show_hidden=show_hidden,
            view_label=view_label,
            rotation_angle_deg=rotation_angle_deg,
            view_label_y=view_label_y,
            model_origin=model_origin,
            sheet=sheet,
            align_to=align_to,
            enforce_iso_scale=enforce_iso_scale,
        )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass


class LeftView(View):
    """Left orthographic projection view."""

    def __new__(
        cls,
        shape: Any,
        center: tuple[float, float] = (150.0, 150.0),
        scale: float | str = 1.0,
        show_hidden: bool = True,
        view_label: str | None = None,
        rotation_angle_deg: float = 0.0,
        view_label_y: float | None = None,
        model_origin: tuple[float, float] | None = None,
        sheet: DrawingSheet | None = None,
        align_to: Any = None,
        enforce_iso_scale: bool = True,
    ) -> LeftView:
        return super().__new__(  # type: ignore[return-value]
            cls,
            shape=shape,
            view_type=ViewType.LEFT,
            center=center,
            scale=scale,
            show_hidden=show_hidden,
            view_label=view_label,
            rotation_angle_deg=rotation_angle_deg,
            view_label_y=view_label_y,
            model_origin=model_origin,
            sheet=sheet,
            align_to=align_to,
            enforce_iso_scale=enforce_iso_scale,
        )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass


class BottomView(View):
    """Bottom orthographic projection view."""

    def __new__(
        cls,
        shape: Any,
        center: tuple[float, float] = (150.0, 150.0),
        scale: float | str = 1.0,
        show_hidden: bool = True,
        view_label: str | None = None,
        rotation_angle_deg: float = 0.0,
        view_label_y: float | None = None,
        model_origin: tuple[float, float] | None = None,
        sheet: DrawingSheet | None = None,
        align_to: Any = None,
        enforce_iso_scale: bool = True,
    ) -> BottomView:
        return super().__new__(  # type: ignore[return-value]
            cls,
            shape=shape,
            view_type=ViewType.BOTTOM,
            center=center,
            scale=scale,
            show_hidden=show_hidden,
            view_label=view_label,
            rotation_angle_deg=rotation_angle_deg,
            view_label_y=view_label_y,
            model_origin=model_origin,
            sheet=sheet,
            align_to=align_to,
            enforce_iso_scale=enforce_iso_scale,
        )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass


class RearView(View):
    """Rear orthographic projection view."""

    def __new__(
        cls,
        shape: Any,
        center: tuple[float, float] = (150.0, 150.0),
        scale: float | str = 1.0,
        show_hidden: bool = True,
        view_label: str | None = None,
        rotation_angle_deg: float = 0.0,
        view_label_y: float | None = None,
        model_origin: tuple[float, float] | None = None,
        sheet: DrawingSheet | None = None,
        align_to: Any = None,
        enforce_iso_scale: bool = True,
    ) -> RearView:
        return super().__new__(  # type: ignore[return-value]
            cls,
            shape=shape,
            view_type=ViewType.REAR,
            center=center,
            scale=scale,
            show_hidden=show_hidden,
            view_label=view_label,
            rotation_angle_deg=rotation_angle_deg,
            view_label_y=view_label_y,
            model_origin=model_origin,
            sheet=sheet,
            align_to=align_to,
            enforce_iso_scale=enforce_iso_scale,
        )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass


class IsometricView(View):
    """3D isometric axonometric projection view."""

    def __new__(
        cls,
        shape: Any,
        center: tuple[float, float] = (150.0, 150.0),
        scale: float | str = 1.0,
        show_hidden: bool = False,
        view_label: str | None = None,
        rotation_angle_deg: float = 0.0,
        view_label_y: float | None = None,
        model_origin: tuple[float, float] | None = None,
        sheet: DrawingSheet | None = None,
        align_to: Any = None,
        enforce_iso_scale: bool = True,
    ) -> IsometricView:
        return super().__new__(  # type: ignore[return-value]
            cls,
            shape=shape,
            view_type=ViewType.ISOMETRIC,
            center=center,
            scale=scale,
            show_hidden=show_hidden,
            view_label=view_label,
            rotation_angle_deg=rotation_angle_deg,
            view_label_y=view_label_y,
            model_origin=model_origin,
            sheet=sheet,
            align_to=align_to,
            enforce_iso_scale=enforce_iso_scale,
        )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass


class SectionView(View):
    """2D planar section cut slice with engineering hatching."""

    def __new__(
        cls,
        shape: Any,
        cutting_plane: bd.Plane,
        center: tuple[float, float] = (150.0, 150.0),
        scale: float | str = 1.0,
        view_label: str = "SECTION A-A",
        view_label_y: float | None = None,
        model_origin: tuple[float, float] | None = None,
        sheet: DrawingSheet | None = None,
        align_to: Any = None,
        show_background: bool = True,
        enforce_iso_scale: bool = True,
        hatch: str = "steel",
        hatch_pitch: float = 4.0,
        hatch_angle: float = 45.0,
    ) -> SectionView:
        calc_scale = _resolve_view_scale(
            shape=shape,
            view_type=ViewType.SECTION,
            scale=scale,
            sheet=sheet,
            enforce_iso_scale=enforce_iso_scale,
        )
        view = project_section_to_view(
            shape=shape,
            cutting_plane=cutting_plane,
            center_x=center[0],
            center_y=center[1],
            scale=calc_scale,
            view_label=view_label,
            view_label_y=view_label_y,
            model_origin=model_origin,
            show_background=show_background,
            hatch=hatch,
            hatch_pitch=hatch_pitch,
            hatch_angle=hatch_angle,
        )
        view.__class__ = cls
        view.shape = shape
        view.enforce_iso_scale = enforce_iso_scale

        if align_to is not None:
            _apply_align_to(view, align_to)

        target_sheet = sheet or CURRENT_SHEET.get()
        if target_sheet is not None:
            target_sheet.add_view(view)

        return view  # type: ignore[return-value]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass


class DetailView(View):
    """Magnified circular or rectangular detail view."""

    def __new__(
        cls,
        parent_view: SheetView,
        region_center: Any = None,
        focus_target: Any = None,
        radius: float = 12.0,
        scale: float | str = 2.0,
        label: str | None = None,
        center: tuple[float, float] = (150.0, 150.0),
        shape: str = "circle",
        view_label: str | None = None,
        label_letter: str = "A",
        view_label_y: float | None = None,
        sheet: DrawingSheet | None = None,
        align_to: Any = None,
        enforce_iso_scale: bool = True,
    ) -> DetailView:
        target = region_center if region_center is not None else focus_target
        eff_letter = label if label is not None else label_letter
        eff_label = view_label if view_label is not None else f"DETAIL {eff_letter}"
        raw_scale = 2.0 if scale == "auto" else float(scale)
        calc_scale = find_closest_iso_scale(raw_scale) if enforce_iso_scale else raw_scale
        view = project_detail_to_view(
            parent_view=parent_view,
            focus_target=target,
            radius=radius,
            scale=calc_scale,
            center_x=center[0],
            center_y=center[1],
            view_label=eff_label,
            label_letter=eff_letter,
            view_label_y=view_label_y,
            shape=shape,
            region_center=region_center,
            label=label,
        )
        view.__class__ = cls
        view.enforce_iso_scale = enforce_iso_scale
        if align_to is not None:
            _apply_align_to(view, align_to)

        target_sheet = sheet or getattr(parent_view, "sheet", None) or CURRENT_SHEET.get()
        if target_sheet is not None and view not in target_sheet.views:
            target_sheet.add_view(view)

        return view  # type: ignore[return-value]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass


class BreakView(View):
    """Orthographic projection view with one or more broken spans."""

    def __new__(
        cls,
        shape: Any,
        interval: tuple[float, float],
        axis: str = "X",
        style: str = "zigzag",
        gap: float = 10.0,
        view_type: ViewType = ViewType.FRONT,
        center: tuple[float, float] = (150.0, 150.0),
        scale: float | str = 1.0,
        show_hidden: bool = True,
        view_label: str | None = None,
        rotation_angle_deg: float = 0.0,
        view_label_y: float | None = None,
        model_origin: tuple[float, float] | None = None,
        sheet: DrawingSheet | None = None,
        align_to: Any = None,
        enforce_iso_scale: bool = True,
    ) -> BreakView:
        view = super().__new__(
            cls,
            shape=shape,
            view_type=view_type,
            center=center,
            scale=scale,
            show_hidden=show_hidden,
            view_label=view_label,
            rotation_angle_deg=rotation_angle_deg,
            view_label_y=view_label_y,
            model_origin=model_origin,
            sheet=sheet,
            align_to=align_to,
            enforce_iso_scale=enforce_iso_scale,
        )
        view.add_break(interval=interval, axis=axis, style=style, gap=gap)
        return view  # type: ignore[return-value]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass


class AuxiliaryView(View):
    """Auxiliary projection view normal to an inclined face."""

    def __new__(
        cls,
        shape: Any,
        face: Any,
        center: tuple[float, float] = (150.0, 150.0),
        scale: float | str = 1.0,
        show_hidden: bool = True,
        view_label: str = "AUXILIARY VIEW",
        view_label_y: float | None = None,
        sheet: DrawingSheet | None = None,
        align_to: Any = None,
        enforce_iso_scale: bool = True,
    ) -> AuxiliaryView:
        calc_scale = _resolve_view_scale(
            shape=shape,
            view_type=ViewType.AUXILIARY,
            scale=scale,
            sheet=sheet,
            enforce_iso_scale=enforce_iso_scale,
        )
        view = project_auxiliary_view(
            shape=shape,
            face=face,
            center_x=center[0],
            center_y=center[1],
            scale=calc_scale,
            show_hidden=show_hidden,
            view_label=view_label,
            view_label_y=view_label_y,
        )
        view.__class__ = cls
        view.enforce_iso_scale = enforce_iso_scale
        if align_to is not None:
            _apply_align_to(view, align_to)

        target_sheet = sheet or CURRENT_SHEET.get()
        if target_sheet is not None and view not in target_sheet.views:
            target_sheet.add_view(view)

        return view  # type: ignore[return-value]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass


class StandardMultiview(MultiviewLayout):
    """Generates standard multiview layout; supports both Builder and Algebraic modes."""

    def __new__(
        cls,
        shape: bd.Solid | bd.Compound | Any,
        sheet: DrawingSheet | None = None,
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
        enforce_iso_scale: bool = True,
    ) -> StandardMultiview:
        target_sheet = sheet or CURRENT_SHEET.get()
        created_detached = False
        if target_sheet is None:
            target_sheet = DrawingSheet(SheetSize.A3_LANDSCAPE)
            created_detached = True

        mv = add_standard_multiview(
            sheet=target_sheet,
            shape=shape,
            views=views,
            layout=layout,
            scale=scale,
            spacing=spacing,
            center=center,
            show_hidden=show_hidden,
            show_labels=show_labels,
            show_scale_label=show_scale_label,
            auto_dimensions=auto_dimensions,
            auto_centerlines=auto_centerlines,
            enforce_iso_scale=enforce_iso_scale,
        )
        mv.__class__ = cls
        if created_detached:
            for v in mv.views.values():
                v.sheet = None
            mv.sheet = None  # type: ignore[assignment]
        return mv  # type: ignore[return-value]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __enter__(self) -> StandardMultiview:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        pass


@dataclass
class TitleBlock:
    """Configures title block metadata in Builder or Algebraic mode."""

    title: str | None = None
    drawing_number: str | None = None
    drawing_no: str | None = None
    drawing_title: str | None = None
    subtitle: str | None = None
    sheet_no: str | None = None
    sheet_num: int | None = None
    num_sheets: int | None = None
    total_sheets: str | int | None = None
    scale: str | None = None
    rev: str | None = None
    date: str | None = None
    material: str | None = None
    finish: str | None = None
    author: str | None = None
    project: str | None = None
    status: str | None = None
    units: str | None = None
    third_angle: bool | None = None
    fields: dict[str, str] | None = None

    def __post_init__(self) -> None:
        ctx = CURRENT_SHEET.get()
        if ctx is not None:
            self._apply_to_sheet(ctx)

    def _apply_to_sheet(self, sheet: DrawingSheet) -> None:
        sheet.set_title_block(
            title=self.title,
            drawing_number=self.drawing_number,
            drawing_title=self.drawing_title,
            drawing_no=self.drawing_no,
            subtitle=self.subtitle,
            sheet_no=self.sheet_no,
            sheet_num=self.sheet_num,
            num_sheets=self.num_sheets,
            total_sheets=self.total_sheets,
            scale=self.scale,
            rev=self.rev,
            date=self.date,
            material=self.material,
            finish=self.finish,
            author=self.author,
            project=self.project,
            status=self.status,
            units=self.units,
            third_angle=self.third_angle,
            fields=self.fields,
        )


@dataclass
class GeneralNotes:
    """Configures general drawing notes in Builder or Algebraic mode."""

    notes: list[str]

    def __post_init__(self) -> None:
        ctx = CURRENT_SHEET.get()
        if ctx is not None:
            self._apply_to_sheet(ctx)

    def _apply_to_sheet(self, sheet: DrawingSheet) -> None:
        sheet.set_notes(self.notes)


@dataclass
class Revision:
    """Appends a revision history row in Builder or Algebraic mode."""

    rev: str
    description: str
    date: str | None = None
    approved_by: str = "APPROVED"

    def __post_init__(self) -> None:
        ctx = CURRENT_SHEET.get()
        if ctx is not None:
            self._apply_to_sheet(ctx)

    def _apply_to_sheet(self, sheet: DrawingSheet) -> None:
        sheet.add_revision(
            rev=self.rev,
            description=self.description,
            date=self.date or sheet.title_info.date,
            approved_by=self.approved_by,
        )


class AssemblyBOM:
    """Generates BOM table and leader balloons in Builder or Algebraic mode."""

    def __init__(
        self,
        assembly: bd.Compound | Any,
        target_view: SheetView | None = None,
        balloon_distance: float = 30.0,
        sheet: DrawingSheet | None = None,
    ):
        self.assembly = assembly
        self.target_view = target_view
        self.balloon_distance = balloon_distance
        self.bom_items: list[BOMItem] = []
        self.balloons: list[BOMBalloon] = []

        target_sheet = sheet or CURRENT_SHEET.get()
        if target_sheet is not None and target_view is not None:
            self._apply_to_sheet(target_sheet)

    def _apply_to_sheet(self, sheet: DrawingSheet) -> None:
        if self.target_view is not None:
            items, balloons = add_assembly_bom_and_balloons(
                sheet=sheet,
                assembly=self.assembly,
                target_view=self.target_view,
                balloon_distance=self.balloon_distance,
            )
            self.bom_items = items
            self.balloons = balloons
