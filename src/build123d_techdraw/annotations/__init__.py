"""Engineering annotations: ISO dimensions, GD&T, surface finish, centerlines, tables, and callouts."""

from build123d_techdraw.annotations.anchors import (
    Anchor,
    AnchorDirection,
    Direction,
    SemanticFingerprint,
)
from build123d_techdraw.annotations.base import Annotation
from build123d_techdraw.annotations.callouts import (
    BOMBalloon,
    ChamferCallout,
    HoleCallout,
    LeaderNote,
    SectionCuttingLine,
    StepBadge,
    ThreadCallout,
    WeldingSymbol,
    WeldType,
)
from build123d_techdraw.annotations.centerlines import (
    BoltCircle,
    Centerline,
    CenterMark,
)
from build123d_techdraw.annotations.dimensions import (
    AngularDimension,
    BaselineDimensionGroup,
    ChainDimensionGroup,
    DiameterDimension,
    DimensionType,
    LinearDimension,
    OrdinateDimension,
    OrdinateDimensionGroup,
    OrdinateDimensionSet,
    RadialDimension,
    Tolerance,
    format_dimension_value,
)
from build123d_techdraw.annotations.gdt import (
    DatumFeatureSymbol,
    FeatureControlFrame,
    GDTCharacteristic,
)
from build123d_techdraw.annotations.resolver import ensure_anchor, resolve_point_3d
from build123d_techdraw.annotations.surface import (
    MaterialRemoval,
    SurfaceFinishSymbol,
)
from build123d_techdraw.annotations.symbols import render_projection_cone_svg
from build123d_techdraw.annotations.tables import Card, RawSVG, Schedule, Table, TextBox
from build123d_techdraw.annotations.threads import (
    CosmeticThread,
    ThreadSpec,
    auto_cosmetic_threads,
    find_threads_by_tap_drill,
    get_clearance_hole,
    get_tap_drill,
    get_thread_spec,
    list_standard_threads,
)

__all__ = [
    "Anchor",
    "AnchorDirection",
    "AngularDimension",
    "Annotation",
    "BOMBalloon",
    "BaselineDimensionGroup",
    "BoltCircle",
    "Card",
    "CenterMark",
    "Centerline",
    "ChainDimensionGroup",
    "ChamferCallout",
    "CosmeticThread",
    "DatumFeatureSymbol",
    "DiameterDimension",
    "DimensionType",
    "Direction",
    "FeatureControlFrame",
    "GDTCharacteristic",
    "HoleCallout",
    "LeaderNote",
    "LinearDimension",
    "MaterialRemoval",
    "OrdinateDimension",
    "OrdinateDimensionGroup",
    "OrdinateDimensionSet",
    "RadialDimension",
    "RawSVG",
    "Schedule",
    "SectionCuttingLine",
    "SemanticFingerprint",
    "StepBadge",
    "SurfaceFinishSymbol",
    "Table",
    "TextBox",
    "ThreadCallout",
    "ThreadSpec",
    "Tolerance",
    "WeldType",
    "WeldingSymbol",
    "auto_cosmetic_threads",
    "ensure_anchor",
    "find_threads_by_tap_drill",
    "format_dimension_value",
    "get_clearance_hole",
    "get_tap_drill",
    "get_thread_spec",
    "list_standard_threads",
    "render_projection_cone_svg",
    "resolve_point_3d",
]
