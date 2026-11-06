"""Multi-page drawing booklets and PDF/SVG package generation."""

import os

from build123d_techdraw.core.sheet import DrawingSheet
from build123d_techdraw.renderers.pdf import PDFRenderer
from build123d_techdraw.renderers.theme import Theme


class DrawingBooklet:
    """Manages an ordered package of multiple technical drawing sheets."""

    def __init__(self, title: str = "ENGINEERING DRAWING PACKAGE"):
        self.title = title
        self.sheets: list[DrawingSheet] = []

    def add_sheet(self, sheet: DrawingSheet) -> None:
        """Add a sheet to the drawing booklet."""
        self.sheets.append(sheet)

    def export_svgs(self, output_dir: str, theme: Theme = Theme.DARK_BLUEPRINT) -> list[str]:
        """Export each sheet in the booklet as an individual SVG file."""
        os.makedirs(output_dir, exist_ok=True)
        paths: list[str] = []
        for i, s in enumerate(self.sheets):
            dwg_no = s.title_info.drawing_no.lower().replace("-", "_")
            fname = f"{i + 1:02d}_{dwg_no}.svg"
            path = os.path.join(output_dir, fname)
            s.export_svg(path, theme=theme)
            paths.append(path)
        return paths

    def export_pdf(self, output_path: str, theme: Theme = Theme.DARK_BLUEPRINT) -> None:
        """Compile all sheets into a single multi-page vector PDF file."""
        sheet_data = [
            (sheet.width_mm, sheet.height_mm, sheet.to_svg(theme)) for sheet in self.sheets
        ]
        PDFRenderer.render_sheets_to_pdf(sheet_data, output_path, theme)
