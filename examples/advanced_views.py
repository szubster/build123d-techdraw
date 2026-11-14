"""Advanced Views example: Section, Detail, Auxiliary, and Break views."""

from pathlib import Path

import build123d as bd

from build123d_techdraw import (
    AuxiliaryView,
    BuildSheet,
    ChamferCallout,
    DetailView,
    FrontView,
    GeneralNotes,
    LinearDimension,
    SectionCuttingLine,
    SectionView,
    SheetSize,
    Theme,
    TitleBlock,
    TopView,
    run_sheet_lint,
)


def generate_advanced_views(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Stepped housing with an inclined chamfered face and internal bore
    base = bd.Box(90.0, 60.0, 30.0)
    boss = bd.Pos(0.0, 0.0, 20.0) * bd.Cylinder(radius=18.0, height=20.0)
    housing = base + boss
    bore = bd.Cylinder(radius=9.0, height=70.0)
    counterbore = bd.Pos(0.0, 0.0, 22.0) * bd.Cylinder(radius=13.0, height=18.0)
    housing = housing - bore - counterbore

    # Inclined chamfer on the right edge for AuxiliaryView
    right_top_edge = housing.edges().filter_by(bd.Axis.Y).sort_by(bd.Axis.Z)[-1]
    housing = bd.chamfer(right_top_edge, length=8.0)
    inclined_faces = [f for f in housing.faces() if 0.1 < abs(f.normal_at().Z) < 0.9]
    inclined_face = inclined_faces[0]

    # Long tie-rod for BreakView demonstration (400 mm long)
    tie_rod = bd.Pos(0.0, 0.0, 0.0) * bd.Box(400.0, 14.0, 14.0)

    # 2. Compose A2 landscape sheet with all 4 advanced view types
    with BuildSheet(sheet_size=SheetSize.A2_LANDSCAPE) as sheet:
        TitleBlock(
            title="ADVANCED VIEWS SHOWCASE",
            drawing_number="ADV-001",
            rev="REV A",
            author="SZUBA",
            material="AL 7075-T6",
        )
        GeneralNotes(
            [
                "1. SECTION A-A SHOWS INTERNAL STEPPED BORE WITH BACKGROUND.",
                "2. DETAIL B MAGNIFIES CHAMFERED CORNER AT 2:1.",
                "3. VIEW C IS AN AUXILIARY PROJECTION NORMAL TO INCLINED FACE.",
                "4. TIE-ROD USES ZIGZAG BREAK WHILE PRESERVING TRUE 400 MM LENGTH.",
            ]
        )

        # Parent TopView with section cutting line
        with TopView(housing, center=(110.0, 140.0), scale=1.0) as top:
            SectionCuttingLine(
                start=(55.0, 140.0),
                end=(165.0, 140.0),
                label="A",
                arrow_dir="up",
            )

        # SectionView A-A below TopView
        with SectionView(
            housing,
            cutting_plane=bd.Plane.XZ,
            center=(110.0, 260.0),
            scale=1.0,
            view_label="SECTION A-A",
            show_background=True,
            hatch="aluminum",
        ):
            pass

        # DetailView B magnifying the chamfered corner of SectionView / TopView
        with DetailView(
            parent_view=top,
            region_center=(148.0, 140.0),
            radius=14.0,
            scale=2.0,
            label="B",
            center=(250.0, 140.0),
            shape="circle",
        ):
            ChamferCallout(
                target_or_edge=(245.0, 140.0),
                width=8.0,
                angle_deg=45.0,
            )

        # AuxiliaryView C projected normal to the inclined chamfered face
        with AuxiliaryView(
            shape=housing,
            face=inclined_face,
            center=(250.0, 260.0),
            scale=1.0,
            view_label="VIEW C",
        ):
            pass

        # BreakView on a 400 mm long tie-rod
        with FrontView(tie_rod, center=(430.0, 180.0), scale=0.8) as rod_view:
            rod_view.add_break(
                interval=(70.0, 330.0),
                axis="X",
                style="zigzag",
                gap=10.0,
            )
            LinearDimension(offset=-18.0)

    issues = run_sheet_lint(sheet.sheet)
    errors = [i for i in issues if i.severity == "ERROR"]
    assert not errors, f"Unexpected lint errors: {errors}"

    sheet.export_svg(str(output_dir / "advanced_views.svg"), theme=Theme.DARK_BLUEPRINT)
    sheet.export_svg(str(output_dir / "advanced_views_white.svg"), theme=Theme.ISO_WHITE)
    sheet.export_pdf(str(output_dir / "advanced_views.pdf"))
    sheet.export_dxf(str(output_dir / "advanced_views.dxf"))
    print("Generated advanced views outputs.")


if __name__ == "__main__":
    generate_advanced_views(Path(__file__).parent / "output")
