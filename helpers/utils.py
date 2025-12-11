# helpers/utils.py — Working version with absolute gaps
import math
import random
from typing import List, Dict, Optional
from adapters.base import BaseAdapter


def quantize_down(x: float, step: float) -> float:
    """Round down to nearest step"""
    if step <= 0:
        return x
    return math.floor((x + 1e-15) / step) * step


def quantize_up(x: float, step: float) -> float:
    """Round up to nearest step"""
    if step <= 0:
        return x
    return math.ceil((x - 1e-15) / step) * step


def clamp_by_limits(amount: float, price: float, limits: Dict[str, Optional[float]]) -> Optional[float]:
    """Ensure order meets exchange minimum amount and notional requirements"""
    min_amt = limits.get("min_amount") or 0.0
    min_cost = limits.get("min_cost") or 0.0

    if amount < min_amt:
        amount = min_amt

    if min_cost > 0 and (price * amount) < min_cost:
        if price > 0:
            amount = max(amount, math.ceil(min_cost / price * 100_000_000) / 100_000_000)

    return amount if amount > 0 else None


def build_ladder(mid: float, side: str, depth: int, gap_min: float, gap_max: float) -> List[float]:
    """
    Build price ladder with ABSOLUTE random gaps (not percentages).

    Client requirement: Gap between orders is absolute OHO units (e.g., 0.000001 to 0.000002)
    - Buy side: Subtract gaps from mid (prices go DOWN)
    - Sell side: Add gaps to mid (prices go UP)
    """
    levels: List[float] = []
    acc = 0.0

    for _ in range(depth):
        # Random absolute gap for this level (e.g., 0.000001 to 0.000002 OHO)
        step = random.uniform(gap_min, gap_max)
        acc += step

        # Apply accumulated distance from mid-price
        if side == "buy":
            levels.append(mid - acc)  # Subtract: go DOWN from mid
        else:
            levels.append(mid + acc)  # Add: go UP from mid

    return levels


def random_sizes(depth: int, size_min: float, size_max: float) -> List[float]:
    """Generate random order sizes (e.g., 10,000 to 25,000 OHO)"""
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
    Ensure order meets minimum notional requirement with safety cushion.
    Increases quantity if needed to meet exchange minimum cost.
    """
    min_cost = float(limits.get("min_cost") or 5.0)
    target = min_cost * (1.0 + cushion)
    notional = px * qty

    if notional >= target:
        return adapter.amount_to_precision(qty)

    # Calculate how much more quantity is needed
    need_base = (target - notional) / max(px, 1e-12)
    steps_needed = math.ceil(need_base / max(amount_step, 1e-18))
    qty_up = qty + steps_needed * amount_step

    # Round up to nearest step
    k = math.ceil((qty_up - 1e-15) / amount_step)
    qty_up = k * amount_step
    qty_up = adapter.amount_to_precision(qty_up)

    # Final safety check
    if px * qty_up < target:
        qty_up = adapter.amount_to_precision(qty_up + amount_step)

    return qty_up