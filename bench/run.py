"""Generation throughput and memory by scale and by chunk size.

uv run python -m bench.run
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from bench.harness import measure, save
from bench.plot import plot
from shopflow_datagen import GenConfig, write

SCALES = [0.1, 1, 10, 50]
CHUNKS = [100_000, 500_000, 2_000_000]


def _case(cfg: GenConfig, label: str, runs: int):
    out = Path(tempfile.mkdtemp(prefix="shopflow-bench-"))

    def clean() -> None:
        shutil.rmtree(out, ignore_errors=True)

    def run() -> dict:
        m = write(cfg, out)
        bytes_ = sum(t["bytes"] for t in m["tables"].values())
        return {"rows": m["total_rows"], "bytes": bytes_}

    try:
        result = measure(run, label=label, params=cfg.to_dict(), runs=runs, setup=clean)
    finally:
        clean()
    result.extra["rows_per_s"] = result.extra["rows"] / result.median_s
    return result


def main() -> None:
    by_scale = [_case(GenConfig(scale=s), f"scale {s:g}", runs=3) for s in SCALES]
    path = save(
        "throughput_by_scale",
        by_scale,
        notes="Parquet + zstd to local disk, default chunk_size=500k, clean data.",
    )
    print(plot(path, "rows_per_s", title="Rows generated per second, by scale"))
    print(plot(path, "median_s", title="Wall time to generate and write, by scale"))

    by_chunk = [_case(GenConfig(scale=10, chunk_size=c), f"chunk {c:,}", runs=3) for c in CHUNKS]
    path = save("memory_by_chunk_size", by_chunk, notes="scale=10 (3.9M rows), Parquet + zstd.")
    print(plot(path, "median_peak_rss_mb", title="Peak memory at scale 10, by chunk size"))
    print(plot(path, "median_s", title="Wall time at scale 10, by chunk size"))


if __name__ == "__main__":
    main()
