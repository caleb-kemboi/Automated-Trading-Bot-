# helpers/utils.py
"""
Exact replica of your original helper functions — cleaned, documented, and fully compatible
with the modular bot structure. No logic changed — only modernized for clarity and safety.
"""

import math
import random
from typing import List, Dict, Optional

from adapters.base import BaseAdapter


def quantize_down(x: float, step: float) -> float:
    """Floor to nearest step (used for price guard)."""
    if step <= 0:
        return x
    return math.floor((x + 1e-15) / step) * step


def quantize_up(x: float, step: float) -> float:
    """Ceil to nearest step (used for price guard)."""
    if step <= 0:
        return x
    return math.ceil((x - 1e-15) / step) * step


def clamp_by_limits(
    amount: float,
    price: float,
    limits: Dict[str, Optional[float]]
) -> Optional[float]:
    """
    Ensure order meets exchange min_amount and min_cost.
    Returns None if impossible.
    """
    min_amt = limits.get("min_amount") or 0.0
    min_cost = limits.get("min_cost") or 0.0

    if amount < min_amt:
        amount = min_amt

    if min_cost > 0 and (price * amount) < min_cost:
        if price > 0:
            amount = max(amount, math.ceil(min_cost / price * 100_000_000) / 100_000_000)  # avoid float error

    return amount if amount > 0 else None


def build_ladder(
    mid: float,
    side: str,
    depth: int,
    gap: float
) -> List[float]:
    """
    Build price ladder with FIXED gap (your current requirement).
    Previously used gap_min/gap_max → now fixed gap = 0.000001
    """
    levels: List[float] = []
    offset = 0.0
    for _ in range(depth):
        offset += gap
        level = mid - offset if side == "buy" else mid + offset
        levels.append(level)
    return levels


def random_sizes(
    depth: int,
    size_min: float,
    size_max: float
) -> List[float]:
    """Generate random order sizes in given range."""
    return [random.uniform(size_min, size_max) for _ in range(depth)]


def ensure_min_notional(
    px: float,
    qty: float,
    limits: Dict[str, Optional[float]],
    amount_step: float,
    adapter: BaseAdapter,
    cushion: float = 0.01
) -> float:
    """
    Bump quantity up if needed to satisfy min notional value.
    This is your original bulletproof logic — unchanged.
    """
    min_cost = float(limits.get("min_cost") or 5.0)
    target = min_cost * (1.0 + cushion)
    notional = px * qty

    if notional >= target:
        return adapter.amount_to_precision(qty)

    # How much more base currency do we need?
    need_base = (target - notional) / max(px, 1e-12)
    steps_needed = math.ceil(need_base / max(amount_step, 1e-18))
    qty_up = qty + steps_needed * amount_step

    # Re-align to step grid
    k = math.ceil((qty_up - 1e-15) / amount_step)
    qty_up = k * amount_step
    qty_up = adapter.amount_to_precision(qty_up)

    # Final safety check
    if px * qty_up < target:
        qty_up = adapter.amount_to_precision(qty_up + amount_step)

    return qty_up