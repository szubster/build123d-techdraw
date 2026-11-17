"""Base class for objects that configure a ``DrawingSheet`` when added to it."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from build123d_techdraw.core.sheet import DrawingSheet


class SheetDirective(ABC):
    """Something ``DrawingSheet.add`` applies to the sheet instead of storing as an annotation.

    Examples: title block and notes settings, revision rows, BOMs and multiview layouts.
    """

    @abstractmethod
    def _apply_to_sheet(self, sheet: DrawingSheet) -> None:
        """Apply this directive's content to ``sheet``."""
