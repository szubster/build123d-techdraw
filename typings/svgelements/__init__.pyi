# svgelements has no type information. Treat everything it exports as Any for pyright, the
# same way mypy does via ``ignore_missing_imports``. See [tool.pyright] in pyproject.toml.
from typing import Any

def __getattr__(name: str) -> Any: ...
