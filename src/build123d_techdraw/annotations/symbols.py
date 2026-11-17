"""ISO standard drafting symbols (ISO 5456 projection cones, etc.)."""


def projection_symbol_elements(
    third_angle: bool,
    centerline_dasharray: str = "2,1",
    centerline_width: float = 0.2,
) -> str:
    """Return the SVG elements of the ISO 5456-2 projection method symbol.

    The symbol is a truncated cone drawn in its side view (a trapezoid with the
    large end on the left and the small end on the right) next to its end view
    (two concentric circles). The end view is the one seen from the small end, so
    both circles are drawn as visible outlines.

    * First-angle (ISO 128-3 / ISO 5456-2 default, "method E"): the end view is
      placed on the side opposite the one it was seen from, so the circles sit to
      the **left** of the trapezoid.
    * Third-angle (ASME Y14.3 / US default, "method A"): the end view is placed on
      the side it was seen from, so the circles sit to the **right** of the
      trapezoid.

    The elements are centred on the local origin and span roughly x in [-18, 18],
    y in [-9, 9] (before any scaling applied by the caller).
    """
    if third_angle:
        cone_x0, circle_cx = -8.0, 8.0
        hline = (-12.0, 18.0)
    else:
        cone_x0, circle_cx = 0.0, -8.0
        hline = (-18.0, 12.0)
    cone_x1 = cone_x0 + 8.0

    # Large end (half-height 7) on the left, small end (half-height 4) on the right.
    cone = (
        f'<polygon points="{cone_x0:g},-7 {cone_x1:g},-4 {cone_x1:g},4 {cone_x0:g},7" '
        'fill="none" class="line-visible" stroke-width="0.35"/>'
    )
    circles = (
        f'<circle cx="{circle_cx:g}" cy="0" r="4" fill="none" class="line-visible" stroke-width="0.35"/>'
        f'<circle cx="{circle_cx:g}" cy="0" r="7" fill="none" class="line-visible" stroke-width="0.35"/>'
    )
    centerlines = (
        f'<line x1="{hline[0]:g}" y1="0" x2="{hline[1]:g}" y2="0" class="line-center" '
        f'stroke-dasharray="{centerline_dasharray}" stroke-width="{centerline_width:g}"/>'
        f'<line x1="{circle_cx:g}" y1="-9" x2="{circle_cx:g}" y2="9" class="line-center" '
        f'stroke-dasharray="{centerline_dasharray}" stroke-width="{centerline_width:g}"/>'
    )
    return f"{cone}{circles}{centerlines}"


def render_projection_cone_svg(
    center: tuple[float, float],
    third_angle: bool = False,
    scale: float = 0.65,
) -> str:
    """Render the ISO 5456-2 projection method symbol (first- or third-angle).

    Defaults to first-angle projection, the ISO 128-3 / ISO 5456-2 default.
    """
    cx, cy = center
    return f"""
    <g class="projection-symbol" transform="translate({cx:.2f}, {cy:.2f}) scale({scale:.4f})">
        {projection_symbol_elements(third_angle)}
    </g>
    """
