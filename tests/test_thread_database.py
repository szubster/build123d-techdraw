"""Unit tests for the bd_warehouse-backed standard hole & thread database."""

from __future__ import annotations

import builtins
import sys
from typing import Any

import build123d as bd
import pytest

from build123d_techdraw import (
    BuildDrawing,
    DrawingSheet,
    FrontView,
    HoleCallout,
    SheetSize,
    ThreadCallout,
    find_threads_by_tap_drill,
    get_clearance_hole,
    get_tap_drill,
    get_thread_spec,
    list_standard_threads,
    recognize_holes,
)


def test_missing_bd_warehouse_errors_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure missing bd_warehouse raises ImportError with no fallback."""
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "bd_warehouse" or name.startswith("bd_warehouse."):
            raise ImportError("No module named 'bd_warehouse'")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "bd_warehouse", raising=False)
    monkeypatch.delitem(sys.modules, "bd_warehouse.fastener", raising=False)
    monkeypatch.delitem(sys.modules, "bd_warehouse.thread", raising=False)
    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ImportError, match="bd_warehouse"):
        get_thread_spec("M6")

    with pytest.raises(ImportError, match="bd_warehouse"):
        get_clearance_hole("M6")

    with pytest.raises(ImportError, match="bd_warehouse"):
        ThreadCallout((50.0, 50.0), thread="M6")

    with pytest.raises(ImportError, match="bd_warehouse"):
        HoleCallout((50.0, 50.0), clearance_for="M6")


def test_metric_thread_lookup_m2_to_m64() -> None:
    """Verify metric coarse and fine ISO 261 thread specs and bd_warehouse tap/clearance tables."""
    m6 = get_thread_spec("M6")
    assert m6.designation == "M6x1.0"
    assert m6.standard == "ISO"
    assert m6.series == "coarse"
    assert m6.major_diameter == pytest.approx(6.0)
    assert m6.pitch == pytest.approx(1.0)
    assert m6.tap_drill == pytest.approx(5.0)
    assert m6.clearance_close == pytest.approx(6.4)
    assert m6.clearance_normal == pytest.approx(6.6)
    assert m6.clearance_loose == pytest.approx(7.0)

    m8_fine = get_thread_spec("M8x1")
    assert m8_fine.designation == "M8x1.0"
    assert m8_fine.series == "fine"
    assert m8_fine.tap_drill == pytest.approx(7.0)

    m2 = get_thread_spec("M2")
    assert m2.designation == "M2x0.4"
    assert m2.tap_drill == pytest.approx(1.6)

    m64 = get_thread_spec("M64")
    assert m64.designation == "M64x6.0"
    assert m64.tap_drill == pytest.approx(58.0)
    assert m64.clearance_normal == pytest.approx(70.0)


def test_imperial_unc_unf_lookup() -> None:
    """Verify imperial UNC/UNF thread parsing and bd_warehouse tap/clearance lookup."""
    q20 = get_thread_spec("1/4-20")
    assert q20.designation == "1/4-20 UNC"
    assert q20.standard == "UTS"
    assert q20.series == "UNC"
    assert q20.tpi == pytest.approx(20.0)
    assert q20.pitch == pytest.approx(25.4 / 20.0, rel=1e-3)
    assert q20.tap_drill == pytest.approx(0.201 * 25.4, abs=0.05)  # #7 drill
    assert q20.clearance_normal == pytest.approx((9 / 32) * 25.4, abs=0.05)

    n10_unf = get_thread_spec("#10-32 UNF")
    assert n10_unf.designation == "#10-32 UNF"
    assert n10_unf.series == "UNF"
    assert n10_unf.tpi == pytest.approx(32.0)

    assert get_tap_drill("M6", material="hard") == pytest.approx(5.4)
    assert get_clearance_hole("M10", fit="close") == pytest.approx(10.5)
    assert get_clearance_hole("M10", fit="medium") == pytest.approx(11.0)
    assert get_clearance_hole("M10", fit="loose") == pytest.approx(12.0)


def test_thread_and_hole_callout_integration_dual_mode() -> None:
    """Verify ThreadCallout & HoleCallout integration in both Builder and Algebraic modes."""
    box = bd.Box(40.0, 30.0, 10.0)

    # Builder mode
    with (
        BuildDrawing(SheetSize.A4_LANDSCAPE),
        FrontView(box, center=(120.0, 100.0), scale=1.0) as front,
    ):
        tc = ThreadCallout((0.0, 0.0), thread="M6", show_tap_drill=True)
        hc = HoleCallout((10.0, 0.0), clearance_for="M6", fit="medium")
        front.thread_callout(0.0, 5.0, thread="1/4-20")
        front.hole_callout(5.0, 5.0, clearance_for="M8", fit="close")

    assert tc.thread == "M6x1.0"
    assert tc.pitch == pytest.approx(1.0)
    assert tc.tap_drill == pytest.approx(5.0)
    assert "M6x1.0" in tc.lines[0]
    assert "⌀5 TAP" in tc.lines[0]
    assert hc.diameter == pytest.approx(6.6)
    assert "⌀6.6" in hc.lines[0]

    # Algebraic mode
    alg_sheet = DrawingSheet(SheetSize.A4_LANDSCAPE)
    alg_tc = alg_sheet.add_thread_callout((60.0, 60.0), thread="M10")
    alg_hc = alg_sheet.add_hole_callout((80.0, 60.0), clearance_for="M10", fit="loose")
    assert alg_tc.thread == "M10x1.5"
    assert alg_hc.diameter == pytest.approx(12.0)

    # Custom non-standard thread string passes through cleanly
    custom_tc = ThreadCallout((40.0, 40.0), thread="G1/4 BSPP")
    assert custom_tc.thread == "G1/4 BSPP"
    assert custom_tc.thread_spec is None


def test_find_threads_by_tap_drill_and_recognize_holes() -> None:
    """Verify tap-drill reverse lookup and opt-in thread inference in recognize_holes."""
    matches = find_threads_by_tap_drill(5.0)
    assert any(m.designation == "M6x1.0" for m in matches)

    plate = bd.Box(80.0, 60.0, 10.0) - bd.Cylinder(radius=2.5, height=20.0)  # Ø5.0 hole

    plain_holes = recognize_holes(plate)
    assert len(plain_holes) == 1
    assert plain_holes[0].is_threaded is False
    assert plain_holes[0].thread_spec is None

    inferred_holes = recognize_holes(plate, infer_threads=True)
    assert len(inferred_holes) == 1
    assert inferred_holes[0].is_threaded is True
    assert inferred_holes[0].thread_spec == "M6x1.0"
    assert inferred_holes[0].thread_source == "inferred"

    all_threads = list_standard_threads()
    assert len(all_threads) > 50
