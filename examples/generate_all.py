"""Generate all example drawings in SVG, PDF, DXF, HTML, and GPU-shaded PNG formats."""

import argparse
import shutil
from collections.abc import Callable
from pathlib import Path

from advanced_views import generate_advanced_views
from algebraic_mode_bracket import generate_algebraic_mode_drawing
from assembly_bom import generate_assembly_bom
from builder_mode_bracket import generate_builder_mode_drawing
from gpu_hlr_assembly import generate_gpu_hlr_assembly
from interactive_3d_blueprint import generate_interactive_3d_blueprint
from precision_flange import generate_precision_flange
from quickstart_enclosure import generate_quickstart
from sdf_smart_layout import generate_sdf_smart_layout
from shaded_technical_views import generate_shaded_technical_views
from stepped_bracket import generate_stepped_bracket
from welded_frame_booklet import generate_welded_frame_booklet

from build123d_techdraw import is_gpu_enabled

# Examples whose SVG outputs are 100% deterministic across CPU/OS and checked byte-for-byte in CI.
DETERMINISTIC_SVG_PREFIXES: tuple[str, ...] = (
    "quickstart_enclosure",
    "assembly_drawing",
    "stepped_bracket",
    "algebraic_mode_bracket",
    "builder_mode_bracket",
    "advanced_views",
    "precision_flange",
    "welded_frame_booklet",
    "interactive_3d_blueprint",
    "sdf_smart_layout",
)

# Examples whose outputs depend on GPU/Vulkan shader rasterization and are verified perceptually
# via SSIM + pixel tolerance in CI (tests/raster_diff.py).
GPU_EXAMPLE_PREFIXES: tuple[str, ...] = (
    "shaded_technical_views",
    "gpu_hlr_assembly",
)


def generate_all(output_dir: Path, sync_docs: bool = True, include_gpu: bool | None = None) -> None:
    """Run all example generators and optionally sync SVG/PNG/HTML assets to docs/assets/examples/."""
    repo_root = Path(__file__).resolve().parent.parent
    docs_assets = repo_root / "docs" / "assets" / "examples"

    output_dir.mkdir(parents=True, exist_ok=True)

    cpu_generators: list[tuple[str, Callable[[Path], None]]] = [
        ("Quickstart Enclosure", generate_quickstart),
        ("Assembly BOM", generate_assembly_bom),
        ("Stepped Bracket", generate_stepped_bracket),
        ("Algebraic Mode Bracket", generate_algebraic_mode_drawing),
        ("Builder Mode Bracket", generate_builder_mode_drawing),
        ("Advanced Views", generate_advanced_views),
        ("Precision Flange", generate_precision_flange),
        ("Welded Frame Booklet", generate_welded_frame_booklet),
        ("Interactive 3D Blueprint", generate_interactive_3d_blueprint),
        ("SDF Smart Layout", generate_sdf_smart_layout),
    ]

    gpu_generators: list[tuple[str, Callable[[Path], None]]] = [
        ("Shaded Technical Views (GPU)", generate_shaded_technical_views),
        ("GPU HLR Assembly", generate_gpu_hlr_assembly),
    ]

    for name, fn in cpu_generators:
        print(f"Generating {name} drawing...")
        fn(output_dir)

    run_gpu = is_gpu_enabled() if include_gpu is None else include_gpu
    if run_gpu:
        for name, fn in gpu_generators:
            print(f"Generating {name} drawing...")
            fn(output_dir)
    else:
        print("Skipping GPU-only examples (GPU disabled or optional [gpu] extra not installed).")

    if sync_docs:
        docs_assets.mkdir(parents=True, exist_ok=True)
        print("Copying SVG, PNG, and HTML outputs to docs/assets/examples/...")
        for pattern in ("*.svg", "*.png", "*.html"):
            for asset_file in output_dir.glob(pattern):
                shutil.copy2(asset_file, docs_assets / asset_file.name)

    print("All example outputs generated successfully!")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optional custom output directory (skips syncing docs/assets/examples/).",
    )
    parser.add_argument(
        "--no-gpu",
        action="store_true",
        help="Skip GPU-only examples even when GPU support is available.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    include_gpu = False if args.no_gpu else None
    if args.output_dir is not None:
        generate_all(args.output_dir, sync_docs=False, include_gpu=include_gpu)
    else:
        generate_all(repo_root / "examples" / "output", sync_docs=True, include_gpu=include_gpu)


if __name__ == "__main__":
    main()
