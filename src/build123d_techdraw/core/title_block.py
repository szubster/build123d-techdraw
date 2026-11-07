"""ISO 7200 title blocks, BOM tables, and revision tables."""

import html
from dataclasses import dataclass


@dataclass
class TitleBlockInfo:
    """Metadata fields for ISO 7200 compliant title blocks."""

    drawing_title: str
    drawing_no: str
    sheet_no: str = "1 OF 1"
    scale: str = "1:1"
    rev: str = "REV A"
    date: str = "2026-11-06"
    material: str = "SEE NOTES"
    finish: str = "STANDARD"
    author: str = "ENG / CAD"
    project: str = "MECHANICAL PROJECT"
    status: str = "APPROVED"
    units: str = "METRIC (mm)"
    third_angle: bool = True


@dataclass
class BOMItem:
    """Bill of Materials table row item."""

    item_no: int
    qty: int
    part_no: str
    description: str
    material: str


@dataclass
class RevisionEntry:
    """Drawing revision history entry."""

    rev: str
    description: str
    date: str
    approved_by: str


def render_title_block_svg(
    info: TitleBlockInfo,
    sheet_width: float,
    sheet_height: float,
    margin_right: float = 8.0,
    margin_bottom: float = 8.0,
    width: float = 145.0,
    height: float = 36.0,
) -> str:
    """Render an ISO 7200 title block positioned in the bottom-right corner."""
    bx = sheet_width - margin_right - width
    by = sheet_height - margin_bottom - height

    title_str = info.drawing_title
    if len(title_str) > 26 and " - " in title_str:
        parts = title_str.split(" - ", 1)
        title_svg = (
            f'<text x="3" y="15.0" class="tb-title" font-size="2.9">{html.escape(parts[0])}</text>'
            f'<text x="3" y="19.2" class="tb-title" font-size="2.7">- {html.escape(parts[1])}</text>'
        )
    elif len(title_str) > 26 and " & " in title_str:
        parts = title_str.split(" & ", 1)
        title_svg = (
            f'<text x="3" y="15.0" class="tb-title" font-size="2.9">{html.escape(parts[0])} &amp;</text>'
            f'<text x="3" y="19.2" class="tb-title" font-size="2.7">{html.escape(parts[1])}</text>'
        )
    elif len(title_str) > 28:
        words = title_str.split()
        mid = len(words) // 2
        l1 = " ".join(words[:mid])
        l2 = " ".join(words[mid:])
        title_svg = (
            f'<text x="3" y="15.0" class="tb-title" font-size="2.9">{html.escape(l1)}</text>'
            f'<text x="3" y="19.2" class="tb-title" font-size="2.7">{html.escape(l2)}</text>'
        )
    else:
        title_svg = (
            f'<text x="3" y="18.0" class="tb-title" font-size="3.2">{html.escape(title_str)}</text>'
        )

    mat_font = "1.9" if len(info.material) > 18 else "2.1" if len(info.material) > 13 else "2.5"
    fin_font = "1.9" if len(info.finish) > 18 else "2.1" if len(info.finish) > 13 else "2.5"

    if info.third_angle:
        # ISO 5456-2 Third Angle Projection symbol (circles to left of frustum)
        circles = (
            '<circle cx="-8" cy="0" r="4" fill="none" class="line-visible" stroke-width="0.35"/>'
            '<circle cx="-8" cy="0" r="7" fill="none" class="line-visible" stroke-width="0.35"/>'
        )
        cone_poly = '<polygon points="0,-4 8,-7 8,7 0,4" fill="none" class="line-visible" stroke-width="0.35"/>'
        centerlines = (
            '<line x1="-18" y1="0" x2="12" y2="0" class="line-center" stroke-dasharray="8,1.5,1.5,1.5" stroke-width="0.25"/>'
            '<line x1="-8" y1="-9" x2="-8" y2="9" class="line-center" stroke-dasharray="8,1.5,1.5,1.5" stroke-width="0.25"/>'
        )
    else:
        # ISO 5456-2 First Angle Projection symbol (frustum to left of circles)
        cone_poly = '<polygon points="-8,-4 0,-7 0,7 -8,4" fill="none" class="line-visible" stroke-width="0.35"/>'
        circles = (
            '<circle cx="8" cy="0" r="4" fill="none" class="line-visible" stroke-width="0.35"/>'
            '<circle cx="8" cy="0" r="7" fill="none" class="line-visible" stroke-width="0.35"/>'
        )
        centerlines = (
            '<line x1="-12" y1="0" x2="18" y2="0" class="line-center" stroke-dasharray="8,1.5,1.5,1.5" stroke-width="0.25"/>'
            '<line x1="8" y1="-9" x2="8" y2="9" class="line-center" stroke-dasharray="8,1.5,1.5,1.5" stroke-width="0.25"/>'
        )

    return f"""
    <g class="title-block" transform="translate({bx:.2f}, {by:.2f})">
        <rect x="0" y="0" width="{width:.2f}" height="{height:.2f}" class="title-bg" stroke-width="0.6"/>
        <line x1="0" y1="8" x2="{width}" y2="8" class="border-sub" stroke-width="0.3"/>
        <line x1="0" y1="22" x2="{width}" y2="22" class="border-sub" stroke-width="0.3"/>
        <line x1="0" y1="29" x2="{width}" y2="29" class="border-sub" stroke-width="0.25"/>

        <line x1="88" y1="0" x2="88" y2="22" class="border-sub" stroke-width="0.3"/>
        <line x1="116" y1="0" x2="116" y2="8" class="border-sub" stroke-width="0.3"/>
        <line x1="38" y1="22" x2="38" y2="36" class="border-sub" stroke-width="0.3"/>
        <line x1="76" y1="22" x2="76" y2="36" class="border-sub" stroke-width="0.3"/>
        <line x1="110" y1="22" x2="110" y2="36" class="border-sub" stroke-width="0.3"/>

        <text x="3" y="3.2" class="tb-field-label">PROJECT</text>
        <text x="3" y="6.8" class="tb-field-val-sm">{html.escape(info.project)}</text>

        <text x="91" y="3.2" class="tb-field-label">DRAWING NO.</text>
        <text x="91" y="7.0" class="tb-field-val-bold">{html.escape(info.drawing_no)}</text>

        <text x="119" y="3.2" class="tb-field-label">REV</text>
        <text x="127" y="7.0" class="tb-field-val-bold" text-anchor="middle">{html.escape(info.rev)}</text>

        <text x="3" y="11.5" class="tb-field-label">TITLE</text>
        {title_svg}

        <text x="91" y="11.5" class="tb-field-label">PROJECTION</text>
        <g transform="translate(102, 17) scale(0.65)">
            {cone_poly}
            {circles}
            {centerlines}
        </g>

        <text x="3" y="25.2" class="tb-field-label">MATERIAL</text>
        <text x="3" y="28.2" class="tb-field-val-sm" font-size="{mat_font}">{html.escape(info.material)}</text>
        <text x="3" y="32.0" class="tb-field-label">FINISH</text>
        <text x="3" y="35.0" class="tb-field-val-sm" font-size="{fin_font}">{html.escape(info.finish)}</text>

        <text x="41" y="25.2" class="tb-field-label">SCALE</text>
        <text x="41" y="28.2" class="tb-field-val-sm">{html.escape(info.scale)}</text>
        <text x="41" y="32.0" class="tb-field-label">SHEET</text>
        <text x="41" y="35.0" class="tb-field-val-sm">{html.escape(info.sheet_no)}</text>

        <text x="79" y="25.2" class="tb-field-label">DATE</text>
        <text x="79" y="28.2" class="tb-field-val-sm">{html.escape(info.date)}</text>
        <text x="79" y="32.0" class="tb-field-label">DRAWN BY</text>
        <text x="79" y="35.0" class="tb-field-val-sm">{html.escape(info.author)}</text>

        <text x="113" y="25.2" class="tb-field-label">STATUS</text>
        <text x="113" y="28.2" class="tb-field-val-sm" fill="#10B981" font-weight="bold">{html.escape(info.status)}</text>
        <text x="113" y="32.0" class="tb-field-label">UNITS</text>
        <text x="113" y="35.0" class="tb-field-val-sm">{html.escape(info.units)}</text>
    </g>
    """


def render_revision_table_svg(
    revision_rows: list[RevisionEntry],
    sheet_width: float,
    margin_right: float = 8.0,
    margin_top: float = 8.0,
    width: float = 110.0,
    row_height: float = 5.0,
) -> str:
    """Render an engineering revision table at the top-right corner."""
    if not revision_rows:
        return ""

    total_h = 5.0 + len(revision_rows) * row_height
    rx = sheet_width - margin_right - width
    ry = margin_top

    rows_svg = []
    for i, row in enumerate(revision_rows):
        y = 5.0 + (i + 1) * row_height - 1.2
        split_y = 5.0 + (i + 1) * row_height
        rows_svg.append(f"""
        <line x1="0" y1="{split_y}" x2="{width}" y2="{split_y}" class="border-sub" stroke-width="0.25"/>
        <text x="4" y="{y:.2f}" class="tb-field-val-sm" text-anchor="middle">{html.escape(row.rev)}</text>
        <text x="12" y="{y:.2f}" class="tb-field-val-sm">{html.escape(row.description)}</text>
        <text x="82" y="{y:.2f}" class="tb-field-val-sm" text-anchor="middle">{html.escape(row.date)}</text>
        <text x="100" y="{y:.2f}" class="tb-field-val-sm" text-anchor="middle">{html.escape(row.approved_by)}</text>
        """)

    return f"""
    <g class="rev-table" transform="translate({rx:.2f}, {ry:.2f})">
        <rect x="0" y="0" width="{width:.2f}" height="{total_h:.2f}" class="title-bg" stroke-width="0.5"/>
        <line x1="0" y1="5.0" x2="{width}" y2="5.0" class="border-sub" stroke-width="0.3"/>
        <line x1="8" y1="0" x2="8" y2="{total_h}" class="border-sub" stroke-width="0.25"/>
        <line x1="72" y1="0" x2="72" y2="{total_h}" class="border-sub" stroke-width="0.25"/>
        <line x1="92" y1="0" x2="92" y2="{total_h}" class="border-sub" stroke-width="0.25"/>

        <text x="4" y="3.6" class="tb-field-label" text-anchor="middle">REV</text>
        <text x="12" y="3.6" class="tb-field-label">DESCRIPTION</text>
        <text x="82" y="3.6" class="tb-field-label" text-anchor="middle">DATE</text>
        <text x="100" y="3.6" class="tb-field-label" text-anchor="middle">APPR</text>
        {"".join(rows_svg)}
    </g>
    """


def render_bom_table_svg(
    bom_items: list[BOMItem],
    sheet_width: float,
    margin_right: float = 8.0,
    top_y: float = 22.0,
    width: float = 175.0,
    row_height: float = 5.2,
    header_height: float = 5.5,
) -> str:
    """Render a structured BOM (Bill of Materials) table."""
    if not bom_items:
        return ""

    total_h = header_height + len(bom_items) * row_height
    bx = sheet_width - margin_right - width
    col_x = [0.0, 10.0, 20.0, 65.0, 135.0, width]

    rows_svg = []
    for i, item in enumerate(bom_items):
        ry = header_height + i * row_height
        text_y = ry + 3.8
        alt_bg = (
            f'<rect x="0" y="{ry:.2f}" width="{width}" height="{row_height}" class="table-row-alt"/>'
            if i % 2 == 1
            else ""
        )
        rows_svg.append(f"""
        {alt_bg}
        <line x1="0" y1="{ry + row_height:.2f}" x2="{width}" y2="{ry + row_height:.2f}" class="border-sub" stroke-width="0.2"/>
        <text x="5" y="{text_y:.2f}" class="tb-field-val-sm" text-anchor="middle">{item.item_no}</text>
        <text x="15" y="{text_y:.2f}" class="tb-field-val-sm" text-anchor="middle">{item.qty}</text>
        <text x="22" y="{text_y:.2f}" class="tb-field-val-sm" font-weight="bold">{html.escape(item.part_no)}</text>
        <text x="67" y="{text_y:.2f}" class="tb-field-val-sm">{html.escape(item.description)}</text>
        <text x="137" y="{text_y:.2f}" class="tb-field-val-sm">{html.escape(item.material)}</text>
        """)

    col_divs = "".join(
        [
            f'<line x1="{cx}" y1="0" x2="{cx}" y2="{total_h}" class="border-sub" stroke-width="0.25"/>'
            for cx in col_x[1:-1]
        ]
    )

    return f"""
    <g class="bom-table" transform="translate({bx:.2f}, {top_y:.2f})">
        <rect x="0" y="0" width="{width:.2f}" height="{total_h:.2f}" class="title-bg" stroke-width="0.5"/>
        <rect x="0" y="0" width="{width:.2f}" height="{header_height}" class="table-header-bg"/>
        <line x1="0" y1="{header_height}" x2="{width}" y2="{header_height}" class="border-sub" stroke-width="0.4"/>

        {col_divs}

        <text x="5" y="4.0" class="tb-field-label" text-anchor="middle">ITEM</text>
        <text x="15" y="4.0" class="tb-field-label" text-anchor="middle">QTY</text>
        <text x="22" y="4.0" class="tb-field-label">PART NO.</text>
        <text x="67" y="4.0" class="tb-field-label">DESCRIPTION</text>
        <text x="137" y="4.0" class="tb-field-label">MATERIAL</text>

        {"".join(rows_svg)}
    </g>
    """
