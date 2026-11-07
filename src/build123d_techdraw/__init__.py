"""build123d-techdraw: 2D technical drawing and blueprint engine for build123d."""

from build123d_techdraw.annotations.callouts import (
    BOMBalloon,
    HoleCallout,
    LeaderNote,
    SectionCuttingLine,
    ThreadCallout,
)
from build123d_techdraw.annotations.centerlines import (
    BoltCircle,
    Centerline,
    CenterMark,
)
from build123d_techdraw.annotations.dimensions import (
    DiameterDimension,
    DimensionType,
    LinearDimension,
    RadialDimension,
    Tolerance,
    format_dimension_value,
)
from build123d_techdraw.annotations.gdt import (
    DatumFeature,
    DatumFeatureSymbol,
    FeatureControlFrame,
    GDTCharacteristic,
)
from build123d_techdraw.annotations.surface import (
    MaterialRemoval,
    SurfaceFinish,
    SurfaceFinishSymbol,
)
from build123d_techdraw.annotations.symbols import render_projection_cone_svg
from build123d_techdraw.annotations.tables import RawSVG, Schedule, Table
from build123d_techdraw.builder import (
    AssemblyBOM,
    BottomView,
    BuildDrawing,
    BuildSheet,
    BuildView,
    FrontView,
    GeneralNotes,
    IsometricView,
    LeftView,
    RearView,
    Revision,
    RightView,
    SectionView,
    StandardMultiview,
    TitleBlock,
    TopView,
    View,
)
from build123d_techdraw.core.booklet import DrawingBooklet
from build123d_techdraw.core.scale import (
    calculate_auto_scale,
    find_closest_iso_scale,
    format_iso_scale,
)
from build123d_techdraw.core.sheet import DrawingSheet, SheetSize
from build123d_techdraw.core.title_block import BOMItem, RevisionEntry, TitleBlockInfo
from build123d_techdraw.core.view import SheetView, ViewType
from build123d_techdraw.layout.multiview import (
    MultiviewLayout,
    add_assembly_bom_and_balloons,
    add_standard_multiview,
    generate_assembly_bom,
)
from build123d_techdraw.renderers.dxf import DXFRenderer
from build123d_techdraw.renderers.pdf import PDFRenderer
from build123d_techdraw.renderers.svg import SVGRenderer
from build123d_techdraw.renderers.theme import THEME_COLORS, Theme, ThemeColors

try:
    from ._version import __version__
except ImportError:  # pragma: no cover
    __version__ = "0.1.0.dev0"

__all__ = [
    "AssemblyBOM",
    "BOMBalloon",
    "BOMItem",
    "BoltCircle",
    "BottomView",
    "BuildDrawing",
    "BuildSheet",
    "BuildView",
    "CenterMark",
    "Centerline",
    "DXFRenderer",
    "DatumFeature",
    "DatumFeatureSymbol",
    "DiameterDimension",
    "DimensionType",
    "DrawingBooklet",
    "DrawingSheet",
    "FeatureControlFrame",
    "FrontView",
    "GDTCharacteristic",
    "GeneralNotes",
    "HoleCallout",
    "IsometricView",
    "LeaderNote",
    "LeftView",
    "LinearDimension",
    "MaterialRemoval",
    "MultiviewLayout",
    "PDFRenderer",
    "RadialDimension",
    "RawSVG",
    "RearView",
    "Revision",
    "RevisionEntry",
    "RightView",
    "SVGRenderer",
    "Schedule",
    "SectionCuttingLine",
    "SectionView",
    "SheetSize",
    "SheetView",
    "StandardMultiview",
    "SurfaceFinish",
    "SurfaceFinishSymbol",
    "THEME_COLORS",
    "Table",
    "Theme",
    "ThemeColors",
    "ThreadCallout",
    "TitleBlock",
    "TitleBlockInfo",
    "Tolerance",
    "TopView",
    "View",
    "ViewType",
    "add_assembly_bom_and_balloons",
    "add_standard_multiview",
    "calculate_auto_scale",
    "find_closest_iso_scale",
    "format_dimension_value",
    "format_iso_scale",
    "generate_assembly_bom",
    "render_projection_cone_svg",
    "__version__",
]
