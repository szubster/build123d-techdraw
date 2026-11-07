"""Builder Mode context managers and algebraic view classes for build123d-techdraw."""

from __future__ import annotations

import contextvars
import types
from dataclasses import dataclass
from typing import Any

import build123d as bd

from build123d_techdraw.annotations.callouts import BOMBalloon
from build123d_techdraw.context import CURRENT_SHEET, CURRENT_VIEW
from build123d_techdraw.core.sheet import DrawingSheet, SheetSize
from build123d_techdraw.core.title_block import BOMItem
from build123d_techdraw.core.view import (
    SheetView,
    ViewType,
    project_section_to_view,
    project_shape_to_view,
)
from build123d_techdraw.layout.multiview import (
    MultiviewLayout,
    add_assembly_bom_and_balloons,
    add_standard_multiview,
)


class BuildSheet:
    """Context manager for constructing drawing sheets in build123d Builder mode."""

    def __init__(
        self,
        sheet_size: SheetSize = SheetSize.A3_LANDSCAPE,
        custom_size: tuple[float, float] | None = None,
        title: str | None = None,
        drawing_number: str | None = None,
        drawing_no: str | None = None,
        drawing_title: str | None = None,
        rev: str | None = None,
        author: str | None = None,
        material: str | None = None,
        finish: str | None = None,
        date: str | None = None,
        left_margin: float = 15.0,
        margin: float = 8.0,
        **kwargs: Any,
    ):
        self.sheet = DrawingSheet(
            sheet_size=sheet_size,
            custom_size=custom_size,
            left_margin=left_margin,
            margin=margin,
        )
        effective_title = title or drawing_title
        effective_dwg_no = drawing_number or drawing_no
        if any(
            v is not None
            for v in [effective_title, effective_dwg_no, rev, author, material, finish, date]
        ):
            self.sheet.set_title_block(
                title=effective_title,
                drawing_no=effective_dwg_no,
                rev=rev,
                author=author,
                material=material,
                finish=finish,
                date=date,
                **kwargs,
            )
        self._token: contextvars.Token[DrawingSheet | None] | None = None

    def __enter__(self) -> BuildSheet:
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

    def __iadd__(self, item: Any) -> BuildSheet:
        self.sheet.add(item)
        return self

    def __add__(self, item: Any) -> BuildSheet:
        self.sheet.add(item)
        return self


# Familiar build123d alias
BuildDrawing = BuildSheet


class BuildView:
    """Helper for accessing the current view context in Builder mode."""

    @classmethod
    def _get_context(cls) -> SheetView | None:
        return CURRENT_VIEW.get()


class View(SheetView):
    """General view class for orthographic, isometric, or section views."""

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
    ) -> View:
        calc_scale = 1.0 if scale == "auto" else float(scale)
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

        target_sheet = sheet or CURRENT_SHEET.get()
        if target_sheet is not None:
            target_sheet.add_view(view)

        return view  # type: ignore[return-value]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Initialized via __new__
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
        )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass


class SectionView(View):
    """2D planar section cut slice with 45-degree cross-hatching."""

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
    ) -> SectionView:
        calc_scale = 1.0 if scale == "auto" else float(scale)
        view = project_section_to_view(
            shape=shape,
            cutting_plane=cutting_plane,
            center_x=center[0],
            center_y=center[1],
            scale=calc_scale,
            view_label=view_label,
            view_label_y=view_label_y,
            model_origin=model_origin,
        )
        view.__class__ = cls
        view.shape = shape

        target_sheet = sheet or CURRENT_SHEET.get()
        if target_sheet is not None:
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
    sheet_no: str | None = None
    sheet_num: int | None = None
    num_sheets: int | None = None
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
            sheet_no=self.sheet_no,
            sheet_num=self.sheet_num,
            num_sheets=self.num_sheets,
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
