# build123d-techdraw Examples

This directory contains standalone, runnable examples demonstrating the capabilities of `build123d-techdraw` along with their generated vector outputs.

---

## 📁 Examples Overview

| Example Script | Description | Generated Outputs |
| :--- | :--- | :--- |
| [`quickstart_enclosure.py`](quickstart_enclosure.py) | Orthographic multi-view layout with automatic ISO 5455 scaling and envelope dimensions. | [SVG (Dark)](output/quickstart_enclosure.svg), [SVG (White)](output/quickstart_enclosure_white.svg), [PDF](output/quickstart_enclosure.pdf), [DXF](output/quickstart_enclosure.dxf) |
| [`assembly_bom.py`](assembly_bom.py) | Multi-component assembly with automated Bill of Materials (BOM) table and radiating balloon callouts. | [SVG (Dark)](output/assembly_drawing.svg), [SVG (White)](output/assembly_drawing_white.svg), [PDF](output/assembly_drawing.pdf), [DXF](output/assembly_drawing.dxf) |
| [`stepped_bracket.py`](stepped_bracket.py) | Detailed component blueprint featuring ISO 1101 GD&T Feature Control Frames, centermarks, and hole tolerances. | [SVG (Dark)](output/stepped_bracket.svg), [SVG (White)](output/stepped_bracket_white.svg), [PDF](output/stepped_bracket.pdf), [DXF](output/stepped_bracket.dxf) |

---

## 🚀 Running Examples

Generate all example drawings in one command:

```bash
uv run python examples/generate_all.py
```

Or run any example individually:

```bash
uv run python examples/quickstart_enclosure.py
uv run python examples/assembly_bom.py
uv run python examples/stepped_bracket.py
```

All outputs are written to the `examples/output/` directory.

---

## 🖼️ Example Outputs Preview

### 1. Stepped Bracket Blueprint (GD&T, Dimensions, ISO 5457 Border)
![Stepped Bracket Blueprint](output/stepped_bracket.svg)

### 2. Multi-Component Assembly & Automated BOM
![Assembly Drawing & BOM](output/assembly_drawing.svg)

### 3. Enclosure Base Quickstart
![Enclosure Base](output/quickstart_enclosure.svg)
