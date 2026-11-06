"""Core technical drawing primitives, sheets, views, and title blocks."""

from build123d_techdraw.core.booklet import DrawingBooklet
from build123d_techdraw.core.scale import (
    calculate_auto_scale,
    find_closest_iso_scale,
    format_iso_scale,
)
from build123d_techdraw.core.sheet import DrawingSheet, SheetSize
from build123d_techdraw.core.title_block import BOMItem, RevisionEntry, TitleBlockInfo
from build123d_techdraw.core.view import SheetView, ViewType

__all__ = [
    "BOMItem",
    "DrawingBooklet",
    "DrawingSheet",
    "RevisionEntry",
    "SheetSize",
    "SheetView",
    "TitleBlockInfo",
    "ViewType",
    "calculate_auto_scale",
    "find_closest_iso_scale",
    "format_iso_scale",
]
