"""Thread-safe context management for build123d-techdraw Builder mode."""

from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from build123d_techdraw.core.sheet import DrawingSheet
    from build123d_techdraw.core.view import SheetView

CURRENT_SHEET: contextvars.ContextVar[DrawingSheet | None] = contextvars.ContextVar(
    "CURRENT_SHEET", default=None
)
CURRENT_VIEW: contextvars.ContextVar[SheetView | None] = contextvars.ContextVar(
    "CURRENT_VIEW", default=None
)
