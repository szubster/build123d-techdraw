"""Generate all example drawings in SVG, PDF, and DXF formats."""

import shutil
from pathlib import Path

from assembly_bom import generate_assembly_bom
from quickstart_enclosure import generate_quickstart
from stepped_bracket import generate_stepped_bracket


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    examples_out = repo_root / "examples" / "output"
    docs_assets = repo_root / "docs" / "assets" / "examples"

    examples_out.mkdir(parents=True, exist_ok=True)
    docs_assets.mkdir(parents=True, exist_ok=True)

    print("Generating Quickstart Enclosure drawing...")
    generate_quickstart(examples_out)

    print("Generating Assembly BOM drawing...")
    generate_assembly_bom(examples_out)

    print("Generating Stepped Bracket drawing...")
    generate_stepped_bracket(examples_out)

    print("Copying SVG outputs to docs/assets/examples/...")
    for svg_file in examples_out.glob("*.svg"):
        shutil.copy2(svg_file, docs_assets / svg_file.name)

    print("All example outputs generated successfully!")


if __name__ == "__main__":
    main()
