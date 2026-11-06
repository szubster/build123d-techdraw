"""ISO 5455 scale ladders and auto-scale utilities."""

# Standard ISO 5455 recommended scale ratios
ISO_5455_ENLARGEMENT_SCALES: list[float] = [50.0, 20.0, 10.0, 5.0, 2.0]
ISO_5455_FULL_SCALE: float = 1.0
ISO_5455_REDUCTION_SCALES: list[float] = [
    1.0 / 2.0,
    1.0 / 5.0,
    1.0 / 10.0,
    1.0 / 20.0,
    1.0 / 50.0,
    1.0 / 100.0,
    1.0 / 200.0,
    1.0 / 500.0,
    1.0 / 1000.0,
]

STANDARD_SCALES: list[float] = (
    ISO_5455_ENLARGEMENT_SCALES + [ISO_5455_FULL_SCALE] + ISO_5455_REDUCTION_SCALES
)


def format_iso_scale(scale: float) -> str:
    """Format scale string according to ISO 5455 standards (e.g. 'SCALE 1:1', 'SCALE 1:2')."""
    if abs(scale - 1.0) < 0.05:
        return "SCALE 1:1"
    if abs(scale - 1.5) < 0.08:
        return "SCALE 3:2"
    if abs(scale - 2.5) < 0.08:
        return "SCALE 5:2"

    if scale > 1.0:
        inv = round(scale)
        if abs(scale - inv) < 0.15:
            return f"SCALE {int(inv)}:1"
        return f"SCALE {scale:.1f}:1"
    else:
        inv = round(1.0 / scale)
        if abs(1.0 / inv - scale) < 0.05 * scale:
            return f"SCALE 1:{int(inv)}"
        return f"SCALE 1:{1.0 / scale:.1f}"


def find_closest_iso_scale(ratio: float) -> float:
    """Find the largest standard ISO 5455 scale that is <= the specified ratio."""
    valid_scales = [s for s in STANDARD_SCALES if s <= ratio * 1.02]
    if valid_scales:
        return max(valid_scales)
    return STANDARD_SCALES[-1]


def calculate_auto_scale(
    model_bbox_size: tuple[float, float],
    available_size: tuple[float, float],
    margin_factor: float = 0.75,
) -> float:
    """Calculate the best standard ISO 5455 scale fitting model dimensions into available space."""
    m_w, m_h = model_bbox_size
    a_w, a_h = available_size

    if m_w <= 0.0 or m_h <= 0.0 or a_w <= 0.0 or a_h <= 0.0:
        return 1.0

    target_w = a_w * margin_factor
    target_h = a_h * margin_factor

    ratio_w = target_w / m_w
    ratio_h = target_h / m_h
    max_ratio = min(ratio_w, ratio_h)

    return find_closest_iso_scale(max_ratio)
