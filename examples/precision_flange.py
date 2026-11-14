"""Precision Flange example: Feature recognition, BoltCircle, Diameter/Radial/Angular/Ordinate dims, Callouts, and Surface Finish."""

import math
from pathlib import Path

import build123d as bd

from build123d_techdraw import (
    AngularDimension,
    BoltCircle,
    BuildDrawing,
    CenterMark,
    DiameterDimension,
    FrontView,
    GeneralNotes,
    HoleCallout,
    LeaderNote,
    MaterialRemoval,
    OrdinateDimensionSet,
    RadialDimension,
    SheetSize,
    SurfaceFinishSymbol,
    Theme,
    ThreadCallout,
    TitleBlock,
    TopView,
    recognize_bolt_circles,
    recognize_holes,
    run_sheet_lint,
)


def generate_precision_flange(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Model a precision mounting flange (OD=100, bore=30, 4x M6 on PCD 70)
    flange = bd.Cylinder(radius=50.0, height=12.0)
    hub = bd.Pos(0.0, 0.0, 10.0) * bd.Cylinder(radius=24.0, height=12.0)
    part = flange + hub
    bore = bd.Cylinder(radius=15.0, height=40.0)
    part = part - bore

    pcd_radius = 35.0
    for i in range(4):
        ang = math.radians(45.0 + i * 90.0)
        hx = pcd_radius * math.cos(ang)
        hy = pcd_radius * math.sin(ang)
        part = part - (bd.Pos(hx, hy, 0.0) * bd.Cylinder(radius=3.0, height=20.0))

    # 2. Run automated CAD feature recognition
    holes = recognize_holes(part)
    bolt_circles = recognize_bolt_circles(part)
    assert len(holes) >= 4
    assert len(bolt_circles) >= 1
    pcd = bolt_circles[0].pcd

    # 3. Compose A3 sheet with TopView and FrontView
    with BuildDrawing(sheet_size=SheetSize.A3_LANDSCAPE) as sheet:
        TitleBlock(
            title="PRECISION MOUNTING FLANGE",
            drawing_number="FLG-200",
            rev="REV B",
            author="SZUBA",
            material="SS 316L",
            finish="PASSIVATED",
        )
        GeneralNotes(
            [
                f"1. RECOGNIZED {len(holes)} HOLES; PCD {pcd:.1f} MM.",
                "2. GENERAL TOLERANCES: ISO 2768-fH.",
                "3. BREAK ALL SHARP EDGES 0.3 X 45 DEG.",
            ]
        )

        # Top view: PCD bolt circle, bore diameter, outer radius, thread/hole callouts, angular dim
        with TopView(part, center=(120.0, 150.0), scale=1.0):
            CenterMark((120.0, 150.0), size=12.0)
            BoltCircle(
                center=(120.0, 150.0),
                diameter=pcd,
                num_holes=bolt_circles[0].hole_count,
                start_angle_deg=45.0,
            )
            DiameterDimension(
                center=(120.0, 150.0),
                diameter=30.0,
                angle_deg=135.0,
                tolerance="H7",
            )
            RadialDimension(
                center=(120.0, 150.0),
                radius=50.0,
                angle_deg=-30.0,
            )
            AngularDimension(
                apex=(120.0, 150.0),
                start_angle_deg=0.0,
                end_angle_deg=45.0,
                radius=40.0,
            )
            ThreadCallout(
                target=(
                    120.0 + 35.0 * math.cos(math.radians(45.0)),
                    150.0 - 35.0 * math.sin(math.radians(45.0)),
                ),
                thread="M6x1.0",
                class_fit="6H",
                depth="THRU",
                quantity=4,
                elbow_offset=(18.0, -18.0),
            )
            HoleCallout(
                target=(120.0 - 15.0, 150.0),
                diameter=30.0,
                depth="THRU",
                elbow_offset=(-22.0, 18.0),
            )

        # Front view: Ordinate dimensions, Surface finish symbol, and Leader note
        with FrontView(part, center=(290.0, 150.0), scale=1.0) as front:
            OrdinateDimensionSet(
                datum_origin=(240.0, 162.0),
                direction="X",
                coordinates=[0.0, 26.0, 50.0, 74.0, 100.0],
                baseline_offset=-20.0,
                jog=True,
                view=front,
            )
            SurfaceFinishSymbol(
                position=(290.0, 134.0),
                ra_value="Ra 1.6",
                material_removal=MaterialRemoval.REQUIRED,
            )
            LeaderNote(
                target=(314.0, 145.0),
                lines=["MACHINED SEALING FACE", "CONCENTRICITY 0.02 A"],
                elbow_offset=(16.0, -16.0),
            )

    issues = run_sheet_lint(sheet.sheet)
    errors = [i for i in issues if i.severity == "ERROR"]
    assert not errors, f"Unexpected lint errors: {errors}"

    sheet.export_svg(str(output_dir / "precision_flange.svg"), theme=Theme.DARK_BLUEPRINT)
    sheet.export_svg(str(output_dir / "precision_flange_white.svg"), theme=Theme.ISO_WHITE)
    sheet.export_pdf(str(output_dir / "precision_flange.pdf"))
    sheet.export_dxf(str(output_dir / "precision_flange.dxf"))
    print("Generated precision flange outputs.")


if __name__ == "__main__":
    generate_precision_flange(Path(__file__).parent / "output")
