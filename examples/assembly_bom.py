"""Assembly Blueprint & Automated BOM example."""

from pathlib import Path

import build123d as bd

from build123d_techdraw import (
    DrawingSheet,
    SheetSize,
    Theme,
    add_assembly_bom_and_balloons,
    add_standard_multiview,
)


def generate_assembly_bom(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Build an assembly of labeled components
    base = bd.Box(120.0, 80.0, 20.0)
    base.label = "Machined Baseplate"

    pin1 = bd.Cylinder(radius=6.0, height=30.0).move(bd.Location((30.0, 20.0, 25.0)))
    pin1.label = "Dowel Pin"

    pin2 = bd.Cylinder(radius=6.0, height=30.0).move(bd.Location((-30.0, -20.0, 25.0)))
    pin2.label = "Dowel Pin"

    assembly = bd.Compound(children=[base, pin1, pin2])

    # 2. Create sheet and multi-view layout
    sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    sheet.set_title_block(
        title="BASE ASSEMBLY",
        drawing_number="ASM-001",
        author="J. DOE",
        material="AL 6061-T6 / STEEL",
    )
    layout = add_standard_multiview(sheet, assembly, scale="auto")

    # 3. Automatically tally quantities, populate BOM table, and place radiating balloons
    assert layout.isometric is not None
    _bom, _balloons = add_assembly_bom_and_balloons(
        sheet=sheet,
        assembly=assembly,
        target_view=layout.isometric,
        balloon_distance=30.0,
    )

    # 4. Export to SVG (dark and white), PDF, and DXF
    sheet.export_svg(str(output_dir / "assembly_drawing.svg"), theme=Theme.DARK_BLUEPRINT)
    sheet.export_svg(str(output_dir / "assembly_drawing_white.svg"), theme=Theme.ISO_WHITE)
    sheet.export_pdf(str(output_dir / "assembly_drawing.pdf"))
    sheet.export_dxf(str(output_dir / "assembly_drawing.dxf"))
    print("Generated assembly drawing outputs.")


if __name__ == "__main__":
    generate_assembly_bom(Path(__file__).parent / "output")
