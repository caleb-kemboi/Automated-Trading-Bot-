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
    """
    Execute one market-making cycle.

    Client Requirements:
    1. Reference price = BTC/USDT * multiplier (exact middle between buy/sell)
    2. Random gaps between orders (absolute OHO units, e.g., 0.000001-0.000002)
    3. Random depth (e.g., 15-20 orders per side)
    4. Random sizes (e.g., 10,000-25,000 OHO per order)
    5. CRITICAL: Never place sell orders below OR buy orders above reference price

    Returns:
        Set of new order IDs created this cycle
    """
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

    # Random depth per client requirement (e.g., 15-20 orders each side)
    depth = random.randint(SETTINGS.depth_min, SETTINGS.depth_max)

    # Build ladders with ABSOLUTE gaps (e.g., 0.000001 to 0.000002 OHO)
    buy_prices = build_ladder(mid_price, "buy", depth, SETTINGS.gap_min, SETTINGS.gap_max)
    sell_prices = build_ladder(mid_price, "sell", depth, SETTINGS.gap_min, SETTINGS.gap_max)

    # Random sizes per client requirement (e.g., 10,000 to 25,000 OHO)
    sizes_buy = random_sizes(depth, SETTINGS.size_min, SETTINGS.size_max)
    sizes_sell = random_sizes(depth, SETTINGS.size_min, SETTINGS.size_max)

    new_order_ids: Set[str] = set()
    attempted = 0
    rejected = 0

    # ==================== BUY SIDE ====================
    # STRICT RULE: All buy orders MUST be BELOW reference price
    for i, (raw_price, raw_qty) in enumerate(zip(buy_prices, sizes_buy)):
        # Manual precision for very low prices (prevents CCXT input snap)
        if raw_price < 0.01:
            adjusted_price = round(raw_price, 8)
            logger.debug(f"{adapter.exchange_name} BUY[{i}] manual prec: {raw_price:.12f} -> {adjusted_price:.12f}")
        else:
            adjusted_price = adapter.price_to_precision(raw_price)

        # CRITICAL CHECK 1: Reject any buy order >= reference price
        if adjusted_price >= mid_price:
            logger.warning(
                f"{adapter.exchange_name} BUY[{i}] REJECTED: "
                f"price {adjusted_price:.12f} >= reference {mid_price:.12f}"
            )
            rejected += 1
            continue

        # Top-of-book maker guard (prevent immediate fill against best ask)
        if best_ask is not None and SETTINGS.maker_guard_ticks > 0:
            allowed_max = best_ask - SETTINGS.maker_guard_ticks * tick
            if adjusted_price >= allowed_max:
                adjusted_price = quantize_down(allowed_max, tick)
                logger.debug(f"{adapter.exchange_name} BUY[{i}] maker guard: -> {adjusted_price:.12f}")

        # Ensure price meets minimum tick
        adjusted_price = max(adjusted_price, tick)

        # CRITICAL CHECK 2: Final safety - double-check still below reference
        if adjusted_price >= mid_price:
            logger.warning(
                f"{adapter.exchange_name} BUY[{i}] REJECTED after adjustments: "
                f"price {adjusted_price:.12f} >= reference {mid_price:.12f}"
            )
            rejected += 1
            continue

        # Validate quantity meets exchange limits
        qty = clamp_by_limits(raw_qty, adjusted_price, limits)
        if qty is None:
            logger.debug(f"{adapter.exchange_name} BUY[{i}] rejected: qty below limits")
            continue

        # Ensure minimum notional value
        qty = ensure_min_notional(adjusted_price, qty, limits, amount_step, adapter)

        # Place order
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
    # STRICT RULE: All sell orders MUST be ABOVE reference price
    for i, (raw_price, raw_qty) in enumerate(zip(sell_prices, sizes_sell)):
        # Manual precision for very low prices
        if raw_price < 0.01:
            adjusted_price = round(raw_price, 8)
            logger.debug(f"{adapter.exchange_name} SELL[{i}] manual prec: {raw_price:.12f} -> {adjusted_price:.12f}")
        else:
            adjusted_price = adapter.price_to_precision(raw_price)

        # CRITICAL CHECK 1: Reject any sell order <= reference price
        if adjusted_price <= mid_price:
            logger.warning(
                f"{adapter.exchange_name} SELL[{i}] REJECTED: "
                f"price {adjusted_price:.12f} <= reference {mid_price:.12f}"
            )
            rejected += 1
            continue

        # Top-of-book maker guard (prevent immediate fill against best bid)
        if best_bid is not None and SETTINGS.maker_guard_ticks > 0:
            allowed_min = best_bid + SETTINGS.maker_guard_ticks * tick
            if adjusted_price <= allowed_min:
                adjusted_price = quantize_up(allowed_min, tick)
                logger.debug(f"{adapter.exchange_name} SELL[{i}] maker guard: -> {adjusted_price:.12f}")

        # Ensure price meets minimum tick
        adjusted_price = max(adjusted_price, tick)

        # CRITICAL CHECK 2: Final safety - double-check still above reference
        if adjusted_price <= mid_price:
            logger.warning(
                f"{adapter.exchange_name} SELL[{i}] REJECTED after adjustments: "
                f"price {adjusted_price:.12f} <= reference {mid_price:.12f}"
            )
            rejected += 1
            continue

        # Validate quantity meets exchange limits
        qty = clamp_by_limits(raw_qty, adjusted_price, limits)
        if qty is None:
            logger.debug(f"{adapter.exchange_name} SELL[{i}] rejected: qty below limits")
            continue

        # Ensure minimum notional value
        qty = ensure_min_notional(adjusted_price, qty, limits, amount_step, adapter)

        # Place order
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
    # Seamlessly replace old orders with new ones (per client requirement #5)
    try:
        if not adapter.dry_run:
            open_orders = adapter.fetch_open_orders()
            current_ids = {str(o.get("id")) for o in open_orders if o.get("id")}
            to_cancel = current_ids - new_order_ids
            if to_cancel:
                logger.debug(f"{adapter.exchange_name} cancelling {len(to_cancel)} old orders")
                adapter.cancel_orders_by_ids(list(to_cancel))
    except Exception as e:
        logger.warning(f"{adapter.exchange_name} cleanup error: {e}")

    # ==================== STATUS REPORT ====================
    status = "live" if rejected == 0 else f"live ({rejected}/{attempted} rejected)"
    logger.info(
        f"{adapter.exchange_name.upper():<9} | BTC={btc_price:,.0f} | "
        f"ref={mid_price:.12f} | depth={depth} | placed={len(new_order_ids)} | {status}"
    )

    return new_order_ids