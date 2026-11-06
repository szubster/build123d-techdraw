"""Quickstart example: Multi-view engineering drawing of an enclosure base."""

from pathlib import Path

import build123d as bd

from build123d_techdraw import DrawingSheet, SheetSize, Theme, add_standard_multiview


def generate_quickstart(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Model a 3D CAD part
    part = bd.Box(100.0, 50.0, 30.0)

    # 2. Set up an ISO A3 drawing sheet
    sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
    sheet.set_title_block(
        title="ENCLOSURE BASE",
        drawing_number="DWG-001",
        author="J. DOE",
        material="AL 6061-T6",
    )

    # 3. Add standard orthographic multi-view layout with auto-scaling
    _layout = add_standard_multiview(
        sheet=sheet,
        shape=part,
        scale="auto",  # Automatically selects standard ISO 5455 scale
        auto_dimensions=True,  # Adds overall Length, Height, and Depth dimensions
    )

    # 4. Export to interactive SVG (dark and white), print-ready vector PDF, and 1:1 DXF
    sheet.export_svg(str(output_dir / "quickstart_enclosure.svg"), theme=Theme.DARK_BLUEPRINT)
    sheet.export_svg(str(output_dir / "quickstart_enclosure_white.svg"), theme=Theme.ISO_WHITE)
    sheet.export_pdf(str(output_dir / "quickstart_enclosure.pdf"))
    sheet.export_dxf(str(output_dir / "quickstart_enclosure.dxf"))
    print("Generated quickstart enclosure outputs.")


if __name__ == "__main__":
    generate_quickstart(Path(__file__).parent / "output")
