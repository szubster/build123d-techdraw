# API Reference Overview

Welcome to the `build123d-techdraw` API Reference.

- [Core Modules](core.md): `DrawingSheet`, `SheetSize`, `SheetView`, `DrawingBooklet`, `TitleBlockInfo`, `BOMItem`, `RevisionEntry`, `PartInfo`.
- [Annotations](annotations.md): `LinearDimension`, `RadialDimension`, `DiameterDimension`, `FeatureControlFrame`, `DatumFeatureSymbol`, `SurfaceFinishSymbol`, `Centerline`, `CenterMark`, `BoltCircle`, `LeaderNote`, `BOMBalloon`, `SectionCuttingLine`.
- [Layout](layout.md): `add_standard_multiview`, `MultiviewLayout`, `generate_assembly_bom`, `add_assembly_bom_and_balloons`, `auto_dimension_sheet`, `auto_dimension_view`, `place_balloons_sdf`, `place_leaders_sdf`.
- [Renderers](renderers.md): `SVGRenderer`, `PDFRenderer`, `DXFRenderer`, `HTMLRenderer`, `render_interactive_html`, `export_interactive_html`, `Theme`, `ThemeColors`.
- [GPU Acceleration](gpu.md): `is_gpu_available`, `is_gpu_enabled`, `gpu_info`, `ViewStyle`, `render_shaded_view`, `project_shape_gpu_hlr`, `compute_sheet_sdf`, `SheetDistanceField`.
