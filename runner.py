# runner.py — CLEANUP MOVED TO ADAPTER (UNIFIED)

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

    # =====================================================
    # CRITICAL: Reset per-cycle order tracking (SMART CANCEL)
    # =====================================================
    if hasattr(adapter, "current_cycle_order_ids"):
        adapter.current_cycle_order_ids.clear()

    # ---------------- Fetch BTC price ----------------
    try:
        btc_price = adapter.fetch_btc_last()
    except Exception as e:
        logger.warning(f"{adapter.exchange_name} BTC fetch failed: {e}, using fallback")
        btc_price = 92_000.0

    # ---------------- Reference price ----------------
    mid_price = btc_price * SETTINGS.reference_multiplier
    if mid_price <= 0:
        logger.warning(f"{adapter.exchange_name} mid_price invalid ({mid_price:.12f}), skipping cycle")
        return prev_cycle_ids

    # ---------------- Exchange info ----------------
    limits = adapter.get_limits()
    price_step, amount_step = adapter.get_steps()
    tick = max(price_step, 1e-10)
    best_bid, best_ask = adapter.fetch_best_quotes() or (None, None)

    # ---------------- Ladder params ----------------
    depth = random.randint(SETTINGS.depth_min, SETTINGS.depth_max)

    buy_prices = build_ladder(mid_price, "buy", depth, SETTINGS.gap_min, SETTINGS.gap_max)
    sell_prices = build_ladder(mid_price, "sell", depth, SETTINGS.gap_min, SETTINGS.gap_max)

    sizes_buy = random_sizes(depth, SETTINGS.size_min, SETTINGS.size_max)
    sizes_sell = random_sizes(depth, SETTINGS.size_min, SETTINGS.size_max)

    new_order_ids: Set[str] = set()
    attempted = 0
    rejected = 0

    # ==================== BUY SIDE ====================
    for i, (raw_price, raw_qty) in enumerate(zip(buy_prices, sizes_buy)):
        adjusted_price = round(raw_price, 8) if raw_price < 0.01 else adapter.price_to_precision(raw_price)

        # Never buy above reference
        if adjusted_price >= mid_price:
            rejected += 1
            continue

        # Maker guard
        if best_ask is not None and SETTINGS.maker_guard_ticks > 0:
            allowed_max = best_ask - SETTINGS.maker_guard_ticks * tick
            if adjusted_price >= allowed_max:
                adjusted_price = quantize_down(allowed_max, tick)

        adjusted_price = max(adjusted_price, tick)

        if adjusted_price >= mid_price:
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
            logger.warning(f"{adapter.exchange_name} BUY[{i}] failed: {e}")
            rejected += 1

    # ==================== SELL SIDE ====================
    for i, (raw_price, raw_qty) in enumerate(zip(sell_prices, sizes_sell)):
        adjusted_price = round(raw_price, 8) if raw_price < 0.01 else adapter.price_to_precision(raw_price)

        # Never sell below reference
        if adjusted_price <= mid_price:
            rejected += 1
            continue

        if best_bid is not None and SETTINGS.maker_guard_ticks > 0:
            allowed_min = best_bid + SETTINGS.maker_guard_ticks * tick
            if adjusted_price <= allowed_min:
                adjusted_price = quantize_up(allowed_min, tick)

        adjusted_price = max(adjusted_price, tick)

        if adjusted_price <= mid_price:
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
            logger.warning(f"{adapter.exchange_name} SELL[{i}] failed: {e}")
            rejected += 1

    # ==================== CLEANUP (ADAPTER-OWNED) ====================
    try:
        if not adapter.dry_run:
            adapter.cancel_all_orders()
            logger.info(f"{adapter.exchange_name} full cleanup complete")
    except Exception as e:
        logger.warning(f"{adapter.exchange_name} cleanup error: {e}")

    # ==================== STATUS ====================
    status = "live" if rejected == 0 else f"live ({rejected}/{attempted} rejected)"
    logger.info(
        f"{adapter.exchange_name.upper():<9} | BTC={btc_price:,.0f} | "
        f"ref={mid_price:.12f} | depth={depth} | placed={len(new_order_ids)} | {status}"
    )

    return new_order_ids
