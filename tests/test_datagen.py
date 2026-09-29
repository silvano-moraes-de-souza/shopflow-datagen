import json
from datetime import date

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest

from shopflow_datagen import DirtyConfig, GenConfig, generate_tables, write
from shopflow_datagen.cli import main
from shopflow_datagen.generate import LOCAL_TO_UTC_US, US_PER_DAY, day_weights
from shopflow_datagen.reference import FREE_SHIPPING_THRESHOLD_CENTS

SMALL = GenConfig(scale=0.05, chunk_size=2_000)
DIRTY = GenConfig(scale=0.1, chunk_size=3_000, dirty=DirtyConfig.default_dirty())


@pytest.fixture(scope="module")
def clean():
    parts, counts = generate_tables(SMALL)
    assert counts == {}
    return {name: pa.concat_tables(p) for name, p in parts.items()}


@pytest.fixture(scope="module")
def dirty():
    return generate_tables(DIRTY)


def _np(table, col):
    return table[col].to_numpy()


def test_same_config_is_deterministic():
    a, _ = generate_tables(SMALL)
    b, _ = generate_tables(SMALL)
    for name in a:
        assert all(x.equals(y) for x, y in zip(a[name], b[name], strict=True)), name


def test_different_seed_changes_output(clean):
    other, _ = generate_tables(GenConfig(scale=0.05, chunk_size=2_000, seed=7))
    assert not pa.concat_tables(other["orders"]).equals(clean["orders"])


def test_row_counts_follow_scale(clean):
    assert clean["customers"].num_rows == SMALL.n_customers
    assert clean["products"].num_rows == SMALL.n_products
    assert clean["orders"].num_rows == SMALL.n_orders
    assert clean["payments"].num_rows == SMALL.n_orders
    ids = _np(clean["order_items"], "order_item_id")
    assert np.array_equal(ids, np.arange(1, len(ids) + 1))


def test_referential_integrity(clean):
    customers = set(_np(clean["customers"], "customer_id"))
    products = set(_np(clean["products"], "product_id"))
    orders = set(_np(clean["orders"], "order_id"))
    assert set(_np(clean["orders"], "customer_id")) <= customers
    assert set(_np(clean["order_items"], "product_id")) <= products
    assert set(_np(clean["order_items"], "order_id")) == orders
    assert set(_np(clean["payments"], "order_id")) == orders


def test_money_invariants(clean):
    o, items = clean["orders"], clean["order_items"]
    assert np.array_equal(
        _np(items, "line_total_cents"),
        _np(items, "quantity") * _np(items, "unit_price_cents"),
    )
    by_order = items.group_by("order_id").aggregate([("line_total_cents", "sum")])
    joined = o.join(by_order, "order_id")
    assert np.array_equal(_np(joined, "subtotal_cents"), _np(joined, "line_total_cents_sum"))
    assert np.array_equal(
        _np(o, "total_cents"),
        _np(o, "subtotal_cents") - _np(o, "discount_cents") + _np(o, "shipping_cents"),
    )
    free = _np(o, "subtotal_cents") >= FREE_SHIPPING_THRESHOLD_CENTS
    assert (_np(o, "shipping_cents")[free] == 0).all()
    assert (_np(o, "shipping_cents")[~free] > 0).all()
    pay = clean["payments"]
    assert np.array_equal(_np(pay, "amount_cents"), _np(o, "total_cents"))


def test_timestamps_are_consistent(clean):
    o, c = clean["orders"], clean["customers"]
    ordered = pc.cast(o["ordered_at"], pa.int64()).to_numpy()
    signup = pc.cast(c["signup_at"], pa.int64()).to_numpy()
    assert (ordered >= signup[_np(o, "customer_id") - 1]).all()
    first = np.datetime64(SMALL.start, "D").astype(np.int64) * US_PER_DAY
    last = (np.datetime64(SMALL.end, "D").astype(np.int64) + 1) * US_PER_DAY
    # The window is in Brasilia time (UTC-3); stored timestamps are UTC.
    assert ordered.min() >= first + LOCAL_TO_UTC_US
    assert ordered.max() < last + LOCAL_TO_UTC_US

    status = o["status"].to_numpy(zero_copy_only=False)
    delivered = np.isin(status, ["delivered", "returned"])
    assert np.array_equal(~o["delivered_at"].is_null().to_numpy(zero_copy_only=False), delivered)

    pay_status = clean["payments"]["status"].to_numpy(zero_copy_only=False)
    unpaid = np.isin(pay_status, ["refused", "pending"])
    paid_null = clean["payments"]["paid_at"].is_null().to_numpy(zero_copy_only=False)
    assert np.array_equal(paid_null, unpaid)


def test_black_friday_is_a_demand_spike():
    days, w = day_weights(GenConfig())
    dates = days.astype("datetime64[D]")
    bf_2024 = w[dates == np.datetime64("2024-11-29")][0]
    november = w[(dates >= np.datetime64("2024-11-01")) & (dates < np.datetime64("2024-11-25"))]
    assert bf_2024 > 3 * np.median(november)


def test_dirty_counts_match_the_data(dirty):
    parts, counts = dirty
    customers = parts["customers"][0]
    assert customers["email"].null_count == counts["customers.email.null"] > 0

    orders = parts["orders"]
    n_rows = sum(p.num_rows for p in orders)
    distinct = len(set(np.concatenate([_np(p, "order_id") for p in orders])))
    assert n_rows - distinct == counts["orders.duplicate_rows"] > 0

    items = pa.concat_tables(parts["order_items"])
    pid = _np(items, "product_id")
    orphan = pid > DIRTY.n_products
    assert orphan.sum() == counts["order_items.orphan_product_id"] > 0

    price = _np(parts["products"][0], "price_cents")
    unit = _np(items, "unit_price_cents")
    outlier = ~orphan & (unit == price[np.minimum(pid, DIRTY.n_products) - 1] * 100)
    assert outlier.sum() == counts["order_items.price_outlier"] > 0


def test_schema_drift_only_in_the_last_part(dirty):
    parts, counts = dirty
    *old, new = parts["orders"]
    assert all("channel" in p.column_names for p in old)
    assert "sales_channel" in new.column_names and "coupon_code" in new.column_names
    assert "channel" not in new.column_names
    assert new.num_rows == counts["orders.schema_drift_rows"]


def test_config_validation():
    with pytest.raises(ValueError):
        GenConfig(scale=0)
    with pytest.raises(ValueError):
        GenConfig(start=date(2025, 1, 1), end=date(2024, 1, 1))


@pytest.mark.parametrize("fmt", ["parquet", "csv"])
def test_write_produces_files_and_manifest(tmp_path, fmt):
    manifest = write(GenConfig(scale=0.02, chunk_size=1_000), tmp_path, fmt)
    on_disk = json.loads((tmp_path / "_manifest.json").read_text())
    assert on_disk["total_rows"] == manifest["total_rows"] > 0
    assert len(manifest["tables"]["orders"]["files"]) == 2
    for info in manifest["tables"].values():
        for f in info["files"]:
            assert (tmp_path / f).stat().st_size > 0
    if fmt == "parquet":
        assert pq.read_table(tmp_path / "orders").num_rows == manifest["tables"]["orders"]["rows"]


def test_cli(tmp_path, capsys):
    main(["--scale", "0.01", "--out", str(tmp_path), "--dirty"])
    out = capsys.readouterr().out
    assert "orders" in out and "injected" in out
    assert (tmp_path / "_manifest.json").exists()


def test_growth_follows_trend_not_signup_timing():
    parts, _ = generate_tables(GenConfig(scale=0.3, chunk_size=10_000))
    orders = pa.concat_tables(parts["orders"])
    month = pc.strftime(orders["ordered_at"], format="%Y-%m", locale="C")
    counts = {r["values"]: r["counts"] for r in pc.value_counts(month).to_pylist()}
    # Same calendar month one year apart, away from retail events: the linear
    # trend (1.0 -> 1.6) plus the growing customer base should stay well under 2x.
    ratio = counts["2025-03"] / counts["2024-03"]
    assert 1.1 < ratio < 2.0


def test_null_counts_describe_the_final_files(dirty):
    parts, counts = dirty
    in_channel = sum(
        p["channel"].null_count for p in parts["orders"] if "channel" in p.column_names
    )
    in_drifted = sum(
        p["sales_channel"].null_count for p in parts["orders"] if "sales_channel" in p.column_names
    )
    assert counts["orders.channel.null"] == in_channel > 0
    assert counts.get("orders.sales_channel.null", 0) == in_drifted
