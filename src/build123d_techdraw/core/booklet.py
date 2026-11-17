"""Multi-page drawing booklets and PDF/SVG package generation."""

import os
from collections.abc import Iterable
from typing import Self

from build123d_techdraw.core.sheet import DrawingSheet
from build123d_techdraw.renderers.pdf import PDFRenderer
from build123d_techdraw.renderers.theme import Theme


class DrawingBooklet:
    """Manages an ordered package of multiple technical drawing sheets with auto-pagination."""

    def __init__(
        self,
        title: str = "ENGINEERING DRAWING PACKAGE",
        sheets: Iterable[DrawingSheet] | None = None,
    ):
        self.title = title
        self.sheets: list[DrawingSheet] = []
        if sheets:
            for s in sheets:
                self.add_sheet(s)

    def _paginate(self) -> None:
        """Dynamically assign sheet_no and total_sheets across all booklet sheets."""
        total = len(self.sheets)
        for idx, sheet in enumerate(self.sheets):
            sheet.title_info.sheet_no = f"{idx + 1}"
            sheet.title_info.total_sheets = f"{total}"

    def add_sheet(self, sheet: DrawingSheet) -> Self:
        """Add a sheet to the drawing booklet and update pagination."""
        self.sheets.append(sheet)
        self._paginate()
        return self

    def __iadd__(self, sheet: DrawingSheet | Iterable[DrawingSheet]) -> Self:
        if isinstance(sheet, DrawingSheet):
            return self.add_sheet(sheet)
        for s in sheet:
            self.add_sheet(s)
        return self

    def __add__(self, sheet: DrawingSheet | Iterable[DrawingSheet]) -> Self:
        return self.__iadd__(sheet)

    def export_svgs(self, output_dir: str, theme: Theme | str = Theme.DARK_BLUEPRINT) -> list[str]:
        """Export each sheet in the booklet as an individual SVG file with auto-pagination."""
        self._paginate()
        os.makedirs(output_dir, exist_ok=True)
        paths: list[str] = []
        for i, s in enumerate(self.sheets):
            dwg_no = s.title_info.drawing_no.lower().replace("-", "_")
            fname = f"{i + 1:02d}_{dwg_no}.svg"
            path = os.path.join(output_dir, fname)
            s.export_svg(path, theme=theme)
            paths.append(path)
        return paths

    def export_pdf(
        self,
        output_path: str,
        theme: Theme | str = Theme.DARK_BLUEPRINT,
        bookmarks: bool = True,
    ) -> None:
        """Compile all sheets into a single multi-page vector PDF file with auto-pagination and document outline bookmarks."""
        self._paginate()
        resolved_theme = (
            Theme.ISO_WHITE
            if (isinstance(theme, str) and theme.lower() in ("print", "white", "iso_white"))
            or theme == Theme.ISO_WHITE
            else Theme.DARK_BLUEPRINT
        )
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        sheet_data = [
            (sheet.width_mm, sheet.height_mm, sheet.to_svg(resolved_theme)) for sheet in self.sheets
        ]
        sheet_titles = []
        for i, s in enumerate(self.sheets):
            d_title = s.title_info.drawing_title
            d_no = s.title_info.drawing_no
            if d_no and d_title:
                sheet_titles.append(f"{d_no} - {d_title}")
            elif d_title:
                sheet_titles.append(d_title)
            elif d_no:
                sheet_titles.append(d_no)
            else:
                sheet_titles.append(f"Sheet {i + 1}")
        PDFRenderer.render_sheets_to_pdf(
            sheet_data,
            output_path,
            resolved_theme,
            bookmarks=bookmarks,
            booklet_title=self.title,
            sheet_titles=sheet_titles,
        )
