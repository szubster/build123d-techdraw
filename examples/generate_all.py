"""Generate all example drawings in SVG, PDF, and DXF formats."""

import argparse
import shutil
from pathlib import Path

from advanced_views import generate_advanced_views
from algebraic_mode_bracket import generate_algebraic_mode_drawing
from assembly_bom import generate_assembly_bom
from builder_mode_bracket import generate_builder_mode_drawing
from precision_flange import generate_precision_flange
from quickstart_enclosure import generate_quickstart
from stepped_bracket import generate_stepped_bracket
from welded_frame_booklet import generate_welded_frame_booklet


def generate_all(output_dir: Path, sync_docs: bool = True) -> None:
    """Run all example generators and optionally sync SVGs to docs/assets/examples/."""
    repo_root = Path(__file__).resolve().parent.parent
    docs_assets = repo_root / "docs" / "assets" / "examples"

    output_dir.mkdir(parents=True, exist_ok=True)

    generators = [
        ("Quickstart Enclosure", generate_quickstart),
        ("Assembly BOM", generate_assembly_bom),
        ("Stepped Bracket", generate_stepped_bracket),
        ("Algebraic Mode Bracket", generate_algebraic_mode_drawing),
        ("Builder Mode Bracket", generate_builder_mode_drawing),
        ("Advanced Views", generate_advanced_views),
        ("Precision Flange", generate_precision_flange),
        ("Welded Frame Booklet", generate_welded_frame_booklet),
    ]

    for name, fn in generators:
        print(f"Generating {name} drawing...")
        fn(output_dir)

    if sync_docs:
        docs_assets.mkdir(parents=True, exist_ok=True)
        print("Copying SVG outputs to docs/assets/examples/...")
        for svg_file in output_dir.glob("*.svg"):
            shutil.copy2(svg_file, docs_assets / svg_file.name)

    print("All example outputs generated successfully!")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optional custom output directory (skips syncing docs/assets/examples/).",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    if args.output_dir is not None:
        generate_all(args.output_dir, sync_docs=False)
    else:
        generate_all(repo_root / "examples" / "output", sync_docs=True)


if __name__ == "__main__":
    main()
