"""Deliberate data problems, injected at known rates and counted exactly.

The counts go to the manifest as ground truth, so a data quality tool built
on top of this data can be measured (did it find all 1,002 orphan rows?)
instead of judged by eye.
"""

from __future__ import annotations

from collections import Counter

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from .config import DirtyConfig


def _mask(rng: np.random.Generator, n: int, rate: float) -> np.ndarray:
    return rng.random(n) < rate if rate > 0 else np.zeros(n, dtype=bool)


def null_out(
    table: pa.Table, column: str, rate: float, rng: np.random.Generator
) -> tuple[pa.Table, int]:
    col = table[column].combine_chunks()
    mask = _mask(rng, len(col), rate) & ~col.is_null().to_numpy(zero_copy_only=False)
    if not mask.any():
        return table, 0
    new = pc.if_else(pa.array(mask), pa.nulls(len(col), col.type), col)
    idx = table.schema.get_field_index(column)
    return table.set_column(idx, column, new), int(mask.sum())


def duplicate_rows(table: pa.Table, rate: float, rng: np.random.Generator) -> tuple[pa.Table, int]:
    """Append exact copies of random rows, like a retried batch load would."""
    dup_idx = np.flatnonzero(_mask(rng, table.num_rows, rate))
    if len(dup_idx) == 0:
        return table, 0
    return pa.concat_tables([table, table.take(pa.array(dup_idx))]), len(dup_idx)


def price_outliers(items: pa.Table, rate: float, rng: np.random.Generator) -> tuple[pa.Table, int]:
    """Multiply unit price and line total by 100 (a cents/reais unit mix-up).

    The parent order total is left untouched, so these rows also break the
    order subtotal = sum(line totals) invariant.
    """
    mask = _mask(rng, items.num_rows, rate)
    if not mask.any():
        return items, 0
    for name in ("unit_price_cents", "line_total_cents"):
        col = items[name].to_numpy()
        items = items.set_column(
            items.schema.get_field_index(name), name, pa.array(np.where(mask, col * 100, col))
        )
    return items, int(mask.sum())


def orphan_products(
    items: pa.Table, n_products: int, rate: float, rng: np.random.Generator
) -> tuple[pa.Table, int]:
    """Point product_id at ids that do not exist in the products table."""
    mask = _mask(rng, items.num_rows, rate)
    if not mask.any():
        return items, 0
    col = items["product_id"].to_numpy()
    bad = n_products + 1 + rng.integers(0, 10_000, items.num_rows)
    idx = items.schema.get_field_index("product_id")
    return items.set_column(idx, "product_id", pa.array(np.where(mask, bad, col))), int(mask.sum())


def drift_orders_schema(orders: pa.Table, rng: np.random.Generator) -> pa.Table:
    """Simulate an upstream release: `channel` renamed, new nullable `coupon_code`."""
    idx = orders.schema.get_field_index("channel")
    orders = orders.rename_columns(
        [("sales_channel" if i == idx else n) for i, n in enumerate(orders.column_names)]
    )
    has_coupon = rng.random(orders.num_rows) < 0.1
    codes = np.array(["BEMVINDO10", "FRETEGRATIS", "BLACK20", "VOLTA15"])
    coupon = pa.array(codes[rng.integers(0, len(codes), orders.num_rows)], mask=~has_coupon)
    return orders.append_column("coupon_code", coupon)


class Injector:
    """Applies a DirtyConfig to generated tables and keeps the tally."""

    def __init__(self, cfg: DirtyConfig, seed: int) -> None:
        self.cfg = cfg
        self.seed = seed
        self.counts: Counter[str] = Counter()

    def _rng(self, *key: int) -> np.random.Generator:
        return np.random.default_rng(np.random.SeedSequence([self.seed, 99, *key]))

    def customers(self, table: pa.Table) -> pa.Table:
        table, k = null_out(table, "email", self.cfg.null_rate, self._rng(0))
        self.counts["customers.email.null"] += k
        return table

    def order_chunk(
        self,
        chunk: int,
        orders: pa.Table,
        items: pa.Table,
        n_products: int,
        last_chunk: bool,
    ) -> tuple[list[pa.Table], pa.Table]:
        """Return the orders as one or more parts (the drifted tail is its own part)."""
        rng = self._rng(1, chunk)
        orders, _ = null_out(orders, "channel", self.cfg.null_rate, rng)
        orders, k = duplicate_rows(orders, self.cfg.duplicate_rate, rng)
        self.counts["orders.duplicate_rows"] += k
        items, k = price_outliers(items, self.cfg.outlier_rate, rng)
        self.counts["order_items.price_outlier"] += k
        items, k = orphan_products(items, n_products, self.cfg.orphan_rate, rng)
        self.counts["order_items.orphan_product_id"] += k
        if self.cfg.schema_drift and last_chunk:
            # The newest 20% of the last chunk arrives with the new schema.
            cut = int(orders.num_rows * 0.8)
            drifted = drift_orders_schema(orders.slice(cut), rng)
            self.counts["orders.schema_drift_rows"] += drifted.num_rows
            parts = [orders.slice(0, cut), drifted]
        else:
            parts = [orders]
        # Null channels are counted in the final parts: duplicated rows carry
        # their nulls, and in drifted rows the column is called sales_channel.
        for part in parts:
            for col in ("channel", "sales_channel"):
                if col in part.column_names and part[col].null_count:
                    self.counts[f"orders.{col}.null"] += part[col].null_count
        return parts, items
