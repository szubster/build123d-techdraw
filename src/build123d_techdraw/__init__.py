"""build123d-techdraw: 2D technical drawing and blueprint engine for build123d."""

from build123d_techdraw.core.booklet import DrawingBooklet
from build123d_techdraw.core.scale import (
    calculate_auto_scale,
    find_closest_iso_scale,
    format_iso_scale,
)
from build123d_techdraw.core.sheet import DrawingSheet, SheetSize
from build123d_techdraw.core.title_block import BOMItem, RevisionEntry, TitleBlockInfo
from build123d_techdraw.core.view import SheetView, ViewType
from build123d_techdraw.renderers.dxf import DXFRenderer
from build123d_techdraw.renderers.pdf import PDFRenderer
from build123d_techdraw.renderers.svg import SVGRenderer
from build123d_techdraw.renderers.theme import THEME_COLORS, Theme, ThemeColors

try:
    from ._version import __version__
except ImportError:  # pragma: no cover
    __version__ = "0.1.0.dev0"

__all__ = [
    "__version__",
    "BOMItem",
    "DXFRenderer",
    "DrawingBooklet",
    "DrawingSheet",
    "PDFRenderer",
    "RevisionEntry",
    "SVGRenderer",
    "SheetSize",
    "SheetView",
    "THEME_COLORS",
    "Theme",
    "ThemeColors",
    "TitleBlockInfo",
    "ViewType",
    "calculate_auto_scale",
    "find_closest_iso_scale",
    "format_iso_scale",
]
