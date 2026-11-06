# build123d-techdraw

[![CI](https://github.com/szubster/build123d-techdraw/actions/workflows/ci.yml/badge.svg)](https://github.com/szubster/build123d-techdraw/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/build123d-techdraw.svg)](https://pypi.org/project/build123d-techdraw/)
[![Python Versions](https://img.shields.io/pypi/pyversions/build123d-techdraw.svg)](https://pypi.org/project/build123d-techdraw/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://www.mypy-lang.org/static/mypy_badge.svg)](https://mypy-lang.org/)

**build123d-techdraw** is a modern, high-performance, Apache 2.0-licensed 2D technical drawing and blueprint engine for [`build123d`](https://github.com/gumyr/build123d) (v0.13.0+) and Python CAD.

---

## 🎯 Why build123d-techdraw?

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

## 🚀 Key Architectural Principles

1. **Paper-Space Hybrid Vector Architecture:**
   Annotations (dimensions, GD&T frames, title blocks, notes) never pollute the 3D CAD B-Rep kernel. 3D geometry is projected to 2D view vectors via `build123d.Drawing`, and all dimension lines and text are computed in true paper space ($mm$). Text heights ($2.5\text{ mm}$, $3.5\text{ mm}$) and arrowheads remain crisp, invariant, and lightning fast regardless of view scale.

2. **Automated Layout & Scale Ladder Solver:**
   Using ISO 5455 scale ladders ($1:1$, $1:2$, $1:5$, $2:1$, $5:1$, etc.), the multi-view engine automatically solves available printable boundaries on the sheet and aligns orthogonal projection lines with sub-millimeter precision.

3. **Dual-Theme Interactive SVG:**
   Exported SVGs bundle dynamic CSS variables with an embedded, zero-dependency theme toggle button. View blueprints in either **Dark Engineering Blueprint Mode** or **Clean White ISO Archival Mode** with a single click.

---

## 📦 Installation

```bash
# Using uv (recommended)
uv add build123d-techdraw

# Using pip
pip install build123d-techdraw
```

---

## ⚡ 30-Second Quickstart

```python
import build123d as bd
from build123d_techdraw import DrawingSheet, SheetSize, add_standard_multiview

# 1. Create a 3D CAD part
part = bd.Box(100.0, 50.0, 30.0)

# 2. Set up an ISO A3 drawing sheet
sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
sheet.set_title_block(
    title="ENCLOSURE BASE",
    drawing_number="DWG-001",
    author="J. DOE",
)

# 3. Add standard multi-view with automatic scale and dimensions
layout = add_standard_multiview(
    sheet=sheet,
    shape=part,
    scale="auto",
    auto_dimensions=True,
)

# 4. Export to interactive SVG, multi-page vector PDF, and 1:1 DXF
sheet.export_svg("drawing.svg")
sheet.export_pdf("drawing.pdf")
sheet.export_dxf("drawing.dxf")
```
