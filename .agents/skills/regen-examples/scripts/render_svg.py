"""Rasterize drawing SVGs to PNG so an agent (or a human) can look at them.

The sheets use CSS custom properties for theming, so a real browser engine is
needed; this uses Playwright's Chromium. Usage:

    uv run --with playwright python .agents/skills/regen-examples/scripts/render_svg.py \
        [--out DIR] [--scale 1.5] FILE.svg [FILE.svg ...]

With no files, renders every SVG under examples/output/ that differs from git HEAD.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PREINSTALLED_CHROMIUM = Path("/opt/pw-browsers/chromium")


def _strip_xml_decl(text: str) -> str:
    return text.split("?>", 1)[1] if text.lstrip().startswith("<?xml") else text


def changed_svgs() -> list[Path]:
    out = subprocess.run(
        ["git", "status", "--porcelain", "--", "examples/output/*.svg"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [Path(line[3:]) for line in out.splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", type=Path)
    parser.add_argument(
        "--out", type=Path, default=Path(tempfile.gettempdir()) / "techdraw-renders"
    )
    parser.add_argument("--scale", type=float, default=1.5, help="device pixels per CSS px")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright missing: run via `uv run --with playwright python ...`", file=sys.stderr)
        return 2

    files = args.files or changed_svgs()
    if not files:
        print("No changed SVGs under examples/output/.")
        return 0
    args.out.mkdir(parents=True, exist_ok=True)

    launch: dict[str, object] = {}
    exe = os.environ.get("CHROMIUM_PATH") or (
        str(PREINSTALLED_CHROMIUM) if PREINSTALLED_CHROMIUM.exists() else shutil.which("chromium")
    )
    if exe:
        launch["executable_path"] = exe

    with sync_playwright() as pw:
        browser = pw.chromium.launch(**launch)
        page = browser.new_page(device_scale_factor=args.scale)
        for svg in files:
            # Inline the SVG in a bare HTML page sized to the sheet (1 mm = 96/25.4 px).
            page.set_content(
                "<!doctype html><style>html,body{margin:0}svg{display:block}</style>"
                + _strip_xml_decl(svg.read_text(encoding="utf-8"))
            )
            w, h = page.evaluate(
                "() => { const r = document.querySelector('svg').getBoundingClientRect();"
                " return [Math.ceil(r.width), Math.ceil(r.height)]; }"
            )
            page.set_viewport_size({"width": max(w, 1), "height": max(h, 1)})
            target = args.out / (svg.stem + ".png")
            page.locator("svg").first.screenshot(path=str(target), animations="disabled")
            print(target)
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
