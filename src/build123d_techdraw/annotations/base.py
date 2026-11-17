"""Common base class for every annotation placed on a ``SheetView`` or ``DrawingSheet``."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from build123d_techdraw.context import CURRENT_SHEET

if TYPE_CHECKING:
    from build123d_techdraw.core.sheet import DrawingSheet
    from build123d_techdraw.core.view import SheetView

BBox2D = tuple[float, float, float, float]
"""Axis-aligned paper-space bounding box ``(min_x, min_y, max_x, max_y)`` in mm."""


class Annotation(ABC):
    """Paper-space annotation rendered on top of a drawing sheet.

    Subclasses implement :meth:`to_svg` and usually override :meth:`_attach_to_view` to resolve
    anchors against the view before calling :meth:`_register`. Annotations that bind to 3D
    topology override :meth:`_rebind_to_view` so ``SheetView.update_shape`` can re-resolve them,
    and every annotation the linter should see overrides :meth:`bounding_box_2d`.
    """

    view: SheetView | None = None
    anchor_stale: bool = False
    stale_reason: str | None = None

    @abstractmethod
    def to_svg(self) -> str:
        """Render the annotation as an SVG fragment in sheet (paper mm) coordinates."""

    def bounding_box_2d(self) -> BBox2D | None:
        """Approximate paper-space bounds for the sheet linter, or ``None`` if not tracked."""
        return None

    def _attach_to_view(self, view: SheetView) -> None:
        """Bind to ``view`` (resolving anchors where applicable) and register on it."""
        self.view = view
        self._register(view)

    def _rebind_to_view(self, view: SheetView) -> None:  # noqa: ARG002
        """Re-resolve against ``view`` after its shape changed. No-op for unanchored annotations."""
        return

    def _attach_to_sheet(self, sheet: DrawingSheet) -> None:  # noqa: ARG002
        """Hook called when the annotation is added to ``sheet``'s annotation list."""
        return

    def _register(self, view: SheetView) -> None:
        """Append to ``view.annotations`` and to the owning (or current) sheet's annotations."""
        if self not in view.annotations:
            view.annotations.append(self)
        sheet = view.sheet or CURRENT_SHEET.get()
        if sheet is not None and self not in sheet.annotations:
            sheet.annotations.append(self)
            self._attach_to_sheet(sheet)
