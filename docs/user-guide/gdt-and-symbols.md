# GD&T & ISO Symbols

`build123d-techdraw` provides built-in vector primitives for ISO 1101 Geometric Dimensioning & Tolerancing (GD&T) and ISO 1302 Surface Texture symbols.

---

## 📐 Feature Control Frames (ISO 1101)

Feature Control Frames display geometric tolerances divided into characteristic symbol, tolerance value, and datum reference compartments:

$$[\text{Symbol} \mid \text{Tolerance [Modifiers]} \mid \text{Datum A} \mid \text{Datum B} \mid \text{Datum C}]$$

### Supported Characteristics

The `GDTCharacteristic` enum provides full ISO 1101 symbols:

| Enum Member | Symbol | Characteristic |
| :--- | :---: | :--- |
| `POSITION` | `⌖` | Position |
| `CONCENTRICITY` | `◎` | Concentricity |
| `SYMMETRY` | `⌯` | Symmetry |
| `PARALLELISM` | `∥` | Parallelism |
| `PERPENDICULARITY`| `⟂` | Perpendicularity |
| `ANGULARITY` | `∠` | Angularity |
| `CYLINDRICITY` | `⌭` | Cylindricity |
| `FLATNESS` | `⏢` | Flatness |
| `ROUNDNESS` | `○` | Circularity / Roundness |
| `STRAIGHTNESS` | `―` | Straightness |
| `PROFILE_SURFACE`| `⌒` | Profile of a Surface |
| `PROFILE_LINE` | `⌓` | Profile of a Line |
| `CIRCULAR_RUNOUT`| `↗` | Circular Runout |
| `TOTAL_RUNOUT` | `⇗` | Total Runout |

### Code Example

```python
from build123d_techdraw import GDTCharacteristic

# Position tolerance with diameter, MMC modifier, and three datums
layout.front.add_gdt_frame(
    model_x=25.0,
    model_y=0.0,
    characteristic=GDTCharacteristic.POSITION,
    tolerance_value="⌀0.05 Ⓜ",
    datum_a="A",
    datum_b="B",
    datum_c="C",
)

# Flatness on base
layout.front.add_gdt_frame(
    model_x=0.0,
    model_y=-20.0,
    characteristic=GDTCharacteristic.FLATNESS,
    tolerance_value="0.02",
)
```

---

## 🏷️ Datum Feature Symbols

ISO datum tags identify reference datum planes and axes:

```python
# Add Datum A pointing upwards to base
layout.front.add_datum_symbol(
    model_x=0.0,
    model_y=-20.0,
    label="A",
    direction="up",  # "up", "down", "left", "right"
)
```

---

## 🪚 Surface Texture Symbols (ISO 1302)

Surface finish annotations denote allowable roughness ($Ra$) and manufacturing processes:

```python
from build123d_techdraw import MaterialRemoval

# Machining required (Ra 1.6, ground)
layout.front.add_surface_finish(
    model_x=30.0,
    model_y=15.0,
    ra_value="Ra 1.6",
    material_removal=MaterialRemoval.REQUIRED,
    process="GROUND",
)

# Machining prohibited / raw stock
layout.front.add_surface_finish(
    model_x=-20.0,
    model_y=15.0,
    ra_value="Ra 6.3",
    material_removal=MaterialRemoval.PROHIBITED,
)
```
