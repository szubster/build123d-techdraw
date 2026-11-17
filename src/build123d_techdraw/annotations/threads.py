"""Standard hole and thread database backed by ``bd_warehouse``."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

# ISO 261 coarse pitches (mm) for metric threads M1.5 - M64
_ISO_261_COARSE_PITCH: dict[float, float] = {
    1.5: 0.35,
    1.6: 0.35,
    1.8: 0.35,
    2.0: 0.4,
    2.2: 0.45,
    2.5: 0.45,
    3.0: 0.5,
    3.5: 0.6,
    4.0: 0.7,
    4.5: 0.75,
    5.0: 0.8,
    5.5: 0.9,
    6.0: 1.0,
    7.0: 1.0,
    8.0: 1.25,
    9.0: 1.25,
    10.0: 1.5,
    11.0: 1.5,
    12.0: 1.75,
    14.0: 2.0,
    15.0: 1.5,
    16.0: 2.0,
    17.0: 1.5,
    18.0: 2.5,
    20.0: 2.5,
    22.0: 2.5,
    24.0: 3.0,
    27.0: 3.0,
    30.0: 3.5,
    33.0: 3.5,
    36.0: 4.0,
    39.0: 4.0,
    42.0: 4.5,
    45.0: 4.5,
    48.0: 5.0,
    52.0: 5.0,
    56.0: 5.5,
    60.0: 5.5,
    64.0: 6.0,
}

# Common ISO 261 fine pitches (mm) for M6 - M64
_ISO_261_FINE_PITCHES: dict[float, tuple[float, ...]] = {
    6.0: (0.75, 0.5),
    8.0: (1.0, 0.75),
    10.0: (1.25, 1.0, 0.75),
    12.0: (1.5, 1.25, 1.0),
    14.0: (1.5, 1.25, 1.0),
    16.0: (1.5, 1.0),
    18.0: (2.0, 1.5, 1.0),
    20.0: (2.0, 1.5, 1.0),
    22.0: (2.0, 1.5, 1.0),
    24.0: (2.0, 1.5),
    27.0: (2.0, 1.5),
    30.0: (2.0, 1.5),
    33.0: (2.0, 1.5),
    36.0: (3.0, 2.0, 1.5),
    39.0: (3.0, 2.0, 1.5),
    42.0: (3.0, 2.0, 1.5),
    45.0: (3.0, 2.0, 1.5),
    48.0: (3.0, 2.0, 1.5),
    52.0: (4.0, 3.0, 2.0),
    56.0: (4.0, 3.0, 2.0),
    60.0: (4.0, 3.0, 2.0),
    64.0: (4.0, 3.0, 2.0),
}

_FIT_MAP: dict[str, str] = {
    "close": "Close",
    "normal": "Normal",
    "medium": "Normal",
    "loose": "Loose",
    "coarse": "Loose",
}

_METRIC_RE = re.compile(
    r"^\s*[mM](\d+(?:\.\d+)?)\s*(?:[x×\-]\s*(\d+(?:\.\d+)?))?\s*(?:([4567][gGhH](?:[4567][gGhH])?))?\s*$"
)
_IMPERIAL_RE = re.compile(
    r"^\s*(#?\d+(?:-\d+/\d+|\s+\d+/\d+|/\d+)?)\s*(?:-\s*(\d+(?:\.\d+)?))?\s*(UNC|UNF|UNEF|UNS)?\s*$",
    re.IGNORECASE,
)


def _require_bd_warehouse() -> tuple[Any, Any]:
    """Import ``bd_warehouse.fastener`` and ``bd_warehouse.thread`` or raise ``ImportError``."""
    try:
        from bd_warehouse import fastener, thread
    except ImportError as exc:
        raise ImportError(
            "bd_warehouse is required for the thread and hole database. "
            "Install it with `pip install bd-warehouse` or `uv add bd-warehouse`."
        ) from exc
    return fastener, thread


def _fmt_num(value: float) -> str:
    """Format a float without trailing zeros (e.g. 6.0 -> '6', 1.25 -> '1.25')."""
    if abs(value - round(value)) < 1e-6:
        return str(int(round(value)))
    return f"{value:g}"


def _fmt_pitch(value: float) -> str:
    """Format metric pitch cleanly (e.g. 1.0 -> '1.0', 1.25 -> '1.25')."""
    if abs(value - round(value)) < 1e-6:
        return f"{int(round(value))}.0"
    return f"{value:g}"


@dataclass(frozen=True)
class ThreadSpec:
    """Standardized thread and hole specification derived from ``bd_warehouse``."""

    designation: str
    standard: str  # "ISO" or "UTS"
    series: str  # "coarse", "fine", "UNC", "UNF", "UNEF"
    nominal_size: str
    major_diameter: float  # mm
    pitch: float  # mm
    tpi: float | None  # threads per inch for UTS
    tap_drill: float  # mm (default soft/standard tap drill)
    tap_drill_soft: float  # mm
    tap_drill_hard: float  # mm
    tap_drill_label: str | None
    clearance_close: float | None
    clearance_normal: float | None
    clearance_loose: float | None
    default_class_fit: str


def _imperial_nominal_diameter_mm(nominal: str, thread_mod: Any) -> float:
    """Convert an imperial nominal size like '#10' or '1/4' to mm."""
    if nominal.startswith("#"):
        num = int(nominal[1:])
        return round((0.060 + 0.013 * num) * 25.4, 4)
    val = thread_mod.imperial_str_to_float(nominal)
    return round(float(val), 4)


def _build_uts_series_map(tap_keys: list[str]) -> dict[str, str]:
    """Classify each imperial key in bd_warehouse tap_hole_sizes into UNC/UNF/UNEF."""
    by_nominal: dict[str, list[tuple[float, str]]] = {}
    for key in tap_keys:
        if key.startswith("M") or "-" not in key:
            continue
        nom, tpi_str = key.rsplit("-", maxsplit=1)
        try:
            tpi = float(tpi_str)
        except ValueError:
            continue
        by_nominal.setdefault(nom, []).append((tpi, key))

    series_map: dict[str, str] = {}
    for _nom, entries in by_nominal.items():
        entries.sort(key=lambda item: item[0])
        for idx, (_tpi, key) in enumerate(entries):
            if idx == 0:
                series_map[key] = "UNC"
            elif idx == 1:
                series_map[key] = "UNF"
            else:
                series_map[key] = "UNEF"
    return series_map


def _lookup_metric_tap_drills(
    nominal: str, major: float, pitch: float, fastener_mod: Any
) -> tuple[float, float, str | None]:
    """Look up metric tap drills in bd_warehouse, falling back to ISO 261 formula for >M22."""
    tap_data: dict[str, dict[str, float]] = fastener_mod.Nut.tap_hole_data
    tap_labels: dict[str, dict[str, str]] = fastener_mod.Nut.tap_hole_drill_sizes

    candidates = [
        f"{nominal}-{_fmt_num(pitch)}",
        f"{nominal}-{_fmt_pitch(pitch)}",
        f"{nominal}-{pitch:g}",
    ]
    for key in candidates:
        if key in tap_data:
            row = tap_data[key]
            lbl_row = tap_labels.get(key, {})
            return float(row["Soft"]), float(row["Hard"]), lbl_row.get("Soft")

    # Standard ISO 68-1 / ISO 261 75% engagement tap drill for sizes above M22 or extra fine pitches
    tap_val = round(major - pitch, 2)
    return tap_val, tap_val, _fmt_num(tap_val)


def _lookup_clearance_row(
    nominal: str, fastener_mod: Any
) -> tuple[float | None, float | None, float | None]:
    """Look up Close/Normal/Loose clearance diameters (mm) in bd_warehouse."""
    clr_data: dict[str, dict[str, float]] = fastener_mod.Nut.clearance_hole_data
    row = clr_data.get(nominal)
    if row is None:
        return None, None, None
    return (
        round(float(row["Close"]), 3),
        round(float(row["Normal"]), 3),
        round(float(row["Loose"]), 3),
    )


def coerce_thread_designation(target: str | Any) -> str:
    """Extract a thread designation string from a string, ``IsoThread``, or ``bd_warehouse`` fastener."""
    if isinstance(target, str):
        return target
    fastener_mod, thread_mod = _require_bd_warehouse()
    if isinstance(target, thread_mod.IsoThread):
        return f"M{_fmt_num(float(target.major_diameter))}x{_fmt_pitch(float(target.pitch))}"
    if isinstance(target, (fastener_mod.Screw, fastener_mod.Nut, fastener_mod.Washer)):
        return str(target.thread_size)
    raise TypeError(
        f"Expected thread designation str, IsoThread, or bd_warehouse fastener, got {type(target).__name__}"
    )


def get_thread_spec(designation: str | Any) -> ThreadSpec:
    """Look up a metric (ISO 261) or imperial (UNC/UNF/UNEF) thread specification.

    Accepts a designation string (e.g. ``'M6'``, ``'1/4-20 UNC'``), an
    ``IsoThread``, or a ``bd_warehouse`` ``Screw``/``Nut`` instance.
    Raises ``ImportError`` if ``bd_warehouse`` is not installed, and ``ValueError``
    if ``designation`` cannot be resolved to a known standard thread.
    """
    fastener_mod, thread_mod = _require_bd_warehouse()

    raw = coerce_thread_designation(designation).strip()
    metric_match = _METRIC_RE.match(raw)
    if metric_match:
        major = float(metric_match.group(1))
        pitch_str = metric_match.group(2)
        nominal = f"M{_fmt_num(major)}"
        coarse_pitch = _ISO_261_COARSE_PITCH.get(major)

        if pitch_str is not None:
            pitch = float(pitch_str)
            series = (
                "coarse"
                if (coarse_pitch is not None and abs(pitch - coarse_pitch) < 1e-6)
                else "fine"
            )
        elif coarse_pitch is not None:
            pitch = coarse_pitch
            series = "coarse"
        else:
            raise ValueError(f"Unknown metric thread size: {designation!r}")

        tap_soft, tap_hard, tap_label = _lookup_metric_tap_drills(
            nominal, major, pitch, fastener_mod
        )
        clr_close, clr_normal, clr_loose = _lookup_clearance_row(nominal, fastener_mod)
        canon = f"{nominal}x{_fmt_pitch(pitch)}"
        return ThreadSpec(
            designation=canon,
            standard="ISO",
            series=series,
            nominal_size=nominal,
            major_diameter=major,
            pitch=pitch,
            tpi=None,
            tap_drill=round(tap_soft, 3),
            tap_drill_soft=round(tap_soft, 3),
            tap_drill_hard=round(tap_hard, 3),
            tap_drill_label=tap_label,
            clearance_close=clr_close,
            clearance_normal=clr_normal,
            clearance_loose=clr_loose,
            default_class_fit="6H",
        )

    imp_match = _IMPERIAL_RE.match(raw)
    if imp_match:
        nom_raw = imp_match.group(1).strip()
        tpi_str = imp_match.group(2)
        series_req = imp_match.group(3).upper() if imp_match.group(3) else None

        tap_data: dict[str, dict[str, float]] = fastener_mod.Nut.tap_hole_data
        tap_labels: dict[str, dict[str, str]] = fastener_mod.Nut.tap_hole_drill_sizes
        series_map = _build_uts_series_map(list(tap_data.keys()))

        # Normalize '#10' vs '10'
        nom_candidates = [nom_raw]
        if nom_raw.isdigit() and f"#{nom_raw}" in fastener_mod.Nut.clearance_hole_data:
            nom_candidates.insert(0, f"#{nom_raw}")

        matched_key: str | None = None
        for nom in nom_candidates:
            if tpi_str is not None:
                cand = f"{nom}-{tpi_str}"
                if cand in tap_data:
                    matched_key = cand
                    break
            else:
                target_series = series_req or "UNC"
                for key, ser in series_map.items():
                    if key.startswith(f"{nom}-") and ser == target_series:
                        matched_key = key
                        break
                if matched_key:
                    break

        if matched_key is None:
            raise ValueError(f"Unknown imperial thread specification: {designation!r}")

        nom, tpi_part = matched_key.rsplit("-", maxsplit=1)
        tpi = float(tpi_part)
        pitch_mm = round(25.4 / tpi, 4)
        series = series_req or series_map.get(matched_key, "UNC")
        major_mm = _imperial_nominal_diameter_mm(nom, thread_mod)
        row = tap_data[matched_key]
        lbl_row = tap_labels.get(matched_key, {})
        clr_close, clr_normal, clr_loose = _lookup_clearance_row(nom, fastener_mod)

        return ThreadSpec(
            designation=f"{matched_key} {series}",
            standard="UTS",
            series=series,
            nominal_size=nom,
            major_diameter=major_mm,
            pitch=pitch_mm,
            tpi=tpi,
            tap_drill=round(float(row["Soft"]), 3),
            tap_drill_soft=round(float(row["Soft"]), 3),
            tap_drill_hard=round(float(row["Hard"]), 3),
            tap_drill_label=lbl_row.get("Soft"),
            clearance_close=clr_close,
            clearance_normal=clr_normal,
            clearance_loose=clr_loose,
            default_class_fit="2B",
        )

    raise ValueError(f"Unrecognized thread designation: {designation!r}")


def get_tap_drill(designation: str, material: str = "soft") -> float:
    """Return the tap drill diameter (mm) for ``designation``.

    ``material`` may be ``"soft"`` (aluminum/brass/plastics) or ``"hard"`` (steel/stainless/iron).
    """
    spec = get_thread_spec(designation)
    mat_norm = material.strip().lower()
    if mat_norm == "soft":
        return spec.tap_drill_soft
    if mat_norm == "hard":
        return spec.tap_drill_hard
    raise ValueError(f"Invalid material {material!r}; expected 'soft' or 'hard'.")


def get_clearance_hole(designation: str | Any, fit: str = "medium") -> float:
    """Return the standard clearance hole diameter (mm) from ``bd_warehouse``.

    ``designation`` can be a nominal fastener size (``"M6"``, ``"1/4"``, ``"#10"``),
    a full thread designation (``"M6x1.0"``, ``"1/4-20 UNC"``), or a ``bd_warehouse``
    ``IsoThread`` / ``Screw`` / ``Nut`` instance.
    ``fit`` may be ``"close"``, ``"medium"`` (or ``"normal"``), or ``"loose"`` (or ``"coarse"``).
    """
    fastener_mod, _ = _require_bd_warehouse()
    fit_key = _FIT_MAP.get(fit.strip().lower())
    if fit_key is None:
        raise ValueError(
            f"Invalid clearance fit {fit!r}; expected one of 'close', 'medium', 'normal', 'loose', 'coarse'."
        )

    raw = coerce_thread_designation(designation).strip()
    clr_data: dict[str, dict[str, float]] = fastener_mod.Nut.clearance_hole_data

    # Direct nominal match first (handles M1-M150, #0-#12, 1/4-4")
    direct_candidates = [raw, raw.upper()]
    if raw.isdigit():
        direct_candidates.append(f"#{raw}")
    for cand in direct_candidates:
        if cand in clr_data:
            return round(float(clr_data[cand][fit_key]), 3)

    # Otherwise resolve via thread spec
    spec = get_thread_spec(raw)
    if spec.nominal_size in clr_data:
        return round(float(clr_data[spec.nominal_size][fit_key]), 3)
    raise ValueError(f"No clearance hole data for {designation!r}")


@lru_cache(maxsize=4)
def _cached_standard_threads(std_filter: str | None) -> tuple[ThreadSpec, ...]:
    """Cached builder for standard metric and imperial thread specs."""
    fastener_mod, _ = _require_bd_warehouse()
    specs: list[ThreadSpec] = []

    if std_filter in (None, "ISO", "METRIC"):
        for major, coarse_p in sorted(_ISO_261_COARSE_PITCH.items()):
            if major < 2.0:
                continue
            specs.append(get_thread_spec(f"M{_fmt_num(major)}x{_fmt_pitch(coarse_p)}"))
            for fine_p in _ISO_261_FINE_PITCHES.get(major, ()):
                specs.append(get_thread_spec(f"M{_fmt_num(major)}x{_fmt_pitch(fine_p)}"))

    if std_filter in (None, "UTS", "IMPERIAL"):
        tap_data: dict[str, dict[str, float]] = fastener_mod.Nut.tap_hole_data
        series_map = _build_uts_series_map(list(tap_data.keys()))
        for key, series in series_map.items():
            specs.append(get_thread_spec(f"{key} {series}"))

    return tuple(specs)


def list_standard_threads(standard: str | None = None) -> list[ThreadSpec]:
    """Return all standard metric (ISO 261 M2-M64) and/or imperial (UTS) thread specs."""
    _require_bd_warehouse()
    std_filter = standard.strip().upper() if standard else None
    return list(_cached_standard_threads(std_filter))


def find_threads_by_tap_drill(diameter: float, tolerance: float = 0.08) -> list[ThreadSpec]:
    """Find standard threads whose tap drill diameter matches ``diameter`` within ``tolerance`` (mm).

    Results are sorted by closeness to ``tap_drill_soft`` and prefer metric coarse threads on ties.
    """
    _require_bd_warehouse()
    matches: list[tuple[float, int, ThreadSpec]] = []
    for spec in list_standard_threads():
        diff = abs(spec.tap_drill_soft - diameter)
        if diff <= tolerance:
            priority = (
                0
                if (spec.standard == "ISO" and spec.series == "coarse")
                else (1 if spec.standard == "ISO" else 2)
            )
            matches.append((diff, priority, spec))
    matches.sort(key=lambda item: (round(item[0], 4), item[1], item[2].major_diameter))
    return [item[2] for item in matches]
