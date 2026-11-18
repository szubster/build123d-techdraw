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
| `CIRCULARITY` | `○` | Circularity / Roundness |
| `STRAIGHTNESS` | `―` | Straightness |
| `PROFILE_SURFACE`| `⌒` | Profile of a Surface |
| `PROFILE_LINE` | `⌓` | Profile of a Line |
| `CIRCULAR_RUNOUT`| `↗` | Circular Runout |
| `TOTAL_RUNOUT` | `⇗` | Total Runout |

Straightness, flatness, circularity and cylindricity are **form tolerances** (ISO 1101): they
control a feature on its own and never reference a datum. `FeatureControlFrame` raises
`ValueError` if one of them (`build123d_techdraw.annotations.gdt.FORM_CHARACTERISTICS`) is given
`datum_refs` / `datum_a..c`. To relate a surface to a datum, use an orientation tolerance such as
`PARALLELISM`.

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

ISO 5459 datum feature indicators identify reference datum planes and axes. The filled triangle
sits with its base on the feature outline and the framed letter is placed **outside the
material**. When `DatumFeature` / `DatumFeatureSymbol` targets a face, that side is found
automatically from the face's outward normal projected into the view (for edges, vertices and
faces seen face-on: the side away from the view centre):

```python
import build123d as bd
from build123d_techdraw import DatumFeature

with FrontView(part, center=(150.0, 105.0), scale=1.0):
    # Bottom face: triangle on the bottom edge, frame below the part.
    DatumFeature(label="A", target=part.faces().sort_by(bd.Axis.Z)[0])
```

Pass `direction="up" | "down" | "left" | "right"` to force the side, e.g. at explicit
coordinates:

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

Surface finish annotations denote allowable roughness ($Ra$) and manufacturing processes. The
symbol's vertex must touch the surface line (or its extension line / a leader). Give it a
`target` face and the vertex snaps to the projected feature: a planar face seen edge-on lands
exactly on its outline and follows the view if it moves:

```python
with FrontView(part, center=(290.0, 150.0), scale=1.0):
    SurfaceFinishSymbol(
        target=part.faces().sort_by(bd.Axis.Z)[-1],  # top face, seen edge-on
        ra_value="Ra 1.6",
        material_removal=MaterialRemoval.REQUIRED,
    )
```

With model coordinates or `position=(x, y)` (sheet mm) you are responsible for putting the
vertex on the outline:

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
