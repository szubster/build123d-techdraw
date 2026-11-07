"""SheetView representations, view projection mathematics, and coordinate transformations."""

import os
import re
import tempfile
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional, Self

import build123d as bd

from build123d_techdraw.annotations.callouts import (
    BOMBalloon,
    HoleCallout,
    LeaderNote,
    ThreadCallout,
)
from build123d_techdraw.annotations.centerlines import BoltCircle, Centerline, CenterMark
from build123d_techdraw.annotations.dimensions import (
    DiameterDimension,
    DimensionType,
    LinearDimension,
    RadialDimension,
    Tolerance,
)
from build123d_techdraw.annotations.gdt import (
    DatumFeatureSymbol,
    FeatureControlFrame,
    GDTCharacteristic,
)
from build123d_techdraw.annotations.surface import MaterialRemoval, SurfaceFinishSymbol
from build123d_techdraw.context import CURRENT_VIEW
from build123d_techdraw.core.scale import format_iso_scale

if TYPE_CHECKING:
    from build123d_techdraw.core.sheet import DrawingSheet


class ViewType(Enum):
    """Standard technical drawing view types and projections."""

    FRONT = "front"
    TOP = "top"
    RIGHT = "right"
    LEFT = "left"
    REAR = "rear"
    BOTTOM = "bottom"
    ISOMETRIC = "isometric"
    ISOMETRIC_LEFT = "isometric_left"
    ISOMETRIC_REAR = "isometric_rear"
    SECTION = "section"


# Standard camera orientations: (look_from, look_up)
VIEW_VECTORS: dict[ViewType, tuple[tuple[float, float, float], tuple[float, float, float]]] = {
    ViewType.FRONT: ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    ViewType.TOP: ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    ViewType.BOTTOM: ((0.0, 0.0, -1.0), (0.0, -1.0, 0.0)),
    ViewType.RIGHT: ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    ViewType.LEFT: ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    ViewType.REAR: ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    ViewType.ISOMETRIC: ((1.2, 1.8, 1.2), (0.0, 0.0, 1.0)),
    ViewType.ISOMETRIC_LEFT: ((-1.2, 1.8, 1.2), (0.0, 0.0, 1.0)),
    ViewType.ISOMETRIC_REAR: ((1.2, -1.8, 1.2), (0.0, 0.0, 1.0)),
}


class SheetView:
    """Represents an orthographic, isometric, or section view placed on a DrawingSheet.

    Provides bidirectional coordinate transformations between 3D model space,
    2D view projection space, and 2D paper sheet space (mm).
    """

    def __init__(
        self,
        view_type: ViewType,
        center_x: float,
        center_y: float,
        scale: float,
        width: float,
        height: float,
        model_origin: tuple[float, float] = (0.0, 0.0),
        projection_origin: bd.Vector | None = None,
        proj_x: bd.Vector | None = None,
        proj_y: bd.Vector | None = None,
        orig_cx: float = 0.0,
        orig_cy: float = 0.0,
        svg_content: str = "",
        view_label: str | None = None,
        view_label_pos: tuple[float, float] | None = None,
        scale_label: str | None = None,
        sheet: Optional["DrawingSheet"] = None,
        shape: Any | None = None,
    ):
        self.sheet = sheet
        self.shape = shape
        self.view_type = view_type
        self.center_x = center_x
        self.center_y = center_y
        self.scale = scale
        self.width = width
        self.height = height
        self.min_x = center_x - width / 2.0
        self.max_x = center_x + width / 2.0
        self.min_y = center_y - height / 2.0
        self.max_y = center_y + height / 2.0

        self.model_origin = model_origin
        self.origin_x = model_origin[0]
        self.origin_y = model_origin[1]

        self.projection_origin = projection_origin
        self.proj_x = proj_x
        self.proj_y = proj_y
        self.orig_cx = orig_cx
        self.orig_cy = orig_cy

        self.svg_content = svg_content
        self.inner_svg_content: str = ""
        self.rotation_angle_deg: float = 0.0
        self.view_label = view_label
        self.view_label_pos = view_label_pos
        self.scale_label = scale_label
        self.bottom_annotation_y: float = self.max_y
        self.annotations: list[Any] = []
        self._ctx_token: Any = None

    def __enter__(self) -> Self:
        self._ctx_token = CURRENT_VIEW.set(self)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        if self._ctx_token is not None:
            CURRENT_VIEW.reset(self._ctx_token)
            self._ctx_token = None

    def __iadd__(self, item: Any) -> Self:
        return self.add(item)

    def __add__(self, item: Any) -> Self:
        return self.add(item)

    def add(self, item: Any) -> Self:
        """Attach an annotation or collection of annotations to this view."""
        if isinstance(item, (list, tuple, set)):
            for sub_item in item:
                self.add(sub_item)
            return self

        if hasattr(item, "_attach_to_view"):
            item._attach_to_view(self)

        if item not in self.annotations:
            self.annotations.append(item)

        if self.sheet is not None and item not in self.sheet.annotations:
            self.sheet.annotations.append(item)

        return self

    add_annotation = add

    def _update_transform(self) -> None:
        """Regenerate the view's SVG transform after moving or scaling."""
        if not self.inner_svg_content:
            return
        rot_str = (
            f" rotate({self.rotation_angle_deg:.1f}, {self.orig_cx:.2f}, {self.orig_cy:.2f})"
            if self.rotation_angle_deg != 0.0
            else ""
        )
        transform = (
            f"translate({self.center_x:.2f}, {self.center_y:.2f}) "
            f"scale({self.scale:.4f}, {self.scale:.4f}){rot_str} "
            f"translate({-self.orig_cx:.2f}, {-self.orig_cy:.2f})"
        )
        view_id = f"view_{self.view_type.value}_{int(self.center_x)}_{int(self.center_y)}"
        self.svg_content = f"""
    <!-- View: {self.view_type.value.upper()} at ({self.center_x}, {self.center_y}) scale={self.scale} -->
    <g id="{view_id}" class="sheet-view" transform="{transform}">
        {self.inner_svg_content}
    </g>
    """

    def locate(self, center: tuple[float, float]) -> Self:
        """Reposition the view center on the sheet (in paper mm)."""
        dx = float(center[0]) - self.center_x
        dy = float(center[1]) - self.center_y
        self.center_x = float(center[0])
        self.center_y = float(center[1])
        self.min_x = self.center_x - self.width / 2.0
        self.max_x = self.center_x + self.width / 2.0
        self.min_y = self.center_y - self.height / 2.0
        self.max_y = self.center_y + self.height / 2.0
        self.bottom_annotation_y = self.max_y
        if self.view_label_pos is not None:
            self.view_label_pos = (self.view_label_pos[0] + dx, self.view_label_pos[1] + dy)
        self._update_transform()
        return self

    def scale_to(self, scale: float) -> Self:
        """Change the view scale factor and update its bounding box."""
        if self.scale != 0.0:
            ratio = scale / self.scale
            self.width *= ratio
            self.height *= ratio
        self.scale = scale
        self.min_x = self.center_x - self.width / 2.0
        self.max_x = self.center_x + self.width / 2.0
        self.min_y = self.center_y - self.height / 2.0
        self.max_y = self.center_y + self.height / 2.0
        self.bottom_annotation_y = self.max_y
        self.scale_label = format_iso_scale(scale)
        self._update_transform()
        return self

    def align_to(
        self,
        other: "SheetView | tuple[Any, ...]",
        direction: str = "right",
        spacing: float = 25.0,
    ) -> Self:
        if isinstance(other, (tuple, list, dict)):
            _apply_align_to(self, other)
            return self
        """Align this view relative to another view on the sheet."""
        d = direction.lower()
        if d in ("right", "east"):
            new_cx = other.max_x + spacing + self.width / 2.0
            new_cy = other.center_y
        elif d in ("left", "west"):
            new_cx = other.min_x - spacing - self.width / 2.0
            new_cy = other.center_y
        elif d in ("above", "top", "north"):
            new_cx = other.center_x
            new_cy = other.min_y - spacing - self.height / 2.0
        elif d in ("below", "bottom", "south"):
            new_cx = other.center_x
            new_cy = other.max_y + spacing + self.height / 2.0
        else:
            raise ValueError(
                f"Unknown alignment direction '{direction}'. Use 'above', 'below', 'right', or 'left'."
            )
        return self.locate((new_cx, new_cy))

    @property
    def center(self) -> tuple[float, float]:
        """View center on the drawing sheet in paper mm."""
        return (self.center_x, self.center_y)

    def __getitem__(self, key: str) -> Any:
        """Provide dictionary-style access for backward compatibility."""
        attrs = {
            "view_type": self.view_type,
            "center_x": self.center_x,
            "center_y": self.center_y,
            "scale": self.scale,
            "width": self.width,
            "height": self.height,
            "min_x": self.min_x,
            "max_x": self.max_x,
            "min_y": self.min_y,
            "max_y": self.max_y,
            "origin_x": self.origin_x,
            "origin_y": self.origin_y,
        }
        if key in attrs:
            return attrs[key]
        raise KeyError(key)

    def to_sheet_x(self, model_x: float) -> float:
        """Transform 2D model X coordinate to sheet paper coordinate (mm)."""
        return self.center_x + ((model_x - self.origin_x) * self.scale)

    def to_sheet_y(self, model_y: float) -> float:
        """Transform 2D model Y coordinate to sheet paper coordinate (mm)."""
        return self.center_y - ((model_y - self.origin_y) * self.scale)

    def to_sheet_coords(self, model_x: float, model_y: float) -> tuple[float, float]:
        """Transform 2D model (X, Y) to sheet paper (X, Y) coordinates (mm)."""
        return (self.to_sheet_x(model_x), self.to_sheet_y(model_y))

    def to_sheet_3d(self, x: float, y: float, z: float) -> tuple[float, float]:
        """Project a 3D part point (X, Y, Z) to 2D sheet paper coordinate space (mm)."""
        if self.projection_origin is None or self.proj_x is None or self.proj_y is None:
            return self.to_sheet_coords(x, y)
        p = bd.Vector(x, y, z) - self.projection_origin
        x2d = p.dot(self.proj_x)
        y2d = p.dot(self.proj_y)
        svg_x = x2d
        svg_y = -y2d
        sx = self.center_x + (svg_x - self.orig_cx) * self.scale
        sy = self.center_y + (svg_y - self.orig_cy) * self.scale
        return (sx, sy)

    def dim_horizontal(
        self,
        model_x1: float,
        model_x2: float,
        model_y: float,
        offset_y: float = -8.0,
        text: str | None = None,
        tolerance: str | None = None,
        prefix: str = "",
        suffix: str = "",
    ) -> LinearDimension:
        """Add a horizontal dimension between model_x1 and model_x2 at model_y."""
        sx1 = self.to_sheet_x(model_x1)
        sx2 = self.to_sheet_x(model_x2)
        sy = self.to_sheet_y(model_y)
        dim = LinearDimension(
            start=(sx1, sy),
            end=(sx2, sy),
            offset=offset_y,
            dim_type=DimensionType.HORIZONTAL,
            text=text,
            tolerance=tolerance,
            prefix=prefix,
            suffix=suffix,
        )
        if self.sheet is not None:
            self.sheet.annotations.append(dim)
        if offset_y > 0:
            self.bottom_annotation_y = max(self.bottom_annotation_y, sy + offset_y + 4.0)
        return dim

    def dim_vertical(
        self,
        model_y1: float,
        model_y2: float,
        model_x: float,
        offset_x: float = -10.0,
        text: str | None = None,
        tolerance: str | None = None,
        prefix: str = "",
        suffix: str = "",
    ) -> LinearDimension:
        """Add a vertical dimension between model_y1 and model_y2 at model_x."""
        sy1 = self.to_sheet_y(model_y1)
        sy2 = self.to_sheet_y(model_y2)
        sx = self.to_sheet_x(model_x)
        dim = LinearDimension(
            start=(sx, sy1),
            end=(sx, sy2),
            offset=offset_x,
            dim_type=DimensionType.VERTICAL,
            text=text,
            tolerance=tolerance,
            prefix=prefix,
            suffix=suffix,
        )
        if self.sheet is not None:
            self.sheet.annotations.append(dim)
        return dim

    def dim_aligned(
        self,
        p1_model: tuple[float, float],
        p2_model: tuple[float, float],
        offset: float = 8.0,
        text: str | None = None,
        tolerance: str | None = None,
        prefix: str = "",
        suffix: str = "",
    ) -> LinearDimension:
        """Add an aligned dimension between two model points."""
        s1 = self.to_sheet_coords(p1_model[0], p1_model[1])
        s2 = self.to_sheet_coords(p2_model[0], p2_model[1])
        dim = LinearDimension(
            start=s1,
            end=s2,
            offset=offset,
            dim_type=DimensionType.ALIGNED,
            text=text,
            tolerance=tolerance,
            prefix=prefix,
            suffix=suffix,
        )
        if self.sheet is not None:
            self.sheet.annotations.append(dim)
        return dim

    def add_overall_horizontal(
        self,
        offset_y: float = -8.0,
        text: str | None = None,
        tolerance: str | None = None,
        prefix: str = "",
        suffix: str = "",
    ) -> LinearDimension:
        """Add an overall horizontal dimension spanning view's bounding box."""
        y_ref = self.min_y if offset_y < 0 else self.max_y
        dim = LinearDimension(
            start=(self.min_x, y_ref),
            end=(self.max_x, y_ref),
            offset=offset_y,
            dim_type=DimensionType.HORIZONTAL,
            text=text,
            tolerance=tolerance,
            prefix=prefix,
            suffix=suffix,
        )
        if self.sheet is not None:
            self.sheet.annotations.append(dim)
        if offset_y > 0:
            self.bottom_annotation_y = max(self.bottom_annotation_y, y_ref + offset_y + 4.0)
        return dim

    def add_overall_vertical(
        self,
        offset_x: float = -10.0,
        text: str | None = None,
        tolerance: str | None = None,
        prefix: str = "",
        suffix: str = "",
    ) -> LinearDimension:
        """Add an overall vertical dimension spanning view's bounding box."""
        x_ref = self.min_x if offset_x < 0 else self.max_x
        dim = LinearDimension(
            start=(x_ref, self.min_y),
            end=(x_ref, self.max_y),
            offset=offset_x,
            dim_type=DimensionType.VERTICAL,
            text=text,
            tolerance=tolerance,
            prefix=prefix,
            suffix=suffix,
        )
        if self.sheet is not None:
            self.sheet.annotations.append(dim)
        return dim

    def dim_radial(
        self,
        center_model: tuple[float, float],
        radius_model: float,
        angle_deg: float = 45.0,
        text: str | None = None,
        tolerance: str | None = None,
    ) -> RadialDimension:
        """Add a radial dimension pointing to an arc defined in model space."""
        cx, cy = self.to_sheet_coords(center_model[0], center_model[1])
        dim = RadialDimension(
            center=(cx, cy),
            radius=radius_model * self.scale,
            angle_deg=angle_deg,
            text=text,
            tolerance=tolerance,
        )
        if self.sheet is not None:
            self.sheet.annotations.append(dim)
        return dim

    def dim_diameter(
        self,
        center_model: tuple[float, float],
        diameter_model: float,
        angle_deg: float = 45.0,
        text: str | None = None,
        tolerance: str | None = None,
    ) -> DiameterDimension:
        """Add a diameter dimension across a hole/cylinder defined in model space."""
        cx, cy = self.to_sheet_coords(center_model[0], center_model[1])
        dim = DiameterDimension(
            center=(cx, cy),
            diameter=diameter_model * self.scale,
            angle_deg=angle_deg,
            text=text,
            tolerance=tolerance,
        )
        if self.sheet is not None:
            self.sheet.annotations.append(dim)
        return dim

    def centermark(
        self,
        model_x: float,
        model_y: float,
        size: float = 6.0,
    ) -> CenterMark:
        """Add a centermark crosshair at (model_x, model_y)."""
        sc = self.to_sheet_coords(model_x, model_y)
        cm = CenterMark(center=sc, size=size)
        if self.sheet is not None:
            self.sheet.annotations.append(cm)
        return cm

    def centerline_horizontal(
        self,
        model_y: float = 0.0,
        model_x1: float | None = None,
        model_x2: float | None = None,
        overhang: float = 4.0,
    ) -> Centerline:
        """Draw an ISO centerline across the horizontal axis at model_y."""
        sy = self.to_sheet_y(model_y)
        sx1 = (
            self.to_sheet_x(model_x1) - overhang if model_x1 is not None else self.min_x - overhang
        )
        sx2 = (
            self.to_sheet_x(model_x2) + overhang if model_x2 is not None else self.max_x + overhang
        )
        cl = Centerline(start=(sx1, sy), end=(sx2, sy))
        if self.sheet is not None:
            self.sheet.annotations.append(cl)
        return cl

    def centerline_vertical(
        self,
        model_x: float = 0.0,
        model_y1: float | None = None,
        model_y2: float | None = None,
        overhang: float = 4.0,
    ) -> Centerline:
        """Draw an ISO centerline across the vertical axis at model_x."""
        sx = self.to_sheet_x(model_x)
        sy1 = (
            self.to_sheet_y(model_y1) + overhang if model_y1 is not None else self.max_y + overhang
        )
        sy2 = (
            self.to_sheet_y(model_y2) - overhang if model_y2 is not None else self.min_y - overhang
        )
        cl = Centerline(start=(sx, min(sy1, sy2)), end=(sx, max(sy1, sy2)))
        if self.sheet is not None:
            self.sheet.annotations.append(cl)
        return cl

    def bolt_circle(
        self,
        center_model: tuple[float, float] = (0.0, 0.0),
        diameter_model: float = 50.0,
        num_holes: int = 4,
        start_angle_deg: float = 0.0,
    ) -> BoltCircle:
        """Add a bolt pitch circle diameter (PCD) annotation in model space."""
        sc = self.to_sheet_coords(center_model[0], center_model[1])
        bc = BoltCircle(
            center=sc,
            diameter=diameter_model * self.scale,
            num_holes=num_holes,
            start_angle_deg=start_angle_deg,
        )
        if self.sheet is not None:
            self.sheet.annotations.append(bc)
        return bc

    def leader(
        self,
        model_x: float,
        model_y: float,
        lines: list[str] | str,
        elbow_dx: float = 15.0,
        elbow_dy: float = -12.0,
        shelf_length: float = 20.0,
        align_right: bool | None = None,
        terminator: str = "dot",
    ) -> LeaderNote:
        """Add a leader note pointing to (model_x, model_y) on the part."""
        target = self.to_sheet_coords(model_x, model_y)
        line_list = [lines] if isinstance(lines, str) else list(lines)
        note = LeaderNote(
            target=target,
            lines=line_list,
            elbow_offset=(elbow_dx, elbow_dy),
            shelf_length=shelf_length,
            align_right=align_right,
            terminator=terminator,
        )
        if self.sheet is not None:
            self.sheet.annotations.append(note)
        return note

    def leader_3d(
        self,
        x: float,
        y: float,
        z: float,
        lines: list[str] | str,
        elbow_dx: float = 15.0,
        elbow_dy: float = -12.0,
        shelf_length: float = 20.0,
        align_right: bool | None = None,
        terminator: str = "dot",
    ) -> LeaderNote:
        """Add a leader note pointing to a 3D part point (x, y, z)."""
        target = self.to_sheet_3d(x, y, z)
        line_list = [lines] if isinstance(lines, str) else list(lines)
        note = LeaderNote(
            target=target,
            lines=line_list,
            elbow_offset=(elbow_dx, elbow_dy),
            shelf_length=shelf_length,
            align_right=align_right,
            terminator=terminator,
        )
        if self.sheet is not None:
            self.sheet.annotations.append(note)
        return note

    def balloon(
        self,
        item_no: int,
        model_x: float,
        model_y: float,
        balloon_x: float,
        balloon_y: float,
    ) -> BOMBalloon:
        """Add a BOM balloon callout pointing to (model_x, model_y)."""
        target = self.to_sheet_coords(model_x, model_y)
        balloon = BOMBalloon(
            item_no=item_no,
            target=target,
            balloon_center=(balloon_x, balloon_y),
        )
        if self.sheet is not None:
            self.sheet.annotations.append(balloon)
        return balloon

    def add_gdt_frame(
        self,
        model_x: float,
        model_y: float,
        characteristic: GDTCharacteristic,
        tolerance_value: str,
        datum_a: str | None = None,
        datum_b: str | None = None,
        datum_c: str | None = None,
        leader_offset: tuple[float, float] | None = None,
    ) -> FeatureControlFrame:
        """Add a GD&T Feature Control Frame near (model_x, model_y), optionally with a leader line."""
        target_pos = self.to_sheet_coords(model_x, model_y)
        if leader_offset is not None:
            pos = (target_pos[0] + leader_offset[0], target_pos[1] + leader_offset[1])
            leader_target: tuple[float, float] | None = target_pos
        else:
            pos = target_pos
            leader_target = None

        fcf = FeatureControlFrame(
            position=pos,
            characteristic=characteristic,
            tolerance_value=tolerance_value,
            datum_a=datum_a,
            datum_b=datum_b,
            datum_c=datum_c,
            leader_target=leader_target,
        )
        if self.sheet is not None:
            self.sheet.annotations.append(fcf)
        return fcf

    def add_datum_symbol(
        self,
        model_x: float,
        model_y: float,
        label: str = "A",
        direction: str = "up",
    ) -> DatumFeatureSymbol:
        """Add an ISO datum feature identifier symbol at (model_x, model_y)."""
        target = self.to_sheet_coords(model_x, model_y)
        sym = DatumFeatureSymbol(target=target, label=label, direction=direction)
        if self.sheet is not None:
            self.sheet.annotations.append(sym)
        return sym

    def add_surface_finish(
        self,
        model_x: float,
        model_y: float,
        ra_value: str = "Ra 3.2",
        material_removal: MaterialRemoval = MaterialRemoval.REQUIRED,
        process: str | None = None,
    ) -> SurfaceFinishSymbol:
        """Add an ISO 1302 surface roughness symbol at (model_x, model_y)."""
        pos = self.to_sheet_coords(model_x, model_y)
        sym = SurfaceFinishSymbol(
            position=pos,
            ra_value=ra_value,
            material_removal=material_removal,
            process=process,
        )
        if self.sheet is not None:
            self.sheet.annotations.append(sym)
        return sym

    def hole_callout(
        self,
        model_x: float,
        model_y: float,
        diameter: float,
        depth: float | str | None = None,
        countersink_dia: float | None = None,
        countersink_angle: float = 90.0,
        counterbore_dia: float | None = None,
        counterbore_depth: float | None = None,
        quantity: int | None = None,
        tolerance: str | Tolerance | None = None,
        elbow_dx: float = 15.0,
        elbow_dy: float = -12.0,
    ) -> HoleCallout:
        """Add a standard engineering hole callout at (model_x, model_y)."""
        target = self.to_sheet_coords(model_x, model_y)
        hc = HoleCallout(
            center=target,
            diameter=diameter,
            depth=depth,
            countersink_dia=countersink_dia,
            countersink_angle=countersink_angle,
            counterbore_dia=counterbore_dia,
            counterbore_depth=counterbore_depth,
            quantity=quantity,
            tolerance=str(tolerance) if tolerance is not None else None,
            elbow_offset=(elbow_dx, elbow_dy),
            view=self,
        )
        return hc

    def thread_callout(
        self,
        model_x: float,
        model_y: float,
        thread: str = "M5x0.8",
        depth: float | str | None = "THRU",
        class_fit: str | None = None,
        quantity: int | None = None,
        elbow_dx: float = 15.0,
        elbow_dy: float = -12.0,
    ) -> ThreadCallout:
        """Add a standard engineering thread callout at (model_x, model_y)."""
        target = self.to_sheet_coords(model_x, model_y)
        tc = ThreadCallout(
            center=target,
            thread=thread,
            depth=depth,
            class_fit=class_fit,
            quantity=quantity,
            elbow_offset=(elbow_dx, elbow_dy),
            view=self,
        )
        return tc


def _apply_align_to(view: Any, align_spec: Any) -> Any:
    """Apply an align_to specification (tuple, dict, or SheetView) to a view."""
    if align_spec is None:
        return view
    if isinstance(align_spec, SheetView):
        return view.align_to(align_spec)
    if isinstance(align_spec, (tuple, list)):
        if len(align_spec) == 1:
            return view.align_to(align_spec[0])
        if len(align_spec) == 2:
            return view.align_to(align_spec[0], direction=str(align_spec[1]))
        if len(align_spec) >= 3:
            return view.align_to(
                align_spec[0], direction=str(align_spec[1]), spacing=float(align_spec[2])
            )
    if isinstance(align_spec, dict):
        return view.align_to(
            align_spec["other"],
            direction=str(align_spec.get("direction", "right")),
            spacing=float(align_spec.get("spacing", 25.0)),
        )
    raise TypeError(
        "Invalid align_to specification. Expected (other_view, direction[, spacing]) or SheetView."
    )


def project_shape_to_view(
    shape: bd.Solid | bd.Compound | Any,
    view_type: ViewType,
    center_x: float,
    center_y: float,
    scale: float = 1.0,
    show_hidden: bool = True,
    view_label: str | None = None,
    rotation_angle_deg: float = 0.0,
    view_label_y: float | None = None,
    view_label_offset_y: float = 7.0,
    model_origin: tuple[float, float] | None = None,
) -> SheetView:
    """Project a 3D build123d shape into a 2D orthographic or isometric SheetView."""
    look_from, look_up = VIEW_VECTORS.get(view_type, ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)))

    # Use build123d.Drawing to project B-Rep edges
    dwg = bd.Drawing(shape, look_from=look_from, look_up=look_up, with_hidden=show_hidden)

    exporter = bd.ExportSVG(unit=bd.Unit.MM)

    # Filter out hidden lines that coincide with visible edges and render hidden layer first
    if show_hidden and len(dwg.hidden_lines.edges()) > 0:
        v_edges = dwg.visible_lines.edges()
        clean_hidden_edges: list[Any] = []
        for h_edge in dwg.hidden_lines.edges():
            h_bb = h_edge.bounding_box()
            coincides = False
            for v_edge in v_edges:
                v_bb = v_edge.bounding_box()
                if (
                    abs(h_bb.min.X - v_bb.min.X) < 1e-2
                    and abs(h_bb.max.X - v_bb.max.X) < 1e-2
                    and abs(h_bb.min.Y - v_bb.min.Y) < 1e-2
                    and abs(h_bb.max.Y - v_bb.max.Y) < 1e-2
                ):
                    coincides = True
                    break
            if not coincides:
                clean_hidden_edges.append(h_edge)

        if clean_hidden_edges:
            clean_hidden_compound = (
                clean_hidden_edges[0]
                if len(clean_hidden_edges) == 1
                else bd.Compound(children=clean_hidden_edges)
            )
            exporter.add_layer(
                "hidden", line_color="gray", line_weight=0.18, line_type=bd.LineType.DASHED
            )
            exporter.add_shape(clean_hidden_compound, layer="hidden")

    # Visible lines added AFTER hidden lines so visible lines render ON TOP in SVG DOM
    exporter.add_layer("visible", line_color="black", line_weight=0.35)
    exporter.add_shape(dwg.visible_lines, layer="visible")

    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as tf:
        tmp_path = tf.name

    try:
        exporter.write(tmp_path)
        with open(tmp_path, encoding="utf-8") as f:
            raw_svg = f.read()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    vb_m = re.search(r'viewBox="([^"]+)"', raw_svg)
    viewbox_str = vb_m.group(1) if vb_m else "0 0 100 100"
    vb_parts = [float(p) for p in viewbox_str.split()]
    vb_min_x, vb_min_y, vb_w, vb_h = vb_parts

    orig_cx = vb_min_x + vb_w / 2.0
    orig_cy = vb_min_y + vb_h / 2.0

    view_id = f"view_{view_type.value}_{int(center_x)}_{int(center_y)}"
    inner_m = re.search(r"<svg[^>]*>(.*)</svg>", raw_svg, flags=re.DOTALL)
    inner_content = inner_m.group(1).strip() if inner_m else raw_svg

    # Clean XML namespaces
    inner_content = re.sub(
        r"</?[a-zA-Z0-9_]+:",
        lambda m: "</" if m.group(0).startswith("</") else "<",
        inner_content,
    )
    inner_content = re.sub(r'\s+xmlns(?::[a-zA-Z0-9_]+)?="[^"]*"', "", inner_content)

    # Substitute styling with CSS custom properties
    inner_content = inner_content.replace('stroke="rgb(0,0,0)"', 'stroke="var(--line-visible)"')
    inner_content = inner_content.replace(
        'stroke="rgb(128,128,128)"', 'stroke="var(--line-hidden)"'
    )
    inner_content = inner_content.replace('id="visible"', 'id="layer_visible" class="line-visible"')
    inner_content = inner_content.replace('id="hidden"', 'id="layer_hidden" class="line-hidden"')

    # Compute exact geometric bounds from projected 2D geometry (pure CAD coordinates, no stroke inflation)
    geom_shapes: list[Any] = [dwg.visible_lines]
    if show_hidden and len(dwg.hidden_lines.edges()) > 0:
        geom_shapes.append(dwg.hidden_lines)

    try:
        combined_geom = (
            geom_shapes[0] if len(geom_shapes) == 1 else bd.Compound(children=geom_shapes)
        )
        geom_bb = combined_geom.bounding_box()
        exact_w = float(geom_bb.size.X)
        exact_h = float(geom_bb.size.Y)
    except Exception:
        exact_w = max(0.0, vb_w - 0.35)
        exact_h = max(0.0, vb_h - 0.35)

    rot_str = f" rotate({rotation_angle_deg})" if rotation_angle_deg != 0 else ""

    transform = (
        f"translate({center_x:.2f}, {center_y:.2f}) "
        f"scale({scale:.4f}, {scale:.4f}){rot_str} "
        f"translate({-orig_cx:.2f}, {-orig_cy:.2f})"
    )

    svg_view_group = f"""
    <!-- View: {view_type.value.upper()} at ({center_x}, {center_y}) scale={scale} -->
    <g id="{view_id}" class="sheet-view" transform="{transform}">
        {inner_content}
    </g>
    """

    lbl_pos = None
    scale_str = None
    if view_label:
        lbl_y = (
            view_label_y
            if view_label_y is not None
            else (center_y + (vb_h * scale / 2.0) + view_label_offset_y)
        )
        scale_str = format_iso_scale(scale)
        lbl_pos = (center_x, lbl_y)

    if model_origin is not None:
        orig_u, orig_v = model_origin
    else:
        try:
            bb = shape.bounding_box()
            bcx = (bb.min.X + bb.max.X) / 2.0
            bcy = (bb.min.Y + bb.max.Y) / 2.0
            bcz = (bb.min.Z + bb.max.Z) / 2.0
            if view_type == ViewType.FRONT:
                orig_u, orig_v = bcx, bcz
            elif view_type == ViewType.TOP:
                orig_u, orig_v = bcx, bcy
            elif view_type == ViewType.RIGHT:
                orig_u, orig_v = bcy, bcz
            elif view_type == ViewType.LEFT:
                orig_u, orig_v = -bcy, bcz
            elif view_type == ViewType.BOTTOM:
                orig_u, orig_v = bcx, -bcy
            elif view_type == ViewType.REAR:
                orig_u, orig_v = -bcx, bcz
            else:
                orig_u, orig_v = 0.0, 0.0
        except Exception:
            orig_u, orig_v = 0.0, 0.0

    try:
        proj_origin = shape.center()
        p_dir = bd.Vector(look_from).normalized()
        p_x = bd.Vector(look_up).normalized().cross(p_dir)
        p_y = p_dir.cross(p_x)
    except Exception:
        proj_origin = None
        p_x = None
        p_y = None

    view_res = SheetView(
        view_type=view_type,
        center_x=center_x,
        center_y=center_y,
        scale=scale,
        width=exact_w * scale,
        height=exact_h * scale,
        model_origin=(orig_u, orig_v),
        projection_origin=proj_origin,
        proj_x=p_x,
        proj_y=p_y,
        orig_cx=orig_cx,
        orig_cy=orig_cy,
        svg_content=svg_view_group,
        view_label=view_label,
        view_label_pos=lbl_pos,
        scale_label=scale_str,
        shape=shape,
    )
    view_res.inner_svg_content = inner_content
    view_res.rotation_angle_deg = rotation_angle_deg
    return view_res


def project_section_to_view(
    shape: bd.Solid | bd.Compound | Any,
    cutting_plane: bd.Plane,
    center_x: float,
    center_y: float,
    scale: float = 1.0,
    view_label: str = "SECTION A-A",
    view_label_y: float | None = None,
    view_label_offset_y: float = 7.0,
    model_origin: tuple[float, float] | None = None,
) -> SheetView:
    """Project a 2D section slice through 3D shape with 45-degree engineering cross-hatching."""
    sec = bd.section(shape, section_by=cutting_plane)
    sec_2d = cutting_plane.to_local_coords(sec)

    exporter = bd.ExportSVG(unit=bd.Unit.MM)
    exporter.add_layer("visible", line_color="black", line_weight=0.5, fill_color=None)
    exporter.add_shape(sec_2d, layer="visible")

    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as tf:
        tmp_path = tf.name

    try:
        exporter.write(tmp_path)
        with open(tmp_path, encoding="utf-8") as f:
            raw_svg = f.read()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    vb_m = re.search(r'viewBox="([^"]+)"', raw_svg)
    viewbox_str = vb_m.group(1) if vb_m else "0 0 100 100"
    vb_parts = [float(p) for p in viewbox_str.split()]
    vb_min_x, vb_min_y, vb_w, vb_h = vb_parts

    orig_cx = vb_min_x + vb_w / 2.0
    orig_cy = vb_min_y + vb_h / 2.0

    view_id = f"view_section_{int(center_x)}_{int(center_y)}"
    inner_m = re.search(r"<svg[^>]*>(.*)</svg>", raw_svg, flags=re.DOTALL)
    inner_content = inner_m.group(1).strip() if inner_m else raw_svg

    inner_content = re.sub(
        r"</?[a-zA-Z0-9_]+:",
        lambda m: "</" if m.group(0).startswith("</") else "<",
        inner_content,
    )
    inner_content = re.sub(r'\s+xmlns(?::[a-zA-Z0-9_]+)?="[^"]*"', "", inner_content)
    inner_content = inner_content.replace('stroke="rgb(0,0,0)"', 'stroke="var(--line-visible)"')
    inner_content = inner_content.replace('fill="none"', 'fill="url(#sectionHatch)"')

    try:
        geom_bb = sec_2d.bounding_box()
        exact_w = float(geom_bb.size.X)
        exact_h = float(geom_bb.size.Y)
    except Exception:
        exact_w = max(0.0, vb_w - 0.35)
        exact_h = max(0.0, vb_h - 0.35)

    transform = (
        f"translate({center_x:.2f}, {center_y:.2f}) "
        f"scale({scale:.4f}, {scale:.4f}) "
        f"translate({-orig_cx:.2f}, {-orig_cy:.2f})"
    )

    svg_view_group = f"""
    <!-- View: SECTION at ({center_x}, {center_y}) scale={scale} -->
    <g id="{view_id}" class="sheet-view" transform="{transform}">
        {inner_content}
    </g>
    """

    lbl_y = (
        view_label_y
        if view_label_y is not None
        else (center_y + (vb_h * scale / 2.0) + view_label_offset_y)
    )
    scale_str = format_iso_scale(scale)

    orig_u, orig_v = model_origin if model_origin is not None else (0.0, 0.0)

    view_res = SheetView(
        view_type=ViewType.SECTION,
        center_x=center_x,
        center_y=center_y,
        scale=scale,
        width=exact_w * scale,
        height=exact_h * scale,
        model_origin=(orig_u, orig_v),
        orig_cx=orig_cx,
        orig_cy=orig_cy,
        svg_content=svg_view_group,
        view_label=view_label,
        view_label_pos=(center_x, lbl_y),
        scale_label=scale_str,
        shape=shape,
    )
    view_res.inner_svg_content = inner_content
    return view_res
