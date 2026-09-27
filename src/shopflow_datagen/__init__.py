"""Deterministic synthetic e-commerce data for the 30 Days of Data & Software Engineering series."""

__version__ = "0.1.0"

from .config import DirtyConfig, GenConfig  # noqa: E402
from .writer import generate_tables, iter_parts, write  # noqa: E402

__all__ = ["DirtyConfig", "GenConfig", "__version__", "generate_tables", "iter_parts", "write"]
