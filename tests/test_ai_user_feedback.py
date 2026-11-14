"""Unit tests validating the bug fixes and enhancements reported by the AI user.

Covers:
1. Duplicate annotation registration fix in SheetView helpers and linter.
2. Model-space & feature-based dimensioning (`dimension`, `dimension_between`).
3. Context-aware BOMBalloon with model coordinates and elbow_offset.
4. Table word-wrapping, column alignments, zebra striping, and Card/TextBox.
5. Canvas layout inspection (`sheet.inspect_layout()`).
6. Annotation-aware `view.align_to()`.
7. Frameless drawing sheet style and StepBadge.
"""

import build123d as bd

from build123d_techdraw import (
    BOMBalloon,
    BuildSheet,
    Card,
    DrawingSheet,
    FrontView,
    SheetSize,
    StepBadge,
    Table,
    TextBox,
    ViewType,
)


def test_duplicate_annotation_registration_fix() -> None:
    """Validate that helper dimension calls inside BuildSheet do not produce duplicates or false lint errors."""
    box = bd.Box(100.0, 50.0, 10.0)

    with BuildSheet(sheet_size="A4_LANDSCAPE") as s:
        view = FrontView(box, center=(100.0, 100.0))
        dim = view.add_overall_horizontal()

    sheet = s.sheet
    assert len(sheet.annotations) == 1
    assert dim in sheet.annotations
    assert dim in view.annotations

    issues = sheet.lint()
    overlap_issues = [i for i in issues if i.code == "OVERLAP_ANNOTATION"]
    assert len(overlap_issues) == 0

    # Also test dim_horizontal, dim_vertical, and add_overall_vertical
    with BuildSheet(sheet_size="A4_LANDSCAPE") as s2:
        v2 = FrontView(box, center=(120.0, 120.0))
        v2.dim_horizontal(0.0, 50.0, model_y=0.0, offset_y=-5.0)
        v2.dim_vertical(0.0, 25.0, model_x=0.0, offset_x=-5.0)
        v2.add_overall_vertical()

    sheet2 = s2.sheet
    assert len(sheet2.annotations) == 3
    issues2 = sheet2.lint()
    assert len([i for i in issues2 if i.code == "OVERLAP_ANNOTATION"]) == 0


def test_model_space_and_feature_based_dimensioning() -> None:
    """Validate `view.dimension()` and `view.dimension_between()` with edges, vertices, and model coordinates."""
    box = bd.Box(100.0, 50.0, 20.0)
    sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    front = FrontView(box, scale=1.0).locate((150.0, 150.0))
    sheet.add_view(front)

    # 1. Dimension an edge directly
    top_edge = box.edges().filter_by(bd.Axis.X).sort_by(bd.Axis.Z)[-1]
    dim_edge = front.dimension(top_edge, offset=-12.0)
    assert dim_edge in front.annotations
    assert dim_edge in sheet.annotations
    assert dim_edge.text == "100"

    # 2. Dimension between two vertices
    v_left = box.vertices().sort_by(bd.Axis.X)[0]
    v_right = box.vertices().sort_by(bd.Axis.X)[-1]
    dim_v = front.dimension_between(v_left, v_right, direction="horizontal", offset=15.0)
    assert dim_v in front.annotations
    assert dim_v.text == "100"

    # 3. Dimension between two 2D model coordinates
    dim_pts = front.dimension_between((10.0, 0.0), (60.0, 0.0), direction="horizontal", offset=-6.0)
    assert dim_pts.text == "50"


def test_context_aware_bom_balloon_model_coords() -> None:
    """Validate context-aware BOMBalloon resolving model coordinates and elbow offset."""
    part = bd.Box(100.0, 50.0, 20.0)
    with (
        BuildSheet(sheet_size="A3_LANDSCAPE") as s,
        FrontView(part, center=(135.0, 100.0), scale=0.5) as front,
    ):
        balloon = BOMBalloon(item=1, target=(40.0, 10.0), elbow_offset=(-15.0, 25.0))

    sheet = s.sheet
    assert balloon in front.annotations
    assert balloon in sheet.annotations
    assert balloon.item_no == 1

    # Target (40.0, 10.0) in model space projected into FrontView (center 135, 100, scale 0.5)
    expected_sheet_target = front.to_sheet_coords(40.0, 10.0)
    assert balloon.target == expected_sheet_target
    assert balloon.balloon_center == (
        expected_sheet_target[0] - 15.0,
        expected_sheet_target[1] + 25.0,
    )


def test_table_word_wrap_and_zebra_and_alignments() -> None:
    """Validate word-wrapping, column alignments, and zebra row striping in Table."""
    headers = ["Part", "Description", "Qty"]
    rows = [
        ["M5 Bolt", "High-tensile Grade 12.9 zinc-plated steel hex socket cap screw", "12"],
        [
            "Base Plate",
            "Precision machined 6061-T6 aluminum plate with hard anodized black finish",
            "1",
        ],
    ]
    tbl = Table(
        headers=headers,
        rows=rows,
        position=(30.0, 30.0),
        col_widths=[25.0, 60.0, 15.0],
        column_alignments=["left", "left", "center"],
        zebra=True,
        word_wrap=True,
    )

    svg = tbl.to_svg()
    assert 'class="table-row-alt"' in svg
    assert "High-tensile" in svg
    assert "aluminum" in svg
    assert 'text-anchor="middle"' in svg  # center alignment on Qty column


def test_card_textbox_primitive() -> None:
    """Validate Card and TextBox specification callouts."""
    card = Card(
        title="ASSEMBLY SPECIFICATIONS",
        body=[
            "1. Apply Loctite 243 to all M5 threads.",
            "2. Torque fasteners to 4.5 N*m in star pattern.",
            "3. Inspect linear rail runout <= 0.02mm.",
        ],
        position=(40.0, 40.0),
        width=75.0,
        bullet="• ",
    )

    assert card.width == 75.0
    assert card.height > 20.0
    svg = card.to_svg()
    assert 'class="specification-card"' in svg
    assert "ASSEMBLY SPECIFICATIONS" in svg
    assert "Loctite 243" in svg

    # TextBox is an alias of Card
    assert TextBox is Card


def test_sheet_inspect_layout() -> None:
    """Validate programmatic canvas inspection with `sheet.inspect_layout()`."""
    part = bd.Box(60.0, 40.0, 20.0)
    sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    front = sheet.add_view(part, view_type=ViewType.FRONT, center=(120.0, 120.0), scale=1.0)
    front.add_overall_horizontal()

    layout = sheet.inspect_layout()
    assert layout["sheet_size"] == (420.0, 297.0)
    assert len(layout["drawable_bounds"]) == 4
    assert len(layout["title_block_bounds"]) == 4
    assert len(layout["views"]) == 1
    assert layout["views"][0]["scale"] == 1.0
    assert layout["annotations_count"] >= 1
    assert "top-left" in layout["free_quadrants"] or "top-right" in layout["free_quadrants"]


def test_view_align_to_include_annotations() -> None:
    """Validate that `align_to(..., include_annotations=True)` measures spacing from visual bounds."""
    part = bd.Box(50.0, 30.0, 20.0)
    front = FrontView(part, scale=1.0).locate((100.0, 100.0))
    # Add dimension on right side of front view (extending min_x / max_x)
    front.dim_vertical(model_y1=-15.0, model_y2=15.0, model_x=25.0, offset_x=20.0)

    # 1. Plain align_to without annotations
    right_plain = FrontView(part, scale=1.0).align_to(
        front, direction="right", spacing=20.0, include_annotations=False
    )
    # 2. Annotation-aware align_to
    right_aware = FrontView(part, scale=1.0).align_to(
        front, direction="right", spacing=20.0, include_annotations=True
    )

    # With annotations included, right_aware must be placed further to the right
    assert right_aware.center_x > right_plain.center_x


def test_frameless_sheet_style() -> None:
    """Validate frameless sheet style and StepBadge for manual and assembly drawings."""
    sheet = DrawingSheet(sheet_size="A4_PORTRAIT", style="frameless")
    assert sheet.show_border is False
    assert sheet.show_title_block is False
    assert sheet.show_grid is False
    assert sheet.show_notes is False

    badge = StepBadge(step_number=1, position=(25.0, 25.0), label="PREPARE CARRIAGE")
    sheet.add(badge)

    svg = sheet.to_svg()
    assert 'class="step-badge"' in svg
    assert "PREPARE CARRIAGE" in svg
    assert 'class="zone-text"' not in svg
    assert 'class="title-block"' not in svg


def test_table_cell_styles_and_theme_panel_classes() -> None:
    """Validate Table col_styles/cell_styles/row_alt_opacity and semantic RawSVG theme classes."""
    from build123d_techdraw.annotations.tables import RawSVG, Table
    from build123d_techdraw.renderers.theme import Theme, generate_theme_css

    tbl = Table(
        headers=["Param", "Value"],
        rows=[["Gain", "1.25"], ["Mode", "ACTIVE"]],
        col_widths=[30.0, 30.0],
        position=(20.0, 20.0),
        row_alt_opacity=0.35,
        col_styles=[{"font_weight": "bold"}, {"color": "#38BDF8"}],
        cell_styles={(1, 1): {"color": "#22C55E", "bg": "#0F172A"}},
    )
    svg = tbl.to_svg()
    assert 'fill-opacity="0.35"' in svg
    assert 'font-weight="bold"' in svg
    assert 'fill="#38BDF8"' in svg
    assert 'fill="#22C55E"' in svg
    assert 'fill="#0F172A"' in svg

    css = generate_theme_css(Theme.ISO_WHITE)
    for cls in (
        ".panel-bg",
        ".panel-stroke",
        ".panel-sub",
        ".panel-text",
        ".panel-muted",
        ".accent-text",
    ):
        assert cls in css

    raw = RawSVG(
        '<rect x="0" y="0" width="40" height="20" class="panel-bg"/><text x="5" y="10" class="accent-text">OK</text>'
    )
    assert raw.bounding_box_2d() == (0.0, 0.0, 40.0, 20.0)
