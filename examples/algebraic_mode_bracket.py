"""Stepped bracket blueprint built using build123d-style Algebraic Mode."""

from pathlib import Path

import build123d as bd

from build123d_techdraw import (
    CenterMark,
    DatumFeature,
    DrawingSheet,
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


def generate_algebraic_mode_drawing(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Model a stepped mechanical bracket with a mounting hole
    base = bd.Box(80.0, 50.0, 15.0)
    flange = bd.Box(30.0, 50.0, 40.0).move(bd.Location((-25.0, 0.0, 20.0)))
    part = base + flange
    hole = bd.Cylinder(radius=5.5, height=60.0).move(bd.Location((-25.0, 0.0, 20.0)))
    part = part - hole

    # 2. Algebraic Mode: Create sheet and compose using operators
    sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    sheet += TitleBlock(
        title="STEPPED BRACKET (ALGEBRAIC MODE)",
        drawing_number="BRK-ALG-01",
        rev="REV A",
        author="SZUBA",
        material="AL 6061-T6",
        finish="ANODIZED CLEAR",
    )
    sheet += GeneralNotes(
        [
            "1. ALL DIMENSIONS IN MILLIMETERS (mm) UNLESS NOTED.",
            "2. GENERAL TOLERANCES ACCORDING TO ISO 2768-mK.",
        ]
    )
    sheet += Revision(rev="A", description="INITIAL ENGINEERING RELEASE", approved_by="SZUBA")

    # 3. Create views explicitly with relative layout chaining
    front = FrontView(part, scale=1.0).locate((130.0, 150.0))
    top = TopView(part, scale=1.0).align_to(front, direction="above", spacing=25.0)
    iso = IsometricView(part, scale=0.8).align_to(front, direction="right", spacing=50.0)

    # 4. Attach annotations to views
    cyl_face = [f for f in part.faces() if f.geom_type == bd.GeomType.CYLINDER][0]
    flange_edge = part.edges().filter_by(bd.Axis.Z).sort_by(bd.Axis.X)[0]

    front += [
        CenterMark(cyl_face),
        LinearDimension(flange_edge, offset=-15.0, tolerance="h9"),
        DatumFeature(label="A", target=part.faces().sort_by(bd.Axis.Z)[0]),
        FeatureControlFrame(
            characteristic=GDTCharacteristic.FLATNESS,
            tolerance_value="0.05",
            datum_refs=["A"],
            target=part.faces().sort_by(bd.Axis.Z)[-1],
            leader_offset=(15.0, -10.0),
        ),
    ]

    v_min = part.vertices().sort_by(bd.Axis.X)[0]
    v_max = part.vertices().sort_by(bd.Axis.X)[-1]
    top += LinearDimension(v_min, v_max, offset=15.0, direction="horizontal")

    # 5. Compose views into sheet
    sheet += [front, top, iso]

    # 6. Export outputs
    sheet.export_svg(str(output_dir / "algebraic_mode_bracket.svg"), theme=Theme.DARK_BLUEPRINT)
    sheet.export_svg(str(output_dir / "algebraic_mode_bracket_white.svg"), theme=Theme.ISO_WHITE)
    sheet.export_pdf(str(output_dir / "algebraic_mode_bracket.pdf"))
    sheet.export_dxf(str(output_dir / "algebraic_mode_bracket.dxf"))
    print("Generated Algebraic Mode outputs.")


if __name__ == "__main__":
    generate_algebraic_mode_drawing(Path(__file__).parent / "output")
