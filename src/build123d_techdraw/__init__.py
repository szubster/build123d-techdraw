"""build123d-techdraw: 2D technical drawing and blueprint engine for build123d."""

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
from build123d_techdraw.annotations.tables import Card, RawSVG, Schedule, Table, TextBox
from build123d_techdraw.builder import (
    AssemblyBOM,
    AuxiliaryView,
    BottomView,
    BreakView,
    BuildDrawing,
    BuildSheet,
    BuildView,
    DetailView,
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
from build123d_techdraw.core.view import SheetView, ViewType, project_shape_to_svg_fragment
from build123d_techdraw.layout.linter import LintIssue, run_sheet_lint
from build123d_techdraw.layout.multiview import (
    MultiviewLayout,
    add_assembly_bom_and_balloons,
    add_standard_multiview,
    generate_assembly_bom,
)
from build123d_techdraw.recognizers.features import (
    RecognizedBoltCircle,
    RecognizedHole,
    recognize_bolt_circles,
    recognize_holes,
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
    "AngularDimension",
    "AssemblyBOM",
    "AuxiliaryView",
    "BOMBalloon",
    "BOMItem",
    "BaselineDimensionGroup",
    "BoltCircle",
    "BottomView",
    "BreakView",
    "BuildDrawing",
    "BuildSheet",
    "BuildView",
    "Card",
    "CenterMark",
    "Centerline",
    "ChainDimensionGroup",
    "ChamferCallout",
    "DXFRenderer",
    "DatumFeature",
    "DatumFeatureSymbol",
    "DetailView",
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
    "LintIssue",
    "MaterialRemoval",
    "MultiviewLayout",
    "OrdinateDimension",
    "OrdinateDimensionGroup",
    "OrdinateDimensionSet",
    "PDFRenderer",
    "RadialDimension",
    "RawSVG",
    "RearView",
    "RecognizedBoltCircle",
    "RecognizedHole",
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
    "StepBadge",
    "SurfaceFinish",
    "SurfaceFinishSymbol",
    "THEME_COLORS",
    "Table",
    "TextBox",
    "Theme",
    "ThemeColors",
    "ThreadCallout",
    "TitleBlock",
    "TitleBlockInfo",
    "Tolerance",
    "TopView",
    "View",
    "ViewType",
    "WeldType",
    "WeldingSymbol",
    "add_assembly_bom_and_balloons",
    "add_standard_multiview",
    "calculate_auto_scale",
    "find_closest_iso_scale",
    "format_dimension_value",
    "format_iso_scale",
    "generate_assembly_bom",
    "project_shape_to_svg_fragment",
    "recognize_bolt_circles",
    "recognize_holes",
    "render_projection_cone_svg",
    "run_sheet_lint",
    "__version__",
]
