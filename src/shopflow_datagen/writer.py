from __future__ import annotations

import json
import time
from collections import defaultdict
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from . import __version__
from .config import GenConfig
from .dirty import Injector
from .generate import customers, order_chunks, products

Format = Literal["parquet", "csv"]
TABLES = ("customers", "products", "orders", "order_items", "payments")


def iter_parts(cfg: GenConfig, injector: Injector | None = None) -> Iterator[tuple[str, pa.Table]]:
    """Yield (table name, part) in generation order. Parts of one table may differ
    in schema only when schema drift is enabled."""
    injector = injector or Injector(cfg.dirty, cfg.seed)
    cust = customers(cfg)
    prod = products(cfg)
    cust_table = injector.customers(cust.table) if cfg.dirty.enabled else cust.table
    yield "customers", cust_table
    yield "products", prod.table

    n_chunks = -(-cfg.n_orders // cfg.chunk_size)
    for chunk in order_chunks(cfg, cust, prod):
        order_parts, items = [chunk.orders], chunk.items
        if cfg.dirty.enabled:
            order_parts, items = injector.order_chunk(
                chunk.index, chunk.orders, chunk.items, cfg.n_products,
                last_chunk=chunk.index == n_chunks - 1,
            )  # fmt: skip
        for part in order_parts:
            yield "orders", part
        yield "order_items", items
        yield "payments", chunk.payments


def generate_tables(cfg: GenConfig) -> tuple[dict[str, list[pa.Table]], dict[str, int]]:
    """In-memory generation for tests and small scales. Returns (parts, dirty counts)."""
    injector = Injector(cfg.dirty, cfg.seed)
    parts: dict[str, list[pa.Table]] = defaultdict(list)
    for name, table in iter_parts(cfg, injector):
        parts[name].append(table)
    return dict(parts), dict(injector.counts)


def write(cfg: GenConfig, out: Path, fmt: Format = "parquet") -> dict[str, Any]:
    """Write every table as part files under ``out/<table>/`` plus ``out/_manifest.json``."""
    out.mkdir(parents=True, exist_ok=True)
    injector = Injector(cfg.dirty, cfg.seed)
    counters: dict[str, int] = defaultdict(int)
    tables: dict[str, dict[str, Any]] = {t: {"rows": 0, "files": [], "bytes": 0} for t in TABLES}

    t0 = time.perf_counter()
    for name, table in iter_parts(cfg, injector):
        part = counters[name]
        counters[name] += 1
        folder = out / name
        folder.mkdir(exist_ok=True)
        path = folder / f"part-{part:05d}.{fmt}"
        if fmt == "parquet":
            pq.write_table(table, path, compression="zstd")
        else:
            pacsv.write_csv(table, path)
        info = tables[name]
        info["rows"] += table.num_rows
        info["files"].append(path.relative_to(out).as_posix())
        info["bytes"] += path.stat().st_size
    elapsed = time.perf_counter() - t0

    manifest = {
        "generator": "shopflow-datagen",
        "version": __version__,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "format": fmt,
        "config": cfg.to_dict(),
        "tables": tables,
        "total_rows": sum(t["rows"] for t in tables.values()),
        "dirty_ground_truth": dict(injector.counts),
        "elapsed_s": round(elapsed, 3),
    }
    (out / "_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
