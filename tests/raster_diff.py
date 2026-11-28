"""Perceptual raster visual regression harness for GPU-rendered drawings and SVG rasters.

Combines structural similarity (SSIM via ``scikit-image``) with bounded per-pixel channel
tolerance so minor 1–2 LSB driver rounding differences across hardware GPUs (Vulkan/D3D12/
Metal/OpenGL) and headless software Mesa Lavapipe pass cleanly while genuine visual
regressions fail deterministically and emit a 3-panel diagnostic diff PNG.
"""

from __future__ import annotations

import base64
import io
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PIL import Image
from skimage.metrics import structural_similarity

ImageInput = Image.Image | Path | str | bytes

_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
_PNG_DATA_URI_RE = re.compile(
    r"^data:image/png(?:;[^;,]+)*;base64,(.*)$", flags=re.IGNORECASE | re.DOTALL
)


@dataclass(frozen=True)
class RasterDiffResult:
    """Quantitative comparison result between an actual and expected raster image."""

    ssim: float
    max_abs_diff: int
    mean_abs_diff: float
    diff_pixel_ratio: float
    passed: bool
    diff_image_path: Path | None = None


def _load_rgba_image(source: ImageInput) -> Image.Image:
    """Load an image from PIL Image, file path, or raw encoded bytes as RGBA."""
    if isinstance(source, Image.Image):
        return source.convert("RGBA")
    if isinstance(source, bytes):
        with Image.open(io.BytesIO(source)) as img:
            return img.convert("RGBA")
    path = Path(source)
    with Image.open(path) as img:
        return img.convert("RGBA")


def _compute_ssim(actual_arr: NDArray[np.uint8], expected_arr: NDArray[np.uint8]) -> float:
    """Compute multi-channel SSIM in [0, 1] safely across arbitrary image sizes."""
    h, w = actual_arr.shape[:2]
    min_dim = min(h, w)
    if min_dim < 3:
        # SSIM requires at least a 3x3 window; fall back to normalized squared similarity.
        mse = float(np.mean((actual_arr.astype(np.float64) - expected_arr.astype(np.float64)) ** 2))
        return max(0.0, 1.0 - mse / (255.0 * 255.0))

    win_size = min(7, min_dim)
    if win_size % 2 == 0:
        win_size -= 1

    score = structural_similarity(
        expected_arr,
        actual_arr,
        win_size=win_size,
        channel_axis=-1,
        data_range=255,
    )
    if isinstance(score, tuple):
        return float(score[0])
    return float(score)


def _save_diff_panel(
    expected_img: Image.Image,
    actual_img: Image.Image,
    save_path: Path,
    *,
    pixel_tolerance: int,
) -> Path:
    """Write a side-by-side 3-panel diagnostic PNG: ``[Expected | Actual | Diff]``."""
    save_path.parent.mkdir(parents=True, exist_ok=True)
    ew, eh = expected_img.size
    aw, ah = actual_img.size
    panel_w = max(ew, aw)
    panel_h = max(eh, ah)

    canvas = Image.new("RGBA", (panel_w * 3, panel_h), (24, 24, 24, 255))
    canvas.paste(expected_img, (0, 0))
    canvas.paste(actual_img, (panel_w, 0))

    if (ew, eh) == (aw, ah):
        exp_arr = np.asarray(expected_img, dtype=np.int16)
        act_arr = np.asarray(actual_img, dtype=np.int16)
        abs_diff = np.abs(act_arr - exp_arr)
        max_ch = abs_diff.max(axis=-1)

        # Amplified grayscale diff with bright magenta highlight on out-of-tolerance pixels.
        amp = np.clip(max_ch * 4, 0, 255).astype(np.uint8)
        heat = np.empty((eh, ew, 4), dtype=np.uint8)
        heat[:, :, 0] = amp
        heat[:, :, 1] = amp
        heat[:, :, 2] = amp
        heat[:, :, 3] = 255
        exceeded = max_ch > pixel_tolerance
        heat[exceeded, 0] = 255
        heat[exceeded, 1] = 32
        heat[exceeded, 2] = 96
        heat[exceeded, 3] = 255
        canvas.paste(Image.fromarray(heat, mode="RGBA"), (panel_w * 2, 0))
    else:
        # Highlight dimension mismatch in solid crimson on the 3rd panel.
        mismatch = Image.new("RGBA", (panel_w, panel_h), (200, 32, 64, 255))
        canvas.paste(mismatch, (panel_w * 2, 0))

    canvas.save(save_path, format="PNG")
    return save_path


def compare_images(
    actual: ImageInput,
    expected: ImageInput,
    *,
    min_ssim: float = 0.985,
    max_diff_pixel_ratio: float = 0.015,
    pixel_tolerance: int = 4,
    save_diff_to: Path | str | None = None,
    always_save_diff: bool = False,
) -> RasterDiffResult:
    """Compare two raster images using perceptual SSIM plus per-pixel channel tolerance.

    Args:
        actual: Actual rendered image (PIL Image, path, or encoded PNG bytes).
        expected: Reference golden image (PIL Image, path, or encoded PNG bytes).
        min_ssim: Minimum required structural similarity index (default ``0.985``).
        max_diff_pixel_ratio: Maximum allowed fraction of pixels with any RGBA channel
            error exceeding ``pixel_tolerance`` (default ``0.015`` = 1.5%).
        pixel_tolerance: Per-channel 8-bit tolerance ignored as benign driver/raster
            rounding noise (default ``4``).
        save_diff_to: Optional path to write a 3-panel visual diff PNG on failure
            (or always if ``always_save_diff=True``).
        always_save_diff: If True and ``save_diff_to`` is set, write the diff PNG even
            when the comparison passes.
    """
    actual_img = _load_rgba_image(actual)
    expected_img = _load_rgba_image(expected)

    if actual_img.size != expected_img.size:
        diff_path: Path | None = None
        if save_diff_to is not None:
            diff_path = _save_diff_panel(
                expected_img,
                actual_img,
                Path(save_diff_to),
                pixel_tolerance=pixel_tolerance,
            )
        return RasterDiffResult(
            ssim=0.0,
            max_abs_diff=255,
            mean_abs_diff=255.0,
            diff_pixel_ratio=1.0,
            passed=False,
            diff_image_path=diff_path,
        )

    actual_arr = np.asarray(actual_img, dtype=np.uint8)
    expected_arr = np.asarray(expected_img, dtype=np.uint8)

    abs_diff = np.abs(actual_arr.astype(np.int16) - expected_arr.astype(np.int16))
    max_abs_diff = int(np.max(abs_diff))
    mean_abs_diff = float(np.mean(abs_diff))
    diff_pixel_ratio = float(np.mean(np.max(abs_diff, axis=-1) > pixel_tolerance))
    ssim = _compute_ssim(actual_arr, expected_arr)

    passed = (ssim >= min_ssim) and (diff_pixel_ratio <= max_diff_pixel_ratio)

    diff_path = None
    if save_diff_to is not None and (not passed or always_save_diff):
        diff_path = _save_diff_panel(
            expected_img,
            actual_img,
            Path(save_diff_to),
            pixel_tolerance=pixel_tolerance,
        )

    return RasterDiffResult(
        ssim=ssim,
        max_abs_diff=max_abs_diff,
        mean_abs_diff=mean_abs_diff,
        diff_pixel_ratio=diff_pixel_ratio,
        passed=passed,
        diff_image_path=diff_path,
    )


def assert_images_match(
    actual: ImageInput,
    expected: ImageInput,
    *,
    min_ssim: float = 0.985,
    max_diff_pixel_ratio: float = 0.015,
    pixel_tolerance: int = 4,
    save_diff_to: Path | str | None = None,
    label: str = "image",
) -> RasterDiffResult:
    """Assert two raster images match perceptually, raising a detailed ``AssertionError``."""
    result = compare_images(
        actual,
        expected,
        min_ssim=min_ssim,
        max_diff_pixel_ratio=max_diff_pixel_ratio,
        pixel_tolerance=pixel_tolerance,
        save_diff_to=save_diff_to,
    )
    if not result.passed:
        diff_note = f", diff_png={result.diff_image_path}" if result.diff_image_path else ""
        raise AssertionError(
            f"Visual regression detected for {label!r}: "
            f"ssim={result.ssim:.5f} (min {min_ssim:.5f}), "
            f"diff_pixel_ratio={result.diff_pixel_ratio:.5f} (max {max_diff_pixel_ratio:.5f}, "
            f"tol={pixel_tolerance}), max_abs_diff={result.max_abs_diff}, "
            f"mean_abs_diff={result.mean_abs_diff:.3f}{diff_note}"
        )
    return result


def extract_embedded_pngs_from_svg(svg_text_or_path: str | Path) -> list[Image.Image]:
    """Decode all base64 ``data:image/png`` ``<image>`` layers embedded in an SVG."""
    if isinstance(svg_text_or_path, Path):
        svg_text = svg_text_or_path.read_text(encoding="utf-8")
    else:
        stripped = svg_text_or_path.lstrip()
        if stripped.startswith("<"):
            svg_text = svg_text_or_path
        else:
            candidate = Path(svg_text_or_path)
            svg_text = (
                candidate.read_text(encoding="utf-8") if candidate.exists() else svg_text_or_path
            )

    root = ET.fromstring(svg_text)
    images: list[Image.Image] = []

    for elem in root.iter():
        local_tag = elem.tag.rsplit("}", 1)[-1] if "}" in elem.tag else elem.tag
        if local_tag != "image":
            continue
        href = elem.attrib.get("href") or elem.attrib.get(_XLINK_HREF)
        if not href:
            continue
        match = _PNG_DATA_URI_RE.match(href.strip())
        if not match:
            continue
        b64_payload = re.sub(r"\s+", "", match.group(1))
        raw_bytes = base64.b64decode(b64_payload)
        with Image.open(io.BytesIO(raw_bytes)) as img:
            images.append(img.copy())

    return images
