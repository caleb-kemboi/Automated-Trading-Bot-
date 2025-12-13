import os, time, hmac, hashlib, requests, json, logging, math, random
from typing import Optional, List, Dict, Tuple, Set

from helpers.batch_cancel import BatchCancelMixin

logger = logging.getLogger(__name__)


class BitMartAdapter(BatchCancelMixin):
    def __init__(self, cfg):
        self.cfg = cfg
        self.exchange_name = cfg.id
        self.symbol = cfg.symbol
        self.btc_symbol = cfg.btc_symbol
        self.dry_run = cfg.dry_run

        self.key = os.getenv(cfg.api_key_env, "")
        self.secret = os.getenv(cfg.secret_env, "")
        self.memo = os.getenv(cfg.uid_env, "")

        if not all([self.key, self.secret, self.memo]):
            raise ValueError("BitMart credentials incomplete")

        self.session = requests.Session()
        self.session.headers.update({"X-BM-KEY": self.key})

    def _sign_v2(self, timestamp: str, body_str: str = "") -> str:
        """Sign for v2 endpoints."""
        msg = f"{timestamp}#{self.memo}"
        if body_str:
            msg += f"#{body_str}"
        return hmac.new(self.secret.encode(), msg.encode(), hashlib.sha256).hexdigest()

    def _sign_v4(self, timestamp: str, body_str: str = "") -> str:
        """Sign for v4 endpoints."""
        msg = f"{timestamp}#{self.memo}#{body_str}"
        return hmac.new(self.secret.encode(), msg.encode(), hashlib.sha256).hexdigest()

    def _request(self, method: str, endpoint: str, params=None, data=None, version: str = "v2"):
        """Unified request method for BatchCancelMixin compatibility."""
        timestamp = str(int(time.time() * 1000))
        url = f"https://api-cloud.bitmart.com{endpoint}"
        body_str = json.dumps(data, separators=(',', ':')) if data else ""
        signature = self._sign_v4(timestamp, body_str) if version == "v4" else self._sign_v2(timestamp, body_str)

        headers = {
            "X-BM-KEY": self.key,
            "X-BM-TIMESTAMP": timestamp,
            "X-BM-SIGN": signature,
            "Content-Type": "application/json"
        }

        if method.upper() == "GET":
            r = self.session.get(url, headers=headers, params=params, timeout=10)
        else:
            r = self.session.post(url, headers=headers, data=body_str, timeout=10)
        r.raise_for_status()
        return r.json()

    # ---------------- Basic market data ---------------- #
    def connect(self):
        logger.info(f"Connected {self.exchange_name} (BitMart)")

    def fetch_btc_last(self) -> float:
        r = requests.get("https://api-cloud.bitmart.com/spot/quotation/v3/ticker?symbol=BTC_USDT", timeout=10).json()
        return float(r["data"]["last"])

    def fetch_best_quotes(self) -> Tuple[Optional[float], Optional[float]]:
        symbol = self.symbol.replace("/", "_")
        r = requests.get(f"https://api-cloud.bitmart.com/spot/quotation/v3/ticker?symbol={symbol}", timeout=10).json()
        if r.get("code") == 1000:
            d = r["data"]
            return float(d.get("bid_px", 0) or 0), float(d.get("ask_px", 0) or 0)
        return None, None

    def fetch_open_orders(self) -> List[dict]:
        """Fetch open orders with proper status filtering."""
        if self.dry_run:
            return []
        symbol = self.symbol.replace("/", "_")
        try:
            r = self._request("GET", "/spot/v2/orders", params={"symbol": symbol, "orderState": "pending"},
                              version="v2")
            orders = r.get("data", {}).get("orders", [])
            # CRITICAL: Only return orders that are actually open (not filled or cancelled)
            return [
                {"id": str(o["order_id"])}
                for o in orders
                if o.get("order_id") and o.get("status") in ["new", "partially_filled"]
            ]
        except Exception as e:
            logger.warning(f"{self.exchange_name} fetch_open_orders error: {e}")
            return []

    def cancel_orders_by_ids(self, order_ids: List[str]):
        """
        Cancel orders using BitMart's batch endpoint.
        BitMart v2/batch_orders_cancel accepts "order_ids" array.
        """
        if self.dry_run or not order_ids:
            return

        def payload_func(batch):
            return {
                "symbol": self.symbol.replace("/", "_"),
                "order_ids": batch  # BitMart expects array of order_id strings
            }

        # Use batch cancel with default 50 batch size
        self._cancel_in_batches(order_ids, "/spot/v2/batch_orders_cancel", payload_func)

    # ---------------- Order placement ---------------- #
    def create_limit(self, side: str, price: float, amount: float) -> Optional[str]:
        """Create a limit maker order (post-only)."""
        price_str = f"{price:.8f}".rstrip("0").rstrip(".")
        amount_str = str(int(round(amount)))

        if self.dry_run:
            return f"dry_{int(time.time() * 1000000)}"

        payload = {
            "symbol": self.symbol.replace("/", "_"),
            "side": side,
            "type": "limit_maker",  # Post-only order type
            "size": amount_str,
            "price": price_str,
            "client_order_id": f"oho{int(time.time() * 1000000)}"[:32]
        }

        try:
            resp = self._request("POST", "/spot/v2/submit_order", data=payload, version="v2")
            if resp.get("code") in ["1000", 1000]:
                oid = resp.get("data", {}).get("order_id")
                logger.info(f"{self.exchange_name} {side.upper()} {amount_str} @ {price_str} id={oid}")
                return str(oid)
        except Exception as e:
            logger.warning(f"{self.exchange_name} create_limit error: {e}")
        return None

    # ---------------- Precision & Limits ---------------- #
    def price_to_precision(self, p):
        return round(p, 8)

    def amount_to_precision(self, a):
        return int(round(a))

    def get_limits(self):
        return {"min_amount": 1000, "min_cost": 1.0}

    def get_steps(self):
        return 1e-8, 1

    def get_precisions(self):
        return 8, 0

    # ---------------- New: refresh orders (place ladder + cancel stale) ---------------- #
    def refresh_orders(self, mid: float, depth: int = 20, gap_min: float = 0.000001, gap_max: float = 0.000002,
                       size_min: float = 4200, size_max: float = 6000, min_spread_bps: float = 5.0,
                       maker_guard_ticks: int = 2) -> Set[str]:
        """
        Place new ladder orders FIRST, then cancel only stale orders.
        This is the key to maintaining continuous liquidity.

        Returns: Set of newly placed order IDs
        """
        limits = self.get_limits()
        best_bid, best_ask = self.fetch_best_quotes()
        price_step, amount_step = self.get_steps()
        tick = price_step

        # Build ladders
        def build_ladder(mid_val, side):
            levels = []
            acc = 0.0
            for _ in range(depth):
                step = random.uniform(gap_min, gap_max)
                acc += step
                levels.append(mid_val - acc if side == "buy" else mid_val + acc)
            return levels

        buy_prices = build_ladder(mid, "buy")
        sell_prices = build_ladder(mid, "sell")
        sizes_buy = [random.uniform(size_min, size_max) for _ in range(depth)]
        sizes_sell = [random.uniform(size_min, size_max) for _ in range(depth)]

        # Guard nearest levels from crossing
        def guard_price(px, side):
            if side == "buy" and best_ask is not None:
                allowed_max = best_ask - maker_guard_ticks * tick
                return max(price_step, min(px, allowed_max))
            if side == "sell" and best_bid is not None:
                allowed_min = best_bid + maker_guard_ticks * tick
                return max(price_step, max(px, allowed_min))
            return max(price_step, px)

        # ===== STEP 1: Place new orders FIRST (no liquidity gap) =====
        new_ids: Set[str] = set()
        placed_buy = 0
        placed_sell = 0

        for px, qty in zip(buy_prices, sizes_buy):
            if placed_buy >= depth:
                break
            qty = max(qty, limits["min_amount"])
            px_adj = guard_price(self.price_to_precision(px), "buy")
            oid = self.create_limit("buy", px_adj, qty)
            if oid:
                new_ids.add(oid)
                placed_buy += 1

        for px, qty in zip(sell_prices, sizes_sell):
            if placed_sell >= depth:
                break
            qty = max(qty, limits["min_amount"])
            px_adj = guard_price(self.price_to_precision(px), "sell")
            oid = self.create_limit("sell", px_adj, qty)
            if oid:
                new_ids.add(oid)
                placed_sell += 1

        logger.info(
            f"{self.exchange_name} placed ladder | mid={mid:.10f} buys={placed_buy} sells={placed_sell}"
        )

        # ===== STEP 2: Cancel ONLY stale orders (stealth cleanup) =====
        try:
            open_orders = self.fetch_open_orders()
            open_ids_now = {str(o["id"]) for o in open_orders if o.get("id")}

            # Cancel everything EXCEPT the orders we just placed
            to_cancel = [oid for oid in open_ids_now if oid not in new_ids]

            if to_cancel:
                self.cancel_orders_by_ids(to_cancel)
                logger.info(f"{self.exchange_name} removed {len(to_cancel)} stale orders | remaining={len(new_ids)}")
        except Exception as e:
            logger.warning(f"{self.exchange_name} refresh cleanup error: {e}")

        return new_ids