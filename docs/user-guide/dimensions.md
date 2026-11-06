# Dimensions & Text Annotations

`build123d-techdraw` renders all dimensions in **true 2D paper space ($mm$)**.

---

## 📏 Dimension Types

### 1. Horizontal Dimensions

```python
# Dimension between model X=-40.0 and X=+40.0 at baseline model Y=-10.0
layout.front.dim_horizontal(
    model_x1=-40.0,
    model_x2=40.0,
    model_y=-10.0,
    offset_y=12.0,  # Positive moves downward in paper space
    tolerance="±0.1",
    prefix="2x ",
)
```

### 2. Vertical Dimensions

```python
# Dimension between model Z=0.0 and Z=30.0 at baseline model X=-40.0
layout.front.dim_vertical(
    model_y1=0.0,
    model_y2=30.0,
    model_x=-40.0,
    offset_x=-15.0,  # Negative moves leftward in paper space
    tolerance="H7",
)
```

### 3. Aligned (Skewed) Dimensions

```python
layout.front.dim_aligned(
    p1=(10.0, 10.0),
    p2=(40.0, 50.0),
    offset=8.0,
)
```

### 4. Radial Dimensions ($R$)

```python
# Radial dimension pointing to a fillet or round
sheet.add_radial_dimension(
    center=(100.0, 100.0),
    radius=15.0,
    angle_deg=45.0,
    tolerance="±0.05",
)
```

### 5. Diameter Dimensions ($\varnothing$)

```python
# Full diametral dimension with leader shelf
sheet.add_diameter_dimension(
    center=(100.0, 100.0),
    diameter=20.0,
    angle_deg=30.0,
    tolerance="H7",
)
```

---

## 🏷️ Centerlines & Crosshairs

### Center Crosshairs (`centermark`)

```python
# Draw center crosshairs on a hole at model coordinates (X, Y)
layout.front.centermark(model_x=0.0, model_y=15.0, size=8.0)
```

### Centerlines (`centerline_horizontal` / `centerline_vertical`)

```python
# Draw dash-dot centerlines through symmetry planes
layout.front.centerline_horizontal(model_y=0.0)
layout.front.centerline_vertical(model_x=0.0)
```

### Bolt Circle / Pitch Circle Diameter (`bolt_circle`)

```python
# 6-hole bolt circle with diameter 50mm
layout.front.bolt_circle(
    center_model=(0.0, 0.0),
    diameter_model=50.0,
    num_holes=6,
    start_angle_deg=0.0,
)
```

---

## 📝 Leaders & Multi-Line Notes

```python
layout.front.leader(
    model_x=12.0,
    model_y=10.0,
    lines=[
        "2x M6x1.0 THRU",
        "C'BORE ⌀10.5 x 6.0 DEEP",
    ],
    elbow_dx=18.0,
    elbow_dy=-14.0,
    terminator="arrow",  # or "dot"
)
```
