"""Vectorized generators for each table.

Customers and products are generated once. Orders, order items and payments
are generated in chunks so memory stays flat as the scale grows; each chunk
has its own random stream derived from (seed, table, chunk index).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from . import reference as ref
from .config import GenConfig

US_PER_SEC = 1_000_000
US_PER_DAY = 86_400 * US_PER_SEC
# Local business hours are Brasilia time (UTC-3, no DST since 2019); stored as UTC.
LOCAL_TO_UTC_US = 3 * 3_600 * US_PER_SEC
TS = pa.timestamp("us", tz="UTC")

_STREAM_CUSTOMERS, _STREAM_PRODUCTS, _STREAM_ORDERS = 1, 2, 3


def _rng(cfg: GenConfig, stream: int, chunk: int = 0) -> np.random.Generator:
    return np.random.default_rng(np.random.SeedSequence([cfg.seed, stream, chunk]))


def _normalize(weights: np.ndarray | list[float]) -> np.ndarray:
    w = np.asarray(weights, dtype=np.float64)
    return w / w.sum()


def _epoch_day(d) -> int:
    return int(np.datetime64(d, "D").astype(np.int64))


def _days(n) -> np.timedelta64:
    return np.timedelta64(int(n), "D")


def day_weights(cfg: GenConfig) -> tuple[np.ndarray, np.ndarray]:
    """Return (epoch days, relative demand) for every day in [start, end].

    Demand = linear growth trend x weekday curve x retail calendar events
    (Black Friday weekend, Cyber Monday, December, Mother's Day week in Brazil).
    """
    first, last = _epoch_day(cfg.start), _epoch_day(cfg.end)
    days = np.arange(first, last + 1, dtype=np.int64)
    n = len(days)
    w = 1.0 + 0.6 * np.arange(n) / max(n - 1, 1)
    w *= np.asarray(ref.WEEKDAY_WEIGHTS)[(days + 3) % 7]  # 1970-01-01 was a Thursday

    dates = days.astype("datetime64[D]")
    years = range(cfg.start.year, cfg.end.year + 1)
    for year in years:
        nov1 = np.datetime64(f"{year}-11-01")
        # 4th Thursday of November, then Friday.
        first_thu = nov1 + _days((3 - (nov1.astype(np.int64) + 3) % 7) % 7)
        black_friday = first_thu + _days(22)
        boosts = {
            black_friday: 3.5,
            black_friday + _days(1): 1.8,
            black_friday + _days(2): 1.6,
            black_friday + _days(3): 2.2,  # Cyber Monday
        }
        for day, factor in boosts.items():
            w[dates == day] *= factor
        december = (dates >= np.datetime64(f"{year}-12-01")) & (
            dates <= np.datetime64(f"{year}-12-20")
        )
        w[december] *= 1.3
        may1 = np.datetime64(f"{year}-05-01")
        first_sun = may1 + _days((6 - (may1.astype(np.int64) + 3) % 7) % 7)
        mothers_day = first_sun + _days(7)
        week_before = (dates >= mothers_day - _days(7)) & (dates < mothers_day)
        w[week_before] *= 1.4
    return days, w


@dataclass
class Customers:
    table: pa.Table
    state_idx: np.ndarray  # index into STATES, per customer
    signup_us: np.ndarray  # UTC microseconds, per customer
    weights: np.ndarray  # probability of placing an order, per customer


@dataclass
class Products:
    table: pa.Table
    price_cents: np.ndarray
    weights: np.ndarray


def customers(cfg: GenConfig) -> Customers:
    rng = _rng(cfg, _STREAM_CUSTOMERS)
    n = cfg.n_customers
    ids = np.arange(1, n + 1, dtype=np.int64)

    first = pa.array(ref.FIRST_NAMES).take(pa.array(rng.integers(0, len(ref.FIRST_NAMES), n)))
    last = pa.array(ref.LAST_NAMES).take(pa.array(rng.integers(0, len(ref.LAST_NAMES), n)))
    domain_idx = rng.choice(len(ref.EMAIL_DOMAINS), n, p=_normalize(ref.EMAIL_DOMAIN_WEIGHTS))
    domain = pa.array(ref.EMAIL_DOMAINS).take(pa.array(domain_idx))
    local = pc.binary_join_element_wise(pc.utf8_lower(first), pc.utf8_lower(last), ".")
    local = pc.binary_join_element_wise(local, pc.cast(pa.array(ids), pa.string()), "")
    email = pc.binary_join_element_wise(local, domain, "@")
    full_name = pc.binary_join_element_wise(first, last, " ")

    state_names = list(ref.STATES)
    state_idx = rng.choice(len(state_names), n, p=_normalize(list(ref.STATES.values())))

    # Signup anywhere from two years before the window until a month before its end.
    lo = _epoch_day(cfg.start - timedelta(days=730)) * US_PER_DAY
    hi = _epoch_day(cfg.end - timedelta(days=30)) * US_PER_DAY
    signup_us = rng.integers(lo, hi, n, dtype=np.int64)

    # Heavy-tailed propensity to buy: most customers order rarely, a few order a lot.
    weights = _normalize(rng.gamma(0.6, 1.0, n))

    table = pa.table(
        {
            "customer_id": ids,
            "full_name": full_name,
            "email": email,
            "state": pa.array(state_names).take(pa.array(state_idx)),
            "signup_at": pa.array(signup_us, TS),
        }
    )
    return Customers(table, state_idx, signup_us, weights)


def products(cfg: GenConfig) -> Products:
    rng = _rng(cfg, _STREAM_PRODUCTS)
    n = cfg.n_products
    ids = np.arange(1, n + 1, dtype=np.int64)
    cats = list(ref.CATEGORIES)
    cat_idx = rng.choice(len(cats), n, p=_normalize([v[2] for v in ref.CATEGORIES.values()]))
    median = np.array([v[0] for v in ref.CATEGORIES.values()])[cat_idx]
    sigma = np.array([v[1] for v in ref.CATEGORIES.values()])[cat_idx]
    price_brl = median * np.exp(rng.normal(0.0, sigma))
    # Retail-style prices ending in ,90.
    price_cents = (np.maximum(np.floor(price_brl), 4) * 100 + 90).astype(np.int64)
    cost_cents = np.round(price_cents * rng.uniform(0.45, 0.75, n)).astype(np.int64)
    brand = pa.array(ref.BRANDS).take(pa.array(rng.integers(0, len(ref.BRANDS), n)))
    category = pa.array(cats).take(pa.array(cat_idx))
    id_str = pc.utf8_lpad(pc.cast(pa.array(ids), pa.string()), 7, "0")
    sku = pc.binary_join_element_wise(pa.scalar("SKU"), id_str, "-")
    name = pc.binary_join_element_wise(brand, pc.utf8_capitalize(category), id_str, " ")

    table = pa.table(
        {
            "product_id": ids,
            "sku": sku,
            "name": name,
            "category": category,
            "brand": brand,
            "price_cents": price_cents,
            "cost_cents": cost_cents,
            "active": rng.random(n) > 0.05,
        }
    )
    # Popularity is heavy tailed and leans toward cheaper items.
    weights = _normalize(rng.lognormal(0.0, 1.5, n) / np.sqrt(price_cents))
    return Products(table, price_cents, weights)


@dataclass
class OrderChunk:
    index: int
    orders: pa.Table
    items: pa.Table
    payments: pa.Table


def order_chunks(cfg: GenConfig, cust: Customers, prod: Products) -> Iterator[OrderChunk]:
    days, dw = day_weights(cfg)
    cdf = np.cumsum(_normalize(dw))
    # Customers sorted by signup: the ones eligible for an order placed at time t
    # are a prefix of this order, so sampling "a customer who already exists,
    # proportional to propensity" is a searchsorted on cumulative weights.
    by_signup = np.argsort(cust.signup_us, kind="stable")
    sorted_signup = cust.signup_us[by_signup]
    cum_weight = np.cumsum(cust.weights[by_signup])
    hour_p = _normalize(ref.HOUR_WEIGHTS)
    region_fee = np.array(
        [ref.SHIPPING_CENTS_BY_REGION[ref.REGION_OF_STATE[s]] for s in ref.STATES],
        dtype=np.int64,
    )
    end_us = (days[-1] + 1) * US_PER_DAY + LOCAL_TO_UTC_US - 1
    recent_cutoff_us = (days[-1] - 14) * US_PER_DAY

    item_offset = 0
    total = cfg.n_orders
    for chunk, start in enumerate(range(0, total, cfg.chunk_size)):
        n = min(cfg.chunk_size, total - start)
        rng = _rng(cfg, _STREAM_ORDERS, chunk)
        order_id = np.arange(start + 1, start + n + 1, dtype=np.int64)

        # Order time follows the demand curve; the customer is drawn among those
        # who signed up at least 5 minutes earlier.
        day = days[np.minimum(np.searchsorted(cdf, rng.random(n)), len(days) - 1)]
        local_us = rng.choice(24, n, p=hour_p) * 3_600 * US_PER_SEC + rng.integers(
            0, 3_600 * US_PER_SEC, n
        )
        ts = day * US_PER_DAY + local_us + LOCAL_TO_UTC_US
        eligible = np.searchsorted(sorted_signup, ts - 300 * US_PER_SEC, side="right")
        eligible = np.maximum(eligible, 1)
        pick = np.searchsorted(cum_weight, rng.random(n) * cum_weight[eligible - 1], side="right")
        cust_idx = by_signup[np.minimum(pick, eligible - 1)]
        # Only reachable when nobody had signed up yet (tiny scales): shift the order.
        ts = np.minimum(np.maximum(ts, cust.signup_us[cust_idx] + 300 * US_PER_SEC), end_us)

        channel = rng.choice(len(ref.CHANNELS), n, p=_normalize(ref.CHANNEL_WEIGHTS))
        recent = ts >= recent_cutoff_us
        settled = rng.choice(
            np.array(["delivered", "canceled", "returned"]), n, p=[0.90, 0.06, 0.04]
        )
        in_flight = rng.choice(
            np.array(["pending", "shipped", "delivered", "canceled"]),
            n,
            p=[0.15, 0.45, 0.35, 0.05],
        )
        status = np.where(recent, in_flight, settled)

        n_items = np.minimum(1 + rng.poisson(0.7, n), 12)
        m = int(n_items.sum())
        item_order = np.repeat(np.arange(n), n_items)
        product_idx = rng.choice(len(prod.weights), m, p=prod.weights)
        qty = np.minimum(rng.geometric(0.75, m), 6).astype(np.int64)
        unit = prod.price_cents[product_idx]
        line = qty * unit

        subtotal = np.bincount(item_order, weights=line, minlength=n).astype(np.int64)
        has_discount = rng.random(n) < 0.2
        discount = np.where(
            has_discount, np.round(subtotal * rng.uniform(0.05, 0.20, n)), 0
        ).astype(np.int64)
        fee = region_fee[cust.state_idx[cust_idx]]
        shipping = np.where(subtotal >= ref.FREE_SHIPPING_THRESHOLD_CENTS, 0, fee)
        total_cents = subtotal - discount + shipping

        delivered = (status == "delivered") | (status == "returned")
        lead_days = rng.integers(2, 16, n)
        delivered_at = np.where(delivered, ts + lead_days * US_PER_DAY, 0)

        orders = pa.table(
            {
                "order_id": order_id,
                "customer_id": cust_idx.astype(np.int64) + 1,
                "ordered_at": pa.array(ts, TS),
                "status": status,
                "channel": pa.array(ref.CHANNELS).take(pa.array(channel)),
                "subtotal_cents": subtotal,
                "discount_cents": discount,
                "shipping_cents": shipping.astype(np.int64),
                "total_cents": total_cents,
                "delivered_at": pa.array(delivered_at, TS, mask=~delivered),
            }
        )
        items = pa.table(
            {
                "order_item_id": np.arange(item_offset + 1, item_offset + m + 1, dtype=np.int64),
                "order_id": order_id[item_order],
                "product_id": product_idx.astype(np.int64) + 1,
                "quantity": qty,
                "unit_price_cents": unit,
                "line_total_cents": line,
            }
        )
        item_offset += m
        payments = _payments(rng, order_id, ts, status, total_cents)
        yield OrderChunk(chunk, orders, items, payments)


def _payments(
    rng: np.random.Generator,
    order_id: np.ndarray,
    ts: np.ndarray,
    status: np.ndarray,
    amount: np.ndarray,
) -> pa.Table:
    n = len(order_id)
    method_idx = rng.choice(len(ref.PAYMENT_METHODS), n, p=_normalize(ref.PAYMENT_METHOD_WEIGHTS))
    method = np.asarray(ref.PAYMENT_METHODS)[method_idx]
    installments = np.where(
        method == "credit_card",
        rng.choice(np.arange(1, 13), n, p=_normalize([40, 8, 12, 6, 5, 8, 2, 2, 2, 8, 1, 6])),
        1,
    ).astype(np.int8)

    refused = rng.random(n) < 0.6
    pay_status = np.select(
        [
            status == "pending",
            (status == "canceled") & refused,
            (status == "canceled") | (status == "returned"),
        ],
        ["pending", "refused", "refunded"],
        default="approved",
    )
    delay_s = np.select(
        [method == "pix", method == "boleto"],
        [rng.integers(5, 120, n), rng.integers(6 * 3600, 3 * 86400, n)],
        default=rng.integers(2, 30, n),
    )
    paid = (pay_status == "approved") | (pay_status == "refunded")
    return pa.table(
        {
            "payment_id": order_id.copy(),
            "order_id": order_id,
            "method": method,
            "installments": installments,
            "status": pay_status,
            "amount_cents": amount,
            "paid_at": pa.array(np.where(paid, ts + delay_s * US_PER_SEC, 0), TS, mask=~paid),
        }
    )
