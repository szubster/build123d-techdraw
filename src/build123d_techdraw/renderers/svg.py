"""Vector SVG renderer with CSS custom variables and theme switching."""

import os
from typing import TYPE_CHECKING

from build123d_techdraw.renderers.theme import Theme

if TYPE_CHECKING:
    from build123d_techdraw.core.sheet import DrawingSheet


class SVGRenderer:
    """Renders DrawingSheet instances to interactive and print-ready SVG vector graphics."""

    @classmethod
    def render_sheet(cls, sheet: "DrawingSheet", theme: Theme = Theme.DARK_BLUEPRINT) -> str:
        """Render sheet to SVG XML string."""
        return sheet.to_svg(theme=theme)

    @classmethod
    def save(
        cls, sheet: "DrawingSheet", output_path: str, theme: Theme = Theme.DARK_BLUEPRINT
    ) -> None:
        """Save rendered sheet to an SVG file."""
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        svg_content = cls.render_sheet(sheet, theme=theme)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(svg_content)
