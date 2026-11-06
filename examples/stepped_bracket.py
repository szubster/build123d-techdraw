"""Detailed component blueprint with GD&T and dimension annotations."""

from pathlib import Path

import build123d as bd

from build123d_techdraw import (
    DrawingSheet,
    GDTCharacteristic,
    SheetSize,
    Theme,
    add_standard_multiview,
)


def generate_stepped_bracket(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Model a stepped mechanical bracket with a mounting hole
    base = bd.Box(80.0, 50.0, 15.0)
    flange = bd.Box(30.0, 50.0, 40.0).move(bd.Location((-25.0, 0.0, 20.0)))
    part = base + flange
    hole = bd.Cylinder(radius=5.5, height=60.0).move(bd.Location((-25.0, 0.0, 20.0)))
    part = part - hole

    # 2. ISO 5457 A3 Drawing Sheet
    sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    sheet.set_title_block(
        title="STEPPED BRACKET",
        drawing_number="BRK-100-A",
        rev="REV A",
        author="SZUBA",
        material="AL 6061-T6",
        finish="ANODIZED CLEAR",
    )

    # 3. Orthographic & Isometric Views
    layout = add_standard_multiview(
        sheet=sheet,
        shape=part,
        scale="auto",
        spacing=25.0,
        show_hidden=True,
        auto_dimensions=True,
    )

    # 4. Annotations & Dimensions
    assert layout.front is not None
    layout.front.dim_vertical(
        model_y1=-5.5,
        model_y2=5.5,
        model_x=-25.0,
        prefix="⌀",
        tolerance="H11",
        offset_x=-15.0,
    )
    layout.front.centermark(-25.0, 0.0, size=8.0)
    layout.front.add_gdt_frame(
        model_x=10.0,
        model_y=-7.5,
        characteristic=GDTCharacteristic.FLATNESS,
        tolerance_value="0.05",
    )

    # 5. Exporting
    sheet.export_svg(str(output_dir / "stepped_bracket.svg"), theme=Theme.DARK_BLUEPRINT)
    sheet.export_svg(str(output_dir / "stepped_bracket_white.svg"), theme=Theme.ISO_WHITE)
    sheet.export_pdf(str(output_dir / "stepped_bracket.pdf"))
    sheet.export_dxf(str(output_dir / "stepped_bracket.dxf"))
    print("Generated stepped bracket outputs.")


if __name__ == "__main__":
    generate_stepped_bracket(Path(__file__).parent / "output")
