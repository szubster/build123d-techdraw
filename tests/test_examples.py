"""Tests verifying all examples run cleanly, deterministic SVGs stay exact, and GPU rasters match."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from build123d_techdraw import is_gpu_enabled
from tests.raster_diff import assert_images_match, extract_embedded_pngs_from_svg

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = REPO_ROOT / "examples"

EXPECTED_CPU_PREFIXES = [
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
]

EXPECTED_STANDALONE_PNGS = [
    "sdf_smart_layout_heatmap.png",
    "shaded_gooch.png",
    "shaded_blueprint.png",
    "shaded_bom_colors.png",
    "shaded_xray.png",
]


def test_all_examples_registered_in_generate_all() -> None:
    """Ensure every drawing example script in examples/ is wired into generate_all.py."""
    generate_all_src = (EXAMPLES_DIR / "generate_all.py").read_text(encoding="utf-8")
    non_drawing_scripts = {"generate_all.py", "benchmark_performance.py"}
    scripts = sorted(p.stem for p in EXAMPLES_DIR.glob("*.py") if p.name not in non_drawing_scripts)
    assert len(scripts) == 11
    for script in scripts:
        assert f"from {script} import" in generate_all_src, (
            f"Example script '{script}.py' is not imported in examples/generate_all.py"
        )


def test_generate_all_examples_runs_cleanly(tmp_path: Path) -> None:
    """Run examples/generate_all.py into a temporary directory and verify all outputs."""
    result = subprocess.run(
        [sys.executable, str(EXAMPLES_DIR / "generate_all.py"), "--output-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"generate_all.py failed:\n{result.stderr}"

    for prefix in EXPECTED_CPU_PREFIXES:
        for suffix in (".svg", "_white.svg", ".pdf", ".dxf"):
            out_file = tmp_path / f"{prefix}{suffix}"
            assert out_file.exists(), f"Missing generated output: {out_file.name}"
            assert out_file.stat().st_size > 0, f"Empty generated output: {out_file.name}"

    assert (tmp_path / "interactive_3d_blueprint.html").stat().st_size > 0
    assert (tmp_path / "sdf_smart_layout_heatmap.png").stat().st_size > 0

    if is_gpu_enabled():
        for name in (
            "shaded_technical_views.svg",
            "shaded_technical_views_white.svg",
            "shaded_technical_views.pdf",
            "shaded_gooch.png",
            "shaded_blueprint.png",
            "shaded_bom_colors.png",
            "shaded_xray.png",
        ):
            out_file = tmp_path / name
            assert out_file.exists(), f"Missing GPU example output: {out_file.name}"
            assert out_file.stat().st_size > 0, f"Empty GPU example output: {out_file.name}"


@pytest.mark.gpu
def test_gpu_example_raster_outputs_match_committed(tmp_path: Path) -> None:
    """Verify GPU-generated example rasters and shaded SVGs perceptually match committed assets."""
    if not is_gpu_enabled():
        pytest.skip("GPU disabled or optional [gpu] extra unavailable")

    committed_dir = EXAMPLES_DIR / "output"
    for script_name in (
        "shaded_technical_views.py",
        "sdf_smart_layout.py",
    ):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import importlib.util, sys; from pathlib import Path; "
                    f"spec = importlib.util.spec_from_file_location('_ex', {str(EXAMPLES_DIR / script_name)!r}); "
                    "assert spec and spec.loader; mod = importlib.util.module_from_spec(spec); "
                    "spec.loader.exec_module(mod); "
                    f"getattr(mod, 'generate_{script_name[:-3]}')(Path({str(tmp_path)!r}))"
                ),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{script_name} failed:\n{result.stderr}"

    # 1. Compare all standalone PNG renders (Gooch, Blueprint, BOM colors, X-Ray, SDF heatmap)
    for png_name in EXPECTED_STANDALONE_PNGS:
        expected_png = committed_dir / png_name
        actual_png = tmp_path / png_name
        assert expected_png.exists(), f"Missing committed reference raster: {expected_png}"
        assert_images_match(
            actual_png,
            expected_png,
            min_ssim=0.985,
            max_diff_pixel_ratio=0.02,
            pixel_tolerance=6,
            save_diff_to=tmp_path / f"diff_{png_name}",
            label=png_name,
        )

    # 2. Compare every embedded base64 PNG inside shaded_technical_views SVGs
    for svg_name in ("shaded_technical_views.svg", "shaded_technical_views_white.svg"):
        actual_imgs = extract_embedded_pngs_from_svg(tmp_path / svg_name)
        expected_imgs = extract_embedded_pngs_from_svg(committed_dir / svg_name)
        assert len(actual_imgs) == len(expected_imgs) and len(actual_imgs) >= 4
        for idx, (act_img, exp_img) in enumerate(zip(actual_imgs, expected_imgs, strict=True)):
            assert_images_match(
                act_img,
                exp_img,
                min_ssim=0.985,
                max_diff_pixel_ratio=0.02,
                pixel_tolerance=6,
                save_diff_to=tmp_path / f"diff_{svg_name}_{idx}.png",
                label=f"{svg_name}[image#{idx}]",
            )


def test_docs_example_assets_in_sync() -> None:
    """Verify docs/assets/examples/{svg,png,html} match examples/output/."""
    examples_out = EXAMPLES_DIR / "output"
    docs_assets = REPO_ROOT / "docs" / "assets" / "examples"

    for pattern in ("*.svg", "*.png", "*.html"):
        output_files = sorted(p.name for p in examples_out.glob(pattern))
        docs_files = sorted(p.name for p in docs_assets.glob(pattern))
        assert output_files == docs_files, (
            f"{pattern} mismatch between examples/output ({output_files}) and "
            f"docs/assets/examples ({docs_files})"
        )
        for name in output_files:
            assert (examples_out / name).read_bytes() == (docs_assets / name).read_bytes(), (
                f"docs/assets/examples/{name} is out of sync with examples/output/{name}; "
                "run 'uv run python examples/generate_all.py'"
            )
