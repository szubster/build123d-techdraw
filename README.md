# build123d-techdraw

[![CI](https://github.com/szubster/build123d-techdraw/actions/workflows/ci.yml/badge.svg)](https://github.com/szubster/build123d-techdraw/actions/workflows/ci.yml)
[![Docs](https://github.com/szubster/build123d-techdraw/actions/workflows/docs.yml/badge.svg)](https://szubster.github.io/build123d-techdraw/)
[![PyPI](https://img.shields.io/pypi/v/build123d-techdraw.svg?color=blue)](https://pypi.org/project/build123d-techdraw/)
[![Python Versions](https://img.shields.io/pypi/pyversions/build123d-techdraw.svg)](https://pypi.org/project/build123d-techdraw/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Mypy](https://img.shields.io/badge/types-mypy%20strict-blue.svg)](https://mypy-lang.org/)

**build123d-techdraw** is a modern, high-performance, Apache 2.0-licensed 2D technical drawing and blueprint engine for [`build123d`](https://github.com/gumyr/build123d) (v0.13.0+) and Python CAD.

---

## 🎯 Comparison Matrix

| Feature | `build123d-techdraw` | `draftwright` | `build123d-drafting-helpers` |
| :--- | :--- | :--- | :--- |
| **Open Source License** | **Apache 2.0** (Permissive, enterprise friendly) | AGPLv3 (Copyleft, restrictive) | MIT |
| **build123d Compatibility** | **>= 0.13.0** (Strictly stable public APIs) | < 0.12 (Crashes on OCP symbols) | Legacy |
| **Annotation Architecture** | **True 2D Paper-Space (mm)** | 3D B-Rep sketch faces (bloats kernel) | Pure 2D overlay |
| **Interactive Dual-Theme SVG** | **Yes** (Dark Blueprint ↔ Clean White ISO) | No (Raster/Basic SVG) | No |
| **Standard ISO 5457 Borders** | **Yes** (A0–A4 portrait/landscape, grid marks) | Custom templates | Partial |
| **ISO 7200 Title Blocks** | **Yes** (Configurable fields, projection cones) | Hardcoded | Minimal |
| **GD&T Feature Control Frames** | **Yes** (ISO 1101 geometric tolerances & datums) | No | No |
| **ISO 1302 Surface Roughness** | **Yes** (Machining required/prohibited symbols) | No | No |
| **Automated Multi-View Layout** | **Yes** (Auto-scale solver & unscaled bounds) | Manual view placement | Manual |
| **Automated Assembly BOM** | **Yes** (Recursive component discovery & balloons) | Manual BOM tables | No |
| **Multi-Format Vector Export** | **SVG, PDF, DXF (1:1 CAD)** | SVG / PDF | SVG |

---

## 🏗️ Core Architecture: Paper-Space Hybrid

Traditional automated drafting tools often generate dimension arrows and text as 3D planar B-Rep faces directly in the CAD kernel. This causes severe computational slowdowns, bloats the OpenCASCADE kernel memory, and causes text sizes to distort when views are scaled.

**build123d-techdraw** implements a **paper-space hybrid vector architecture**:
1. **Geometry Projection:** 3D shapes are projected via `build123d.Drawing` into pure 2D vector path segments.
2. **Paper-Space Annotations:** Dimensions, GD&T frames, surface finish symbols, BOM balloons, centerlines, and ISO 7200 title blocks are rendered directly in true 2D paper space ($mm$).
3. **Invariant Typography:** Text heights ($2.5\text{ mm}$ for dimensions, $3.5\text{ mm}$ for headers) and arrowheads remain crisp, invariant, and fast, regardless of whether a view is scaled $1:1$, $1:5$, or $10:1$.

---

## 📦 Installation

```bash
# With uv (recommended)
uv add build123d-techdraw

# With pip
pip install build123d-techdraw
```

---

## ⚡ Quickstart

```python
import build123d as bd
from build123d_techdraw import DrawingSheet, SheetSize, add_standard_multiview

# 1. Model a 3D CAD part
part = bd.Box(100.0, 50.0, 30.0)

# 2. Set up an ISO A3 drawing sheet
sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
sheet.set_title_block(
    title="ENCLOSURE BASE",
    drawing_number="DWG-001",
    author="J. DOE",
    material="AL 6061-T6",
)

# 3. Add standard orthographic multi-view layout with auto-scaling
layout = add_standard_multiview(
    sheet=sheet,
    shape=part,
    scale="auto",  # Automatically selects standard ISO 5455 scale
    auto_dimensions=True,  # Adds overall Length, Height, and Depth dimensions
)

# 4. Export to interactive SVG, print-ready vector PDF, and 1:1 DXF
sheet.export_svg("blueprint.svg")
sheet.export_pdf("drawing.pdf")
sheet.export_dxf("flat_pattern.dxf")
```

### 🖼️ Generated Output Preview

<p align="center">
  <img src="examples/output/quickstart_enclosure.svg" alt="Enclosure Base Blueprint" width="100%">
</p>

> [!TIP]
> View full interactive SVG, vector PDF, and 1:1 DXF files in the [`examples/output/`](examples/output/) directory. Run this script directly with `uv run python examples/quickstart_enclosure.py`.

---

## 🎨 Native build123d Paradigms: Builder & Algebraic Modes

`build123d-techdraw` supports **both programming modes** that `build123d` users love, with **direct 3D topological selections** (`edges()`, `faces()`, `vertices()`):

### 1. 🏗️ Builder Mode (Context Managers & Auto-Registration)

```python
import build123d as bd
from build123d_techdraw import (
    BuildSheet,
    CenterMark,
    DatumFeature,
    FeatureControlFrame,
    FrontView,
    GDTCharacteristic,
    GeneralNotes,
    IsometricView,
    LinearDimension,
    SheetSize,
    TitleBlock,
    TopView,
)

with BuildSheet(SheetSize.A3_LANDSCAPE) as sheet:
    TitleBlock(title="BRACKET BLUEPRINT", drawing_no="DWG-001", material="AL 6061-T6")
    GeneralNotes(["1. ALL DIMENSIONS IN MILLIMETERS.", "2. ISO 2768-mK."])

    # Front View with topological annotations
    with FrontView(part, center=(130.0, 150.0), scale=1.0) as front:
        CenterMark(part.faces().filter_by(bd.GeomType.CYLINDER)[0])
        LinearDimension(part.edges().filter_by(bd.Axis.Z).sort_by(bd.Axis.X)[0], offset=-15.0)
        DatumFeature("A", target=part.faces().sort_by(bd.Axis.Z)[0])
        FeatureControlFrame(
            GDTCharacteristic.FLATNESS,
            tolerance_value="0.05",
            datum_refs=["A"],
            target=part.faces().sort_by(bd.Axis.Z)[-1],
        )

    # Top View with two-vertex dimension
    with TopView(part, center=(130.0, 230.0), scale=1.0):
        v_min = part.vertices().sort_by(bd.Axis.X)[0]
        v_max = part.vertices().sort_by(bd.Axis.X)[-1]
        LinearDimension(v_min, v_max, offset=15.0, direction="horizontal")

    with IsometricView(part, center=(320.0, 190.0), scale=0.8):
        pass

sheet.export_svg("bracket_builder.svg")
```

### 2. ⚡ Algebraic Mode (Operators, Chaining & Composition)

```python
import build123d as bd
from build123d_techdraw import (
    CenterMark,
    DrawingSheet,
    FrontView,
    IsometricView,
    LinearDimension,
    SheetSize,
    TitleBlock,
    TopView,
)

sheet = DrawingSheet(SheetSize.A3_LANDSCAPE)
sheet += TitleBlock(title="BRACKET BLUEPRINT", drawing_no="DWG-001", material="AL 6061-T6")

# Create views and layout relatively
front = FrontView(part, scale=1.0).locate((130.0, 150.0))
top = TopView(part, scale=1.0).align_to(front, direction="above", spacing=25.0)
iso = IsometricView(part, scale=0.8).align_to(front, direction="right", spacing=50.0)

# Attach dimensions directly to views
front += CenterMark(part.faces().filter_by(bd.GeomType.CYLINDER)[0])
front += LinearDimension(part.edges().filter_by(bd.Axis.Z).sort_by(bd.Axis.X)[0], offset=-15.0)
top += LinearDimension(
    part.vertices().sort_by(bd.Axis.X)[0], part.vertices().sort_by(bd.Axis.X)[-1], offset=15.0
)

# Compose onto sheet via operators
sheet += [front, top, iso]
sheet.export_svg("bracket_algebraic.svg")
```

---

## 🔩 Assembly Blueprint & Automated BOM

```python
import build123d as bd
from build123d_techdraw import (
    DrawingSheet,
    SheetSize,
    add_assembly_bom_and_balloons,
    add_standard_multiview,
)

# 1. Build an assembly of labeled components
base = bd.Box(120.0, 80.0, 20.0)
base.label = "Machined Baseplate"

pin1 = bd.Cylinder(radius=6.0, height=30.0).move(bd.Location((30.0, 20.0, 25.0)))
pin1.label = "Dowel Pin"

pin2 = bd.Cylinder(radius=6.0, height=30.0).move(bd.Location((-30.0, -20.0, 25.0)))
pin2.label = "Dowel Pin"

assembly = bd.Compound(children=[base, pin1, pin2])

# 2. Create sheet and multi-view layout
sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
layout = add_standard_multiview(sheet, assembly, scale="auto")

# 3. Automatically tally quantities, populate BOM table, and place radiating balloons
bom, balloons = add_assembly_bom_and_balloons(
    sheet=sheet,
    assembly=assembly,
    target_view=layout.isometric,
    balloon_distance=30.0,
)

sheet.export_svg("assembly_drawing.svg")
```

### 🖼️ Generated Output Preview

<p align="center">
  <img src="examples/output/assembly_drawing.svg" alt="Assembly Drawing with Automated BOM and Balloons" width="100%">
</p>

> [!TIP]
> Run this example directly with `uv run python examples/assembly_bom.py`. Output files are saved to [`examples/output/`](examples/output/).

---

## 🎨 Interactive Dual-Theme SVG

Exported SVGs include dynamic CSS variables and an embedded client-side theme switcher button:

- **Dark Engineering Blueprint:** Slate blue background (`#0d1929`), glowing cyan visible lines, golden dimensions, and high-contrast drafting layout.
- **Clean White ISO Archival:** Pure white paper (`#FFFFFF`), solid black lines, gray dashed hidden lines, and standard engineering typography.

---

## 🧪 Development & Quality Standards

`build123d-techdraw` adheres to modern Python packaging and CI/CD standards:

- **Package Manager:** `uv` (`uv run`, `uv lock`).
- **Build Backend:** `hatchling` + `hatch-vcs` for dynamic Git-tag versioning.
- **Linter & Formatter:** `ruff` with line length 100.
- **Type Checker:** `mypy` in **strict mode** (100% typed public APIs).
- **Test Matrix:** `pytest` + `pytest-cov` across Python 3.11, 3.12, 3.13.

```bash
# Run linters
uv run ruff check
uv run ruff format --check

# Run strict type checking
uv run mypy src tests

# Run test suite with coverage
uv run pytest

# Build documentation
uv run mkdocs build --strict
```

---

## 📄 License

Licensed under the [Apache License, Version 2.0](LICENSE).
