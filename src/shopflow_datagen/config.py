from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

BASE_CUSTOMERS = 20_000
BASE_ORDERS = 100_000


@dataclass(frozen=True)
class DirtyConfig:
    """Rates of deliberate data problems. All zero means clean output.

    Every injected problem is counted in the manifest, so downstream quality
    checks can be scored against ground truth instead of eyeballed.
    """

    null_rate: float = 0.0
    duplicate_rate: float = 0.0
    outlier_rate: float = 0.0
    orphan_rate: float = 0.0
    schema_drift: bool = False

    @classmethod
    def default_dirty(cls) -> DirtyConfig:
        return cls(
            null_rate=0.01,
            duplicate_rate=0.005,
            outlier_rate=0.001,
            orphan_rate=0.001,
            schema_drift=True,
        )

    @property
    def enabled(self) -> bool:
        return (
            any([self.null_rate, self.duplicate_rate, self.outlier_rate, self.orphan_rate])
            or self.schema_drift
        )


@dataclass(frozen=True)
class GenConfig:
    """What to generate.

    Output is deterministic for a given (seed, scale, start, end, chunk_size,
    dirty). Changing chunk_size changes the random streams and therefore the rows.
    """

    scale: float = 1.0
    seed: int = 42
    start: date = date(2024, 1, 1)
    end: date = date(2025, 12, 31)
    chunk_size: int = 500_000
    dirty: DirtyConfig = field(default_factory=DirtyConfig)

    def __post_init__(self) -> None:
        if self.scale <= 0:
            raise ValueError("scale must be positive")
        if self.end <= self.start:
            raise ValueError("end must be after start")
        if self.chunk_size < 1:
            raise ValueError("chunk_size must be >= 1")

    @property
    def n_customers(self) -> int:
        return max(10, round(BASE_CUSTOMERS * self.scale))

    @property
    def n_products(self) -> int:
        # Catalogs grow slower than traffic.
        return max(20, round(500 + 4_500 * math.sqrt(self.scale)))

    @property
    def n_orders(self) -> int:
        return max(10, round(BASE_ORDERS * self.scale))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["start"] = self.start.isoformat()
        data["end"] = self.end.isoformat()
        data["n_customers"] = self.n_customers
        data["n_products"] = self.n_products
        data["n_orders"] = self.n_orders
        return data
