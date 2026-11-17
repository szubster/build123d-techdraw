"""ISO standard drafting symbols (ISO 5456 projection cones, etc.)."""


def render_projection_cone_svg(
    center: tuple[float, float],
    third_angle: bool = True,
    scale: float = 0.65,
) -> str:
    """Render ISO 5456-2 projection cone symbol (Third Angle or First Angle)."""
    cx, cy = center
    if third_angle:
        # Third angle: frustum on left, projection circles on right
        cone = '<polygon points="-8,-4 0,-7 0,7 -8,4" fill="none" class="line-visible" stroke-width="0.35"/>'
        circles = (
            '<circle cx="8" cy="0" r="4" fill="none" class="line-visible" stroke-width="0.35"/>'
            '<circle cx="8" cy="0" r="7" fill="none" class="line-visible" stroke-width="0.35"/>'
        )
        centerlines = (
            '<line x1="-12" y1="0" x2="18" y2="0" class="line-center" stroke-dasharray="2,1" stroke-width="0.2"/>'
            '<line x1="8" y1="-9" x2="8" y2="9" class="line-center" stroke-dasharray="2,1" stroke-width="0.2"/>'
        )
    else:
        # First angle: projection circles on left, frustum on right
        circles = (
            '<circle cx="-8" cy="0" r="4" fill="none" class="line-visible" stroke-width="0.35"/>'
            '<circle cx="-8" cy="0" r="7" fill="none" class="line-visible" stroke-width="0.35"/>'
        )
        cone = '<polygon points="0,-4 8,-7 8,7 0,4" fill="none" class="line-visible" stroke-width="0.35"/>'
        centerlines = (
            '<line x1="-18" y1="0" x2="12" y2="0" class="line-center" stroke-dasharray="2,1" stroke-width="0.2"/>'
            '<line x1="-8" y1="-9" x2="-8" y2="9" class="line-center" stroke-dasharray="2,1" stroke-width="0.2"/>'
        )

    return f"""
    <g class="projection-symbol" transform="translate({cx:.2f}, {cy:.2f}) scale({scale:.4f})">
        {cone}
        {circles}
        {centerlines}
    </g>
    """
