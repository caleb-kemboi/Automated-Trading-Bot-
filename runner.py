# runner.py
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

    # === 1. Get fresh market data ===
    try:
        btc_price = adapter.fetch_btc_last()
    except Exception as e:
        logger.error(f"{adapter.exchange_name} | failed to fetch BTC price: {e}")
        return prev_cycle_ids  # skip cycle

    # CRITICAL FIX: Clamp mid_price to sane range
    mid_price = btc_price * SETTINGS.reference_multiplier
    if mid_price <= 0:
        logger.error(f"{adapter.exchange_name} | invalid mid_price {mid_price} — skipping cycle")
        return prev_cycle_ids

    limits = adapter.get_limits()
    best_bid, best_ask = adapter.fetch_best_quotes()  # may be None
    price_step, amount_step = adapter.get_steps()
    tick = max(price_step, 1e-10)  # avoid division by zero

    # === 2. Random depth this cycle ===
    depth = random.randint(SETTINGS.depth_min, SETTINGS.depth_max)

    # === 3. Build ladders with FIXED gap ===
    buy_prices = build_ladder(mid_price, "buy", depth, SETTINGS.gap)
    sell_prices = build_ladder(mid_price, "sell", depth, SETTINGS.gap)

    sizes_buy = random_sizes(depth, SETTINGS.size_min, SETTINGS.size_max)
    sizes_sell = random_sizes(depth, SETTINGS.size_min, SETTINGS.size_max)

    # === 4. Min spread guard: 5 bps away from mid ===
    min_dist = mid_price * (SETTINGS.min_spread_bps / 10_000.0)
    if buy_prices and (mid_price - buy_prices[0]) < min_dist:
        shift = min_dist - (mid_price - buy_prices[0])
        buy_prices = [p - shift for p in buy_prices]
    if sell_prices and (sell_prices[0] - mid_price) < min_dist:
        shift = min_dist - (sell_prices[0] - mid_price)
        sell_prices = [p + shift for p in sell_prices]

    # === 5. FINAL PRICE SANITY GUARD (this fixes negative prices) ===
    def sanitize_price(px: float, side: str) -> float:
        px = adapter.price_to_precision(px)
        if side == "buy":
            # Never go below 1 tick or negative
            return max(px, tick * 10)
        else:
            # Never go below mid + 1 tick
            return max(px, mid_price + tick)

    # === 6. Place new orders FIRST (maintain liquidity) ===
    new_order_ids: Set[str] = set()

    # BUY SIDE
    for raw_price, raw_qty in zip(buy_prices, sizes_buy):
        price = sanitize_price(raw_price, "buy")
        qty = clamp_by_limits(raw_qty, price, limits)
        if qty is None:
            continue
        qty = ensure_min_notional(price, qty, limits, amount_step, adapter)
        qty = min(qty, SETTINGS.size_max * 2)  # extra safety cap

        oid = adapter.create_limit("buy", price, qty)
        if oid:
            new_order_ids.add(oid)

    # SELL SIDE
    for raw_price, raw_qty in zip(sell_prices, sizes_sell):
        price = sanitize_price(raw_price, "sell")
        qty = clamp_by_limits(raw_qty, price, limits)
        if qty is None:
            continue
        qty = ensure_min_notional(price, qty, limits, amount_step, adapter)
        qty = min(qty, SETTINGS.size_max * 2)

        oid = adapter.create_limit("sell", price, qty)
        if oid:
            new_order_ids.add(oid)

    # === 7. Cancel stale orders (seamless cleanup) ===
    try:
        open_orders = adapter.fetch_open_orders()
        current_ids = {str(o.get("id")) for o in open_orders if o.get("id")}
        to_cancel = current_ids - new_order_ids

        if to_cancel:
            adapter.cancel_orders_by_ids(to_cancel)
            logger.info(f"{adapter.exchange_name} | removed {len(to_cancel)} stale | kept {len(new_order_ids)}")
    except Exception as e:
        # In dry_run mode, fetch_open_orders should return empty list, so this shouldn't happen
        # But if it does, log it as debug since it's expected behavior
        if adapter.dry_run:
            logger.debug(f"{adapter.exchange_name} | cleanup skipped (dry_run mode)")
        else:
            logger.warning(f"{adapter.exchange_name} | cleanup error: {e}")

    # === 8. Final log ===
    logger.info(
        f"{adapter.exchange_name} | BTC={btc_price:,.2f} | mid={mid_price:.10f} | "
        f"depth={depth} | orders={len(new_order_ids)*2} | dry_run={adapter.dry_run}"
    )

    return new_order_ids
