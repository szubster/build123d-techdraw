"""Initial test verifying package metadata."""

from build123d_techdraw import __version__


def test_version() -> None:
    assert __version__ == "0.1.0"
