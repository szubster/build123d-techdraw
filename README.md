# build123d-techdraw

[![CI](https://github.com/szubster/build123d-techdraw/actions/workflows/ci.yml/badge.svg)](https://github.com/szubster/build123d-techdraw/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/build123d-techdraw.svg?color=blue)](https://pypi.org/project/build123d-techdraw/)
[![Python versions](https://img.shields.io/pypi/pyversions/build123d-techdraw.svg)](https://pypi.org/project/build123d-techdraw/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Mypy](https://img.shields.io/badge/types-mypy-blue.svg)](https://mypy-lang.org/)

A modern, high-performance, Apache 2.0-licensed 2D technical drawing and blueprint engine for [build123d](https://github.com/gumyr/build123d) (v0.13.0+) and Python CAD.

---

## 🎯 Highlights

- **Permissive License (Apache 2.0):** Clean commercial and enterprise adoption, matching `build123d`'s license.
- **Strict `build123d >= 0.13.0` Compatibility:** Built exclusively on modern public B-Rep APIs, free of brittle internal C++ OCP pointer hacks.
- **Paper-Space Hybrid Vector Architecture:** 3D CAD projections remain pure geometry; dimensions, GD&T frames, BOM callouts, and ISO 7200 title blocks render in invariant 2D paper space ($2.5\text{ mm} / 3.5\text{ mm}$ text heights).
- **Multi-Format Vector Export:** Crisp SVG (Dark Blueprint & Clean White ISO themes), multi-page PDF, and 1:1 DXF flat patterns.

---

## 📦 Installation

```bash
# With uv (recommended)
uv add build123d-techdraw

# With pip
pip install build123d-techdraw
```

---

## 🚀 Quick Example

```python
import build123d as bd
from build123d_techdraw import DrawingSheet, SheetSize, SheetView, ViewType

# 1. Create a 3D Part
part = bd.Box(100, 60, 40) - bd.Pos(0, 0, 10) * bd.Cylinder(15, 30)

# 2. Set up an ISO A3 Sheet
sheet = DrawingSheet(size=SheetSize.A3, landscape=True, title="Machined Baseplate")

# 3. Add Standard Orthographic Views
sheet.add_view(SheetView(part, view_type=ViewType.FRONT, center=(120, 150), scale=1.0))
sheet.add_view(SheetView(part, view_type=ViewType.TOP, center=(120, 240), scale=1.0))
sheet.add_view(SheetView(part, view_type=ViewType.ISOMETRIC, center=(280, 220), scale=0.8))

# 4. Export Vector Drawings
sheet.export_svg("blueprint.svg", theme="dark")
sheet.export_pdf("drawing.pdf")
```

---

## 📄 License

Apache License 2.0. See [LICENSE](LICENSE) for details.
