# runner.py — FINAL FIX: Manual precision BEFORE CCXT to prevent input snap
import random
import logging
from typing import Set, Optional
import math  # For manual round

from config import SETTINGS
from helpers.utils import (
    build_ladder, random_sizes, clamp_by_limits, ensure_min_notional,
    quantize_down, quantize_up
)
from adapters.base import BaseAdapter

logger = logging.getLogger("oho_bot")

def run_once(adapter: BaseAdapter, prev_cycle_ids: Optional[Set[str]] = None) -> Set[str]:
    if prev_cycle_ids is None:
        prev_cycle_ids = set()

    # Fetch BTC price with fallback
    try:
        btc_price = adapter.fetch_btc_last()
    except Exception:
        btc_price = 92_000.0

    mid_price = btc_price * SETTINGS.reference_multiplier
    if mid_price <= 0:
        logger.warning("%s mid_price invalid (%f), skipping cycle", adapter.exchange_name, mid_price)
        return prev_cycle_ids

    limits = adapter.get_limits()
    price_step, amount_step = adapter.get_steps()
    tick = max(price_step, 1e-10)
    best_bid, best_ask = adapter.fetch_best_quotes() or (None, None)

    # Random depth per client requirement
    depth = random.randint(SETTINGS.depth_min, SETTINGS.depth_max)

    # Build ladders with random gaps
    buy_prices = build_ladder(mid_price, "buy", depth, SETTINGS.gap_min, SETTINGS.gap_max)
    sell_prices = build_ladder(mid_price, "sell", depth, SETTINGS.gap_min, SETTINGS.gap_max)

    sizes_buy = random_sizes(depth, SETTINGS.size_min, SETTINGS.size_max)
    sizes_sell = random_sizes(depth, SETTINGS.size_min, SETTINGS.size_max)

    # Min spread guard (distance from mid)
    min_dist = mid_price * (SETTINGS.min_spread_bps / 10_000.0)
    if buy_prices and (mid_price - buy_prices[0]) < min_dist:
        shift = min_dist - (mid_price - buy_prices[0])
        buy_prices = [p - shift for p in buy_prices]
        logger.debug(f"{adapter.exchange_name} buy shift: {shift:.10f}")
    if sell_prices and (sell_prices[0] - mid_price) < min_dist:
        shift = min_dist - (sell_prices[0] - mid_price)
        sell_prices = [p + shift for p in sell_prices]
        logger.debug(f"{adapter.exchange_name} sell shift: {shift:.10f}")

    new_order_ids: Set[str] = set()
    attempted = rejected = 0

    # BUY SIDE
    for i, (raw_price, raw_qty) in enumerate(zip(buy_prices, sizes_buy)):
        # FINAL FIX: Manual round BEFORE adapter for low prices — prevents CCXT input snap
        if raw_price < 0.01:
            adjusted_price = round(raw_price, 8)  # Direct: ~0.000946 -> 0.00094600
            logger.info(f"{adapter.exchange_name} BUY[{i}] MANUAL PREC: raw={raw_price:.12f} -> {adjusted_price:.12f}")
        else:
            adjusted_price = adapter.price_to_precision(raw_price)

        # HARD CLAMP: Never place buy above reference price (client critical requirement)
        adjusted_price = min(adjusted_price, mid_price * 0.9999)

        # Top-of-book maker guard
        if best_ask is not None:
            allowed_max = best_ask - SETTINGS.maker_guard_ticks * tick
            adjusted_price = min(adjusted_price, quantize_down(allowed_max, tick))

        # Final minimum tick alignment
        adjusted_price = max(adjusted_price, tick)

        qty = clamp_by_limits(raw_qty, adjusted_price, limits)
        if qty is None:
            continue
        qty = ensure_min_notional(adjusted_price, qty, limits, amount_step, adapter)

        attempted += 1
        oid = adapter.create_limit("buy", adjusted_price, qty)
        if oid and oid != "dry":
            new_order_ids.add(oid)
        elif oid != "dry":
            rejected += 1

    # SELL SIDE (same manual prec fix)
    for i, (raw_price, raw_qty) in enumerate(zip(sell_prices, sizes_sell)):
        if raw_price < 0.01:
            adjusted_price = round(raw_price, 8)
            logger.info(f"{adapter.exchange_name} SELL[{i}] MANUAL PREC: raw={raw_price:.12f} -> {adjusted_price:.12f}")
        else:
            adjusted_price = adapter.price_to_precision(raw_price)

        # HARD CLAMP: Never place sell below reference price (client critical requirement)
        adjusted_price = max(adjusted_price, mid_price * 1.0001)

        # Top-of-book maker guard
        if best_bid is not None:
            allowed_min = best_bid + SETTINGS.maker_guard_ticks * tick
            adjusted_price = max(adjusted_price, quantize_up(allowed_min, tick))

        # Final minimum tick alignment
        adjusted_price = max(adjusted_price, tick)

        qty = clamp_by_limits(raw_qty, adjusted_price, limits)
        if qty is None:
            continue
        qty = ensure_min_notional(adjusted_price, qty, limits, amount_step, adapter)

        attempted += 1
        oid = adapter.create_limit("sell", adjusted_price, qty)
        if oid and oid != "dry":
            new_order_ids.add(oid)
        elif oid != "dry":
            rejected += 1

    # Cleanup stale orders (seamless, no exposure gap)
    try:
        if not adapter.dry_run:
            open_orders = adapter.fetch_open_orders()
            current_ids = {str(o.get("id")) for o in open_orders if o.get("id")}
            to_cancel = current_ids - new_order_ids
            if to_cancel:
                adapter.cancel_orders_by_ids(to_cancel)
    except Exception as e:
        logger.warning("%s cleanup error: %s", adapter.exchange_name, e)

    status = "live" if rejected == 0 else f"live ({rejected}/{attempted} rejected)"
    logger.info(
        f"{adapter.exchange_name.upper():<9} | BTC={btc_price:,.0f} | mid={mid_price:.12f} | "
        f"depth={depth} | placed={len(new_order_ids)} | {status}"
    )

    return new_order_ids