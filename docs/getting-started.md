# Getting Started

This guide walks you through creating your first engineering blueprint with `build123d-techdraw`.

---

## 🛠️ Requirements

- Python **3.11** or newer
- `build123d >= 0.13.0`

---

## 📥 Installation

Install via `uv` or `pip`:

=== "uv"

    ```bash
    uv add build123d-techdraw
    ```

=== "pip"

    ```bash
    pip install build123d-techdraw
    ```

---

## 📐 First Technical Drawing: Step-by-Step

### 1. Modeling the Geometry

Let's model a stepped mechanical bracket with a mounting hole:

```python
import build123d as bd

# Base block
base = bd.Box(80.0, 50.0, 15.0)

# Upright flange
flange = bd.Box(30.0, 50.0, 40.0).move(bd.Location((-25.0, 0.0, 20.0)))

# Fuse together and drill an M10 clearance hole
part = base + flange
hole = bd.Cylinder(radius=5.5, height=60.0).move(bd.Location((-25.0, 0.0, 20.0)))
part = part - hole
```

### 2. Creating an ISO 5457 Drawing Sheet

Create an `A3_LANDSCAPE` sheet with title block metadata:

```python
from build123d_techdraw import DrawingSheet, SheetSize

sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
sheet.set_title_block(
    title="STEPPED BRACKET",
    drawing_number="BRK-100-A",
    rev="REV A",
    author="SZUBA",
    material="AL 6061-T6",
    finish="ANODIZED CLEAR",
)
```

### 3. Adding Orthographic & Isometric Views

Use `add_standard_multiview` to layout Front, Top, Right, and Isometric views automatically:

```python
from build123d_techdraw import add_standard_multiview

layout = add_standard_multiview(
    sheet=sheet,
    shape=part,
    scale="auto",  # Automatically selects the best ISO 5455 scale (e.g. 1:1, 1:2)
    spacing=25.0,  # 25 mm gap between views
    show_hidden=True,  # Dashed lines for hidden features
    auto_dimensions=True,  # Envelope dimensions
)
```

### 4. Adding Annotations & Dimensions

Chain paper-space annotations using model coordinates:

```python
from build123d_techdraw import GDTCharacteristic

# Hole diameter with tolerance on Front view
layout.front.dim_vertical(
    model_y1=-5.5,
    model_y2=5.5,
    model_x=-25.0,
    prefix="⌀",
    tolerance="H11",
    offset_x=-15.0,
)

# Center crosshairs on the hole
layout.front.centermark(-25.0, 0.0, size=8.0)

# GD&T Feature Control Frame: Flatness on the base
layout.front.add_gdt_frame(
    model_x=10.0,
    model_y=-7.5,
    characteristic=GDTCharacteristic.FLATNESS,
    tolerance_value="0.05",
)
```

### 5. Exporting

Export to all standard engineering formats:

```python
# 1. Interactive Dual-Theme SVG
sheet.export_svg("bracket.svg")

# 2. Vector PDF ready for print
sheet.export_pdf("bracket.pdf")

# 3. 1:1 DXF for CNC machining / waterjet / laser cutting
sheet.export_dxf("bracket.dxf")
```

---

## 🖼️ Generated Output Preview

Here is the rendered SVG output produced by the script above:

![Stepped Bracket Blueprint](assets/examples/stepped_bracket.svg)

!!! tip "Try It Yourself"
    You can run this complete script directly from the repository:
    ```bash
    uv run python examples/stepped_bracket.py
    ```
    Outputs will be saved in `examples/output/` (SVG, PDF, and DXF).
