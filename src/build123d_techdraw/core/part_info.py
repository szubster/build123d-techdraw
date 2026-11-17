"""Typed bill-of-materials metadata for build123d shapes.

build123d already carries ``label`` (BOM description) and ``material`` (a typed
``FinishedMaterial``) on every shape. It has no field for a part number or a free-text
material specification, so this module attaches a frozen :class:`PartInfo` to a shape.
This is the only place that reads or writes that attribute.

The attribute lives in the shape's ``__dict__``, so it survives ``copy``/``deepcopy``
(and therefore ``moved()``/``located()`` copies). Boolean operations (``a - b``) create
a new shape without it: set the part info on the finished shape.
"""

from dataclasses import dataclass
from typing import Any

from build123d.topology import Shape

_PART_INFO_ATTR = "_techdraw_part_info"


@dataclass(frozen=True)
class PartInfo:
    """BOM metadata for one assembly component.

    Attributes:
        part_no: Part / drawing number shown in the BOM. Empty -> ``PART-<item no>``.
        material_spec: Material designation text for the BOM (e.g. ``"AL 6061-T6"``).
            Empty -> derived from the shape's build123d ``material``, else ``"STANDARD"``.
    """

    part_no: str = ""
    material_spec: str = ""


def set_part_info(shape: Shape[Any], info: PartInfo) -> None:
    """Attach BOM metadata to ``shape`` (replacing any previous value)."""
    setattr(shape, _PART_INFO_ATTR, info)


def get_part_info(shape: Shape[Any]) -> PartInfo | None:
    """Return the BOM metadata attached with :func:`set_part_info`, if any."""
    info = shape.__dict__.get(_PART_INFO_ATTR)
    return info if isinstance(info, PartInfo) else None
