"""Standard hole and thread database backed by ``bd_warehouse``."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any

import build123d as bd
from build123d.topology import Shape

from build123d_techdraw.annotations.base import Annotation, BBox2D
from build123d_techdraw.annotations.resolver import is_topological_entity, resolve_point_to_sheet
from build123d_techdraw.context import CURRENT_SHEET, CURRENT_VIEW

if TYPE_CHECKING:
    from build123d_techdraw.core.view import SheetView

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


def resolve_thread_diameters(
    thread: str | Any | None = None,
    nominal_diameter: float | None = None,
    minor_diameter: float | None = None,
) -> tuple[float, float]:
    """Resolve ``(nominal_diameter, minor_diameter)`` in mm from a thread spec or explicit values."""
    parsed_major: float | None = None
    parsed_minor: float | None = None
    if thread is not None:
        spec = get_thread_spec(thread)
        parsed_major = spec.major_diameter
        parsed_minor = spec.tap_drill

    nom = nominal_diameter if nominal_diameter is not None else parsed_major
    min_d = minor_diameter if minor_diameter is not None else parsed_minor

    if nom is None and min_d is None:
        raise ValueError(
            "CosmeticThread requires 'thread', 'nominal_diameter', or 'minor_diameter'."
        )
    if nom is None and min_d is not None:
        nom = round(min_d / 0.85, 3)
    if min_d is None and nom is not None:
        min_d = round(nom * 0.85, 3)

    assert nom is not None and min_d is not None
    return (float(nom), float(min_d))


class CosmeticThread(Annotation):
    """ISO 6410-1 cosmetic thread representation for internal and external threads.

    - End view: 3/4 (270°) thin circular arc (``stroke-width="0.25"``) with a 90° gap in the
      upper-right quadrant, drawn at nominal (major) diameter for internal threads and at
      minor (root) diameter for external threads.
    - Side / Section view: parallel thin lines at the thread crest/root diameter, rendered
      solid in ``SectionView`` and dashed in unsectioned side views.
    """

    def __init__(
        self,
        target: Any = None,
        thread: str | Any | None = None,
        nominal_diameter: float | None = None,
        minor_diameter: float | None = None,
        thread_type: str = "internal",
        depth: float | None = None,
        center: tuple[float, float] | None = None,
        start: tuple[float, float] | None = None,
        end: tuple[float, float] | None = None,
        view_mode: str | None = None,
        gap_start_deg: float = 15.0,
        gap_angle_deg: float = 90.0,
        stroke_width: float = 0.25,
        view: SheetView | None = None,
    ) -> None:
        self.target = target
        self.thread = thread
        self.thread_type = thread_type.lower().strip()
        if self.thread_type not in ("internal", "external"):
            raise ValueError(
                f"Invalid thread_type {thread_type!r}; expected 'internal' or 'external'."
            )
        self.depth = depth
        self.view_mode = view_mode.lower().strip() if view_mode is not None else None
        self.gap_start_deg = float(gap_start_deg)
        self.gap_angle_deg = float(gap_angle_deg)
        self.stroke_width = float(stroke_width)
        self.view: SheetView | None = view

        self._explicit_nominal = nominal_diameter
        self._explicit_minor = minor_diameter
        self.nominal_diameter: float = float(nominal_diameter) if nominal_diameter else 0.0
        self.minor_diameter: float = float(minor_diameter) if minor_diameter else 0.0

        self.center: tuple[float, float] | None = (
            (float(center[0]), float(center[1])) if center is not None else None
        )
        self.start: tuple[float, float] | None = (
            (float(start[0]), float(start[1])) if start is not None else None
        )
        self.end: tuple[float, float] | None = (
            (float(end[0]), float(end[1])) if end is not None else None
        )
        self.paper_radius: float = 0.0
        self.has_end_cap: bool = depth is not None
        self.resolved: bool = False
        self._skip: bool = False

        if self.center is not None and (
            thread is not None or nominal_diameter is not None or minor_diameter is not None
        ):
            self.nominal_diameter, self.minor_diameter = resolve_thread_diameters(
                thread, nominal_diameter, minor_diameter
            )
            if self.view_mode is None:
                self.view_mode = "end"
            scale = view.scale if view is not None else 1.0
            d_eff = self.nominal_diameter if self.thread_type == "internal" else self.minor_diameter
            self.paper_radius = (d_eff / 2.0) * float(scale)
            self.resolved = True
        elif self.start is not None and self.end is not None:
            self.nominal_diameter, self.minor_diameter = resolve_thread_diameters(
                thread, nominal_diameter, minor_diameter
            )
            if self.view_mode is None:
                self.view_mode = "side"
            scale = view.scale if view is not None else 1.0
            d_eff = self.nominal_diameter if self.thread_type == "internal" else self.minor_diameter
            self.paper_radius = (d_eff / 2.0) * float(scale)
            self.resolved = True

        target_view = view or CURRENT_VIEW.get()
        if target_view is not None:
            self._attach_to_view(target_view)
        else:
            target_sheet = CURRENT_SHEET.get()
            if target_sheet is not None and self not in target_sheet.annotations:
                target_sheet.annotations.append(self)

    @staticmethod
    def _get_view_direction(view: SheetView) -> tuple[float, float, float]:
        """Return unit 3D view projection direction (look_from)."""
        px = view.proj_x
        py = view.proj_y
        if px is not None and py is not None:
            c = px.cross(py)
            length = math.sqrt(float(c.X) ** 2 + float(c.Y) ** 2 + float(c.Z) ** 2)
            if length > 1e-6:
                return (float(c.X) / length, float(c.Y) / length, float(c.Z) / length)

        from build123d_techdraw.core.view import VIEW_VECTORS

        if view.view_type in VIEW_VECTORS:
            lf = VIEW_VECTORS[view.view_type][0]
            length = math.sqrt(lf[0] ** 2 + lf[1] ** 2 + lf[2] ** 2)
            if length > 1e-6:
                return (lf[0] / length, lf[1] / length, lf[2] / length)
        return (0.0, -1.0, 0.0)

    @staticmethod
    def _span_along_axis(
        shape: Shape[Any] | None,
        axis_pos: tuple[float, float, float],
        axis_dir: tuple[float, float, float],
        max_depth: float | None = None,
    ) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
        """Compute 3D endpoints of ``shape`` projected onto ``(axis_pos, axis_dir)``."""
        if shape is None:
            return None
        pts: list[tuple[float, float, float]] = []
        try:
            pts = [(float(v.X), float(v.Y), float(v.Z)) for v in shape.vertices()]
        except Exception:
            pts = []
        if not pts:
            bb = shape.bounding_box()
            pts = [
                (float(bb.min.X), float(bb.min.Y), float(bb.min.Z)),
                (float(bb.max.X), float(bb.max.Y), float(bb.max.Z)),
            ]
        ts = [
            (px - axis_pos[0]) * axis_dir[0]
            + (py - axis_pos[1]) * axis_dir[1]
            + (pz - axis_pos[2]) * axis_dir[2]
            for px, py, pz in pts
        ]
        t_min, t_max = min(ts), max(ts)
        if max_depth is not None and max_depth > 0.0:
            t_max = min(t_max, t_min + float(max_depth))
        p0 = (
            axis_pos[0] + axis_dir[0] * t_min,
            axis_pos[1] + axis_dir[1] * t_min,
            axis_pos[2] + axis_dir[2] * t_min,
        )
        p1 = (
            axis_pos[0] + axis_dir[0] * t_max,
            axis_pos[1] + axis_dir[1] * t_max,
            axis_pos[2] + axis_dir[2] * t_max,
        )
        return p0, p1

    def _attach_to_view(self, view: SheetView) -> None:
        from build123d_techdraw.core.view import ViewType
        from build123d_techdraw.recognizers.features import RecognizedHole

        self.view = view
        scale = float(view.scale)

        if not self.resolved and self.target is not None:
            axis_pos: tuple[float, float, float] | None = None
            axis_dir: tuple[float, float, float] | None = None
            face_dia: float | None = None
            p0_3d: tuple[float, float, float] | None = None
            p1_3d: tuple[float, float, float] | None = None

            if isinstance(self.target, bd.Face) and self.target.geom_type == bd.GeomType.CYLINDER:
                axis = self.target.axis_of_rotation
                if axis is not None:
                    axis_pos = (
                        float(axis.position.X),
                        float(axis.position.Y),
                        float(axis.position.Z),
                    )
                    d_len = math.sqrt(
                        float(axis.direction.X) ** 2
                        + float(axis.direction.Y) ** 2
                        + float(axis.direction.Z) ** 2
                    )
                    if d_len > 1e-6:
                        axis_dir = (
                            float(axis.direction.X) / d_len,
                            float(axis.direction.Y) / d_len,
                            float(axis.direction.Z) / d_len,
                        )
                if self.target.radius is not None:
                    face_dia = 2.0 * float(self.target.radius)
                if axis_pos is not None and axis_dir is not None:
                    span = self._span_along_axis(self.target, axis_pos, axis_dir, self.depth)
                    if span is not None:
                        p0_3d, p1_3d = span
            elif isinstance(self.target, bd.Edge) and self.target.geom_type == bd.GeomType.CIRCLE:
                c = self.target.arc_center
                axis_pos = (float(c.X), float(c.Y), float(c.Z))
                face_dia = 2.0 * float(self.target.radius)
                try:
                    n = self.target.normal()
                    axis_dir = (float(n.X), float(n.Y), float(n.Z))
                except Exception:
                    axis_dir = (0.0, 0.0, 1.0)
            elif isinstance(self.target, RecognizedHole):
                c3 = self.target.center_3d
                axis_pos = (float(c3[0]), float(c3[1]), float(c3[2]))
                ad = self.target.axis_dir
                d_len = math.sqrt(ad[0] ** 2 + ad[1] ** 2 + ad[2] ** 2) or 1.0
                axis_dir = (ad[0] / d_len, ad[1] / d_len, ad[2] / d_len)
                face_dia = float(self.target.diameter)
                if self.thread is None and self.target.thread_spec is not None:
                    self.thread = self.target.thread_spec
                h_depth = self.depth
                if h_depth is None and isinstance(self.target.depth, (int, float)):
                    h_depth = float(self.target.depth)
                    self.has_end_cap = True
                if h_depth is not None and h_depth > 0.0:
                    p0_3d = axis_pos
                    p1_3d = (
                        axis_pos[0] + axis_dir[0] * h_depth,
                        axis_pos[1] + axis_dir[1] * h_depth,
                        axis_pos[2] + axis_dir[2] * h_depth,
                    )
                elif isinstance(view.shape, Shape):
                    span = self._span_along_axis(view.shape, axis_pos, axis_dir)
                    if span is not None:
                        p0_3d, p1_3d = span
            elif is_topological_entity(self.target):
                self.center = resolve_point_to_sheet(self.target, view)
                axis_dir = self._get_view_direction(view)

            inferred_minor = (
                self._explicit_minor
                if self._explicit_minor is not None
                else (face_dia if self.thread_type == "internal" and self.thread is None else None)
            )
            inferred_nominal = (
                self._explicit_nominal
                if self._explicit_nominal is not None
                else (face_dia if self.thread_type == "external" and self.thread is None else None)
            )
            self.nominal_diameter, self.minor_diameter = resolve_thread_diameters(
                self.thread, inferred_nominal, inferred_minor
            )

            d_eff = self.nominal_diameter if self.thread_type == "internal" else self.minor_diameter
            self.paper_radius = (d_eff / 2.0) * scale

            if self.view_mode is None and axis_dir is not None:
                v_dir = self._get_view_direction(view)
                dot = abs(axis_dir[0] * v_dir[0] + axis_dir[1] * v_dir[1] + axis_dir[2] * v_dir[2])
                if dot > 0.9:
                    self.view_mode = "end"
                elif dot < 0.2:
                    self.view_mode = "section" if view.view_type == ViewType.SECTION else "side"
                else:
                    self._skip = True
                    self.resolved = True

            if not self._skip:
                if self.view_mode == "end" and self.center is None and axis_pos is not None:
                    self.center = view.to_sheet_3d(axis_pos[0], axis_pos[1], axis_pos[2])
                elif (
                    self.view_mode in ("side", "section")
                    and p0_3d is not None
                    and p1_3d is not None
                ):
                    self.start = view.to_sheet_3d(p0_3d[0], p0_3d[1], p0_3d[2])
                    self.end = view.to_sheet_3d(p1_3d[0], p1_3d[1], p1_3d[2])
                elif self.center is None and axis_pos is not None:
                    self.center = view.to_sheet_3d(axis_pos[0], axis_pos[1], axis_pos[2])
                    self.view_mode = "end"
                self.resolved = True

        elif (
            self.resolved
            and self.paper_radius == 0.0
            and (self.nominal_diameter > 0.0 or self.minor_diameter > 0.0)
        ):
            d_eff = self.nominal_diameter if self.thread_type == "internal" else self.minor_diameter
            self.paper_radius = (d_eff / 2.0) * scale

        self._register(view)

    def bounding_box_2d(self) -> BBox2D | None:
        """Approximate paper-space bounds for the sheet linter."""
        if self._skip or self.paper_radius <= 0.0:
            return None
        r = self.paper_radius
        if self.view_mode == "end" and self.center is not None:
            cx, cy = self.center
            return (cx - r, cy - r, cx + r, cy + r)
        if (
            self.view_mode in ("side", "section")
            and self.start is not None
            and self.end is not None
        ):
            x0, y0 = self.start
            x1, y1 = self.end
            return (min(x0, x1) - r, min(y0, y1) - r, max(x0, x1) + r, max(y0, y1) + r)
        return None

    def to_svg(self) -> str:
        """Render the cosmetic thread as ISO 6410-1 SVG vector geometry."""
        if self._skip:
            return ""

        if self.view_mode == "end" and self.center is not None and self.paper_radius > 0.0:
            cx, cy = self.center
            r = self.paper_radius
            a_start = math.radians(self.gap_start_deg + self.gap_angle_deg)
            a_end = math.radians(self.gap_start_deg)
            x1 = cx + r * math.cos(a_start)
            y1 = cy - r * math.sin(a_start)
            x2 = cx + r * math.cos(a_end)
            y2 = cy - r * math.sin(a_end)
            sweep_deg = 360.0 - self.gap_angle_deg
            large_arc = 1 if sweep_deg > 180.0 else 0
            return (
                f'<path d="M {x1:.2f} {y1:.2f} A {r:.2f} {r:.2f} 0 {large_arc} 0 {x2:.2f} {y2:.2f}" '
                f'class="line-thread" stroke-width="{self.stroke_width:.2f}" fill="none"/>'
            )

        if (
            self.view_mode in ("side", "section")
            and self.start is not None
            and self.end is not None
            and self.paper_radius > 0.0
        ):
            x0, y0 = self.start
            x1, y1 = self.end
            dx = x1 - x0
            dy = y1 - y0
            length = math.hypot(dx, dy)
            if length < 1e-6:
                return ""
            nx = -dy / length
            ny = dx / length
            r = self.paper_radius
            ax0, ay0 = x0 + nx * r, y0 + ny * r
            ax1, ay1 = x1 + nx * r, y1 + ny * r
            bx0, by0 = x0 - nx * r, y0 - ny * r
            bx1, by1 = x1 - nx * r, y1 - ny * r

            if self.view_mode == "section":
                cls = "line-thread"
                dash_attr = ""
            else:
                cls = "line-thread line-thread-dashed"
                dash_attr = ' stroke-dasharray="2.0,1.2"'

            parts = [
                f'<line x1="{ax0:.2f}" y1="{ay0:.2f}" x2="{ax1:.2f}" y2="{ay1:.2f}" '
                f'class="{cls}"{dash_attr} stroke-width="{self.stroke_width:.2f}"/>',
                f'<line x1="{bx0:.2f}" y1="{by0:.2f}" x2="{bx1:.2f}" y2="{by1:.2f}" '
                f'class="{cls}"{dash_attr} stroke-width="{self.stroke_width:.2f}"/>',
            ]
            if self.has_end_cap:
                parts.append(
                    f'<line x1="{ax1:.2f}" y1="{ay1:.2f}" x2="{bx1:.2f}" y2="{by1:.2f}" '
                    f'class="{cls}"{dash_attr} stroke-width="{self.stroke_width:.2f}"/>'
                )
            return "\n".join(parts)

        return ""


def auto_cosmetic_threads(
    view: SheetView,
    shape: Shape[Any] | None = None,
    *,
    threaded_diameters: dict[float, str] | None = None,
    infer_threads: bool = False,
    tolerance: float = 0.08,
) -> list[CosmeticThread]:
    """Recognize threaded holes in a view's 3D shape and attach ISO 6410-1 CosmeticThreads."""
    from build123d_techdraw.recognizers.features import recognize_holes

    target_shape = shape if shape is not None else view.shape
    if target_shape is None:
        return []

    holes = recognize_holes(
        target_shape,
        infer_threads=infer_threads,
        threaded_diameters=threaded_diameters,
        thread_tolerance=tolerance,
    )
    created: list[CosmeticThread] = []
    for h in holes:
        if not h.is_threaded or h.thread_spec is None:
            continue
        ct = CosmeticThread(
            target=h,
            thread=h.thread_spec,
            thread_type="internal",
            view=view,
        )
        if not ct._skip:
            created.append(ct)
    return created
