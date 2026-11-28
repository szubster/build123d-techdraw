"""High-precision multi-page vector PDF export with native PDF 1.4 streaming and raster image support."""

from __future__ import annotations

import base64
import io
import math
import os
import re
import xml.etree.ElementTree as ET
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypeAlias

# Set MPLCONFIGDIR before importing matplotlib so it does not probe ~/.config/matplotlib.
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib  # noqa: E402

matplotlib.use("Agg")

import matplotlib.font_manager as fm  # noqa: E402
import matplotlib.patches as patches  # noqa: E402
import matplotlib.path as mpath  # noqa: E402
import matplotlib.transforms as mtransforms  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib import ft2font  # noqa: E402
from matplotlib.backends.backend_pdf import Name, PdfPages  # noqa: E402
from PIL import Image  # noqa: E402

from build123d_techdraw.renderers.theme import THEME_COLORS, Theme, ThemeColors  # noqa: E402

if TYPE_CHECKING:
    from build123d_techdraw.core.sheet import DrawingSheet

SheetInput: TypeAlias = "tuple[float, float, str] | DrawingSheet | str | os.PathLike[str]"
"""A sheet to render: (width_mm, height_mm, svg), a ``DrawingSheet``, or an SVG path/string."""

MM_TO_PT: float = 72.0 / 25.4
# Contours (ISO 128 Type A): 0.50mm -> 1.42pt
# Dimensions/Leaders (ISO 128 Type B): 0.25mm -> 0.71pt
# Borders: 0.70mm -> 1.98pt

_Matrix2D: TypeAlias = tuple[float, float, float, float, float, float]
_IDENTITY_MATRIX: _Matrix2D = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

_MoveCmd: TypeAlias = tuple[Literal["M"], float, float]
_LineCmd: TypeAlias = tuple[Literal["L"], float, float]
_CubicCmd: TypeAlias = tuple[Literal["C"], float, float, float, float, float, float]
_CloseCmd: TypeAlias = tuple[Literal["Z"]]
_PathCmd: TypeAlias = _MoveCmd | _LineCmd | _CubicCmd | _CloseCmd

# Exact 4-segment cubic Bezier circle approximation constant: 4 * (sqrt(2) - 1) / 3
_CIRCLE_KAPPA: float = 0.5522847498307936

_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"

_NUM_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_TRANSFORM_RE = re.compile(r"([a-zA-Z]+)\s*\(([^)]*)\)")
_CSS_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_CLIP_URL_RE = re.compile(r"url\(\s*#([^)\s]+)\s*\)")

# Standard CSS named colors used occasionally in inline SVG attributes
_NAMED_COLORS: dict[str, tuple[float, float, float, float]] = {
    "black": (0.0, 0.0, 0.0, 1.0),
    "white": (1.0, 1.0, 1.0, 1.0),
    "gray": (0.502, 0.502, 0.502, 1.0),
    "grey": (0.502, 0.502, 0.502, 1.0),
    "red": (1.0, 0.0, 0.0, 1.0),
    "green": (0.0, 0.502, 0.0, 1.0),
    "blue": (0.0, 0.0, 1.0, 1.0),
    "cyan": (0.0, 1.0, 1.0, 1.0),
    "magenta": (1.0, 0.0, 1.0, 1.0),
    "yellow": (1.0, 1.0, 0.0, 1.0),
    "orange": (1.0, 0.647, 0.0, 1.0),
    "transparent": (0.0, 0.0, 0.0, 0.0),
}

# WinAnsi non-ASCII Unicode -> Adobe AFM glyph name mapping for exact Helvetica widths
_WINANSI_GLYPH_NAMES: dict[str, str] = {
    "°": "degree",
    "±": "plusminus",
    "×": "multiply",
    "÷": "divide",
    "µ": "mu",
    "–": "endash",
    "—": "emdash",
    "©": "copyright",
    "®": "registered",
    "™": "trademark",
    "•": "bullet",
    "¼": "onequarter",
    "½": "onehalf",
    "¾": "threequarters",
    "²": "twosuperior",
    "³": "threesuperior",
    "§": "section",
    "€": "Euro",
}


class BookmarkedPdfPages(PdfPages):
    """PdfPages subclass supporting native PDF document outline bookmarks."""

    def __init__(
        self,
        filename: str,
        booklet_title: str = "ENGINEERING DRAWINGS",
        sheet_titles: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        super().__init__(filename, metadata=metadata)
        self.booklet_title = booklet_title
        self.sheet_titles = sheet_titles or []

    def close(self) -> None:
        if self._file is not None and self.sheet_titles:
            pdf = self._file
            n = len(pdf.pageList)
            if n > 0:
                outlines_obj = pdf.reserveObject("outlines")
                top_obj = pdf.reserveObject("outline_top")
                child_objs = [pdf.reserveObject(f"outline_child_{i}") for i in range(n)]

                for i in range(n):
                    title = self.sheet_titles[i] if i < len(self.sheet_titles) else f"Sheet {i + 1}"
                    child_dict: dict[str, Any] = {
                        "Title": title,
                        "Parent": top_obj,
                        "Dest": [pdf.pageList[i], Name("Fit")],  # type: ignore[no-untyped-call]
                    }
                    if i > 0:
                        child_dict["Prev"] = child_objs[i - 1]
                    if i < n - 1:
                        child_dict["Next"] = child_objs[i + 1]
                    pdf.writeObject(child_objs[i], child_dict)

                top_dict: dict[str, Any] = {
                    "Title": self.booklet_title,
                    "Parent": outlines_obj,
                    "Count": n,
                    "Dest": [pdf.pageList[0], Name("Fit")],  # type: ignore[no-untyped-call]
                }
                if child_objs:
                    top_dict["First"] = child_objs[0]
                    top_dict["Last"] = child_objs[-1]
                pdf.writeObject(top_obj, top_dict)

                outlines_dict: dict[str, Any] = {
                    "Type": Name("Outlines"),  # type: ignore[no-untyped-call]
                    "First": top_obj,
                    "Last": top_obj,
                    "Count": n + 1,
                }
                pdf.writeObject(outlines_obj, outlines_dict)

                catalog_dict: dict[str, Any] = {
                    "Type": Name("Catalog"),  # type: ignore[no-untyped-call]
                    "Pages": pdf.pagesObject,
                    "Outlines": outlines_obj,
                }
                pdf.writeObject(pdf.rootObject, catalog_dict)

        super().close()  # type: ignore[no-untyped-call]


# ---------------------------------------------------------------------------
# Parsed SVG Scene Intermediate Representation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _VectorItem:
    """Resolved vector path in paper-space mm (Y-up)."""

    commands: tuple[_PathCmd, ...]
    fill: str | None
    stroke: str | None
    linewidth_pt: float
    dash_pt: tuple[float, ...] | None
    hatch: str | None
    fill_alpha: float | None
    stroke_alpha: float | None
    clip_paths: tuple[tuple[_PathCmd, ...], ...]


@dataclass(frozen=True)
class _TextItem:
    """Resolved text element in paper-space mm (Y-up)."""

    x_mm: float
    y_mm: float
    text: str
    color: str
    fontsize_pt: float
    bold: bool
    ha: Literal["left", "center", "right"]
    va: Literal["baseline", "center"]
    rotation_deg: float
    knockout_bg: str | None
    clip_paths: tuple[tuple[_PathCmd, ...], ...]


@dataclass(frozen=True)
class _ImageItem:
    """Resolved raster ``<image>`` element with full accumulated SVG transform."""

    x: float
    y: float
    width: float
    height: float
    transform: _Matrix2D
    href: str
    opacity: float
    preserve_aspect_ratio: str
    clip_paths: tuple[tuple[_PathCmd, ...], ...]


_SceneItem: TypeAlias = _VectorItem | _TextItem | _ImageItem


@dataclass(frozen=True)
class _InheritedStyle:
    """SVG attributes inherited down ``<g>`` and nested ``<svg>`` containers."""

    fill: str = ""
    stroke: str = ""
    stroke_width: str = ""
    stroke_dasharray: str = ""
    fill_opacity: str = ""
    opacity: float = 1.0
    font_size: str = ""
    font_weight: str = ""
    text_anchor: str = ""
    ancestor_hidden: bool = False


# ---------------------------------------------------------------------------
# 2D Affine Transform Helpers
# ---------------------------------------------------------------------------


def _mat_mul(m1: _Matrix2D, m2: _Matrix2D) -> _Matrix2D:
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def _mat_apply(m: _Matrix2D, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def _parse_transform(raw: str | None) -> _Matrix2D:
    """Parse an SVG ``transform`` attribute string into a 2D affine matrix."""
    if not raw:
        return _IDENTITY_MATRIX
    mat = _IDENTITY_MATRIX
    for match in _TRANSFORM_RE.finditer(raw):
        fn = match.group(1).strip()
        vals = [float(v) for v in _NUM_RE.findall(match.group(2))]
        if fn == "translate" and vals:
            tx = vals[0]
            ty = vals[1] if len(vals) > 1 else 0.0
            mat = _mat_mul(mat, (1.0, 0.0, 0.0, 1.0, tx, ty))
        elif fn == "scale" and vals:
            sx = vals[0]
            sy = vals[1] if len(vals) > 1 else sx
            mat = _mat_mul(mat, (sx, 0.0, 0.0, sy, 0.0, 0.0))
        elif fn == "rotate" and vals:
            rad = math.radians(vals[0])
            cos_a = math.cos(rad)
            sin_a = math.sin(rad)
            if len(vals) >= 3:
                cx, cy = vals[1], vals[2]
                mat = _mat_mul(mat, (1.0, 0.0, 0.0, 1.0, cx, cy))
                mat = _mat_mul(mat, (cos_a, sin_a, -sin_a, cos_a, 0.0, 0.0))
                mat = _mat_mul(mat, (1.0, 0.0, 0.0, 1.0, -cx, -cy))
            else:
                mat = _mat_mul(mat, (cos_a, sin_a, -sin_a, cos_a, 0.0, 0.0))
        elif fn == "matrix" and len(vals) >= 6:
            mat = _mat_mul(mat, (vals[0], vals[1], vals[2], vals[3], vals[4], vals[5]))
        elif fn == "skewX" and vals:
            mat = _mat_mul(mat, (1.0, 0.0, math.tan(math.radians(vals[0])), 1.0, 0.0, 0.0))
        elif fn == "skewY" and vals:
            mat = _mat_mul(mat, (1.0, math.tan(math.radians(vals[0])), 0.0, 1.0, 0.0, 0.0))
    return mat


# ---------------------------------------------------------------------------
# SVG Geometry -> Cubic Bezier Path Commands
# ---------------------------------------------------------------------------


def _local_tag(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _parse_float(raw: str | None, default: float = 0.0) -> float:
    if not raw:
        return default
    cleaned = raw.strip()
    for suffix in ("mm", "px", "pt"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
            break
    try:
        return float(cleaned)
    except ValueError:
        return default


def _parse_stroke_width(raw: Any, default: float) -> float:
    """Parse an SVG stroke-width value (e.g. '0.25', '0.25px', '0.25mm') to float mm."""
    if raw is None:
        return default
    cleaned = str(raw).replace("px", "").replace("mm", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return default


def _arc_to_cubics(
    x1: float,
    y1: float,
    rx_in: float,
    ry_in: float,
    x_rot_deg: float,
    large_arc: int,
    sweep: int,
    x2: float,
    y2: float,
) -> list[_PathCmd]:
    """Convert an SVG elliptical arc segment to exact cubic Bezier segments (W3C SVG 1.1 F.6)."""
    if math.hypot(x2 - x1, y2 - y1) < 1e-12:
        return []
    rx = abs(rx_in)
    ry = abs(ry_in)
    if rx < 1e-12 or ry < 1e-12:
        return [("L", x2, y2)]

    phi = math.radians(x_rot_deg % 360.0)
    cos_phi = math.cos(phi)
    sin_phi = math.sin(phi)

    dx2 = (x1 - x2) * 0.5
    dy2 = (y1 - y2) * 0.5
    x1p = cos_phi * dx2 + sin_phi * dy2
    y1p = -sin_phi * dx2 + cos_phi * dy2

    # Ensure radii are large enough
    rx2 = rx * rx
    ry2 = ry * ry
    x1p2 = x1p * x1p
    y1p2 = y1p * y1p
    lam = x1p2 / rx2 + y1p2 / ry2
    if lam > 1.0:
        scale = math.sqrt(lam)
        rx *= scale
        ry *= scale
        rx2 = rx * rx
        ry2 = ry * ry

    denom = rx2 * y1p2 + ry2 * x1p2
    if denom < 1e-18:
        return [("L", x2, y2)]

    num = max(0.0, rx2 * ry2 - denom)
    coef = math.sqrt(num / denom)
    if large_arc == sweep:
        coef = -coef

    cxp = coef * (rx * y1p / ry)
    cyp = coef * (-ry * x1p / rx)

    cx = cos_phi * cxp - sin_phi * cyp + (x1 + x2) * 0.5
    cy = sin_phi * cxp + cos_phi * cyp + (y1 + y2) * 0.5

    def _vec_angle(ux: float, uy: float, vx: float, vy: float) -> float:
        dot = ux * vx + uy * vy
        mag = math.hypot(ux, uy) * math.hypot(vx, vy)
        if mag < 1e-18:
            return 0.0
        cos_v = max(-1.0, min(1.0, dot / mag))
        ang = math.acos(cos_v)
        return -ang if (ux * vy - uy * vx) < 0.0 else ang

    ux = (x1p - cxp) / rx
    uy = (y1p - cyp) / ry
    vx = (-x1p - cxp) / rx
    vy = (-y1p - cyp) / ry

    theta1 = _vec_angle(1.0, 0.0, ux, uy)
    dtheta = _vec_angle(ux, uy, vx, vy)
    if sweep == 0 and dtheta > 0.0:
        dtheta -= 2.0 * math.pi
    elif sweep == 1 and dtheta < 0.0:
        dtheta += 2.0 * math.pi

    n_segs = max(1, int(math.ceil(abs(dtheta) / (math.pi * 0.5))))
    seg_angle = dtheta / n_segs
    alpha = (4.0 / 3.0) * math.tan(seg_angle * 0.25)

    cmds: list[_PathCmd] = []
    t0 = theta1
    for _ in range(n_segs):
        t1 = t0 + seg_angle
        cos0, sin0 = math.cos(t0), math.sin(t0)
        cos1, sin1 = math.cos(t1), math.sin(t1)

        p0x, p0y = cos0, sin0
        p1x, p1y = p0x - alpha * sin0, p0y + alpha * cos0
        p2x, p2y = cos1 + alpha * sin1, sin1 - alpha * cos1
        p3x, p3y = cos1, sin1

        c1x = cos_phi * (rx * p1x) - sin_phi * (ry * p1y) + cx
        c1y = sin_phi * (rx * p1x) + cos_phi * (ry * p1y) + cy
        c2x = cos_phi * (rx * p2x) - sin_phi * (ry * p2y) + cx
        c2y = sin_phi * (rx * p2x) + cos_phi * (ry * p2y) + cy
        ex = cos_phi * (rx * p3x) - sin_phi * (ry * p3y) + cx
        ey = sin_phi * (rx * p3x) + cos_phi * (ry * p3y) + cy

        cmds.append(("C", c1x, c1y, c2x, c2y, ex, ey))
        t0 = t1

    return cmds


def _parse_svg_path_d(d: str) -> list[_PathCmd]:
    """Parse an SVG ``<path d="...">`` string into normalized M/L/C/Z commands."""
    cmds: list[_PathCmd] = []
    n = len(d)
    pos = 0

    def _skip_ws_comma() -> None:
        nonlocal pos
        while pos < n and d[pos] in " \t\r\n,":
            pos += 1

    def _read_float() -> float | None:
        nonlocal pos
        _skip_ws_comma()
        m = _NUM_RE.match(d, pos)
        if m is None:
            return None
        pos = m.end()
        return float(m.group(0))

    def _read_flag() -> int | None:
        nonlocal pos
        _skip_ws_comma()
        if pos < n and d[pos] in "01":
            val = 1 if d[pos] == "1" else 0
            pos += 1
            return val
        return None

    cx = cy = 0.0
    sx = sy = 0.0
    last_ctrl_x = last_ctrl_y = 0.0
    last_cmd = ""
    cmd = ""

    while pos < n:
        _skip_ws_comma()
        if pos >= n:
            break
        ch = d[pos]
        if ch.isalpha():
            cmd = ch
            pos += 1
        elif not cmd:
            break

        if cmd in ("M", "m"):
            x = _read_float()
            y = _read_float()
            if x is None or y is None:
                break
            if cmd == "m":
                x += cx
                y += cy
            cmds.append(("M", x, y))
            cx, cy = x, y
            sx, sy = x, y
            last_cmd = cmd
            cmd = "L" if cmd == "M" else "l"
        elif cmd in ("L", "l"):
            x = _read_float()
            y = _read_float()
            if x is None or y is None:
                break
            if cmd == "l":
                x += cx
                y += cy
            cmds.append(("L", x, y))
            cx, cy = x, y
            last_cmd = cmd
        elif cmd in ("H", "h"):
            x = _read_float()
            if x is None:
                break
            if cmd == "h":
                x += cx
            cmds.append(("L", x, cy))
            cx = x
            last_cmd = cmd
        elif cmd in ("V", "v"):
            y = _read_float()
            if y is None:
                break
            if cmd == "v":
                y += cy
            cmds.append(("L", cx, y))
            cy = y
            last_cmd = cmd
        elif cmd in ("C", "c"):
            x1 = _read_float()
            y1 = _read_float()
            x2 = _read_float()
            y2 = _read_float()
            x = _read_float()
            y = _read_float()
            if x1 is None or y1 is None or x2 is None or y2 is None or x is None or y is None:
                break
            if cmd == "c":
                x1 += cx
                y1 += cy
                x2 += cx
                y2 += cy
                x += cx
                y += cy
            cmds.append(("C", x1, y1, x2, y2, x, y))
            last_ctrl_x, last_ctrl_y = x2, y2
            cx, cy = x, y
            last_cmd = cmd
        elif cmd in ("S", "s"):
            x2 = _read_float()
            y2 = _read_float()
            x = _read_float()
            y = _read_float()
            if x2 is None or y2 is None or x is None or y is None:
                break
            if last_cmd in ("C", "c", "S", "s"):
                x1 = 2.0 * cx - last_ctrl_x
                y1 = 2.0 * cy - last_ctrl_y
            else:
                x1, y1 = cx, cy
            if cmd == "s":
                x2 += cx
                y2 += cy
                x += cx
                y += cy
            cmds.append(("C", x1, y1, x2, y2, x, y))
            last_ctrl_x, last_ctrl_y = x2, y2
            cx, cy = x, y
            last_cmd = cmd
        elif cmd in ("Q", "q"):
            qx = _read_float()
            qy = _read_float()
            x = _read_float()
            y = _read_float()
            if qx is None or qy is None or x is None or y is None:
                break
            if cmd == "q":
                qx += cx
                qy += cy
                x += cx
                y += cy
            c1x = cx + (2.0 / 3.0) * (qx - cx)
            c1y = cy + (2.0 / 3.0) * (qy - cy)
            c2x = x + (2.0 / 3.0) * (qx - x)
            c2y = y + (2.0 / 3.0) * (qy - y)
            cmds.append(("C", c1x, c1y, c2x, c2y, x, y))
            last_ctrl_x, last_ctrl_y = qx, qy
            cx, cy = x, y
            last_cmd = cmd
        elif cmd in ("T", "t"):
            x = _read_float()
            y = _read_float()
            if x is None or y is None:
                break
            if last_cmd in ("Q", "q", "T", "t"):
                qx = 2.0 * cx - last_ctrl_x
                qy = 2.0 * cy - last_ctrl_y
            else:
                qx, qy = cx, cy
            if cmd == "t":
                x += cx
                y += cy
            c1x = cx + (2.0 / 3.0) * (qx - cx)
            c1y = cy + (2.0 / 3.0) * (qy - cy)
            c2x = x + (2.0 / 3.0) * (qx - x)
            c2y = y + (2.0 / 3.0) * (qy - y)
            cmds.append(("C", c1x, c1y, c2x, c2y, x, y))
            last_ctrl_x, last_ctrl_y = qx, qy
            cx, cy = x, y
            last_cmd = cmd
        elif cmd in ("A", "a"):
            rx = _read_float()
            ry = _read_float()
            rot = _read_float()
            large_arc = _read_flag()
            sweep = _read_flag()
            x = _read_float()
            y = _read_float()
            if (
                rx is None
                or ry is None
                or rot is None
                or large_arc is None
                or sweep is None
                or x is None
                or y is None
            ):
                break
            if cmd == "a":
                x += cx
                y += cy
            cmds.extend(_arc_to_cubics(cx, cy, rx, ry, rot, large_arc, sweep, x, y))
            cx, cy = x, y
            last_cmd = cmd
        elif cmd in ("Z", "z"):
            cmds.append(("Z",))
            cx, cy = sx, sy
            last_cmd = cmd
            cmd = ""
        else:
            break

    return cmds


def _shape_element_to_local_path(el: ET.Element, tag: str) -> list[_PathCmd]:
    """Convert an SVG geometric primitive element into local-coordinate path commands."""
    attrib = el.attrib
    if tag == "line":
        x1 = _parse_float(attrib.get("x1"))
        y1 = _parse_float(attrib.get("y1"))
        x2 = _parse_float(attrib.get("x2"))
        y2 = _parse_float(attrib.get("y2"))
        return [("M", x1, y1), ("L", x2, y2)]

    if tag == "rect":
        x = _parse_float(attrib.get("x"))
        y = _parse_float(attrib.get("y"))
        w = _parse_float(attrib.get("width"))
        h = _parse_float(attrib.get("height"))
        if w <= 0.0 or h <= 0.0:
            return []
        rx = _parse_float(attrib.get("rx"))
        ry = _parse_float(attrib.get("ry"))
        if rx == 0.0 and ry > 0.0:
            rx = ry
        elif ry == 0.0 and rx > 0.0:
            ry = rx
        if rx <= 0.0 and ry <= 0.0:
            return [
                ("M", x, y),
                ("L", x + w, y),
                ("L", x + w, y + h),
                ("L", x, y + h),
                ("Z",),
            ]
        rx = min(rx, w * 0.5)
        ry = min(ry, h * 0.5)
        kx = rx * _CIRCLE_KAPPA
        ky = ry * _CIRCLE_KAPPA
        return [
            ("M", x + rx, y),
            ("L", x + w - rx, y),
            ("C", x + w - rx + kx, y, x + w, y + ry - ky, x + w, y + ry),
            ("L", x + w, y + h - ry),
            ("C", x + w, y + h - ry + ky, x + w - rx + kx, y + h, x + w - rx, y + h),
            ("L", x + rx, y + h),
            ("C", x + rx - kx, y + h, x, y + h - ry + ky, x, y + h - ry),
            ("L", x, y + ry),
            ("C", x, y + ry - ky, x + rx - kx, y, x + rx, y),
            ("Z",),
        ]

    if tag == "circle":
        cx = _parse_float(attrib.get("cx"))
        cy = _parse_float(attrib.get("cy"))
        r = _parse_float(attrib.get("r"))
        if r <= 0.0:
            return []
        k = r * _CIRCLE_KAPPA
        return [
            ("M", cx + r, cy),
            ("C", cx + r, cy + k, cx + k, cy + r, cx, cy + r),
            ("C", cx - k, cy + r, cx - r, cy + k, cx - r, cy),
            ("C", cx - r, cy - k, cx - k, cy - r, cx, cy - r),
            ("C", cx + k, cy - r, cx + r, cy - k, cx + r, cy),
            ("Z",),
        ]

    if tag == "ellipse":
        cx = _parse_float(attrib.get("cx"))
        cy = _parse_float(attrib.get("cy"))
        rx = _parse_float(attrib.get("rx"))
        ry = _parse_float(attrib.get("ry"))
        if rx <= 0.0 or ry <= 0.0:
            return []
        kx = rx * _CIRCLE_KAPPA
        ky = ry * _CIRCLE_KAPPA
        return [
            ("M", cx + rx, cy),
            ("C", cx + rx, cy + ky, cx + kx, cy + ry, cx, cy + ry),
            ("C", cx - kx, cy + ry, cx - rx, cy + ky, cx - rx, cy),
            ("C", cx - rx, cy - ky, cx - kx, cy - ry, cx, cy - ry),
            ("C", cx + kx, cy - ry, cx + rx, cy - ky, cx + rx, cy),
            ("Z",),
        ]

    if tag in ("polyline", "polygon"):
        nums = [float(v) for v in _NUM_RE.findall(attrib.get("points", ""))]
        if len(nums) < 4:
            return []
        cmds: list[_PathCmd] = [("M", nums[0], nums[1])]
        for idx in range(2, len(nums) - 1, 2):
            cmds.append(("L", nums[idx], nums[idx + 1]))
        if tag == "polygon":
            cmds.append(("Z",))
        return cmds

    if tag == "path":
        d = attrib.get("d", "")
        return _parse_svg_path_d(d) if d else []

    return []


def _transform_path_to_paper(
    local_cmds: Sequence[_PathCmd],
    matrix: _Matrix2D,
    height_mm: float,
) -> tuple[_PathCmd, ...]:
    """Apply SVG affine matrix and flip Y to paper-space mm (Y-up)."""
    a, b, c, d, e, f = matrix
    out: list[_PathCmd] = []
    for cmd in local_cmds:
        if cmd[0] == "M":
            x, y = cmd[1], cmd[2]
            out.append(("M", a * x + c * y + e, height_mm - (b * x + d * y + f)))
        elif cmd[0] == "L":
            x, y = cmd[1], cmd[2]
            out.append(("L", a * x + c * y + e, height_mm - (b * x + d * y + f)))
        elif cmd[0] == "C":
            x1, y1, x2, y2, x, y = cmd[1], cmd[2], cmd[3], cmd[4], cmd[5], cmd[6]
            out.append(
                (
                    "C",
                    a * x1 + c * y1 + e,
                    height_mm - (b * x1 + d * y1 + f),
                    a * x2 + c * y2 + e,
                    height_mm - (b * x2 + d * y2 + f),
                    a * x + c * y + e,
                    height_mm - (b * x + d * y + f),
                )
            )
        else:
            out.append(("Z",))
    return tuple(out)


# ---------------------------------------------------------------------------
# Color, CSS & SVG Scene Parser
# ---------------------------------------------------------------------------


def _parse_color_rgba(raw: str) -> tuple[float, float, float, float] | None:
    """Parse ``#RGB``, ``#RRGGBB``, ``#RRGGBBAA``, ``rgb(...)``, or named CSS color to RGBA [0,1]."""
    s = raw.strip().lower()
    if not s or s in ("none", "currentcolor") or s.startswith("var(") or s.startswith("url("):
        return None
    if s in _NAMED_COLORS:
        return _NAMED_COLORS[s]
    if s.startswith("#"):
        hex_str = s[1:]
        if len(hex_str) == 3:
            r = int(hex_str[0] * 2, 16) / 255.0
            g = int(hex_str[1] * 2, 16) / 255.0
            b = int(hex_str[2] * 2, 16) / 255.0
            return (r, g, b, 1.0)
        if len(hex_str) == 6:
            r = int(hex_str[0:2], 16) / 255.0
            g = int(hex_str[2:4], 16) / 255.0
            b = int(hex_str[4:6], 16) / 255.0
            return (r, g, b, 1.0)
        if len(hex_str) == 8:
            r = int(hex_str[0:2], 16) / 255.0
            g = int(hex_str[2:4], 16) / 255.0
            b = int(hex_str[4:6], 16) / 255.0
            a = int(hex_str[6:8], 16) / 255.0
            return (r, g, b, a)
        return None
    if s.startswith("rgb(") and s.endswith(")"):
        nums = [float(v) for v in _NUM_RE.findall(s[4:-1])]
        if len(nums) >= 3:
            return (
                max(0.0, min(1.0, nums[0] / 255.0)),
                max(0.0, min(1.0, nums[1] / 255.0)),
                max(0.0, min(1.0, nums[2] / 255.0)),
                1.0,
            )
    return None


def _parse_inline_style(raw: str | None) -> dict[str, str]:
    """Parse an SVG inline ``style="..."`` attribute into lowercase key/value pairs."""
    if not raw:
        return {}
    result: dict[str, str] = {}
    for decl in raw.split(";"):
        if ":" in decl:
            k, v = decl.split(":", 1)
            key = k.strip().lower()
            val = v.strip()
            if key and val:
                result[key] = val
    return result


def _parse_css_class_styles(root: ET.Element) -> dict[str, dict[str, str]]:
    """Extract base ``.class-name { prop: val; }`` rules from ``<style>`` blocks."""
    class_rules: dict[str, dict[str, str]] = {}
    for el in root.iter():
        if _local_tag(el.tag).lower() != "style" or not el.text:
            continue
        for match in _CSS_RULE_RE.finditer(el.text):
            selectors_raw, body = match.group(1), match.group(2)
            decls = _parse_inline_style(body)
            if not decls:
                continue
            for sel in selectors_raw.split(","):
                sel_s = sel.strip()
                # Only capture simple single-class base rules (e.g. ".zone-text"),
                # skipping compound theme overrides handled by ThemeColors.
                if sel_s.startswith(".") and " " not in sel_s and ":" not in sel_s:
                    cls_name = sel_s[1:]
                    existing = class_rules.setdefault(cls_name, {})
                    existing.update(decls)
    return class_rules


def _parse_dasharray_pt(raw: str) -> tuple[float, ...] | None:
    """Parse an SVG ``stroke-dasharray`` string (mm) into PDF dash lengths (pt)."""
    s = raw.strip().lower()
    if not s or s == "none":
        return None
    nums = [float(v) * MM_TO_PT for v in _NUM_RE.findall(s)]
    if not nums or all(v <= 0.0 for v in nums):
        return None
    return tuple(nums)


def _nested_svg_transform(el: ET.Element) -> _Matrix2D:
    """Compute the local transform implied by a nested ``<svg x y width height viewBox>``."""
    mat = _IDENTITY_MATRIX
    x = _parse_float(el.attrib.get("x"))
    y = _parse_float(el.attrib.get("y"))
    if x != 0.0 or y != 0.0:
        mat = _mat_mul(mat, (1.0, 0.0, 0.0, 1.0, x, y))
    vb_raw = el.attrib.get("viewBox")
    w_raw = el.attrib.get("width")
    h_raw = el.attrib.get("height")
    if vb_raw and w_raw and h_raw:
        parts = [float(v) for v in _NUM_RE.findall(vb_raw)]
        w_val = PDFRenderer._parse_svg_length_mm(w_raw)
        h_val = PDFRenderer._parse_svg_length_mm(h_raw)
        if len(parts) == 4 and w_val and h_val and parts[2] > 0.0 and parts[3] > 0.0:
            sx = w_val / parts[2]
            sy = h_val / parts[3]
            mat = _mat_mul(mat, (sx, 0.0, 0.0, sy, 0.0, 0.0))
            if parts[0] != 0.0 or parts[1] != 0.0:
                mat = _mat_mul(mat, (1.0, 0.0, 0.0, 1.0, -parts[0], -parts[1]))
    return mat


def _parse_svg_scene(
    svg_code: str,
    height_mm: float,
    colors: ThemeColors,
) -> list[_SceneItem]:
    """Parse an SVG document directly via ElementTree into ordered paper-space scene items."""
    root = ET.fromstring(svg_code)
    css_rules = _parse_css_class_styles(root)

    # Pass 1: index all <clipPath id="..."> definitions anywhere in the SVG tree
    clip_defs: dict[str, list[tuple[list[_PathCmd], _Matrix2D]]] = {}
    for el in root.iter():
        if _local_tag(el.tag).lower() == "clippath":
            clip_id = el.attrib.get("id", "").strip()
            if not clip_id:
                continue
            shapes: list[tuple[list[_PathCmd], _Matrix2D]] = []
            cp_mat = _parse_transform(el.attrib.get("transform"))
            for child in el:
                ctag = _local_tag(child.tag).lower()
                local_cmds = _shape_element_to_local_path(child, ctag)
                if local_cmds:
                    cmat = _mat_mul(cp_mat, _parse_transform(child.attrib.get("transform")))
                    shapes.append((local_cmds, cmat))
            if shapes:
                clip_defs[clip_id] = shapes

    items: list[_SceneItem] = []

    def _effective_attr(
        el: ET.Element,
        inline_css: dict[str, str],
        cls_list: list[str],
        name: str,
        inherited_val: str,
    ) -> str:
        if name in inline_css:
            return inline_css[name]
        direct = el.attrib.get(name)
        if direct is not None:
            return direct
        for c in cls_list:
            rule = css_rules.get(c)
            if rule and name in rule:
                return rule[name]
        return inherited_val

    def _walk(
        el: ET.Element,
        parent_mat: _Matrix2D,
        inherited: _InheritedStyle,
        active_clips: tuple[tuple[_PathCmd, ...], ...],
        is_root: bool = False,
    ) -> None:
        tag = _local_tag(el.tag).lower()
        if tag in ("defs", "style", "pattern", "clippath", "title", "desc", "metadata"):
            return

        inline_css = _parse_inline_style(el.attrib.get("style"))
        if inline_css.get("display", el.attrib.get("display", "")).strip().lower() == "none":
            return
        if (
            inline_css.get("visibility", el.attrib.get("visibility", "")).strip().lower()
            == "hidden"
        ):
            return

        cls_str = el.attrib.get("class", "")
        cls_list = cls_str.split() if cls_str else []

        # Background and grid pattern rects already handled by page background fill
        if "sheet-bg" in cls_list or "blueprint-grid" in cls_list:
            return

        # Compute element affine matrix
        mat = parent_mat
        if tag == "svg" and not is_root:
            mat = _mat_mul(mat, _nested_svg_transform(el))
        el_tf = el.attrib.get("transform")
        if el_tf:
            mat = _mat_mul(mat, _parse_transform(el_tf))

        # Resolve any clip-path="url(#...)" applied to this element (in element's user space)
        clips = active_clips
        cp_attr = inline_css.get("clip-path", el.attrib.get("clip-path", ""))
        if cp_attr:
            m_cp = _CLIP_URL_RE.search(cp_attr)
            if m_cp:
                cp_shapes = clip_defs.get(m_cp.group(1))
                if cp_shapes:
                    # Union of shapes inside a <clipPath> becomes one PDF clip subpath list
                    merged_clip: list[_PathCmd] = []
                    for local_cmds, shape_mat in cp_shapes:
                        full_cp_mat = _mat_mul(mat, shape_mat)
                        merged_clip.extend(
                            _transform_path_to_paper(local_cmds, full_cp_mat, height_mm)
                        )
                    if merged_clip:
                        clips = (*clips, tuple(merged_clip))

        # Compute inherited style for this element & descendants
        el_opacity_raw = inline_css.get("opacity", el.attrib.get("opacity", ""))
        el_opacity = inherited.opacity * (
            _parse_float(el_opacity_raw, 1.0) if el_opacity_raw else 1.0
        )
        el_id = el.attrib.get("id", "")
        is_hidden_node = inherited.ancestor_hidden or ("hidden" in el_id) or ("hidden" in cls_str)

        cur_style = _InheritedStyle(
            fill=_effective_attr(el, inline_css, cls_list, "fill", inherited.fill),
            stroke=_effective_attr(el, inline_css, cls_list, "stroke", inherited.stroke),
            stroke_width=_effective_attr(
                el, inline_css, cls_list, "stroke-width", inherited.stroke_width
            ),
            stroke_dasharray=_effective_attr(
                el, inline_css, cls_list, "stroke-dasharray", inherited.stroke_dasharray
            ),
            fill_opacity=_effective_attr(
                el, inline_css, cls_list, "fill-opacity", inherited.fill_opacity
            ),
            opacity=el_opacity,
            font_size=_effective_attr(el, inline_css, cls_list, "font-size", inherited.font_size),
            font_weight=_effective_attr(
                el, inline_css, cls_list, "font-weight", inherited.font_weight
            ),
            text_anchor=_effective_attr(
                el, inline_css, cls_list, "text-anchor", inherited.text_anchor
            ),
            ancestor_hidden=is_hidden_node,
        )

        # Container elements recurse into children
        if tag in ("svg", "g", "a"):
            for child in el:
                _walk(child, mat, cur_style, clips, is_root=False)
            return

        # Embedded raster image (<image href="data:image/png;base64,..." />)
        if tag == "image":
            href = (el.attrib.get("href") or el.attrib.get(_XLINK_HREF) or "").strip()
            iw = _parse_float(el.attrib.get("width"))
            ih = _parse_float(el.attrib.get("height"))
            if href and iw > 0.0 and ih > 0.0:
                ix = _parse_float(el.attrib.get("x"))
                iy = _parse_float(el.attrib.get("y"))
                par = el.attrib.get("preserveAspectRatio", "xMidYMid meet").strip()
                items.append(
                    _ImageItem(
                        x=ix,
                        y=iy,
                        width=iw,
                        height=ih,
                        transform=mat,
                        href=href,
                        opacity=cur_style.opacity,
                        preserve_aspect_ratio=par,
                        clip_paths=clips,
                    )
                )
            return

        # Text element (<text>...</text>, including nested <tspan>)
        if tag == "text":
            txt = "".join(el.itertext())
            if txt:
                a, b = mat[0], mat[1]
                angle_deg = math.degrees(math.atan2(b, a))
                rotation = -angle_deg if abs(angle_deg) > 0.01 else 0.0

                tx_local = _parse_float(el.attrib.get("x"))
                ty_local = _parse_float(el.attrib.get("y"))
                px, py = _mat_apply(mat, tx_local, ty_local)
                x_mm = px
                y_mm = height_mm - py

                fs_mm = _parse_float(cur_style.font_size, 3.0) if cur_style.font_size else 3.0
                fs_pt = fs_mm * MM_TO_PT * 0.90
                bold = cur_style.font_weight.strip().lower() in ("bold", "700", "800", "900")

                raw_text_fill = el.attrib.get("fill") or inline_css.get("fill")
                if (
                    raw_text_fill
                    and raw_text_fill not in ("none", "currentColor")
                    and not raw_text_fill.strip().startswith("var(")
                ):
                    color = raw_text_fill.strip()
                elif "balloon-text" in cls_list:
                    color = colors.balloon_text
                elif "dim-text" in cls_list:
                    color = colors.dim_text
                elif "accent-text" in cls_list:
                    color = colors.text_accent
                elif "section-label-text" in cls_list:
                    color = colors.line_section
                elif any(
                    c in cls_list
                    for c in (
                        "zone-text",
                        "view-scale",
                        "tb-field-label",
                        "tb-field-val-sm",
                        "panel-muted",
                    )
                ):
                    color = colors.text_muted
                else:
                    color = colors.text

                anchor_map: dict[str, Literal["left", "center", "right"]] = {
                    "start": "left",
                    "middle": "center",
                    "end": "right",
                }
                ha: Literal["left", "center", "right"] = anchor_map.get(
                    cur_style.text_anchor.strip().lower(), "left"
                )
                va: Literal["baseline", "center"] = "center" if rotation != 0.0 else "baseline"
                knockout_bg = colors.bg if "dim-text" in cls_list else None

                items.append(
                    _TextItem(
                        x_mm=x_mm,
                        y_mm=y_mm,
                        text=txt,
                        color=color,
                        fontsize_pt=fs_pt,
                        bold=bold,
                        ha=ha,
                        va=va,
                        rotation_deg=rotation,
                        knockout_bg=knockout_bg,
                        clip_paths=clips,
                    )
                )
            return

        # Geometric vector primitives
        if tag in ("line", "rect", "circle", "ellipse", "polyline", "polygon", "path"):
            local_cmds = _shape_element_to_local_path(el, tag)
            if not local_cmds:
                return
            paper_cmds = _transform_path_to_paper(local_cmds, mat, height_mm)

            stroke: str | None = None
            fill: str | None = None
            lw: float = 0.35 * MM_TO_PT
            dash_pt: tuple[float, ...] | None = None
            hatch: str | None = None

            raw_stroke = cur_style.stroke
            is_hidden = (
                "line-hidden" in cls_list
                or "layer_hidden" in cls_list
                or "line-hidden" in raw_stroke
                or inherited.ancestor_hidden
            )

            fill_str = cur_style.fill
            is_hatch = (
                "section-hatch-fill" in cls_list
                or "sectionHatch" in fill_str
                or "hatch_" in fill_str
            )

            if is_hidden:
                stroke = colors.line_hidden
                lw = 0.20 * MM_TO_PT
                dash_pt = (3.0, 2.0)
            elif is_hatch:
                stroke = colors.line_visible
                lw = 0.35 * MM_TO_PT
                # Open <line>/<polyline> background edges inside section views are stroke-only.
                if tag not in ("line", "polyline"):
                    fill = colors.table_header_bg
                    fill_lower = fill_str.lower()
                    if "aluminum" in fill_lower or "ansi38" in fill_lower:
                        hatch = "//"
                    elif "brass" in fill_lower or "ansi32" in fill_lower:
                        hatch = "/."
                    elif "plastic" in fill_lower:
                        hatch = "xx"
                    elif "rubber" in fill_lower:
                        hatch = ".."
                    else:
                        hatch = "/"
            elif (
                "stroke-visible" in cls_list
                or "line-visible" in cls_list
                or "line-visible" in raw_stroke
            ):
                stroke = colors.line_visible
                lw = _parse_stroke_width(cur_style.stroke_width or None, 0.50) * MM_TO_PT
            elif "line-center" in cls_list:
                stroke = colors.line_center
                lw = 0.25 * MM_TO_PT
                dash_pt = (6.0, 2.0, 1.0, 2.0)
            elif "line-thread" in cls_list:
                stroke = colors.line_visible
                lw = _parse_stroke_width(cur_style.stroke_width or None, 0.25) * MM_TO_PT
                if "line-thread-dashed" in cls_list:
                    dash_pt = (3.0, 2.0)
            elif "line-section" in cls_list or "line-section-thick" in cls_list:
                stroke = colors.line_section
                lw = _parse_stroke_width(cur_style.stroke_width or None, 0.6) * MM_TO_PT
            elif "section-arrow-head" in cls_list:
                fill = colors.line_section
            elif "border-main" in cls_list:
                stroke = colors.border
                lw = 0.70 * MM_TO_PT
            elif "border-sub" in cls_list or "panel-sub" in cls_list:
                stroke = colors.border_sub
                lw = _parse_stroke_width(cur_style.stroke_width or None, 0.35) * MM_TO_PT
            elif "panel-stroke" in cls_list:
                stroke = colors.border
                lw = _parse_stroke_width(cur_style.stroke_width or None, 0.50) * MM_TO_PT
            elif "title-bg" in cls_list or "panel-bg" in cls_list:
                fill = colors.title_bg
                stroke = colors.border
                lw = 0.6 * MM_TO_PT
            elif "table-header-bg" in cls_list:
                fill = colors.table_header_bg
            elif "table-row-alt" in cls_list:
                fill = colors.table_row_alt
            elif "dim-line" in cls_list:
                stroke = colors.dim_line
                lw = 0.25 * MM_TO_PT
                if cur_style.stroke_dasharray:
                    dash_pt = _parse_dasharray_pt(cur_style.stroke_dasharray)
            elif "dim-arrow" in cls_list or "leader-dot" in cls_list:
                fill = colors.dim_line
            elif "balloon-circle" in cls_list:
                fill = colors.balloon_bg
                stroke = colors.balloon_border
                lw = 0.5 * MM_TO_PT
            elif "balloon-dot" in cls_list:
                fill = colors.balloon_border
            elif "balloon-leader" in cls_list:
                stroke = colors.balloon_border
                lw = 0.25 * MM_TO_PT
            else:
                if tag not in ("line", "polyline") and fill_str:
                    fill_rgba = _parse_color_rgba(fill_str)
                    if (
                        fill_rgba is not None
                        and fill_rgba[3] > 0.0
                        and fill_str.strip().lower() not in ("#000000", "#000", "black")
                    ):
                        fill = fill_str.strip()
                stroke_rgba = _parse_color_rgba(raw_stroke) if raw_stroke else None
                if stroke_rgba is not None and stroke_rgba[3] > 0.0:
                    stroke = raw_stroke.strip()
                    lw = _parse_stroke_width(cur_style.stroke_width or None, 0.35) * MM_TO_PT
                    if cur_style.stroke_dasharray:
                        dash_pt = _parse_dasharray_pt(cur_style.stroke_dasharray)
                elif not fill:
                    stroke = colors.line_visible
                    lw = 0.35 * MM_TO_PT

            fill_alpha: float | None = None
            stroke_alpha: float | None = None
            if cur_style.fill_opacity:
                fill_alpha = _parse_float(cur_style.fill_opacity, 1.0)
            if cur_style.opacity < 0.999:
                fill_alpha = (
                    cur_style.opacity if fill_alpha is None else fill_alpha * cur_style.opacity
                )
                stroke_alpha = cur_style.opacity

            items.append(
                _VectorItem(
                    commands=paper_cmds,
                    fill=fill,
                    stroke=stroke,
                    linewidth_pt=lw,
                    dash_pt=dash_pt,
                    hatch=hatch,
                    fill_alpha=fill_alpha,
                    stroke_alpha=stroke_alpha,
                    clip_paths=clips,
                )
            )

    _walk(root, _IDENTITY_MATRIX, _InheritedStyle(), (), is_root=True)
    return items


# ---------------------------------------------------------------------------
# Image Decoding & Aspect Ratio Fitting
# ---------------------------------------------------------------------------


def _decode_image_href(href: str) -> tuple[bytes, Image.Image] | None:
    """Decode a ``data:image/...;base64,...`` URI or local image path into ``(raw_bytes, Image)``."""
    raw: bytes
    if href.startswith("data:"):
        comma = href.find(",")
        if comma < 0:
            return None
        header = href[:comma].lower()
        payload = href[comma + 1 :]
        if ";base64" in header:
            try:
                raw = base64.b64decode(payload)
            except Exception:
                return None
        else:
            return None
    elif os.path.isfile(href):
        with open(href, "rb") as f:
            raw = f.read()
    else:
        return None

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
        return raw, img
    except Exception:
        return None


def _fit_image_rect(
    x: float,
    y: float,
    w: float,
    h: float,
    px_w: int,
    px_h: int,
    preserve_aspect_ratio: str,
) -> tuple[float, float, float, float]:
    """Compute fitted local image box honoring SVG ``preserveAspectRatio``."""
    par = preserve_aspect_ratio.strip()
    if not par or par.lower().startswith("none") or px_w <= 0 or px_h <= 0:
        return (x, y, w, h)

    parts = par.split()
    align = parts[0] if parts else "xMidYMid"
    meet_or_slice = parts[1].lower() if len(parts) > 1 else "meet"

    img_aspect = px_w / px_h
    box_aspect = w / h
    if abs(img_aspect - box_aspect) < 1e-4:
        return (x, y, w, h)

    if (meet_or_slice == "slice") ^ (img_aspect > box_aspect):
        fit_w = w
        fit_h = w / img_aspect
    else:
        fit_h = h
        fit_w = h * img_aspect

    if align.startswith("xMin"):
        fx = x
    elif align.startswith("xMax"):
        fx = x + (w - fit_w)
    else:
        fx = x + (w - fit_w) * 0.5

    if "YMin" in align:
        fy = y
    elif "YMax" in align:
        fy = y + (h - fit_h)
    else:
        fy = y + (h - fit_h) * 0.5

    return (fx, fy, fit_w, fit_h)


# ---------------------------------------------------------------------------
# Cached Font Metrics (Helvetica AFM + DejaVu Sans FreeType Unicode Outlines)
# ---------------------------------------------------------------------------

_AFM_WIDTH_CACHE: dict[bool, dict[str, float]] = {}
_FT_FONT_CACHE: dict[bool, ft2font.FT2Font] = {}
_GLYPH_OUTLINE_CACHE: dict[
    tuple[bool, int], tuple[float, tuple[float, float, float, float], bytes]
] = {}


def _get_helvetica_widths(bold: bool) -> dict[str, float]:
    """Load exact Adobe Helvetica (or Helvetica-Bold) glyph widths in 1000-em units once."""
    cached = _AFM_WIDTH_CACHE.get(bold)
    if cached is not None:
        return cached

    afm_name = "Helvetica-Bold.afm" if bold else "Helvetica.afm"
    afm_path = os.path.join(matplotlib.get_data_path(), "fonts", "pdfcorefonts", afm_name)
    widths: dict[str, float] = {}
    by_name: dict[str, float] = {}

    if os.path.isfile(afm_path):
        with open(afm_path, encoding="latin1") as f:
            for line in f:
                if not line.startswith("C "):
                    continue
                parts = line.split(";")
                code: int | None = None
                wx: float | None = None
                gname: str | None = None
                for part in parts:
                    toks = part.strip().split()
                    if len(toks) == 2:
                        if toks[0] == "C":
                            code = int(toks[1])
                        elif toks[0] == "WX":
                            wx = float(toks[1])
                        elif toks[0] == "N":
                            gname = toks[1]
                if wx is not None:
                    if code is not None and 32 <= code <= 126:
                        widths[chr(code)] = wx
                    if gname is not None:
                        by_name[gname] = wx

    for ch, gname in _WINANSI_GLYPH_NAMES.items():
        if gname in by_name:
            widths[ch] = by_name[gname]

    _AFM_WIDTH_CACHE[bold] = widths
    return widths


def _get_dejavu_ft2(bold: bool) -> ft2font.FT2Font:
    """Return cached FreeType handle for bundled DejaVu Sans (regular or bold)."""
    cached = _FT_FONT_CACHE.get(bold)
    if cached is not None:
        return cached
    weight = "bold" if bold else "normal"
    ttf_path = fm.findfont(fm.FontProperties(family="DejaVu Sans", weight=weight))
    font = ft2font.FT2Font(ttf_path)
    _FT_FONT_CACHE[bold] = font
    return font


def _get_unicode_glyph_charproc(
    codepoint: int, bold: bool
) -> tuple[float, tuple[float, float, float, float], bytes]:
    """Extract a 1000-em PDF Type 3 CharProc stream for a Unicode glyph via FreeType."""
    cache_key = (bold, codepoint)
    cached = _GLYPH_OUTLINE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    font = _get_dejavu_ft2(bold)
    if font.get_char_index(codepoint) == 0 and bold:
        font = _get_dejavu_ft2(False)

    if font.get_char_index(codepoint) == 0:
        # Missing glyph fallback: crisp empty box outline
        wx = 600.0
        bbox = (60.0, 0.0, 540.0, 700.0)
        stream = b"600 0 60 0 540 700 d1 60 0 480 700 re 110 50 380 600 re f*"
        res = (wx, bbox, stream)
        _GLYPH_OUTLINE_CACHE[cache_key] = res
        return res

    glyph = font.load_char(codepoint, flags=ft2font.LoadFlags.NO_SCALE)
    upem = float(font.units_per_EM or 2048)
    wx = float(glyph.horiAdvance) * (1000.0 / upem)
    # ft2font.get_path() scales FreeType coordinates by 1/64
    scale_v = 64000.0 / upem

    verts, codes = font.get_path()
    ops: list[str] = []
    min_x = float("inf")
    min_y = float("inf")
    max_x = float("-inf")
    max_y = float("-inf")

    def _track(x: float, y: float) -> None:
        nonlocal min_x, min_y, max_x, max_y
        if x < min_x:
            min_x = x
        if y < min_y:
            min_y = y
        if x > max_x:
            max_x = x
        if y > max_y:
            max_y = y

    cur_x = 0.0
    cur_y = 0.0
    idx = 0
    n_pts = len(codes)
    while idx < n_pts:
        code = int(codes[idx])
        if code == 1:  # MOVETO
            x = float(verts[idx, 0]) * scale_v
            y = float(verts[idx, 1]) * scale_v
            _track(x, y)
            ops.append(f"{x:.1f} {y:.1f} m")
            cur_x, cur_y = x, y
            idx += 1
        elif code == 2:  # LINETO
            x = float(verts[idx, 0]) * scale_v
            y = float(verts[idx, 1]) * scale_v
            _track(x, y)
            ops.append(f"{x:.1f} {y:.1f} l")
            cur_x, cur_y = x, y
            idx += 1
        elif code == 3 and idx + 1 < n_pts:  # CURVE3 (quadratic -> cubic)
            qx = float(verts[idx, 0]) * scale_v
            qy = float(verts[idx, 1]) * scale_v
            x = float(verts[idx + 1, 0]) * scale_v
            y = float(verts[idx + 1, 1]) * scale_v
            c1x = cur_x + (2.0 / 3.0) * (qx - cur_x)
            c1y = cur_y + (2.0 / 3.0) * (qy - cur_y)
            c2x = x + (2.0 / 3.0) * (qx - x)
            c2y = y + (2.0 / 3.0) * (qy - y)
            _track(c1x, c1y)
            _track(c2x, c2y)
            _track(x, y)
            ops.append(f"{c1x:.1f} {c1y:.1f} {c2x:.1f} {c2y:.1f} {x:.1f} {y:.1f} c")
            cur_x, cur_y = x, y
            idx += 2
        elif code == 4 and idx + 2 < n_pts:  # CURVE4 (cubic)
            c1x = float(verts[idx, 0]) * scale_v
            c1y = float(verts[idx, 1]) * scale_v
            c2x = float(verts[idx + 1, 0]) * scale_v
            c2y = float(verts[idx + 1, 1]) * scale_v
            x = float(verts[idx + 2, 0]) * scale_v
            y = float(verts[idx + 2, 1]) * scale_v
            _track(c1x, c1y)
            _track(c2x, c2y)
            _track(x, y)
            ops.append(f"{c1x:.1f} {c1y:.1f} {c2x:.1f} {c2y:.1f} {x:.1f} {y:.1f} c")
            cur_x, cur_y = x, y
            idx += 3
        elif code == 79:  # CLOSEPOLY
            ops.append("h")
            idx += 1
        else:
            idx += 1

    if not math.isfinite(min_x):
        bbox = (0.0, 0.0, wx, 700.0)
        stream = f"{wx:.1f} 0 0 0 {wx:.1f} 700 d1".encode("ascii")
    else:
        bbox = (min_x, min_y, max_x, max_y)
        header = f"{wx:.1f} 0 {min_x:.1f} {min_y:.1f} {max_x:.1f} {max_y:.1f} d1 "
        stream = (header + " ".join(ops) + " f").encode("ascii")

    res = (wx, bbox, stream)
    _GLYPH_OUTLINE_CACHE[cache_key] = res
    return res


def _encode_winansi_char(ch: str) -> int | None:
    """Return PDF WinAnsiEncoding byte value (32..255) for ``ch``, or None if non-WinAnsi."""
    try:
        encoded = ch.encode("cp1252")
    except UnicodeEncodeError:
        return None
    if len(encoded) == 1 and encoded[0] >= 32:
        return encoded[0]
    return None


def _pdf_escape_bytes(raw: bytes) -> bytes:
    """Escape bytes for a PDF literal string ``(...)``."""
    out = bytearray()
    for b in raw:
        if b in (0x28, 0x29, 0x5C):  # (, ), \
            out.append(0x5C)
            out.append(b)
        elif b == 0x0A:
            out.extend(b"\\n")
        elif b == 0x0D:
            out.extend(b"\\r")
        elif b == 0x09:
            out.extend(b"\\t")
        elif b < 0x20 or b > 0x7E:
            out.extend(f"\\{b:03o}".encode("ascii"))
        else:
            out.append(b)
    return bytes(out)


def _pdf_escape_text_string(text: str) -> bytes:
    """Encode a PDF metadata / outline string (WinAnsi literal when ASCII/Latin-1, else UTF-16BE)."""
    try:
        raw = text.encode("latin1")
        return b"(" + _pdf_escape_bytes(raw) + b")"
    except UnicodeEncodeError:
        raw = b"\xfe\xff" + text.encode("utf-16-be")
        return b"<" + raw.hex().upper().encode("ascii") + b">"


# ---------------------------------------------------------------------------
# Direct Native PDF 1.4 Vector & Raster Writer
# ---------------------------------------------------------------------------


class _NativePdfWriter:
    """Fast, pure-Python PDF 1.4 writer with vector paths, patterns, clipping, Type 3 Unicode,
    raster Image XObjects (RGB + RGBA SMask), and native document outlines."""

    def __init__(
        self,
        *,
        bookmarks: bool = False,
        booklet_title: str = "ENGINEERING DRAWINGS",
        sheet_titles: list[str] | None = None,
    ) -> None:
        self._bookmarks = bookmarks
        self._booklet_title = booklet_title
        self._sheet_titles = sheet_titles or []

        self._objects: list[bytes | None] = []
        self._page_ids: list[int] = []

        # Reserve standard root objects upfront
        self._catalog_id = self._reserve()
        self._pages_id = self._reserve()
        self._font_reg_id = self._reserve()
        self._font_bold_id = self._reserve()

        # Dynamic Type 3 Unicode fallback fonts per bold flag: codepoint -> (glyph_code, wx_em)
        self._t3_glyphs: dict[bool, dict[int, tuple[int, float]]] = {False: {}, True: {}}
        self._t3_font_ids: dict[bool, int] = {}

        # Deduplicated document-wide resources
        self._extgstates: dict[tuple[int, int], tuple[str, int]] = {}
        self._patterns: dict[tuple[str, str], tuple[str, int]] = {}
        self._images: dict[bytes, tuple[str, int, int, int]] = {}

    def _reserve(self) -> int:
        self._objects.append(None)
        return len(self._objects)

    def _set_obj(self, obj_id: int, body: bytes) -> None:
        self._objects[obj_id - 1] = body

    def _add_obj(self, body: bytes) -> int:
        obj_id = self._reserve()
        self._set_obj(obj_id, body)
        return obj_id

    def _add_stream_obj(self, dict_entries: str, data: bytes, *, compress: bool = True) -> int:
        if compress:
            payload = zlib.compress(data, level=6)
            filter_entry = " /Filter /FlateDecode"
        else:
            payload = data
            filter_entry = ""
        header = f"<< {dict_entries}{filter_entry} /Length {len(payload)} >>\nstream\n".encode(
            "ascii"
        )
        return self._add_obj(header + payload + b"\nendstream")

    def _get_extgstate(self, fill_alpha: float, stroke_alpha: float) -> str:
        key = (int(round(fill_alpha * 1000)), int(round(stroke_alpha * 1000)))
        existing = self._extgstates.get(key)
        if existing is not None:
            return existing[0]
        name = f"GS{len(self._extgstates) + 1}"
        ca_f = max(0.0, min(1.0, key[0] / 1000.0))
        ca_s = max(0.0, min(1.0, key[1] / 1000.0))
        obj_id = self._add_obj(
            f"<< /Type /ExtGState /ca {ca_f:.4f} /CA {ca_s:.4f} >>".encode("ascii")
        )
        self._extgstates[key] = (name, obj_id)
        return name

    def _get_hatch_pattern(self, hatch: str, color_hex: str) -> str:
        key = (hatch, color_hex)
        existing = self._patterns.get(key)
        if existing is not None:
            return existing[0]
        rgba = _parse_color_rgba(color_hex) or (0.5, 0.5, 0.5, 1.0)
        r, g, b, _ = rgba
        name = f"P{len(self._patterns) + 1}"

        if hatch == "//":
            step = 10.0
            stream = (
                f"{r:.4f} {g:.4f} {b:.4f} RG 0.55 w "
                "-2 -2 m 12 12 l -2 8 m 2 12 l 8 -2 m 12 2 l "
                "-2 0.5 m 9.5 12 l 5.5 -2 m 12 4.5 l S"
            ).encode("ascii")
        elif hatch == "/.":
            step = 10.0
            stream = (
                f"{r:.4f} {g:.4f} {b:.4f} RG 0.55 w "
                "-2 -2 m 12 12 l -2 8 m 2 12 l 8 -2 m 12 2 l S "
                "[2.5 2.0] 0 d 0 5 m 5 10 l 5 0 m 10 5 l S"
            ).encode("ascii")
        elif hatch == "xx":
            step = 8.0
            stream = (
                f"{r:.4f} {g:.4f} {b:.4f} RG 0.50 w -2 -2 m 10 10 l -2 10 m 10 -2 l S"
            ).encode("ascii")
        elif hatch == "..":
            step = 6.0
            stream = (f"{r:.4f} {g:.4f} {b:.4f} rg 1.5 1.5 0.9 0.9 re 4.5 4.5 0.9 0.9 re f").encode(
                "ascii"
            )
        else:
            step = 8.0
            stream = (
                f"{r:.4f} {g:.4f} {b:.4f} RG 0.55 w -2 -2 m 10 10 l -2 6 m 2 10 l 6 -2 m 10 2 l S"
            ).encode("ascii")

        obj_id = self._add_stream_obj(
            f"/Type /Pattern /PatternType 1 /PaintType 1 /TilingType 1 "
            f"/BBox [0 0 {step:.1f} {step:.1f}] /XStep {step:.1f} /YStep {step:.1f} "
            f"/Resources << >>",
            stream,
            compress=True,
        )
        self._patterns[key] = (name, obj_id)
        return name

    def _get_image_xobject(self, href: str) -> tuple[str, int, int] | None:
        decoded = _decode_image_href(href)
        if decoded is None:
            return None
        raw, img = decoded
        existing = self._images.get(raw)
        if existing is not None:
            return (existing[0], existing[2], existing[3])

        px_w, px_h = img.size
        has_alpha = img.mode in ("RGBA", "LA") or ("transparency" in img.info)
        smask_ref = ""
        if has_alpha:
            rgba_img = img.convert("RGBA")
            rgb_bytes = rgba_img.convert("RGB").tobytes()
            alpha_channel = rgba_img.getchannel("A")
            extrema = alpha_channel.getextrema()
            min_alpha = (
                float(extrema[0])
                if isinstance(extrema, tuple) and isinstance(extrema[0], (int, float))
                else 0.0
            )
            if min_alpha < 255.0:
                alpha_bytes = alpha_channel.tobytes()
                smask_id = self._add_stream_obj(
                    f"/Type /XObject /Subtype /Image /Width {px_w} /Height {px_h} "
                    f"/ColorSpace /DeviceGray /BitsPerComponent 8",
                    alpha_bytes,
                    compress=True,
                )
                smask_ref = f" /SMask {smask_id} 0 R"
        else:
            rgb_bytes = img.convert("RGB").tobytes()

        img_id = self._add_stream_obj(
            f"/Type /XObject /Subtype /Image /Width {px_w} /Height {px_h} "
            f"/ColorSpace /DeviceRGB /BitsPerComponent 8{smask_ref}",
            rgb_bytes,
            compress=True,
        )
        name = f"Im{len(self._images) + 1}"
        self._images[raw] = (name, img_id, px_w, px_h)
        return (name, px_w, px_h)

    def _register_t3_char(self, codepoint: int, bold: bool) -> tuple[str, int, float]:
        """Assign a 1-byte code inside the Type 3 Unicode fallback font and return ``(font_name, code, wx_em)``."""
        table = self._t3_glyphs[bold]
        if codepoint in table:
            gcode, wx = table[codepoint]
        else:
            gcode = len(table) + 1
            if gcode > 255:
                gcode = 255
            wx, _bbox, _stream = _get_unicode_glyph_charproc(codepoint, bold)
            table[codepoint] = (gcode, wx)
        if bold not in self._t3_font_ids:
            self._t3_font_ids[bold] = self._reserve()
        fname = "F4" if bold else "F3"
        return (fname, gcode, wx)

    @staticmethod
    def _emit_path_cmds(buf: list[bytes], cmds: Sequence[_PathCmd]) -> None:
        s = MM_TO_PT
        for cmd in cmds:
            if cmd[0] == "M":
                buf.append(f"{cmd[1] * s:.3f} {cmd[2] * s:.3f} m\n".encode("ascii"))
            elif cmd[0] == "L":
                buf.append(f"{cmd[1] * s:.3f} {cmd[2] * s:.3f} l\n".encode("ascii"))
            elif cmd[0] == "C":
                buf.append(
                    f"{cmd[1] * s:.3f} {cmd[2] * s:.3f} "
                    f"{cmd[3] * s:.3f} {cmd[4] * s:.3f} "
                    f"{cmd[5] * s:.3f} {cmd[6] * s:.3f} c\n".encode("ascii")
                )
            else:
                buf.append(b"h\n")

    def _measure_and_segment_text(
        self, text: str, bold: bool, fontsize_pt: float
    ) -> tuple[float, list[tuple[str, bytes]]]:
        """Split ``text`` into ``(pdf_font_name, raw_bytes)`` runs and compute width in pt."""
        afm_widths = _get_helvetica_widths(bold)
        base_font = "F2" if bold else "F1"
        runs: list[tuple[str, bytearray]] = []
        total_em = 0.0

        for ch in text:
            wa = _encode_winansi_char(ch)
            if wa is not None:
                fname = base_font
                byte_val = wa
                total_em += afm_widths.get(ch, 556.0)
            else:
                fname, byte_val, wx_em = self._register_t3_char(ord(ch), bold)
                total_em += wx_em

            if runs and runs[-1][0] == fname:
                runs[-1][1].append(byte_val)
            else:
                runs.append((fname, bytearray([byte_val])))

        width_pt = total_em * (fontsize_pt / 1000.0)
        return width_pt, [(fn, bytes(raw)) for fn, raw in runs]

    def add_page(
        self,
        width_mm: float,
        height_mm: float,
        svg_code: str,
        colors: ThemeColors,
    ) -> None:
        """Parse one SVG sheet and append it as a compressed vector+raster PDF page."""
        w_pt = width_mm * MM_TO_PT
        h_pt = height_mm * MM_TO_PT
        scene_items = _parse_svg_scene(svg_code, height_mm, colors)

        buf: list[bytes] = []
        page_gs: set[str] = set()
        page_patterns: set[str] = set()
        page_images: set[str] = set()
        page_fonts: set[str] = set()

        # Paint explicit sheet background canvas
        bg_rgba = _parse_color_rgba(colors.bg) or (1.0, 1.0, 1.0, 1.0)
        buf.append(
            f"{bg_rgba[0]:.4f} {bg_rgba[1]:.4f} {bg_rgba[2]:.4f} rg "
            f"0 0 {w_pt:.3f} {h_pt:.3f} re f\n".encode("ascii")
        )
        # Round line caps & joins match technical drafting & ExportSVG stroke-linecap="round"
        buf.append(b"1 J 1 j\n")

        for item in scene_items:
            if isinstance(item, _VectorItem):
                fill_rgba = _parse_color_rgba(item.fill) if item.fill else None
                stroke_rgba = _parse_color_rgba(item.stroke) if item.stroke else None
                if fill_rgba is None and stroke_rgba is None:
                    continue

                f_alpha = fill_rgba[3] if fill_rgba is not None else 1.0
                s_alpha = stroke_rgba[3] if stroke_rgba is not None else 1.0
                if item.fill_alpha is not None:
                    f_alpha *= item.fill_alpha
                if item.stroke_alpha is not None:
                    s_alpha *= item.stroke_alpha

                needs_save = bool(
                    item.clip_paths
                    or item.dash_pt
                    or item.hatch
                    or f_alpha < 0.999
                    or s_alpha < 0.999
                )
                if needs_save:
                    buf.append(b"q\n")
                for cp in item.clip_paths:
                    self._emit_path_cmds(buf, cp)
                    buf.append(b"W n\n")

                if f_alpha < 0.999 or s_alpha < 0.999:
                    gs_name = self._get_extgstate(f_alpha, s_alpha)
                    page_gs.add(gs_name)
                    buf.append(f"/{gs_name} gs\n".encode("ascii"))

                if item.hatch and fill_rgba is not None:
                    # 1. Base fill under hatch
                    buf.append(
                        f"{fill_rgba[0]:.4f} {fill_rgba[1]:.4f} {fill_rgba[2]:.4f} rg\n".encode(
                            "ascii"
                        )
                    )
                    self._emit_path_cmds(buf, item.commands)
                    buf.append(b"f\n")
                    # 2. Vector tiling hatch pattern overlay
                    pat_name = self._get_hatch_pattern(item.hatch, colors.hatch)
                    page_patterns.add(pat_name)
                    buf.append(f"/Pattern cs /{pat_name} scn\n".encode("ascii"))
                    self._emit_path_cmds(buf, item.commands)
                    buf.append(b"f\n")
                    # 3. Section contour outline
                    if stroke_rgba is not None:
                        buf.append(
                            f"{stroke_rgba[0]:.4f} {stroke_rgba[1]:.4f} {stroke_rgba[2]:.4f} RG "
                            f"{item.linewidth_pt:.3f} w\n".encode("ascii")
                        )
                        self._emit_path_cmds(buf, item.commands)
                        buf.append(b"S\n")
                else:
                    if fill_rgba is not None:
                        buf.append(
                            f"{fill_rgba[0]:.4f} {fill_rgba[1]:.4f} {fill_rgba[2]:.4f} rg\n".encode(
                                "ascii"
                            )
                        )
                    if stroke_rgba is not None:
                        buf.append(
                            f"{stroke_rgba[0]:.4f} {stroke_rgba[1]:.4f} {stroke_rgba[2]:.4f} RG "
                            f"{item.linewidth_pt:.3f} w\n".encode("ascii")
                        )
                        if item.dash_pt:
                            d_str = " ".join(f"{v:.2f}" for v in item.dash_pt)
                            buf.append(f"[{d_str}] 0 d\n".encode("ascii"))

                    self._emit_path_cmds(buf, item.commands)
                    if fill_rgba is not None and stroke_rgba is not None:
                        buf.append(b"B\n")
                    elif fill_rgba is not None:
                        buf.append(b"f\n")
                    else:
                        buf.append(b"S\n")

                if needs_save:
                    buf.append(b"Q\n")

            elif isinstance(item, _ImageItem):
                xobj = self._get_image_xobject(item.href)
                if xobj is None:
                    continue
                im_name, px_w, px_h = xobj
                page_images.add(im_name)

                fx, fy, fw, fh = _fit_image_rect(
                    item.x,
                    item.y,
                    item.width,
                    item.height,
                    px_w,
                    px_h,
                    item.preserve_aspect_ratio,
                )

                # Map unit square [0,1]^2 of PDF Image XObject to SVG image corners:
                # (0,0) -> bottom-left, (1,0) -> bottom-right, (0,1) -> top-left
                bl_sx, bl_sy = _mat_apply(item.transform, fx, fy + fh)
                br_sx, br_sy = _mat_apply(item.transform, fx + fw, fy + fh)
                tl_sx, tl_sy = _mat_apply(item.transform, fx, fy)

                bl_x = bl_sx * MM_TO_PT
                bl_y = (height_mm - bl_sy) * MM_TO_PT
                br_x = br_sx * MM_TO_PT
                br_y = (height_mm - br_sy) * MM_TO_PT
                tl_x = tl_sx * MM_TO_PT
                tl_y = (height_mm - tl_sy) * MM_TO_PT

                ma = br_x - bl_x
                mb = br_y - bl_y
                mc = tl_x - bl_x
                md = tl_y - bl_y
                me = bl_x
                mf = bl_y

                buf.append(b"q\n")
                for cp in item.clip_paths:
                    self._emit_path_cmds(buf, cp)
                    buf.append(b"W n\n")
                if item.opacity < 0.999:
                    gs_name = self._get_extgstate(item.opacity, item.opacity)
                    page_gs.add(gs_name)
                    buf.append(f"/{gs_name} gs\n".encode("ascii"))
                buf.append(
                    f"{ma:.4f} {mb:.4f} {mc:.4f} {md:.4f} {me:.4f} {mf:.4f} cm /{im_name} Do\n".encode(
                        "ascii"
                    )
                )
                buf.append(b"Q\n")

            elif isinstance(item, _TextItem):
                width_pt, runs = self._measure_and_segment_text(
                    item.text, item.bold, item.fontsize_pt
                )
                for fn, _ in runs:
                    page_fonts.add(fn)

                x_pt = item.x_mm * MM_TO_PT
                y_pt = item.y_mm * MM_TO_PT

                dx_local = 0.0
                if item.ha == "center":
                    dx_local = -width_pt * 0.5
                elif item.ha == "right":
                    dx_local = -width_pt
                dy_local = -0.359 * item.fontsize_pt if item.va == "center" else 0.0

                rot_rad = math.radians(item.rotation_deg)
                cos_r = math.cos(rot_rad)
                sin_r = math.sin(rot_rad)

                has_state = bool(
                    item.clip_paths or item.knockout_bg or abs(item.rotation_deg) > 1e-4
                )
                if has_state:
                    buf.append(b"q\n")
                for cp in item.clip_paths:
                    self._emit_path_cmds(buf, cp)
                    buf.append(b"W n\n")

                if abs(item.rotation_deg) > 1e-4:
                    buf.append(
                        f"{cos_r:.6f} {sin_r:.6f} {-sin_r:.6f} {cos_r:.6f} "
                        f"{x_pt:.3f} {y_pt:.3f} cm\n".encode("ascii")
                    )
                    tx, ty = dx_local, dy_local
                else:
                    tx, ty = x_pt + dx_local, y_pt + dy_local

                # Dimension text knockout background rectangle (breaks dimension line behind value)
                if item.knockout_bg:
                    ko_rgba = _parse_color_rgba(item.knockout_bg) or bg_rgba
                    pad = 0.15 * item.fontsize_pt
                    bx = tx - pad
                    by = ty - 0.22 * item.fontsize_pt - pad
                    bw = width_pt + 2.0 * pad
                    bh = 0.96 * item.fontsize_pt + 2.0 * pad
                    buf.append(
                        f"{ko_rgba[0]:.4f} {ko_rgba[1]:.4f} {ko_rgba[2]:.4f} rg "
                        f"{bx:.3f} {by:.3f} {bw:.3f} {bh:.3f} re f\n".encode("ascii")
                    )

                txt_rgba = _parse_color_rgba(item.color) or (0.0, 0.0, 0.0, 1.0)
                buf.append(
                    f"{txt_rgba[0]:.4f} {txt_rgba[1]:.4f} {txt_rgba[2]:.4f} rg\n".encode("ascii")
                )
                buf.append(f"BT 1 0 0 1 {tx:.3f} {ty:.3f} Tm ".encode("ascii"))
                for fn, raw_bytes in runs:
                    escaped = _pdf_escape_bytes(raw_bytes)
                    buf.append(
                        f"/{fn} {item.fontsize_pt:.3f} Tf (".encode("ascii") + escaped + b") Tj "
                    )
                buf.append(b"ET\n")

                if has_state:
                    buf.append(b"Q\n")

        content_bytes = b"".join(buf)
        content_id = self._add_stream_obj("", content_bytes, compress=True)

        # Assemble per-page Resource dictionary
        res_parts: list[str] = []
        font_entries: list[str] = []
        if "F1" in page_fonts:
            font_entries.append(f"/F1 {self._font_reg_id} 0 R")
        if "F2" in page_fonts:
            font_entries.append(f"/F2 {self._font_bold_id} 0 R")
        if "F3" in page_fonts and False in self._t3_font_ids:
            font_entries.append(f"/F3 {self._t3_font_ids[False]} 0 R")
        if "F4" in page_fonts and True in self._t3_font_ids:
            font_entries.append(f"/F4 {self._t3_font_ids[True]} 0 R")
        if font_entries:
            res_parts.append("/Font << " + " ".join(font_entries) + " >>")

        if page_gs:
            gs_entries = [
                f"/{name} {oid} 0 R" for name, oid in self._extgstates.values() if name in page_gs
            ]
            res_parts.append("/ExtGState << " + " ".join(gs_entries) + " >>")

        if page_patterns:
            pat_entries = [
                f"/{name} {oid} 0 R"
                for name, oid in self._patterns.values()
                if name in page_patterns
            ]
            res_parts.append("/Pattern << " + " ".join(pat_entries) + " >>")

        if page_images:
            im_entries = [
                f"/{name} {oid} 0 R"
                for name, oid, _w, _h in self._images.values()
                if name in page_images
            ]
            res_parts.append("/XObject << " + " ".join(im_entries) + " >>")

        resources_str = "<< " + " ".join(res_parts) + " >>"
        page_id = self._add_obj(
            (
                f"<< /Type /Page /Parent {self._pages_id} 0 R "
                f"/MediaBox [0 0 {w_pt:.3f} {h_pt:.3f}] "
                f"/Resources {resources_str} "
                f"/Contents {content_id} 0 R >>"
            ).encode("ascii")
        )
        self._page_ids.append(page_id)

    def _finalize_type3_font(self, bold: bool) -> None:
        font_id = self._t3_font_ids.get(bold)
        table = self._t3_glyphs[bold]
        if font_id is None or not table:
            return

        # Sort by assigned byte code (1..N)
        sorted_items = sorted(table.items(), key=lambda kv: kv[1][0])
        first_char = sorted_items[0][1][0]
        last_char = sorted_items[-1][1][0]

        charprocs_entries: list[str] = []
        diff_entries: list[str] = [str(first_char)]
        widths: list[str] = []
        cmap_lines: list[str] = []

        by_code = {gcode: (cp, wx) for cp, (gcode, wx) in table.items()}
        for code in range(first_char, last_char + 1):
            entry = by_code.get(code)
            if entry is None:
                diff_entries.append("/.notdef")
                widths.append("0")
                continue
            cp, wx = entry
            gname = f"u{cp:04X}"
            _, _bbox, stream_bytes = _get_unicode_glyph_charproc(cp, bold)
            cp_obj_id = self._add_stream_obj("", stream_bytes, compress=True)
            charprocs_entries.append(f"/{gname} {cp_obj_id} 0 R")
            diff_entries.append(f"/{gname}")
            widths.append(f"{wx:.1f}")
            cmap_lines.append(f"<{code:02X}> <{cp:04X}>")

        charprocs_id = self._add_obj(("<< " + " ".join(charprocs_entries) + " >>").encode("ascii"))
        encoding_id = self._add_obj(
            ("<< /Type /Encoding /Differences [" + " ".join(diff_entries) + "] >>").encode("ascii")
        )
        cmap_body = (
            "/CIDInit /ProcSet findresource begin\n"
            "12 dict begin begincmap\n"
            "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
            "/CMapName /Adobe-Identity-UCS def /CMapType 2 def\n"
            "1 begincodespacerange\n<01> <FF>\nendcodespacerange\n"
            f"{len(cmap_lines)} beginbfchar\n"
            + "\n".join(cmap_lines)
            + "\nendbfchar\nendcmap CMapName currentdict /CMap defineresource pop end end\n"
        ).encode("ascii")
        tounicode_id = self._add_stream_obj("", cmap_body, compress=True)

        self._set_obj(
            font_id,
            (
                f"<< /Type /Font /Subtype /Type3 "
                f"/FontBBox [-200 -300 1200 1000] "
                f"/FontMatrix [0.001 0 0 0.001 0 0] "
                f"/CharProcs {charprocs_id} 0 R "
                f"/Encoding {encoding_id} 0 R "
                f"/FirstChar {first_char} /LastChar {last_char} "
                f"/Widths [{' '.join(widths)}] "
                f"/ToUnicode {tounicode_id} 0 R >>"
            ).encode("ascii"),
        )

    def to_bytes(self) -> bytes:
        """Finalize PDF catalog, fonts, outlines, xref table, and return raw PDF bytes."""
        # Standard Type 1 Helvetica fonts
        self._set_obj(
            self._font_reg_id,
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        )
        self._set_obj(
            self._font_bold_id,
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
        )
        # Dynamic Type 3 Unicode fallback fonts (only if non-WinAnsi chars appeared)
        self._finalize_type3_font(False)
        self._finalize_type3_font(True)

        kids_str = " ".join(f"{pid} 0 R" for pid in self._page_ids)
        self._set_obj(
            self._pages_id,
            f"<< /Type /Pages /Kids [{kids_str}] /Count {len(self._page_ids)} >>".encode("ascii"),
        )

        # Optional PDF Document Outlines / Bookmarks
        outlines_ref = ""
        if self._bookmarks and self._sheet_titles and self._page_ids:
            n = len(self._page_ids)
            outlines_id = self._reserve()
            top_id = self._reserve()
            child_ids = [self._reserve() for _ in range(n)]

            for i in range(n):
                title = self._sheet_titles[i] if i < len(self._sheet_titles) else f"Sheet {i + 1}"
                parts = [
                    b"/Title " + _pdf_escape_text_string(title),
                    f"/Parent {top_id} 0 R".encode("ascii"),
                    f"/Dest [{self._page_ids[i]} 0 R /Fit]".encode("ascii"),
                ]
                if i > 0:
                    parts.append(f"/Prev {child_ids[i - 1]} 0 R".encode("ascii"))
                if i < n - 1:
                    parts.append(f"/Next {child_ids[i + 1]} 0 R".encode("ascii"))
                self._set_obj(child_ids[i], b"<< " + b" ".join(parts) + b" >>")

            top_parts = [
                b"/Title " + _pdf_escape_text_string(self._booklet_title),
                f"/Parent {outlines_id} 0 R".encode("ascii"),
                f"/Count {n}".encode("ascii"),
                f"/Dest [{self._page_ids[0]} 0 R /Fit]".encode("ascii"),
                f"/First {child_ids[0]} 0 R".encode("ascii"),
                f"/Last {child_ids[-1]} 0 R".encode("ascii"),
            ]
            self._set_obj(top_id, b"<< " + b" ".join(top_parts) + b" >>")

            self._set_obj(
                outlines_id,
                (
                    f"<< /Type /Outlines /First {top_id} 0 R /Last {top_id} 0 R /Count {n + 1} >>"
                ).encode("ascii"),
            )
            outlines_ref = f" /Outlines {outlines_id} 0 R"

        self._set_obj(
            self._catalog_id,
            f"<< /Type /Catalog /Pages {self._pages_id} 0 R{outlines_ref} >>".encode("ascii"),
        )

        # Emit binary PDF 1.4 file with exact xref byte offsets
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets: list[int] = [0]
        for idx, body in enumerate(self._objects, start=1):
            offsets.append(len(out))
            out.extend(f"{idx} 0 obj\n".encode("ascii"))
            out.extend(body if body is not None else b"null")
            out.extend(b"\nendobj\n")

        xref_pos = len(out)
        n_objs = len(self._objects) + 1
        out.extend(f"xref\n0 {n_objs}\n0000000000 65535 f \n".encode("ascii"))
        for off in offsets[1:]:
            out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
        out.extend(
            (
                f"trailer\n<< /Size {n_objs} /Root {self._catalog_id} 0 R >>\n"
                f"startxref\n{xref_pos}\n%%EOF\n"
            ).encode("ascii")
        )
        return bytes(out)


# ---------------------------------------------------------------------------
# Matplotlib Path Conversion Helper (shared by _render_svg_to_ax)
# ---------------------------------------------------------------------------


def _path_cmds_to_mpl_path(cmds: Sequence[_PathCmd]) -> mpath.Path | None:
    verts: list[tuple[float, float]] = []
    codes: list[int] = []
    for cmd in cmds:
        if cmd[0] == "M":
            verts.append((cmd[1], cmd[2]))
            codes.append(int(mpath.Path.MOVETO))
        elif cmd[0] == "L":
            verts.append((cmd[1], cmd[2]))
            codes.append(int(mpath.Path.LINETO))
        elif cmd[0] == "C":
            verts.extend([(cmd[1], cmd[2]), (cmd[3], cmd[4]), (cmd[5], cmd[6])])
            codes.extend([int(mpath.Path.CURVE4), int(mpath.Path.CURVE4), int(mpath.Path.CURVE4)])
        else:
            verts.append((0.0, 0.0))
            codes.append(int(mpath.Path.CLOSEPOLY))
    if not verts:
        return None
    return mpath.Path(verts, codes)


# ---------------------------------------------------------------------------
# Public PDFRenderer Facade
# ---------------------------------------------------------------------------


class PDFRenderer:
    """Renders vector technical drawings and embedded raster views to multi-page PDF documents."""

    @classmethod
    def _render_svg_to_ax(
        cls,
        ax: Any,
        svg_code: str,
        width_mm: float,
        height_mm: float,
        colors: ThemeColors,
    ) -> None:
        """Render parsed SVG vector paths, text, and embedded raster images onto a Matplotlib Axes."""
        ax.set_xlim(0, width_mm)
        ax.set_ylim(0, height_mm)
        ax.axis("off")

        # Explicit background canvas
        ax.add_patch(
            patches.Rectangle((0, 0), width_mm, height_mm, facecolor=colors.bg, edgecolor="none")
        )

        scene_items = _parse_svg_scene(svg_code, height_mm, colors)
        for item in scene_items:
            if isinstance(item, _VectorItem):
                mpl_path = _path_cmds_to_mpl_path(item.commands)
                if mpl_path is None:
                    continue
                ls: Any = (0, item.dash_pt) if item.dash_pt else "-"
                patch = patches.PathPatch(
                    mpl_path,
                    facecolor=item.fill or "none",
                    edgecolor=item.stroke or "none",
                    linewidth=item.linewidth_pt,
                    linestyle=ls,
                    hatch=item.hatch,
                    alpha=item.fill_alpha if item.fill_alpha is not None else item.stroke_alpha,
                )
                ax.add_patch(patch)
                if item.clip_paths:
                    cp_path = _path_cmds_to_mpl_path(item.clip_paths[-1])
                    if cp_path is not None:
                        patch.set_clip_path(cp_path, transform=ax.transData)

            elif isinstance(item, _ImageItem):
                decoded = _decode_image_href(item.href)
                if decoded is None:
                    continue
                _, img = decoded
                rgba = img.convert("RGBA")
                px_w, px_h = rgba.size
                fx, fy, fw, fh = _fit_image_rect(
                    item.x,
                    item.y,
                    item.width,
                    item.height,
                    px_w,
                    px_h,
                    item.preserve_aspect_ratio,
                )
                bl_sx, bl_sy = _mat_apply(item.transform, fx, fy + fh)
                br_sx, br_sy = _mat_apply(item.transform, fx + fw, fy + fh)
                tl_sx, tl_sy = _mat_apply(item.transform, fx, fy)

                bl_x, bl_y = bl_sx, height_mm - bl_sy
                br_x, br_y = br_sx, height_mm - br_sy
                tl_x, tl_y = tl_sx, height_mm - tl_sy

                affine = mtransforms.Affine2D.from_values(
                    br_x - bl_x,
                    br_y - bl_y,
                    tl_x - bl_x,
                    tl_y - bl_y,
                    bl_x,
                    bl_y,
                )
                im_artist = ax.imshow(
                    np.asarray(rgba),
                    extent=(0.0, 1.0, 0.0, 1.0),
                    origin="upper",
                    interpolation="antialiased",
                    transform=affine + ax.transData,
                    alpha=item.opacity,
                    clip_on=True,
                )
                if item.clip_paths:
                    cp_path = _path_cmds_to_mpl_path(item.clip_paths[-1])
                    if cp_path is not None:
                        im_artist.set_clip_path(cp_path, transform=ax.transData)

            elif isinstance(item, _TextItem):
                bbox_props = (
                    {
                        "boxstyle": "square,pad=0.15",
                        "facecolor": item.knockout_bg,
                        "edgecolor": "none",
                    }
                    if item.knockout_bg
                    else None
                )
                txt_artist = ax.text(
                    item.x_mm,
                    item.y_mm,
                    item.text,
                    color=item.color,
                    fontsize=item.fontsize_pt,
                    fontweight="bold" if item.bold else "normal",
                    ha=item.ha,
                    va=item.va,
                    rotation=item.rotation_deg,
                    rotation_mode="anchor",
                    bbox=bbox_props,
                )
                if item.clip_paths:
                    cp_path = _path_cmds_to_mpl_path(item.clip_paths[-1])
                    if cp_path is not None:
                        txt_artist.set_clip_path(cp_path, transform=ax.transData)

    @staticmethod
    def _parse_svg_length_mm(raw: str) -> float | None:
        """Convert an SVG length attribute (mm/cm/in/pt/px/unitless) to mm, rejecting '%'."""
        m = re.match(r"^\s*([-+]?\d*\.?\d+)\s*([a-zA-Z%]*)\s*$", raw)
        if not m:
            return None
        val = float(m.group(1))
        unit = m.group(2).lower()
        if unit == "%":
            return None
        unit_scale = {
            "": 1.0,
            "mm": 1.0,
            "cm": 10.0,
            "in": 25.4,
            "pt": 25.4 / 72.0,
            "pc": 25.4 / 6.0,
            "px": 25.4 / 96.0,
        }
        factor = unit_scale.get(unit)
        return val * factor if factor is not None else None

    @classmethod
    def _parse_svg_dimensions_mm(cls, svg_code: str) -> tuple[float, float]:
        """Extract (width_mm, height_mm) from the root <svg> tag or viewBox, defaulting to A4."""
        root_m = re.search(r"<svg\b([^>]*)>", svg_code, re.IGNORECASE)
        if root_m:
            attrs = root_m.group(1)
            w_m = re.search(r'\bwidth\s*=\s*["\']([^"\']+)["\']', attrs, re.IGNORECASE)
            h_m = re.search(r'\bheight\s*=\s*["\']([^"\']+)["\']', attrs, re.IGNORECASE)
            if w_m and h_m:
                w_mm = cls._parse_svg_length_mm(w_m.group(1))
                h_mm = cls._parse_svg_length_mm(h_m.group(1))
                if w_mm is not None and h_mm is not None:
                    return w_mm, h_mm
            vb_m = re.search(
                r'\bviewBox\s*=\s*["\']\s*[-+]?\d*\.?\d+[\s,]+[-+]?\d*\.?\d+[\s,]+([-+]?\d*\.?\d+)[\s,]+([-+]?\d*\.?\d+)',
                attrs,
                re.IGNORECASE,
            )
            if vb_m:
                return float(vb_m.group(1)), float(vb_m.group(2))
        return (297.0, 210.0)

    @classmethod
    def _coerce_sheet_input(
        cls,
        item: SheetInput,
        theme: Theme,
    ) -> tuple[float, float, str]:
        """Normalize a tuple, DrawingSheet, SVG path, or SVG string into (width_mm, height_mm, svg_str)."""
        # Runtime import: core.sheet imports this module lazily for export_pdf().
        from build123d_techdraw.core.sheet import DrawingSheet

        if isinstance(item, tuple) and len(item) == 3:
            return (float(item[0]), float(item[1]), str(item[2]))
        if isinstance(item, DrawingSheet):
            return (item.width_mm, item.height_mm, item.to_svg(theme=theme))
        if isinstance(item, (str, os.PathLike)):
            path_or_svg = os.fspath(item)
            if os.path.isfile(path_or_svg):
                with open(path_or_svg, encoding="utf-8") as f:
                    svg_code = f.read()
            else:
                svg_code = path_or_svg
            w_mm, h_mm = cls._parse_svg_dimensions_mm(svg_code)
            return (w_mm, h_mm, svg_code)
        raise TypeError(
            f"Expected (width_mm, height_mm, svg_str), DrawingSheet, or SVG path/string; got {type(item).__name__}"
        )

    @classmethod
    def render_sheets_to_pdf(
        cls,
        sheets_data: Sequence[SheetInput],
        output_path: str,
        theme: Theme = Theme.DARK_BLUEPRINT,
        bookmarks: bool = False,
        booklet_title: str = "ENGINEERING DRAWINGS",
        sheet_titles: list[str] | None = None,
    ) -> None:
        """Render multiple sheets (tuples, DrawingSheets, or SVG paths/strings) to a multi-page PDF."""
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        colors = THEME_COLORS[theme]

        writer = _NativePdfWriter(
            bookmarks=bookmarks,
            booklet_title=booklet_title,
            sheet_titles=sheet_titles,
        )
        for item in sheets_data:
            width_mm, height_mm, svg_code = cls._coerce_sheet_input(item, theme)
            writer.add_page(width_mm, height_mm, svg_code, colors)

        pdf_bytes = writer.to_bytes()
        with open(output_path, "wb") as f:
            f.write(pdf_bytes)
