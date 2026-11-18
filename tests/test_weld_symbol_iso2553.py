"""Regression tests: ISO 2553 welding symbols must not cross their own text.

Covers the dashed identification line crossing the other-side weld text, the tail fork
direction, and the bounding box of other-side text.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from build123d_techdraw import WeldingSymbol, WeldType
from build123d_techdraw.annotations.callouts import WELD_IDENT_LINE_GAP_MM


def _parse(svg: str) -> ET.Element:
    return ET.fromstring(svg)


def _ref_and_dashed_y(root: ET.Element) -> tuple[float, float]:
    lines = root.findall("line")
    dashed = [ln for ln in lines if "weld-ref-dashed" in ln.get("class", "")]
    solid = [
        ln
        for ln in lines
        if "weld-ref-dashed" not in ln.get("class", "") and ln.get("y1") == ln.get("y2")
    ]
    assert len(dashed) == 1 and solid
    return float(solid[0].get("y1", "nan")), float(dashed[0].get("y1", "nan"))


@pytest.mark.parametrize("arrow_side", [True, False])
def test_welding_symbol_lines_do_not_cross_text(arrow_side: bool) -> None:
    """ISO 2553 system A: symbol on the solid line (arrow side) or under the dashed line."""
    weld = WeldingSymbol(
        target=(115.0, 180.0),
        weld_type=WeldType.FILLET,
        size="a4",
        field_weld=True,
        arrow_side=arrow_side,
        elbow_offset=(20.0, 18.0),
    )
    root = _parse(weld.to_svg())
    ref_y, dashed_y = _ref_and_dashed_y(root)
    assert dashed_y == pytest.approx(ref_y + WELD_IDENT_LINE_GAP_MM)

    texts = root.findall("text")
    assert texts
    for t in texts:
        baseline = float(t.get("y", "nan"))
        size = float(t.get("font-size", "nan"))
        top = baseline - 0.75 * size
        if arrow_side:
            # Whole glyph above the continuous reference line.
            assert baseline < ref_y
        else:
            # Whole glyph below the dashed identification line.
            assert top > dashed_y
    if not arrow_side:
        assert any(t.text == "▽" for t in texts)  # mirrored fillet symbol below the line


def test_welding_symbol_tail_fork_opens_away_from_reference_line() -> None:
    weld = WeldingSymbol(
        target=(92.0, 180.0), size="a5", tail_text="GMAW 135", elbow_offset=(22.0, -22.0)
    )
    root = _parse(weld.to_svg())
    fork = next(p for p in root.findall("polyline") if "weld-tail" in p.get("class", ""))
    pts = [tuple(float(v) for v in p.split(",")) for p in fork.get("points", "").split()]
    ref_end_x = 92.0 + 22.0 + weld.shelf_length
    # Apex at the far end of the reference line, legs diverging away from it.
    assert pts[1][0] == pytest.approx(ref_end_x)
    assert pts[0][0] > ref_end_x and pts[2][0] > ref_end_x


def test_welding_symbol_bbox_covers_other_side_text() -> None:
    weld = WeldingSymbol(target=(0.0, 0.0), arrow_side=False, elbow_offset=(10.0, 10.0))
    root = _parse(weld.to_svg())
    max_text_y = max(float(t.get("y", "nan")) for t in root.findall("text"))
    assert weld.bounding_box_2d()[3] >= max_text_y
