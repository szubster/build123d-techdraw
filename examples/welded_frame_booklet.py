"""Welded Frame Booklet example: Multi-page DrawingBooklet, ISO 2553 WeldingSymbol, Tables, Cards, StepBadges, and Chain/Baseline Dimensions."""

from pathlib import Path

import build123d as bd

from build123d_techdraw import (
    BaselineDimensionGroup,
    BuildSheet,
    Card,
    ChainDimensionGroup,
    DrawingBooklet,
    FrontView,
    IsometricView,
    SheetSize,
    StepBadge,
    Table,
    Theme,
    TitleBlock,
    TopView,
    WeldingSymbol,
    WeldType,
    run_sheet_lint,
)


def generate_welded_frame_booklet(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Model a welded L-bracket with a triangular gusset
    base_plate = bd.Box(120.0, 60.0, 10.0)
    upright = bd.Pos(-45.0, 0.0, 40.0) * bd.Box(10.0, 60.0, 70.0)
    gusset = bd.Pos(-15.0, 0.0, 20.0) * bd.Box(50.0, 8.0, 30.0)
    weldment = bd.Compound(children=[base_plate, upright, gusset])

    # 2. Sheet 1: Welded Assembly with ISO 2553 Welding Symbols, StepBadges, and Weld Schedule Table
    with BuildSheet(sheet_size=SheetSize.A3_LANDSCAPE) as sheet1:
        TitleBlock(
            title="WELDED GUSSET FRAME - ASSEMBLY",
            drawing_number="WLD-001",
            rev="REV A",
            author="SZUBA",
            material="S355J2+N",
            finish="SHOT BLASTED",
        )

        with FrontView(weldment, center=(130.0, 150.0), scale=1.2) as fv:
            StepBadge(step_number=1, position=(60.0, 105.0), label="TACK")
            StepBadge(step_number=2, position=(60.0, 130.0), label="WELD")
            WeldingSymbol(
                target=(92.0, 180.0),
                weld_type=WeldType.FILLET,
                size="a5",
                length="60",
                all_around=True,
                tail_text="GMAW 135",
                elbow_offset=(22.0, -22.0),
                view=fv,
            )
            WeldingSymbol(
                target=(115.0, 175.0),
                weld_type=WeldType.FILLET,
                size="a4",
                field_weld=True,
                arrow_side=False,
                elbow_offset=(20.0, 18.0),
                view=fv,
            )

        with IsometricView(weldment, center=(310.0, 110.0), scale=0.9):
            pass

        Table(
            title="WELD SCHEDULE (ISO 2553)",
            headers=["ID", "TYPE", "SIZE", "PROCESS", "NDT"],
            rows=[
                ["W1", "FILLET (ALL AROUND)", "a5", "GMAW 135", "VT 100%"],
                ["W2", "FILLET (FIELD)", "a4", "SMAW 111", "MT 20%"],
            ],
            position=(235.0, 175.0),
            col_widths=[14.0, 44.0, 16.0, 26.0, 24.0],
        )

    # 3. Sheet 2: Gusset Plate Detail with Chain & Baseline Dimensions and WPS Card
    with BuildSheet(sheet_size=SheetSize.A3_LANDSCAPE) as sheet2:
        TitleBlock(
            title="WELDED GUSSET FRAME - PLATE DETAIL",
            drawing_number="WLD-002",
            rev="REV A",
            author="SZUBA",
            material="S355J2+N",
        )

        with TopView(base_plate, center=(140.0, 140.0), scale=1.2) as tv:
            v_left = base_plate.vertices().sort_by(bd.Axis.X)[0]
            v_right = base_plate.vertices().sort_by(bd.Axis.X)[-1]
            v_mid = bd.Vertex(0.0, -30.0, 5.0)
            ChainDimensionGroup(
                targets=[v_left, v_mid, v_right],
                direction="horizontal",
                offset=15.0,
                view=tv,
            )
            BaselineDimensionGroup(
                base_target=v_left,
                targets=[v_mid, v_right],
                direction="horizontal",
                first_offset=-15.0,
                step_offset=-10.0,
                view=tv,
            )

        Card(
            title="WELDING PROCEDURE (WPS-135-04)",
            body=[
                "PREHEAT: MIN 20 C",
                "SHIELDING GAS: ISO 14175 M21",
                "FILLER: EN ISO 14341-A G 42 4 M21",
                "ACCEPTANCE: ISO 5817 LEVEL B",
            ],
            position=(260.0, 80.0),
            width=110.0,
        )

    for idx, s in enumerate((sheet1.sheet, sheet2.sheet), start=1):
        issues = run_sheet_lint(s)
        errors = [i for i in issues if i.severity == "ERROR"]
        assert not errors, f"Sheet {idx} lint errors: {errors}"

    # 4. Combine into a multi-page DrawingBooklet with PDF bookmarks
    booklet = DrawingBooklet(title="WELDED GUSSET FRAME PACKAGE")
    booklet.add_sheet(sheet1.sheet)
    booklet.add_sheet(sheet2.sheet)
    booklet.export_pdf(str(output_dir / "welded_frame_booklet.pdf"), bookmarks=True)

    # Also export Sheet 1 to SVG and DXF for preview and CAD interchange
    sheet1.export_svg(str(output_dir / "welded_frame_booklet.svg"), theme=Theme.DARK_BLUEPRINT)
    sheet1.export_svg(str(output_dir / "welded_frame_booklet_white.svg"), theme=Theme.ISO_WHITE)
    sheet1.export_dxf(str(output_dir / "welded_frame_booklet.dxf"))
    print("Generated welded frame booklet outputs.")


if __name__ == "__main__":
    generate_welded_frame_booklet(Path(__file__).parent / "output")
