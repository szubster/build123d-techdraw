"""Regression tests: ISO 1101 form tolerances never reference a datum (BUG-09)."""

from __future__ import annotations

import pytest

from build123d_techdraw import FeatureControlFrame, GDTCharacteristic
from build123d_techdraw.annotations.gdt import FORM_CHARACTERISTICS

_FORM_LIST: list[GDTCharacteristic] = sorted(FORM_CHARACTERISTICS, key=lambda c: c.name)


@pytest.mark.parametrize("characteristic", _FORM_LIST)
def test_form_tolerance_with_datum_refs_is_rejected(characteristic: GDTCharacteristic) -> None:
    with pytest.raises(ValueError, match="form tolerance"):
        FeatureControlFrame(characteristic=characteristic, datum_refs=["A"], position=(0, 0))


def test_form_tolerance_with_datum_a_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not reference a datum"):
        FeatureControlFrame(GDTCharacteristic.FLATNESS, "0.05", datum_a="A", position=(0, 0))


def test_form_characteristics_cover_iso_1101_form_group() -> None:
    assert {
        GDTCharacteristic.STRAIGHTNESS,
        GDTCharacteristic.FLATNESS,
        GDTCharacteristic.CIRCULARITY,
        GDTCharacteristic.CYLINDRICITY,
    } == FORM_CHARACTERISTICS


def test_form_tolerance_without_datum_and_orientation_with_datum_are_accepted() -> None:
    flat = FeatureControlFrame(GDTCharacteristic.FLATNESS, "0.05", position=(0, 0))
    assert flat.datum_a is None
    par = FeatureControlFrame(
        GDTCharacteristic.PARALLELISM, "0.05", datum_refs=["A"], position=(0, 0)
    )
    assert par.datum_a == "A"
