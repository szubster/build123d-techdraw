"""ISO 5457 drawing sheets, standard sizes, borders, and coordinate grids."""

import os
from enum import Enum
from typing import Any

import build123d as bd

from build123d_techdraw.annotations.callouts import (
    BOMBalloon,
    LeaderNote,
    SectionCuttingLine,
)
from build123d_techdraw.annotations.centerlines import (
    BoltCircle,
    Centerline,
    CenterMark,
)
from build123d_techdraw.annotations.dimensions import (
    DiameterDimension,
    DimensionType,
    LinearDimension,
    RadialDimension,
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
    project_section_to_view,
    project_shape_to_view,
)
from build123d_techdraw.renderers.theme import Theme, generate_theme_css


class SheetSize(Enum):
    """Standard ISO 216 paper sheet sizes in millimeters (width, height)."""

    A0_LANDSCAPE = (1189.0, 841.0)
    A0_PORTRAIT = (841.0, 1189.0)
    A1_LANDSCAPE = (841.0, 594.0)
    A1_PORTRAIT = (594.0, 841.0)
    A2_LANDSCAPE = (594.0, 420.0)
    A2_PORTRAIT = (420.0, 594.0)
    A3_LANDSCAPE = (420.0, 297.0)
    A3_PORTRAIT = (297.0, 420.0)
    A4_LANDSCAPE = (297.0, 210.0)
    A4_PORTRAIT = (210.0, 297.0)

    @property
    def width(self) -> float:
        return self.value[0]

    @property
    def height(self) -> float:
        return self.value[1]


class DrawingSheet:
    """Represents a single ISO 5457 technical drawing sheet with views and annotations."""

    def __init__(
        self,
        sheet_size: SheetSize = SheetSize.A3_LANDSCAPE,
        custom_size: tuple[float, float] | None = None,
        title_info: TitleBlockInfo | None = None,
        left_margin: float = 15.0,
        margin: float = 8.0,
    ):
        if custom_size is not None:
            self.width_mm, self.height_mm = custom_size
            self.sheet_size = sheet_size
        else:
            self.width_mm, self.height_mm = sheet_size.value
            self.sheet_size = sheet_size

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

    def set_title_block(
        self,
        title: str | None = None,
        drawing_number: str | None = None,
        drawing_title: str | None = None,
        drawing_no: str | None = None,
        sheet_no: str | None = None,
        sheet_num: int | None = None,
        num_sheets: int | None = None,
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
    ) -> None:
        """Update metadata fields in the ISO 7200 title block."""
        if title is not None:
            self.title_info.drawing_title = title
        if drawing_title is not None:
            self.title_info.drawing_title = drawing_title
        if drawing_number is not None:
            self.title_info.drawing_no = drawing_number
        if drawing_no is not None:
            self.title_info.drawing_no = drawing_no
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
        view.sheet = self
        self.views.append(view)
        return view

    def add_section_view(
        self,
        shape: bd.Solid | bd.Compound | Any,
        cutting_plane: bd.Plane,
        center: tuple[float, float],
        scale: float = 1.0,
        view_label: str = "SECTION A-A",
        view_label_y: float | None = None,
        model_origin: tuple[float, float] | None = None,
    ) -> SheetView:
        """Add a 2D section slice view with 45-degree cross-hatching to the sheet."""
        view = project_section_to_view(
            shape=shape,
            cutting_plane=cutting_plane,
            center_x=center[0],
            center_y=center[1],
            scale=scale,
            view_label=view_label,
            view_label_y=view_label_y,
            model_origin=model_origin,
        )
        view.sheet = self
        self.views.append(view)
        return view

    def add_dimension(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        offset: float = 8.0,
        dim_type: DimensionType = DimensionType.ALIGNED,
        text: str | None = None,
        tolerance: str | None = None,
        prefix: str = "",
        suffix: str = "",
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
        )
        self.annotations.append(dim)
        return dim

    def add_radial_dimension(
        self,
        center: tuple[float, float],
        radius: float,
        angle_deg: float = 45.0,
        text: str | None = None,
        tolerance: str | None = None,
    ) -> RadialDimension:
        """Add a radial dimension in sheet paper coordinates."""
        dim = RadialDimension(
            center=center,
            radius=radius,
            angle_deg=angle_deg,
            text=text,
            tolerance=tolerance,
        )
        self.annotations.append(dim)
        return dim

    def add_diameter_dimension(
        self,
        center: tuple[float, float],
        diameter: float,
        angle_deg: float = 45.0,
        text: str | None = None,
        tolerance: str | None = None,
    ) -> DiameterDimension:
        """Add a diameter dimension in sheet paper coordinates."""
        dim = DiameterDimension(
            center=center,
            diameter=diameter,
            angle_deg=angle_deg,
            text=text,
            tolerance=tolerance,
        )
        self.annotations.append(dim)
        return dim

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
        self.annotations.append(cl)
        return cl

    def add_centermark(
        self,
        center: tuple[float, float],
        size: float = 6.0,
    ) -> CenterMark:
        """Add an ISO center crosshair."""
        cm = CenterMark(center=center, size=size)
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
        self.annotations.append(note)
        return note

    add_leader = add_leader_note

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

        # Annotation SVG rendering (dimensions, leaders, GD&T, etc.)
        annotation_svgs = "".join([a.to_svg() for a in self.annotations if hasattr(a, "to_svg")])

        # Position BOM table stacked above title block per ISO drafting standard
        tb_y = h - self.bottom_margin - 36.0
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
    <pattern id="sectionHatch" width="4" height="4" patternTransform="rotate(45 0 0)" patternUnits="userSpaceOnUse">
      <line x1="0" y1="0" x2="0" y2="4" stroke="var(--hatch-color)" stroke-width="0.35"/>
    </pattern>
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

    def export_dxf(self, path: str, dxfversion: str = "R2010") -> None:
        """Export sheet view shape(s) to a 1:1 DXF file."""
        from build123d_techdraw.renderers.dxf import DXFRenderer

        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        unique_shapes: list[Any] = []
        for v in self.views:
            if hasattr(v, "shape") and v.shape is not None and v.shape not in unique_shapes:
                unique_shapes.append(v.shape)

        if unique_shapes:
            shape_to_export = (
                unique_shapes[0] if len(unique_shapes) == 1 else bd.Compound(children=unique_shapes)
            )
            DXFRenderer.export_shape_to_dxf(shape_to_export, path, dxfversion=dxfversion)
        else:
            import ezdxf

            doc = ezdxf.new(dxfversion=dxfversion)  # type: ignore[attr-defined]
            doc.saveas(path)
