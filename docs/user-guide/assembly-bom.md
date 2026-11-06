# Assemblies & Bill of Materials (BOM)

`build123d-techdraw` provides automated assembly drafting with recursive component identification, structured BOM table generation, and outward-radiating balloon callouts.

---

## 🔩 Labeling Assembly Components

In `build123d`, assign labels to parts or sub-assemblies before combining them into a `bd.Compound`:

```python
import build123d as bd

# 1. Component 1: Aluminum Base
base = bd.Box(100.0, 60.0, 15.0)
base.label = "Base Plate"

# 2. Component 2: Steel Pins (Quantity = 2)
pin1 = bd.Cylinder(radius=5.0, height=20.0).move(bd.Location((25.0, 15.0, 17.5)))
pin1.label = "Alignment Pin"

pin2 = bd.Cylinder(radius=5.0, height=20.0).move(bd.Location((-25.0, -15.0, 17.5)))
pin2.label = "Alignment Pin"

# Combine into compound
assembly = bd.Compound(children=[base, pin1, pin2])
```

---

## 📋 Generating the BOM Table

Use `generate_assembly_bom` to tally quantities:

```python
from build123d_techdraw import generate_assembly_bom

bom_items = generate_assembly_bom(assembly)
for item in bom_items:
    print(f"Item #{item.item_no}: {item.qty}x {item.description} ({item.part_no})")
# Output:
# Item #1: 1x Base Plate (PART-001)
# Item #2: 2x Alignment Pin (PART-002)
```

---

## 🎈 Automated Balloon Callouts

Use `add_assembly_bom_and_balloons` to automatically populate the sheet's BOM table and place numbered balloon circles pointing directly to each part's center in 3D space:

```python
from build123d_techdraw import (
    DrawingSheet,
    SheetSize,
    add_assembly_bom_and_balloons,
    add_standard_multiview,
)

sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)
sheet.set_title_block(title="BASE ASSEMBLY", drawing_number="ASM-001")

# Create multi-view layout
layout = add_standard_multiview(sheet, assembly, scale="auto")

# Auto-place BOM table and radiating balloons on the Isometric view
bom, balloons = add_assembly_bom_and_balloons(
    sheet=sheet,
    assembly=assembly,
    target_view=layout.isometric,
    balloon_distance=30.0,
)

sheet.export_svg("assembly_drawing.svg")
```
