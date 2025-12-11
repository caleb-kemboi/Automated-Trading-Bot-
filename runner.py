# runner.py — Complete working version with absolute gaps and strict reference price protection
import random
import logging
from typing import Set, Optional

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
    except Exception as e:
        logger.warning(f"{adapter.exchange_name} BTC fetch failed: {e}, using fallback")
        btc_price = 92_000.0

    # Calculate reference price (middle between buy and sell)
    mid_price = btc_price * SETTINGS.reference_multiplier
    if mid_price <= 0:
        logger.warning(f"{adapter.exchange_name} mid_price invalid ({mid_price:.12f}), skipping cycle")
        return prev_cycle_ids

    # Get exchange info
    limits = adapter.get_limits()
    price_step, amount_step = adapter.get_steps()
    tick = max(price_step, 1e-10)
    best_bid, best_ask = adapter.fetch_best_quotes() or (None, None)

    # Random depth per client requirement
    depth = random.randint(SETTINGS.depth_min, SETTINGS.depth_max)

    # Build ladders with ABSOLUTE gaps
    buy_prices = build_ladder(mid_price, "buy", depth, SETTINGS.gap_min, SETTINGS.gap_max)
    sell_prices = build_ladder(mid_price, "sell", depth, SETTINGS.gap_min, SETTINGS.gap_max)

    # Random sizes per client requirement
    sizes_buy = random_sizes(depth, SETTINGS.size_min, SETTINGS.size_max)
    sizes_sell = random_sizes(depth, SETTINGS.size_min, SETTINGS.size_max)

    new_order_ids: Set[str] = set()
    attempted = 0
    rejected = 0

    # ==================== BUY SIDE ====================
    for i, (raw_price, raw_qty) in enumerate(zip(buy_prices, sizes_buy)):
        # Manual precision for low prices
        if raw_price < 0.01:
            adjusted_price = round(raw_price, 8)
        else:
            adjusted_price = adapter.price_to_precision(raw_price)

        # CRITICAL: Never buy above reference price
        if adjusted_price >= mid_price:
            logger.warning(f"{adapter.exchange_name} BUY[{i}] REJECTED: price >= mid")
            rejected += 1
            continue

        # Maker guard
        if best_ask is not None and SETTINGS.maker_guard_ticks > 0:
            allowed_max = best_ask - SETTINGS.maker_guard_ticks * tick
            if adjusted_price >= allowed_max:
                adjusted_price = quantize_down(allowed_max, tick)

        adjusted_price = max(adjusted_price, tick)

        # Final safety check
        if adjusted_price >= mid_price:
            logger.warning(f"{adapter.exchange_name} BUY[{i}] REJECTED after adjustments")
            rejected += 1
            continue

        qty = clamp_by_limits(raw_qty, adjusted_price, limits)
        if qty is None:
            continue
        qty = ensure_min_notional(adjusted_price, qty, limits, amount_step, adapter)

        attempted += 1
        try:
            oid = adapter.create_limit("buy", adjusted_price, qty)
            if oid and oid != "dry":
                new_order_ids.add(oid)
            elif oid != "dry":
                rejected += 1
        except Exception as e:
            logger.warning(f"{adapter.exchange_name} BUY[{i}] placement failed: {e}")
            rejected += 1

    # ==================== SELL SIDE ====================
    for i, (raw_price, raw_qty) in enumerate(zip(sell_prices, sizes_sell)):
        if raw_price < 0.01:
            adjusted_price = round(raw_price, 8)
        else:
            adjusted_price = adapter.price_to_precision(raw_price)

        # CRITICAL: Never sell below reference price
        if adjusted_price <= mid_price:
            logger.warning(f"{adapter.exchange_name} SELL[{i}] REJECTED: price <= mid")
            rejected += 1
            continue

        if best_bid is not None and SETTINGS.maker_guard_ticks > 0:
            allowed_min = best_bid + SETTINGS.maker_guard_ticks * tick
            if adjusted_price <= allowed_min:
                adjusted_price = quantize_up(allowed_min, tick)

        adjusted_price = max(adjusted_price, tick)

        if adjusted_price <= mid_price:
            logger.warning(f"{adapter.exchange_name} SELL[{i}] REJECTED after adjustments")
            rejected += 1
            continue

        qty = clamp_by_limits(raw_qty, adjusted_price, limits)
        if qty is None:
            continue
        qty = ensure_min_notional(adjusted_price, qty, limits, amount_step, adapter)

        attempted += 1
        try:
            oid = adapter.create_limit("sell", adjusted_price, qty)
            if oid and oid != "dry":
                new_order_ids.add(oid)
            elif oid != "dry":
                rejected += 1
        except Exception as e:
            logger.warning(f"{adapter.exchange_name} SELL[{i}] placement failed: {e}")
            rejected += 1

    # ==================== CLEANUP OLD ORDERS ====================
    try:
        if not adapter.dry_run:
            open_orders = adapter.fetch_open_orders()
            current_ids = set()
            for o in open_orders:
                oid = o.get("id") or o.get("orderId") or o.get("info", {}).get("order_id")
                if oid is not None:
                    current_ids.add(str(oid))

            to_cancel = current_ids - new_order_ids
            if to_cancel:
                logger.info(f"{adapter.exchange_name} cancelling {len(to_cancel)} old orders")
                adapter.cancel_orders_by_ids(list(to_cancel))

            # Emergency cap
            if len(current_ids) > 40:
                logger.warning(f"{adapter.exchange_name} forcing full cancel — {len(current_ids)} orders open!")
                adapter.cancel_all_orders()
    except Exception as e:
        logger.warning(f"{adapter.exchange_name} cleanup error: {e}")

    # ==================== STATUS REPORT ====================
    status = "live" if rejected == 0 else f"live ({rejected}/{attempted} rejected)"
    logger.info(
        f"{adapter.exchange_name.upper():<9} | BTC={btc_price:,.0f} | "
        f"ref={mid_price:.12f} | depth={depth} | placed={len(new_order_ids)} | {status}"
    )

    return new_order_ids