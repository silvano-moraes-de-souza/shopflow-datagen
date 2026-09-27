"""shopflow-datagen command line.

shopflow-datagen --scale 1 --out data/sf1
shopflow-datagen --scale 10 --out data/sf10-dirty --dirty --format csv
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from .config import DirtyConfig, GenConfig
from .writer import write


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="shopflow-datagen",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="1.0 = 20k customers, ~5k products, 100k orders (default: 1)",
    )
    p.add_argument("--out", type=Path, required=True, help="output directory")
    p.add_argument("--format", choices=["parquet", "csv"], default="parquet")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--start", type=date.fromisoformat, default=date(2024, 1, 1))
    p.add_argument("--end", type=date.fromisoformat, default=date(2025, 12, 31))
    p.add_argument("--chunk-size", type=int, default=500_000)
    p.add_argument(
        "--dirty",
        action="store_true",
        help="inject nulls, duplicates, outliers, orphan FKs and schema drift",
    )
    return p  # fmt: skip


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    cfg = GenConfig(
        scale=args.scale,
        seed=args.seed,
        start=args.start,
        end=args.end,
        chunk_size=args.chunk_size,
        dirty=DirtyConfig.default_dirty() if args.dirty else DirtyConfig(),
    )
    m = write(cfg, args.out, args.format)
    width = max(len(t) for t in m["tables"])
    for name, info in m["tables"].items():
        print(f"{name:<{width}}  {info['rows']:>12,} rows  {len(info['files']):>3} files")
    print(f"{'total':<{width}}  {m['total_rows']:>12,} rows  in {m['elapsed_s']:.2f}s")
    for key, count in m["dirty_ground_truth"].items():
        print(f"  injected {key}: {count:,}")


if __name__ == "__main__":
    main()
