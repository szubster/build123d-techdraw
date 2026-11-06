"""build123d-techdraw: 2D technical drawing and blueprint engine for build123d."""

try:
    from ._version import __version__
except ImportError:  # pragma: no cover
    __version__ = "0.1.0.dev0"

__all__ = ["__version__"]
