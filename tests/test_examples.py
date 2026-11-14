"""Tests verifying all examples run cleanly and stay in sync with docs assets."""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = REPO_ROOT / "examples"

EXPECTED_PREFIXES = [
    "quickstart_enclosure",
    "assembly_drawing",
    "stepped_bracket",
    "algebraic_mode_bracket",
    "builder_mode_bracket",
    "advanced_views",
    "precision_flange",
    "welded_frame_booklet",
]


def test_all_examples_registered_in_generate_all() -> None:
    """Ensure every example script in examples/ is wired into generate_all.py."""
    generate_all_src = (EXAMPLES_DIR / "generate_all.py").read_text(encoding="utf-8")
    scripts = sorted(p.stem for p in EXAMPLES_DIR.glob("*.py") if p.name != "generate_all.py")
    assert len(scripts) == 8
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

    for prefix in EXPECTED_PREFIXES:
        for suffix in (".svg", "_white.svg", ".pdf", ".dxf"):
            out_file = tmp_path / f"{prefix}{suffix}"
            assert out_file.exists(), f"Missing generated output: {out_file.name}"
            assert out_file.stat().st_size > 0, f"Empty generated output: {out_file.name}"


def test_docs_example_svgs_in_sync() -> None:
    """Verify docs/assets/examples/*.svg match examples/output/*.svg."""
    examples_out = EXAMPLES_DIR / "output"
    docs_assets = REPO_ROOT / "docs" / "assets" / "examples"

    output_svgs = sorted(p.name for p in examples_out.glob("*.svg"))
    docs_svgs = sorted(p.name for p in docs_assets.glob("*.svg"))
    assert output_svgs == docs_svgs, (
        f"SVG mismatch between examples/output ({output_svgs}) and docs/assets/examples ({docs_svgs})"
    )

    for name in output_svgs:
        assert (examples_out / name).read_bytes() == (docs_assets / name).read_bytes(), (
            f"docs/assets/examples/{name} is out of sync with examples/output/{name}; "
            "run 'uv run python examples/generate_all.py'"
        )
