"""Unit tests for ISO 5455 scale ladders and auto-scale calculations."""

import build123d as bd
import pytest

from build123d_techdraw.builder import BuildDrawing, BuildView
from build123d_techdraw.core.scale import (
    STANDARD_SCALES,
    calculate_auto_scale,
    find_closest_iso_scale,
    format_iso_scale,
)


def test_format_iso_scale() -> None:
    assert format_iso_scale(1.0) == "SCALE 1:1"
    assert format_iso_scale(0.5) == "SCALE 1:2"
    assert format_iso_scale(0.2) == "SCALE 1:5"
    assert format_iso_scale(0.1) == "SCALE 1:10"
    assert format_iso_scale(2.0) == "SCALE 2:1"
    assert format_iso_scale(1.5) == "SCALE 3:2"
    assert format_iso_scale(2.5) == "SCALE 5:2"
    assert format_iso_scale(5.0) == "SCALE 5:1"


def test_find_closest_iso_scale() -> None:
    assert find_closest_iso_scale(1.0) == 1.0
    assert find_closest_iso_scale(0.95) == 0.5
    assert find_closest_iso_scale(0.49) == 0.2
    assert find_closest_iso_scale(3.0) == 2.0


def test_calculate_auto_scale() -> None:
    # 200x100mm model fitting into 250x150mm available area
    scale = calculate_auto_scale((200.0, 100.0), (250.0, 150.0), margin_factor=0.8)
    assert scale <= 1.0
    assert scale in [0.5, 1.0, 0.2]

    # Zero or negative handling
    assert calculate_auto_scale((0.0, 10.0), (100.0, 100.0)) == 1.0


def test_build_view_enforce_iso_scale() -> None:
    """Verify BuildView snaps automatic and arbitrary scales to ISO 5455 ladder when enforce_iso_scale=True."""
    allowed_iso = {1.0, 0.5, 0.2, 0.1, 0.05, 2.0, 5.0}
    assert set(STANDARD_SCALES) == allowed_iso

    part = bd.Box(260.0, 140.0, 110.0)
    with BuildDrawing("A4") as sheet:
        with BuildView(part, scale="auto", enforce_iso_scale=True) as v_snapped:
            assert BuildView._get_context() is v_snapped
        with BuildView(part, scale="auto", enforce_iso_scale=False) as v_raw:
            pass
        with BuildView(part, scale=0.37, enforce_iso_scale=True) as v_quantized:
            pass
        with BuildView(part, scale=0.37, enforce_iso_scale=False) as v_exact:
            pass

    assert v_snapped.scale in allowed_iso
    assert v_snapped.scale == 0.5
    assert v_raw.scale not in allowed_iso
    assert v_quantized.scale == 0.2
    assert v_exact.scale == pytest.approx(0.37)
    assert len(sheet.views) == 4
