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
    layout="first_angle",  # ISO default; use "third_angle" for ASME / US drawings
    scale="auto",
    spacing=25.0,
    show_hidden=True,
    auto_dimensions=True,  # Automatically dimensions overall Length, Height, and Depth
)

print(f"Chosen scale: {layout.scale_str}")  # e.g. "SCALE 1:2"
```

---

## 📐 Projection Standards

`build123d-techdraw` follows ISO, so the default is **first-angle projection**
(`layout="first_angle"`). Pass `layout="third_angle"` for ASME / US drawings.
Any other value raises `ValueError`.

| `layout` | Standard | Top view | Right view | Left view | Isometric |
|---|---|---|---|---|---|
| `"first_angle"` *(default)* | ISO 128-3 / ISO 5456-2 (method E). Default in Europe and most of the world | **below** the Front view | to the **left** of the Front view | to the **right** of the Front view | bottom-left (free corner) |
| `"third_angle"` | ASME Y14.3 (method A). Default in the USA and Canada | **above** the Front view | to the **right** of the Front view | to the **left** of the Front view | top-right (free corner) |

In first-angle projection each view sits on the side *opposite* the one it is
seen from. In third-angle projection it sits on the *same* side.

### Projection symbol

`add_standard_multiview` sets `sheet.title_info.third_angle` to match the chosen
`layout`, so the ISO 5456-2 symbol in the title block always agrees with the view
arrangement. The symbol shows a truncated cone (large end on the left) next to its end view:

- **First-angle:** the end-view circles are to the **left** of the cone.
- **Third-angle:** the end-view circles are to the **right** of the cone.

If you place views by hand (`FrontView(...)`, `align_to(...)`) instead of using
`add_standard_multiview`, arrange them for first-angle projection (for example
`top.align_to(front, direction="below")`), or set
`TitleBlock(..., third_angle=True)` when you lay them out in third-angle projection.
