# runner.py  –  DIAGNOSTIC VERSION
import random
import logging
from typing import Set, Optional

from config import SETTINGS
from helpers.utils import (
    build_ladder,
    random_sizes,
    clamp_by_limits,
    ensure_min_notional,
)
from adapters.base import BaseAdapter

logger = logging.getLogger("oho_bot")


def run_once(adapter: BaseAdapter, prev_cycle_ids: Optional[Set[str]] = None) -> Set[str]:
    if prev_cycle_ids is None:
        prev_cycle_ids = set()

    # === 1. Get BTC price (silent fallback) ===
    try:
        btc_price = adapter.fetch_btc_last()
    except Exception:
        btc_price = 92_000.0

    mid_price = btc_price * SETTINGS.reference_multiplier
    if mid_price <= 0:
        return prev_cycle_ids

    limits = adapter.get_limits()
    price_step, amount_step = adapter.get_steps()
    tick = max(price_step, 1e-10)

    # === 2. Build ladder ===
    depth = random.randint(SETTINGS.depth_min, SETTINGS.depth_max)
    buy_prices = build_ladder(mid_price, "buy", depth, SETTINGS.gap)
    sell_prices = build_ladder(mid_price, "sell", depth, SETTINGS.gap)

    sizes_buy = random_sizes(depth, SETTINGS.size_min, SETTINGS.size_max)
    sizes_sell = random_sizes(depth, SETTINGS.size_min, SETTINGS.size_max)

    # === 3. Min spread guard (silent) ===
    min_dist = mid_price * (SETTINGS.min_spread_bps / 10_000.0)
    if buy_prices and (mid_price - buy_prices[0]) < min_dist:
        shift = min_dist - (mid_price - buy_prices[0])
        buy_prices = [p - shift for p in buy_prices]
    if sell_prices and (sell_prices[0] - mid_price) < min_dist:
        shift = min_dist - (sell_prices[0] - mid_price)
        sell_prices = [p + shift for p in sell_prices]

    # === 4. Place orders with diagnostic tracking ===
    new_order_ids: Set[str] = set()
    attempted = 0
    rejected = 0

    # BUY SIDE
    for raw_price, raw_qty in zip(buy_prices, sizes_buy):
        price = max(adapter.price_to_precision(raw_price), tick * 10)
        qty = clamp_by_limits(raw_qty, price, limits)
        if qty is None:
            continue
        qty = ensure_min_notional(price, qty, limits, amount_step, adapter)

        attempted += 1
        oid = adapter.create_limit("buy", price, qty)
        if oid and oid != "dry":
            new_order_ids.add(oid)
        elif oid != "dry":
            rejected += 1

    # SELL SIDE
    for raw_price, raw_qty in zip(sell_prices, sizes_sell):
        price = max(adapter.price_to_precision(raw_price), mid_price + tick)
        qty = clamp_by_limits(raw_qty, price, limits)
        if qty is None:
            continue
        qty = ensure_min_notional(price, qty, limits, amount_step, adapter)

        attempted += 1
        oid = adapter.create_limit("sell", price, qty)
        if oid and oid != "dry":
            new_order_ids.add(oid)
        elif oid != "dry":
            rejected += 1

    # === 5. Silent cleanup of stale orders ===
    try:
        if not adapter.dry_run:
            open_orders = adapter.fetch_open_orders()
            current_ids = {str(o.get("id")) for o in open_orders if o.get("id")}
            to_cancel = current_ids - new_order_ids
            if to_cancel:
                adapter.cancel_orders_by_ids(to_cancel)
    except Exception:
        pass

    # === 6. Summary with diagnostic info ===
    status = "live"
    if rejected > 0:
        status = f"live, {rejected}/{attempted} rejected"

    logger.info(
        f"{adapter.exchange_name.upper():<9} | BTC={btc_price:,.0f} | mid={mid_price:.10f} | "
        f"depth={depth} | orders={len(new_order_ids)} | {status}"
    )

    return new_order_ids