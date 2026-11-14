# Sheets & Views

## 📄 DrawingSheet & ISO 5457 Borders

The `DrawingSheet` class manages paper-space dimensions, margins, title blocks, revision tables, and views.

### Standard Sheet Sizes

`SheetSize` enum supports standard ISO 216 paper sizes in both portrait and landscape:

- `SheetSize.A0_LANDSCAPE`, `SheetSize.A0_PORTRAIT` ($1189 \times 841\text{ mm}$)
- `SheetSize.A1_LANDSCAPE`, `SheetSize.A1_PORTRAIT` ($841 \times 594\text{ mm}$)
- `SheetSize.A2_LANDSCAPE`, `SheetSize.A2_PORTRAIT` ($594 \times 420\text{ mm}$)
- `SheetSize.A3_LANDSCAPE`, `SheetSize.A3_PORTRAIT` ($420 \times 297\text{ mm}$)
- `SheetSize.A4_LANDSCAPE`, `SheetSize.A4_PORTRAIT` ($297 \times 210\text{ mm}$)

```python
from build123d_techdraw import DrawingSheet, SheetSize

sheet = DrawingSheet(
    sheet_size=SheetSize.A3_LANDSCAPE,
    left_margin=20.0,  # 20mm left margin for binder punching (ISO standard)
    right_margin=10.0,
    top_margin=10.0,
    bottom_margin=10.0,
)
```

### Centering Marks & Alphanumeric Grid Zones

`DrawingSheet` automatically generates:
- **Centering marks** at the midpoint of each border (ISO 5457).
- **Alphanumeric grid zones** ($A$–$H$ vertically, $1$–$8$ horizontally) along the inner frame to facilitate coordinates referencing in large assemblies.

---

## 🪟 SheetView & Projections

A `SheetView` represents a 2D orthographic or isometric projection placed onto a sheet.

### View Types

- `ViewType.FRONT`: Projection along $+Y$
- `ViewType.TOP`: Projection along $+Z$
- `ViewType.RIGHT`: Projection along $+X$
- `ViewType.LEFT`: Projection along $-X$
- `ViewType.BOTTOM`: Projection along $-Z$
- `ViewType.REAR`: Projection along $-Y$
- `ViewType.ISOMETRIC`: Standard trimetric/isometric perspective ($1.2, 1.8, 1.2$)
- `ViewType.SECTION`: Planar cross-section cut with engineering hatching

### Adding Views Manually

```python
# Add Front view centered at (150 mm, 120 mm)
view_front = sheet.add_view(
    shape=part,
    view_type=ViewType.FRONT,
    center=(150.0, 120.0),
    scale=1.0,
    show_hidden=True,  # Dotted lines for hidden contours
    view_label="FRONT",
)
```

### Adding Section Views with 45° Cross-Hatching

```python
import build123d as bd

# Slice through part along XZ plane at Y=0
section_view = sheet.add_section_view(
    shape=part,
    cutting_plane=bd.Plane.XZ,
    center=(280.0, 120.0),
    scale=1.0,
    view_label="SECTION A-A",
)
```

---

## 🏗️ Builder Mode vs Algebraic Mode

`build123d-techdraw` supports both `build123d` programming paradigms:

### Builder Mode (`BuildDrawing`)
```python
with BuildDrawing(SheetSize.A3_LANDSCAPE) as sheet:
    TitleBlock(title="BRACKET", drawing_no="DWG-001")
    GeneralNotes(["1. ALL DIMENSIONS IN MM."])

    with FrontView(part, center=(130, 150), scale=1.0) as front:
        CenterMark(hole_face)
        LinearDimension(part.edges().filter_by(bd.Axis.Z)[0], offset=-15.0)

    with TopView(part, center=(130, 230), scale=1.0):
        LinearDimension(v_min, v_max, offset=15.0, direction="horizontal")

    with IsometricView(part, center=(320, 190), scale=0.8):
        pass
```

### Algebraic Mode
```python
sheet = DrawingSheet(SheetSize.A3_LANDSCAPE)
sheet += TitleBlock(title="BRACKET", drawing_no="DWG-001")

front = FrontView(part, scale=1.0).locate((130, 150))
top = TopView(part, scale=1.0).align_to(front, direction="above", spacing=25.0)
iso = IsometricView(part, scale=0.8).align_to(front, direction="right", spacing=50.0)

front += [
    CenterMark(hole_face),
    LinearDimension(part.edges().filter_by(bd.Axis.Z)[0], offset=-15.0),
]
top += LinearDimension(v_min, v_max, offset=15.0, direction="horizontal")

sheet += [front, top, iso]
```
