"""ISO 5457 drawing sheets, standard sizes, borders, and coordinate grids."""

import os
from enum import Enum
from typing import Any, Self

import build123d as bd

from build123d_techdraw.annotations.callouts import (
    BOMBalloon,
    ChamferCallout,
    HoleCallout,
    LeaderNote,
    SectionCuttingLine,
    ThreadCallout,
    WeldingSymbol,
    WeldType,
)
from build123d_techdraw.annotations.centerlines import (
    BoltCircle,
    Centerline,
    CenterMark,
)
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
from build123d_techdraw.annotations.surface import (
    MaterialRemoval,
    SurfaceFinishSymbol,
)
from build123d_techdraw.annotations.tables import RawSVG, Table
from build123d_techdraw.context import CURRENT_SHEET
from build123d_techdraw.core.title_block import (
    BOMItem,
    RevisionEntry,
    TitleBlockInfo,
    render_bom_table_svg,
    render_revision_table_svg,
    render_title_block_svg,
)
from build123d_techdraw.core.view import (
    SheetView,
    ViewType,
    _apply_align_to,
    project_auxiliary_view,
    project_detail_to_view,
    project_section_to_view,
    project_shape_to_view,
)
from build123d_techdraw.layout.linter import LintIssue, run_sheet_lint
from build123d_techdraw.renderers.theme import Theme, generate_theme_css


class SheetSize(Enum):
    """Standard ISO 216 and ANSI/US paper sheet sizes in millimeters (width, height)."""

    A0_LANDSCAPE = (1189.0, 841.0)
    A0_PORTRAIT = (841.0, 1189.0)
    A0 = (1189.0, 841.0)
    A1_LANDSCAPE = (841.0, 594.0)
    A1_PORTRAIT = (594.0, 841.0)
    A1 = (841.0, 594.0)
    A2_LANDSCAPE = (594.0, 420.0)
    A2_PORTRAIT = (420.0, 594.0)
    A2 = (594.0, 420.0)
    A3_LANDSCAPE = (420.0, 297.0)
    A3_PORTRAIT = (297.0, 420.0)
    A3 = (420.0, 297.0)
    A4_LANDSCAPE = (297.0, 210.0)
    A4_PORTRAIT = (210.0, 297.0)
    A4 = (297.0, 210.0)
    LETTER_LANDSCAPE = (279.4, 215.9)
    LETTER_PORTRAIT = (215.9, 279.4)
    LETTER = (279.4, 215.9)
    ANSI_A = (279.4, 215.9)
    LEGAL_LANDSCAPE = (355.6, 215.9)
    LEGAL_PORTRAIT = (215.9, 355.6)
    LEGAL = (355.6, 215.9)
    TABLOID_LANDSCAPE = (431.8, 279.4)
    TABLOID_PORTRAIT = (279.4, 431.8)
    TABLOID = (431.8, 279.4)
    ANSI_B = (431.8, 279.4)
    ANSI_C = (558.8, 431.8)
    ANSI_D = (863.6, 558.8)
    ANSI_E = (1117.6, 863.6)

    @property
    def width(self) -> float:
        return self.value[0]

    @property
    def height(self) -> float:
        return self.value[1]

    @classmethod
    def coerce(cls, value: "SheetSize | str") -> "SheetSize":
        """Coerce a SheetSize enum or case-insensitive string into a SheetSize."""
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            key = value.strip().upper().replace("-", "_").replace(" ", "_")
            if key.startswith("US_"):
                key = key[3:]
            if key in cls.__members__:
                return cls.__members__[key]
            if f"{key}_LANDSCAPE" in cls.__members__:
                return cls.__members__[f"{key}_LANDSCAPE"]
            raise ValueError(
                f"Unknown sheet_size '{value}'. Expected SheetSize or string like 'A3', 'A4_LANDSCAPE', 'LETTER'."
            )
        raise TypeError(f"Expected SheetSize or str, got {type(value).__name__}")


class DrawingSheet:
    """Represents a single ISO 5457 technical drawing sheet with views and annotations."""

    def __init__(
        self,
        sheet_size: SheetSize | str | tuple[float, float] = SheetSize.A3_LANDSCAPE,
        custom_size: tuple[float, float] | None = None,
        title_info: TitleBlockInfo | None = None,
        left_margin: float = 15.0,
        margin: float = 8.0,
    ):
        if (
            custom_size is None
            and isinstance(sheet_size, (tuple, list))
            and not isinstance(sheet_size, SheetSize)
            and len(sheet_size) == 2
        ):
            custom_size = (float(sheet_size[0]), float(sheet_size[1]))
            resolved_size = SheetSize.A3_LANDSCAPE
        else:
            resolved_size = SheetSize.coerce(sheet_size)  # type: ignore[arg-type]

        if custom_size is not None:
            self.width_mm, self.height_mm = float(custom_size[0]), float(custom_size[1])
            self.sheet_size = resolved_size
        else:
            self.width_mm, self.height_mm = resolved_size.value
            self.sheet_size = resolved_size

        self.left_margin = left_margin
        self.right_margin = margin
        self.top_margin = margin
        self.bottom_margin = margin

        self.title_info: TitleBlockInfo = title_info or TitleBlockInfo(
            drawing_title="COMPONENT DRAWING",
            drawing_no="DWG-001",
        )

        self.views: list[SheetView] = []
        self.bom_items: list[BOMItem] = []
        self.bom_position: str = "above_title_block"
        self.revision_rows: list[RevisionEntry] = [
            RevisionEntry(
                rev="A",
                description="INITIAL ENGINEERING RELEASE",
                date=self.title_info.date,
                approved_by="APPROVED",
            )
        ]
        self.notes: list[str] = [
            "1. ALL DIMENSIONS IN MILLIMETERS (mm) UNLESS NOTED.",
            "2. GENERAL TOLERANCES ACCORDING TO ISO 2768-mK.",
            "3. DEBURR AND BREAK ALL SHARP EDGES 0.2mm x 45°.",
            "4. 100% CAD MODEL CONFORMANCE; DO NOT SCALE DRAWING.",
        ]

        self.custom_svg_elements: list[str] = []
        self.annotations: list[Any] = []
        self._ctx_token: Any = None

    def __enter__(self) -> Self:
        self._ctx_token = CURRENT_SHEET.set(self)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        if self._ctx_token is not None:
            CURRENT_SHEET.reset(self._ctx_token)
            self._ctx_token = None

    def __iadd__(self, item: Any) -> Self:
        return self.add(item)

    def __add__(self, item: Any) -> Self:
        return self.add(item)

    def add(self, item: Any) -> Self:
        """Add a view, layout, annotation, title block, or notes to the drawing sheet."""
        if isinstance(item, (list, tuple, set)):
            for sub_item in item:
                self.add(sub_item)
            return self

        if hasattr(item, "_apply_to_sheet"):
            item._apply_to_sheet(self)
            return self

        if hasattr(item, "views") and isinstance(item.views, dict):
            for v in item.views.values():
                self.add_view(v)
            return self

        if isinstance(item, SheetView):
            self.add_view(item)
            return self

        if isinstance(item, TitleBlockInfo):
            self.title_info = item
            return self

        if item not in self.annotations:
            self.annotations.append(item)
        if hasattr(item, "sheet"):
            item.sheet = self
        return self

    add_annotation = add

    def set_title_block(
        self,
        title: str | None = None,
        drawing_number: str | None = None,
        drawing_title: str | None = None,
        drawing_no: str | None = None,
        subtitle: str | None = None,
        sheet_no: str | None = None,
        sheet_num: int | None = None,
        num_sheets: int | None = None,
        total_sheets: str | int | None = None,
        scale: str | None = None,
        rev: str | None = None,
        date: str | None = None,
        material: str | None = None,
        finish: str | None = None,
        author: str | None = None,
        project: str | None = None,
        status: str | None = None,
        units: str | None = None,
        third_angle: bool | None = None,
        fields: dict[str, str] | None = None,
    ) -> None:
        """Update metadata fields in the ISO 7200 title block."""
        if title is not None:
            self.title_info.drawing_title = title
        if drawing_title is not None:
            self.title_info.drawing_title = drawing_title
        if subtitle is not None:
            self.title_info.subtitle = subtitle
        if drawing_number is not None:
            self.title_info.drawing_no = drawing_number
        if drawing_no is not None:
            self.title_info.drawing_no = drawing_no
        if total_sheets is not None:
            self.title_info.total_sheets = str(total_sheets)
        if sheet_no is not None:
            self.title_info.sheet_no = sheet_no
        elif sheet_num is not None:
            total = num_sheets if num_sheets is not None else sheet_num
            self.title_info.sheet_no = f"{sheet_num} OF {total}"
        if scale is not None:
            self.title_info.scale = scale
        if rev is not None:
            self.title_info.rev = rev
        if date is not None:
            self.title_info.date = date
        if material is not None:
            self.title_info.material = material
        if finish is not None:
            self.title_info.finish = finish
        if author is not None:
            self.title_info.author = author
        if project is not None:
            self.title_info.project = project
        if status is not None:
            self.title_info.status = status
        if units is not None:
            self.title_info.units = units
        if third_angle is not None:
            self.title_info.third_angle = third_angle
        if fields is not None:
            self.title_info.apply_custom_fields(fields)

    def set_notes(self, notes: list[str]) -> None:
        """Set drawing general notes block."""
        self.notes = list(notes)

    def add_bom_item(
        self, item_no: int, qty: int, part_no: str, description: str, material: str
    ) -> None:
        """Add a row to the Bill of Materials table."""
        self.bom_items.append(
            BOMItem(
                item_no=item_no,
                qty=qty,
                part_no=part_no,
                description=description,
                material=material,
            )
        )

    def add_revision(self, rev: str, description: str, date: str, approved_by: str) -> None:
        """Add a row to the Revision History table."""
        self.revision_rows.append(
            RevisionEntry(
                rev=rev,
                description=description,
                date=date,
                approved_by=approved_by,
            )
        )

    def add_view(
        self,
        shape_or_view: SheetView | bd.Solid | bd.Compound | Any,
        view_type: ViewType = ViewType.FRONT,
        center: tuple[float, float] = (150.0, 150.0),
        scale: float = 1.0,
        show_hidden: bool = True,
        view_label: str | None = None,
        rotation_angle_deg: float = 0.0,
        view_label_y: float | None = None,
        model_origin: tuple[float, float] | None = None,
        align_to: Any = None,
    ) -> SheetView:
        """Add a projected orthographic/isometric view to the sheet."""
        if isinstance(shape_or_view, SheetView):
            view = shape_or_view
        else:
            view = project_shape_to_view(
                shape=shape_or_view,
                view_type=view_type,
                center_x=center[0],
                center_y=center[1],
                scale=scale,
                show_hidden=show_hidden,
                view_label=view_label,
                rotation_angle_deg=rotation_angle_deg,
                view_label_y=view_label_y,
                model_origin=model_origin,
            )
        if align_to is not None:
            _apply_align_to(view, align_to)
        view.sheet = self
        if view not in self.views:
            self.views.append(view)
        for ann in getattr(view, "annotations", []):
            if ann not in self.annotations:
                self.annotations.append(ann)
        return view

    def add_section_view(
        self,
        shape: bd.Solid | bd.Compound | Any,
        cutting_plane: bd.Plane,
        center: tuple[float, float] = (150.0, 150.0),
        scale: float = 1.0,
        view_label: str = "SECTION A-A",
        view_label_y: float | None = None,
        model_origin: tuple[float, float] | None = None,
        align_to: Any = None,
        show_background: bool = True,
        hatch: str = "steel",
        hatch_pitch: float = 4.0,
        hatch_angle: float = 45.0,
    ) -> SheetView:
        """Add a 2D section slice view with material hatching to the sheet."""
        view = project_section_to_view(
            shape=shape,
            cutting_plane=cutting_plane,
            center_x=center[0],
            center_y=center[1],
            scale=scale,
            view_label=view_label,
            view_label_y=view_label_y,
            model_origin=model_origin,
            show_background=show_background,
            hatch=hatch,
            hatch_pitch=hatch_pitch,
            hatch_angle=hatch_angle,
        )
        if align_to is not None:
            _apply_align_to(view, align_to)
        view.sheet = self
        if view not in self.views:
            self.views.append(view)
        for ann in getattr(view, "annotations", []):
            if ann not in self.annotations:
                self.annotations.append(ann)
        return view

    def add_detail_view(
        self,
        parent_view: SheetView,
        focus_target: Any = None,
        radius: float = 12.0,
        scale: float = 2.0,
        center: tuple[float, float] = (150.0, 150.0),
        view_label: str | None = None,
        label_letter: str = "A",
        view_label_y: float | None = None,
        align_to: Any = None,
        shape: str = "circle",
        region_center: Any = None,
        label: str | None = None,
    ) -> SheetView:
        """Add a magnified circular or rectangular detail view to the sheet."""
        view = project_detail_to_view(
            parent_view=parent_view,
            focus_target=focus_target,
            radius=radius,
            scale=scale,
            center_x=center[0],
            center_y=center[1],
            view_label=view_label,
            label_letter=label_letter,
            view_label_y=view_label_y,
            shape=shape,
            region_center=region_center,
            label=label,
        )
        if align_to is not None:
            _apply_align_to(view, align_to)
        view.sheet = self
        if view not in self.views:
            self.views.append(view)
        return view

    def add_auxiliary_view(
        self,
        shape: bd.Solid | bd.Compound | Any,
        face: Any,
        center: tuple[float, float] = (150.0, 150.0),
        scale: float = 1.0,
        show_hidden: bool = True,
        view_label: str = "AUXILIARY VIEW",
        view_label_y: float | None = None,
        align_to: Any = None,
    ) -> SheetView:
        """Add an auxiliary view normal to an inclined face."""
        view = project_auxiliary_view(
            shape=shape,
            face=face,
            center_x=center[0],
            center_y=center[1],
            scale=scale,
            show_hidden=show_hidden,
            view_label=view_label,
            view_label_y=view_label_y,
        )
        if align_to is not None:
            _apply_align_to(view, align_to)
        view.sheet = self
        if view not in self.views:
            self.views.append(view)
        return view

    def add_dimension(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        offset: float = 8.0,
        dim_type: DimensionType = DimensionType.ALIGNED,
        text: str | None = None,
        tolerance: str | Tolerance | None = None,
        prefix: str = "",
        suffix: str = "",
        nominal: float | None = None,
        view: Any | None = None,
    ) -> LinearDimension:
        """Add a linear dimension directly in sheet paper coordinates."""
        dim = LinearDimension(
            start=start,
            end=end,
            offset=offset,
            dim_type=dim_type,
            text=text,
            tolerance=tolerance,
            prefix=prefix,
            suffix=suffix,
            nominal=nominal,
            view=view,
        )
        if dim not in self.annotations:
            self.annotations.append(dim)
        if view is not None and hasattr(view, "annotations") and dim not in view.annotations:
            view.annotations.append(dim)
        return dim

    def add_radial_dimension(
        self,
        center: tuple[float, float],
        radius: float,
        angle_deg: float = 45.0,
        text: str | None = None,
        tolerance: str | Tolerance | None = None,
    ) -> RadialDimension:
        """Add a radial dimension in sheet paper coordinates."""
        dim = RadialDimension(
            center=center,
            radius=radius,
            angle_deg=angle_deg,
            text=text,
            tolerance=tolerance,
        )
        if dim not in self.annotations:
            self.annotations.append(dim)
        return dim

    def add_diameter_dimension(
        self,
        center: tuple[float, float],
        diameter: float,
        angle_deg: float = 45.0,
        text: str | None = None,
        tolerance: str | Tolerance | None = None,
    ) -> DiameterDimension:
        """Add a diameter dimension in sheet paper coordinates."""
        dim = DiameterDimension(
            center=center,
            diameter=diameter,
            angle_deg=angle_deg,
            text=text,
            tolerance=tolerance,
        )
        if dim not in self.annotations:
            self.annotations.append(dim)
        return dim

    def add_angular_dimension(
        self,
        target1: Any,
        target2: Any,
        radius: float = 20.0,
        text: str | None = None,
        tolerance: str | None = None,
        prefix: str = "",
        suffix: str = "°",
        apex: tuple[float, float] | None = None,
        view: Any | None = None,
    ) -> AngularDimension:
        """Add an angular dimension to the sheet."""
        dim = AngularDimension(
            target1=target1,
            target2=target2,
            radius=radius,
            text=text,
            tolerance=tolerance,
            prefix=prefix,
            suffix=suffix,
            apex=apex,
            view=view,
        )
        if dim not in self.annotations:
            self.annotations.append(dim)
        if view is not None and hasattr(view, "annotations") and dim not in view.annotations:
            view.annotations.append(dim)
        return dim

    def add_ordinate(
        self,
        target: Any,
        datum_coord: float = 0.0,
        direction: str = "horizontal",
        jog: float = 0.0,
        extension_length: float = 12.0,
        prefix: str = "",
        suffix: str = "",
        view: Any | None = None,
    ) -> OrdinateDimension:
        """Add an ordinate dimension point to the sheet."""
        dim = OrdinateDimension(
            target=target,
            datum_coord=datum_coord,
            direction=direction,
            jog=jog,
            extension_length=extension_length,
            prefix=prefix,
            suffix=suffix,
            view=view,
        )
        if dim not in self.annotations:
            self.annotations.append(dim)
        if view is not None and hasattr(view, "annotations") and dim not in view.annotations:
            view.annotations.append(dim)
        return dim

    def add_chamfer_callout(
        self,
        target: Any,
        width: float | None = None,
        angle_deg: float = 45.0,
        style: str = "iso",
        quantity: int | None = None,
        elbow_offset: tuple[float, float] = (15.0, -12.0),
        view: Any | None = None,
    ) -> ChamferCallout:
        """Add an ISO/ASME chamfer callout note to the sheet."""
        cc = ChamferCallout(
            target_or_edge=target,
            width=width,
            angle_deg=angle_deg,
            style=style,
            quantity=quantity,
            elbow_offset=elbow_offset,
            view=view,
        )
        if cc not in self.annotations:
            self.annotations.append(cc)
        if view is not None and hasattr(view, "annotations") and cc not in view.annotations:
            view.annotations.append(cc)
        return cc

    def add_welding_symbol(
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
        elbow_offset: tuple[float, float] = (16.0, -12.0),
        view: Any | None = None,
    ) -> WeldingSymbol:
        """Add an ISO 2553 welding annotation to the sheet."""
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
            elbow_offset=elbow_offset,
            view=view,
        )
        if ws not in self.annotations:
            self.annotations.append(ws)
        if view is not None and hasattr(view, "annotations") and ws not in view.annotations:
            view.annotations.append(ws)
        return ws

    def lint(self) -> list[LintIssue]:
        """Inspect sheet layout, annotations, and margins for drawing defects."""
        return run_sheet_lint(self)

    def add_gdt_frame(
        self,
        position: tuple[float, float],
        characteristic: GDTCharacteristic,
        tolerance_value: str,
        datum_a: str | None = None,
        datum_b: str | None = None,
        datum_c: str | None = None,
    ) -> FeatureControlFrame:
        """Add a GD&T Feature Control Frame in sheet paper coordinates."""
        fcf = FeatureControlFrame(
            position=position,
            characteristic=characteristic,
            tolerance_value=tolerance_value,
            datum_a=datum_a,
            datum_b=datum_b,
            datum_c=datum_c,
        )
        if fcf not in self.annotations:
            self.annotations.append(fcf)
        return fcf

    def add_datum_symbol(
        self,
        target: tuple[float, float],
        label: str = "A",
        direction: str = "up",
    ) -> DatumFeatureSymbol:
        """Add an ISO datum feature identifier symbol."""
        sym = DatumFeatureSymbol(target=target, label=label, direction=direction)
        if sym not in self.annotations:
            self.annotations.append(sym)
        return sym

    def add_surface_finish(
        self,
        position: tuple[float, float],
        ra_value: str = "Ra 3.2",
        material_removal: MaterialRemoval = MaterialRemoval.REQUIRED,
        process: str | None = None,
    ) -> SurfaceFinishSymbol:
        """Add an ISO 1302 surface roughness / finish symbol."""
        sym = SurfaceFinishSymbol(
            position=position,
            ra_value=ra_value,
            material_removal=material_removal,
            process=process,
        )
        if sym not in self.annotations:
            self.annotations.append(sym)
        return sym

    def add_centerline(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        stroke_width: float = 0.25,
    ) -> Centerline:
        """Add an ISO dash-dot centerline between two sheet points."""
        cl = Centerline(start=start, end=end, stroke_width=stroke_width)
        if cl not in self.annotations:
            self.annotations.append(cl)
        return cl

    def add_centermark(
        self,
        center: tuple[float, float],
        size: float = 6.0,
    ) -> CenterMark:
        """Add an ISO center crosshair."""
        cm = CenterMark(center=center, size=size)
        if cm not in self.annotations:
            self.annotations.append(cm)
        return cm

    def add_bolt_circle(
        self,
        center: tuple[float, float],
        diameter: float,
        num_holes: int = 4,
        start_angle_deg: float = 0.0,
    ) -> BoltCircle:
        """Add a bolt pitch circle diameter (PCD) annotation."""
        bc = BoltCircle(
            center=center,
            diameter=diameter,
            num_holes=num_holes,
            start_angle_deg=start_angle_deg,
        )
        if bc not in self.annotations:
            self.annotations.append(bc)
        return bc

    def add_leader_note(
        self,
        target: tuple[float, float],
        lines: list[str] | str,
        elbow_offset: tuple[float, float] = (15.0, -12.0),
        shelf_length: float = 20.0,
        align_right: bool | None = None,
        terminator: str = "dot",
    ) -> LeaderNote:
        """Add a leader note with pointer dot/arrow, elbow, and horizontal text shelf."""
        line_list = [lines] if isinstance(lines, str) else list(lines)
        note = LeaderNote(
            target=target,
            lines=line_list,
            elbow_offset=elbow_offset,
            shelf_length=shelf_length,
            align_right=align_right,
            terminator=terminator,
        )
        if note not in self.annotations:
            self.annotations.append(note)
        return note

    add_leader = add_leader_note

    def add_hole_callout(
        self,
        center: tuple[float, float],
        diameter: float,
        depth: float | str | None = None,
        countersink_dia: float | None = None,
        countersink_angle: float = 90.0,
        counterbore_dia: float | None = None,
        counterbore_depth: float | None = None,
        quantity: int | None = None,
        tolerance: str | None = None,
        elbow_offset: tuple[float, float] = (15.0, -12.0),
    ) -> HoleCallout:
        """Add a standard engineering hole callout in sheet paper coordinates."""
        hc = HoleCallout(
            center=center,
            diameter=diameter,
            depth=depth,
            countersink_dia=countersink_dia,
            countersink_angle=countersink_angle,
            counterbore_dia=counterbore_dia,
            counterbore_depth=counterbore_depth,
            quantity=quantity,
            tolerance=tolerance,
            elbow_offset=elbow_offset,
        )
        if hc not in self.annotations:
            self.annotations.append(hc)
        return hc

    def add_thread_callout(
        self,
        center: tuple[float, float],
        thread: str = "M5x0.8",
        depth: float | str | None = "THRU",
        class_fit: str | None = None,
        quantity: int | None = None,
        elbow_offset: tuple[float, float] = (15.0, -12.0),
    ) -> ThreadCallout:
        """Add a standard engineering thread callout in sheet paper coordinates."""
        tc = ThreadCallout(
            center=center,
            thread=thread,
            depth=depth,
            class_fit=class_fit,
            quantity=quantity,
            elbow_offset=elbow_offset,
        )
        if tc not in self.annotations:
            self.annotations.append(tc)
        return tc

    def add_table(
        self,
        headers: list[str] | None = None,
        rows: list[list[Any]] | None = None,
        position: tuple[float, float] = (0.0, 0.0),
        col_widths: list[float] | None = None,
        title: str | None = None,
        **kwargs: Any,
    ) -> Table:
        """Add a tabular schedule to the sheet."""
        tbl = Table(
            headers=headers,
            rows=rows,
            position=position,
            col_widths=col_widths,
            title=title,
            sheet=self,
            **kwargs,
        )
        if tbl not in self.annotations:
            self.annotations.append(tbl)
        return tbl

    def add_raw_svg(
        self,
        content: str,
        position: tuple[float, float] = (0.0, 0.0),
        **kwargs: Any,
    ) -> RawSVG:
        """Add a raw SVG vector element to the sheet."""
        raw = RawSVG(content=content, position=position, sheet=self, **kwargs)
        if raw not in self.annotations:
            self.annotations.append(raw)
        return raw

    def add_balloon(
        self,
        item_no: int,
        target: tuple[float, float],
        balloon_center: tuple[float, float],
        radius: float = 4.5,
        terminator: str = "dot",
    ) -> BOMBalloon:
        """Add an ISO BOM balloon callout circle connected to component via leader."""
        balloon = BOMBalloon(
            item_no=item_no,
            target=target,
            balloon_center=balloon_center,
            radius=radius,
            terminator=terminator,
        )
        if balloon not in self.annotations:
            self.annotations.append(balloon)
        return balloon

    def add_section_cutting_line(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        label: str = "A",
        arrow_dir: str = "up",
    ) -> SectionCuttingLine:
        """Add an ISO section cutting plane line."""
        cut = SectionCuttingLine(
            start=start,
            end=end,
            label=label,
            arrow_dir=arrow_dir,
        )
        if cut not in self.annotations:
            self.annotations.append(cut)
        return cut

    add_section_cut = add_section_cutting_line

    def render_border_and_grid(self) -> str:
        """Render ISO 5457 borders, centering marks, and alphanumeric grid zones."""
        w = self.width_mm
        h = self.height_mm
        bx = self.left_margin
        by = self.top_margin
        bw = w - self.left_margin - self.right_margin
        bh = h - self.top_margin - self.bottom_margin

        num_cols = 8 if w > 350 else 6
        num_rows = 6 if h > 250 else 4
        col_w = bw / num_cols
        row_h = bh / num_rows

        grid_lines: list[str] = []
        labels: list[str] = []

        # Vertical grid ticks and column numbers
        for i in range(num_cols + 1):
            gx = bx + i * col_w
            grid_lines.append(
                f'<line x1="{gx:.2f}" y1="{by - 3.0:.2f}" x2="{gx:.2f}" y2="{by:.2f}" class="border-sub"/>'
            )
            grid_lines.append(
                f'<line x1="{gx:.2f}" y1="{by + bh:.2f}" x2="{gx:.2f}" y2="{by + bh + 3.0:.2f}" class="border-sub"/>'
            )
            if i < num_cols:
                zone_num = str(num_cols - i)
                labels.append(
                    f'<text x="{gx + col_w / 2:.2f}" y="{by - 1.0:.2f}" class="zone-text">{zone_num}</text>'
                )
                labels.append(
                    f'<text x="{gx + col_w / 2:.2f}" y="{by + bh + 4.5:.2f}" class="zone-text">{zone_num}</text>'
                )

        # Horizontal grid ticks and row letters
        row_letters = ["A", "B", "C", "D", "E", "F", "G", "H"]
        for j in range(num_rows + 1):
            gy = by + j * row_h
            grid_lines.append(
                f'<line x1="{bx - 3.0:.2f}" y1="{gy:.2f}" x2="{bx:.2f}" y2="{gy:.2f}" class="border-sub"/>'
            )
            grid_lines.append(
                f'<line x1="{bx + bw:.2f}" y1="{gy:.2f}" x2="{bx + bw + 3.0:.2f}" y2="{gy:.2f}" class="border-sub"/>'
            )
            if j < num_rows:
                zone_let = row_letters[j % len(row_letters)]
                labels.append(
                    f'<text x="{bx - 1.8:.2f}" y="{gy + row_h / 2 + 1.2:.2f}" class="zone-text">{zone_let}</text>'
                )
                labels.append(
                    f'<text x="{bx + bw + 1.8:.2f}" y="{gy + row_h / 2 + 1.2:.2f}" class="zone-text">{zone_let}</text>'
                )

        # Centering marks (ISO 5457 section 4.3)
        mid_x = bx + bw / 2.0
        mid_y = by + bh / 2.0
        cm_len = 5.0
        centering_marks = f"""
        <line x1="{mid_x:.2f}" y1="0" x2="{mid_x:.2f}" y2="{cm_len}" class="border-main" stroke-width="0.7"/>
        <line x1="{mid_x:.2f}" y1="{h - cm_len:.2f}" x2="{mid_x:.2f}" y2="{h:.2f}" class="border-main" stroke-width="0.7"/>
        <line x1="0" y1="{mid_y:.2f}" x2="{cm_len}" y2="{mid_y:.2f}" class="border-main" stroke-width="0.7"/>
        <line x1="{w - cm_len:.2f}" y1="{mid_y:.2f}" x2="{w:.2f}" y2="{mid_y:.2f}" class="border-main" stroke-width="0.7"/>
        """

        return f"""
        <rect x="0" y="0" width="{w}" height="{h}" class="sheet-bg"/>
        <rect x="0" y="0" width="{w}" height="{h}" class="blueprint-grid" fill="url(#techGrid)"/>
        <rect x="2" y="2" width="{w - 4}" height="{h - 4}" class="border-sub" stroke-width="0.25" fill="none"/>
        <rect x="{bx:.2f}" y="{by:.2f}" width="{bw:.2f}" height="{bh:.2f}" class="border-main" stroke-width="0.7" fill="none"/>
        {centering_marks}
        <g class="grid-ticks">{"".join(grid_lines)}</g>
        <g class="zone-labels">{"".join(labels)}</g>
        """

    def render_notes(self) -> str:
        """Render general notes block in bottom-left."""
        if not self.notes:
            return ""
        bx = self.left_margin + 5.0
        by = self.height_mm - self.bottom_margin - 30.0

        notes_svg: list[str] = [
            '<text x="0" y="0" class="tb-field-label" font-weight="bold">GENERAL NOTES:</text>'
        ]
        for i, note in enumerate(self.notes):
            notes_svg.append(
                f'<text x="0" y="{4.5 + i * 4.2:.2f}" class="notes-text">{note}</text>'
            )

        return f"""
        <g class="sheet-notes" transform="translate({bx:.2f}, {by:.2f})">
            {"".join(notes_svg)}
        </g>
        """

    def render_view_labels(self) -> str:
        """Render view titles and scale notes positioned cleanly below annotations."""
        out: list[str] = []
        for v in self.views:
            if v.view_label and v.scale_label:
                vx = v.center_x
                # Position label dynamically below any bottom annotations with clearance
                vy = max(
                    v.bottom_annotation_y + 8.0,
                    v.view_label_pos[1] if v.view_label_pos else v.max_y + 8.0,
                )
                line_w = max(len(v.view_label) * 2.8, 20.0)
                out.append(f"""
                <text x="{vx:.2f}" y="{vy:.2f}" class="view-title" text-anchor="middle" font-size="4.2" font-weight="bold">{v.view_label}</text>
                <text x="{vx:.2f}" y="{vy + 3.8:.2f}" class="view-scale" text-anchor="middle" font-size="2.8">{v.scale_label}</text>
                <line x1="{vx - line_w / 2:.2f}" y1="{vy + 1.0:.2f}" x2="{vx + line_w / 2:.2f}" y2="{vy + 1.0:.2f}" class="border-sub" stroke-width="0.3"/>
                """)
        return f'<g class="view-labels">{"".join(out)}</g>'

    def render_hatch_defs(self) -> str:
        """Render standard ISO 128 / ANSI hatch patterns in SVG defs."""
        patterns = [
            """<pattern id="sectionHatch" width="4" height="4" patternTransform="rotate(45 0 0)" patternUnits="userSpaceOnUse">
      <line x1="0" y1="0" x2="0" y2="4" stroke="var(--hatch-color)" stroke-width="0.35"/>
    </pattern>""",
            """<pattern id="hatch_steel" width="4" height="4" patternTransform="rotate(45 0 0)" patternUnits="userSpaceOnUse">
      <line x1="0" y1="0" x2="0" y2="4" stroke="var(--hatch-color)" stroke-width="0.35"/>
    </pattern>""",
            """<pattern id="hatch_aluminum" width="4" height="4" patternTransform="rotate(45 0 0)" patternUnits="userSpaceOnUse">
      <line x1="0" y1="0" x2="0" y2="4" stroke="var(--hatch-color)" stroke-width="0.35"/>
      <line x1="1.2" y1="0" x2="1.2" y2="4" stroke="var(--hatch-color)" stroke-width="0.35"/>
    </pattern>""",
            """<pattern id="hatch_brass" width="4" height="4" patternTransform="rotate(45 0 0)" patternUnits="userSpaceOnUse">
      <line x1="0" y1="0" x2="0" y2="4" stroke="var(--hatch-color)" stroke-width="0.35"/>
      <line x1="2" y1="0" x2="2" y2="4" stroke="var(--hatch-color)" stroke-width="0.35" stroke-dasharray="1.5,1.5"/>
    </pattern>""",
            """<pattern id="hatch_plastic" width="4" height="4" patternTransform="rotate(60 0 0)" patternUnits="userSpaceOnUse">
      <line x1="0" y1="0" x2="0" y2="4" stroke="var(--hatch-color)" stroke-width="0.35"/>
      <line x1="0" y1="0" x2="4" y2="0" stroke="var(--hatch-color)" stroke-width="0.35"/>
    </pattern>""",
            """<pattern id="hatch_rubber" width="4" height="4" patternUnits="userSpaceOnUse">
      <circle cx="2" cy="2" r="0.45" fill="var(--hatch-color)"/>
      <circle cx="0" cy="0" r="0.45" fill="var(--hatch-color)"/>
      <circle cx="4" cy="0" r="0.45" fill="var(--hatch-color)"/>
      <circle cx="0" cy="4" r="0.45" fill="var(--hatch-color)"/>
      <circle cx="4" cy="4" r="0.45" fill="var(--hatch-color)"/>
    </pattern>""",
        ]
        for v in self.views:
            h_id = getattr(v, "hatch_id", None)
            if h_id and not any(f'id="{h_id}"' in p for p in patterns):
                mat = getattr(v, "hatch", "steel")
                pitch = getattr(v, "hatch_pitch", 4.0)
                angle = getattr(v, "hatch_angle", 45.0)
                if mat in ("aluminum", "ansi38"):
                    p_svg = f"""<pattern id="{h_id}" width="{pitch:.2f}" height="{pitch:.2f}" patternTransform="rotate({angle:.1f} 0 0)" patternUnits="userSpaceOnUse">
      <line x1="0" y1="0" x2="0" y2="{pitch:.2f}" stroke="var(--hatch-color)" stroke-width="0.35"/>
      <line x1="{pitch * 0.3:.2f}" y1="0" x2="{pitch * 0.3:.2f}" y2="{pitch:.2f}" stroke="var(--hatch-color)" stroke-width="0.35"/>
    </pattern>"""
                elif mat in ("brass", "ansi32"):
                    p_svg = f"""<pattern id="{h_id}" width="{pitch:.2f}" height="{pitch:.2f}" patternTransform="rotate({angle:.1f} 0 0)" patternUnits="userSpaceOnUse">
      <line x1="0" y1="0" x2="0" y2="{pitch:.2f}" stroke="var(--hatch-color)" stroke-width="0.35"/>
      <line x1="{pitch * 0.5:.2f}" y1="0" x2="{pitch * 0.5:.2f}" y2="{pitch:.2f}" stroke="var(--hatch-color)" stroke-width="0.35" stroke-dasharray="1.5,1.5"/>
    </pattern>"""
                elif mat in ("plastic", "rubber"):
                    p_svg = f"""<pattern id="{h_id}" width="{pitch:.2f}" height="{pitch:.2f}" patternTransform="rotate({angle:.1f} 0 0)" patternUnits="userSpaceOnUse">
      <line x1="0" y1="0" x2="0" y2="{pitch:.2f}" stroke="var(--hatch-color)" stroke-width="0.35"/>
      <line x1="0" y1="0" x2="{pitch:.2f}" y2="0" stroke="var(--hatch-color)" stroke-width="0.35"/>
    </pattern>"""
                else:
                    p_svg = f"""<pattern id="{h_id}" width="{pitch:.2f}" height="{pitch:.2f}" patternTransform="rotate({angle:.1f} 0 0)" patternUnits="userSpaceOnUse">
      <line x1="0" y1="0" x2="0" y2="{pitch:.2f}" stroke="var(--hatch-color)" stroke-width="0.35"/>
    </pattern>"""
                patterns.append(p_svg)
        return "\n    ".join(patterns)

    def to_svg(self, theme: Theme | str = Theme.DARK_BLUEPRINT) -> str:
        """Render the complete drawing sheet to an SVG vector string."""
        if isinstance(theme, str):
            theme = (
                Theme.ISO_WHITE
                if theme.lower() in ("print", "white", "iso_white")
                else Theme.DARK_BLUEPRINT
            )
        w, h = self.width_mm, self.height_mm
        theme_class = "theme-iso-white" if theme == Theme.ISO_WHITE else "theme-dark-blueprint"

        view_elements = "".join([v.svg_content for v in self.views])

        # Annotation SVG rendering (dimensions, leaders, GD&T, tables, raw SVG, etc.)
        annotation_svgs = "".join([a.to_svg() for a in self.annotations if hasattr(a, "to_svg")])

        # Position BOM table (bottom-up above title block or top-right below revisions)
        if getattr(self, "bom_position", "above_title_block") == "top_right":
            rev_h = 10.0 if self.revision_rows else 0.0
            bom_top_y = self.top_margin + rev_h + 2.0
        else:
            extra_fields = self.title_info.extra_fields
            extra_rows = (
                (len(extra_fields) + min(3, len(extra_fields)) - 1) // min(3, len(extra_fields))
                if extra_fields
                else 0
            )
            tb_y = h - self.bottom_margin - 36.0 - extra_rows * 7.0
            bom_h = 5.5 + len(self.bom_items) * 5.2
            bom_top_y = tb_y - bom_h - 1.0

        return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg"
     viewBox="0 0 {w} {h}"
     width="{w}mm"
     height="{h}mm"
     class="{theme_class}"
     id="sheet_{self.title_info.drawing_no}">
  <defs>
    <style type="text/css">
      {generate_theme_css(theme)}
    </style>
    <pattern id="techGrid" width="10" height="10" patternUnits="userSpaceOnUse">
      <path d="M 10 0 L 0 0 0 10" fill="none" stroke="var(--grid-color)" stroke-width="0.15"/>
      <circle cx="0" cy="0" r="0.3" fill="var(--grid-color)"/>
    </pattern>
    {self.render_hatch_defs()}
  </defs>

  {self.render_border_and_grid()}
  {render_title_block_svg(self.title_info, w, h, self.right_margin, self.bottom_margin)}
  {render_revision_table_svg(self.revision_rows, w, self.right_margin, self.top_margin)}
  {render_bom_table_svg(self.bom_items, w, self.right_margin, bom_top_y)}
  {self.render_notes()}

  <g class="drawing-views">
    {view_elements}
  </g>

  {self.render_view_labels()}

  <g class="annotations">
    {annotation_svgs}
  </g>
</svg>
"""

    def export_svg(self, path: str, theme: Theme | str = Theme.DARK_BLUEPRINT) -> None:
        """Export sheet to an SVG file."""
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        svg = self.to_svg(theme=theme)
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)

    def export_pdf(self, path: str, theme: Theme | str = Theme.DARK_BLUEPRINT) -> None:
        """Export sheet to a vector PDF file."""
        from build123d_techdraw.renderers.pdf import PDFRenderer

        resolved_theme = (
            Theme.ISO_WHITE
            if (isinstance(theme, str) and theme.lower() in ("print", "white", "iso_white"))
            or theme == Theme.ISO_WHITE
            else Theme.DARK_BLUEPRINT
        )
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        PDFRenderer.render_sheets_to_pdf(
            [(self.width_mm, self.height_mm, self.to_svg(resolved_theme))], path, resolved_theme
        )

    def export_dxf(self, path: str, flat_pattern: bool = False, dxfversion: str = "R2010") -> None:
        """Export sheet to a multi-layer DXF CAD drawing. If flat_pattern=True, exports 1:1 unannotated CNC geometry."""
        from build123d_techdraw.renderers.dxf import DXFRenderer

        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        if flat_pattern:
            unique_shapes: list[Any] = []
            for v in self.views:
                if hasattr(v, "shape") and v.shape is not None and v.shape not in unique_shapes:
                    unique_shapes.append(v.shape)

            if unique_shapes:
                shape_to_export = (
                    unique_shapes[0]
                    if len(unique_shapes) == 1
                    else bd.Compound(children=unique_shapes)
                )
                DXFRenderer.export_shape_to_dxf(shape_to_export, path, dxfversion=dxfversion)
            else:
                import ezdxf

                doc = ezdxf.new(dxfversion=dxfversion)  # type: ignore[attr-defined]
                doc.saveas(path)
        else:
            DXFRenderer.export_sheet_to_dxf(self, path, dxfversion=dxfversion)
