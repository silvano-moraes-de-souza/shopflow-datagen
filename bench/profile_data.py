"""Profile a generated dataset so the README's realism claims trace to a file.

uv run python -m bench.profile_data            # scale 1, clean
-> results/data_profile.json
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pyarrow.compute as pc
import pyarrow.parquet as pq
from matplotlib.ticker import FuncFormatter

from bench.harness import RESULTS_DIR
from bench.plot import GRID, INK, INK_MUTED, SERIES, SURFACE, plt  # plot sets the Agg backend
from shopflow_datagen import GenConfig, write


def _shares(table, column: str) -> dict[str, float]:
    n = table.num_rows
    return {
        r["values"]: round(r["counts"] / n, 4) for r in pc.value_counts(table[column]).to_pylist()
    }


def profile(cfg: GenConfig) -> dict:
    out = Path(tempfile.mkdtemp(prefix="shopflow-profile-"))
    try:
        write(cfg, out)
        orders = pq.read_table(out / "orders")
        items = pq.read_table(out / "order_items")
        payments = pq.read_table(out / "payments")
    finally:
        shutil.rmtree(out, ignore_errors=True)
    n_customers = cfg.n_customers

    total = orders["total_cents"].to_numpy() / 100
    per_customer = np.bincount(orders["customer_id"].to_numpy(), minlength=n_customers + 1)[1:]
    top1 = np.sort(per_customer)[::-1][: max(1, n_customers // 100)].sum() / per_customer.sum()

    # Calendar in Brasilia time, which is how the demand curve is defined.
    local = pc.add(orders["ordered_at"], pc.cast(-3 * 3_600_000_000, "duration[us]"))
    day = pc.strftime(local, format="%Y-%m-%d", locale="C")
    month = pc.utf8_slice_codeunits(day, 0, 7)
    by_month = dict(sorted((r["values"], r["counts"]) for r in pc.value_counts(month).to_pylist()))
    by_day = {r["values"]: r["counts"] for r in pc.value_counts(day).to_pylist()}
    nov_2025 = [v for k, v in by_day.items() if "2025-11-01" <= k < "2025-11-24"]

    return {
        "config": cfg.to_dict(),
        "orders": orders.num_rows,
        "avg_order_value_brl": round(float(total.mean()), 2),
        "median_order_value_brl": round(float(np.median(total)), 2),
        "p95_order_value_brl": round(float(np.percentile(total, 95)), 2),
        "items_per_order": round(items.num_rows / orders.num_rows, 3),
        "customers_with_orders_share": round(float(np.mean(per_customer > 0)), 4),
        "top1pct_customers_order_share": round(float(top1), 4),
        "max_orders_one_customer": int(per_customer.max()),
        "status_share": _shares(orders, "status"),
        "channel_share": _shares(orders, "channel"),
        "payment_method_share": _shares(payments, "method"),
        "orders_by_month_local": by_month,
        "black_friday_2025_orders": by_day.get("2025-11-28"),
        "median_day_nov_1_to_23_2025": int(np.median(nov_2025)),
        "mar_2025_over_mar_2024": round(by_month["2025-03"] / by_month["2024-03"], 3),
    }


def plot_monthly(result: dict, out: Path) -> Path:
    end_month = result["config"]["end"][:7]
    months = {k: v for k, v in result["orders_by_month_local"].items() if k <= end_month}
    labels = list(months)
    x = range(len(labels))
    fig, ax = plt.subplots(figsize=(10, 3.6), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    ax.bar(x, list(months.values()), width=0.7, color=SERIES, zorder=2)
    ax.set_xticks(x, [m[2:] for m in labels], rotation=90, fontsize=7, color=INK_MUTED)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.tick_params(axis="y", colors=INK_MUTED, labelsize=8)
    ax.tick_params(axis="x", length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.8, zorder=0)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.set_ylabel("Orders", color=INK_MUTED, fontsize=9)
    title = "Orders per month at scale 1 (Brasilia time)"
    ax.set_title(title, loc="left", color=INK, fontsize=10, pad=10)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, facecolor=SURFACE)
    plt.close(fig)
    return out


def main() -> None:
    result = profile(GenConfig(scale=1))
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / "data_profile.json"
    path.write_text(json.dumps(result, indent=2), encoding="utf-8", newline="\n")
    print(path)
    print(plot_monthly(result, Path("docs/assets/orders_by_month.png")))
    for k, v in result.items():
        if not isinstance(v, dict):
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
