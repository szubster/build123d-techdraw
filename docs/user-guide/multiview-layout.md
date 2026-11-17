# Multi-View Auto-Layout

The declarative `add_standard_multiview` function simplifies generating production blueprints by solving view footprints, projection alignments, and scale ratios automatically.

---

## 🎯 How It Works

Instead of calculating manual 2D coordinates for each view, `add_standard_multiview`:
1. Calculates the unscaled 3D bounding box ($X, Y, Z$) of your model.
2. Identifies the unscaled multi-view envelope for the requested views (Front, Top, Right, Isometric).
3. If `scale="auto"`, calculates the maximum scale that fits the printable sheet area (factoring in margins and title block clearance) and selects the closest standard **ISO 5455 scale**.
4. Aligns Front and Top views on the exact same vertical projection axis ($X$).
5. Aligns Front and Right views on the exact same horizontal projection axis ($Y$).
6. Returns a strongly-typed `MultiviewLayout` object.

---

## ⚙️ Usage Example

```python
import build123d as bd
from build123d_techdraw import DrawingSheet, SheetSize, ViewType, add_standard_multiview

part = bd.Box(120.0, 60.0, 40.0)
sheet = DrawingSheet(sheet_size=SheetSize.A3_LANDSCAPE)

layout = add_standard_multiview(
    sheet=sheet,
    shape=part,
    views=[ViewType.FRONT, ViewType.TOP, ViewType.RIGHT, ViewType.ISOMETRIC],
    layout="third_angle",  # Or "first_angle"
    scale="auto",
    spacing=25.0,
    show_hidden=True,
    auto_dimensions=True,  # Automatically dimensions overall Length, Height, and Depth
)

print(f"Chosen scale: {layout.scale_str}")  # e.g. "SCALE 1:2"
```

---

## 📐 Projection Standards

### Third-Angle Projection (`layout="third_angle"`, default)
ISO 5456-2 projection method A; the ASME / North American convention:
- **Top View** is placed directly **above** the Front View.
- **Right View** is placed directly to the **right** of the Front View (a Left View goes on the left).
- **Isometric View** is placed at the top-right.
- The title block shows the third-angle symbol (end-view circles to the left of the frustum).

### First-Angle Projection (`layout="first_angle"`)
ISO 5456-2 projection method E; the usual choice under ISO in Europe and most of Asia:
- **Top View** is placed directly **below** the Front View.
- **Right View** is placed directly to the **left** of the Front View (a Left View goes on the right).
- **Isometric View** is placed at the bottom-left, in the free grid cell.
- The title block shows the first-angle symbol (frustum to the left of the end-view circles).

Both methods are valid ISO drawings as long as the symbol in the title block matches the view
arrangement, which `add_standard_multiview` keeps in sync.
