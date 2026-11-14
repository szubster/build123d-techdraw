"""Stepped bracket blueprint built using build123d-style Builder Mode."""

from pathlib import Path

import build123d as bd

from build123d_techdraw import (
    BuildSheet,
    CenterMark,
    DatumFeature,
    FeatureControlFrame,
    FrontView,
    GDTCharacteristic,
    GeneralNotes,
    IsometricView,
    LinearDimension,
    Revision,
    SheetSize,
    Theme,
    TitleBlock,
    TopView,
)


def generate_builder_mode_drawing(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Model a stepped mechanical bracket with a mounting hole
    base = bd.Box(80.0, 50.0, 15.0)
    flange = bd.Box(30.0, 50.0, 40.0).move(bd.Location((-25.0, 0.0, 20.0)))
    part = base + flange
    hole = bd.Cylinder(radius=5.5, height=60.0).move(bd.Location((-25.0, 0.0, 20.0)))
    part = part - hole

    # 2. Builder Mode: Declarative drawing sheet construction
    with BuildSheet(SheetSize.A3_LANDSCAPE) as sheet:
        TitleBlock(
            title="STEPPED BRACKET (BUILDER MODE)",
            drawing_number="BRK-BUILDER-01",
            rev="REV A",
            author="SZUBA",
            material="AL 6061-T6",
            finish="ANODIZED CLEAR",
        )
        GeneralNotes(
            [
                "1. ALL DIMENSIONS IN MILLIMETERS (mm) UNLESS NOTED.",
                "2. GENERAL TOLERANCES ACCORDING TO ISO 2768-mK.",
                "3. 100% CAD MODEL CONFORMANCE; DO NOT SCALE DRAWING.",
            ]
        )
        Revision(rev="A", description="INITIAL ENGINEERING RELEASE", approved_by="SZUBA")

        # Front view with topological annotations
        with FrontView(part, center=(130.0, 150.0), scale=1.0):
            cyl_face = [f for f in part.faces() if f.geom_type == bd.GeomType.CYLINDER][0]
            CenterMark(cyl_face)

            # Dimension height of stepped flange directly from edge
            flange_edge = part.edges().filter_by(bd.Axis.Z).sort_by(bd.Axis.X)[0]
            LinearDimension(flange_edge, offset=-15.0, tolerance="h9")

            # Flatness GD&T frame and datum
            DatumFeature(label="A", target=part.faces().sort_by(bd.Axis.Z)[0])
            FeatureControlFrame(
                characteristic=GDTCharacteristic.FLATNESS,
                tolerance_value="0.05",
                datum_refs=["A"],
                target=part.faces().sort_by(bd.Axis.Z)[-1],
                leader_offset=(15.0, -10.0),
            )

        # Top view aligned above front view
        with TopView(part, center=(130.0, 230.0), scale=1.0):
            v_min = part.vertices().sort_by(bd.Axis.X)[0]
            v_max = part.vertices().sort_by(bd.Axis.X)[-1]
            LinearDimension(v_min, v_max, offset=15.0, direction="horizontal")

        # Isometric view
        with IsometricView(part, center=(320.0, 190.0), scale=0.8):
            pass

        # 3. Export outputs directly from builder
        sheet.export_svg(str(output_dir / "builder_mode_bracket.svg"), theme=Theme.DARK_BLUEPRINT)
        sheet.export_svg(str(output_dir / "builder_mode_bracket_white.svg"), theme=Theme.ISO_WHITE)
        sheet.export_pdf(str(output_dir / "builder_mode_bracket.pdf"))
        sheet.export_dxf(str(output_dir / "builder_mode_bracket.dxf"))
        print("Generated Builder Mode outputs.")


if __name__ == "__main__":
    generate_builder_mode_drawing(Path(__file__).parent / "output")
