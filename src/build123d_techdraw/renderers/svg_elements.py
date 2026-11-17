"""Access helpers for parsed ``svgelements`` nodes.

``svgelements`` is untyped and exposes SVG attributes as dynamic Python attributes whose
presence depends on the element type. This module is the only place that probes them with
``getattr``; renderer code goes through these helpers.
"""

from typing import Any


def svg_property(el: Any, name: str, default: Any = None) -> Any:
    """Return the parsed ``svgelements`` property ``name`` of ``el``, or ``default`` if absent."""
    return getattr(el, name, default)


def has_hidden_ancestor(el: Any) -> bool:
    """Return True if any ancestor group of ``el`` has ``hidden`` in its id or class."""
    parent = svg_property(el, "parent")
    while parent:
        p_id = svg_property(parent, "id", "") or parent.values.get("id", "")
        p_cls = svg_property(parent, "class", "") or parent.values.get("class", "")
        if "hidden" in p_id or "hidden" in p_cls:
            return True
        parent = svg_property(parent, "parent")
    return False
