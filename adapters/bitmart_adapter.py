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

        # Track current cycle's order IDs (set by runner before cancel_all_orders)
        self.current_cycle_order_ids: Set[str] = set()

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

    def cancel_all_orders(self):
        """
        SMART CANCEL: Cancel ONLY stale orders (preserves current cycle's orders).
        Called by runner AFTER placing new orders.

        The runner has already placed new orders and stored their IDs in self.current_cycle_order_ids.
        This method cancels everything EXCEPT those new orders.
        """
        if self.dry_run:
            logger.info(f"[DRY] {self.exchange_name} skip cancel_all_orders()")
            return

        try:
            # Fetch all open orders
            open_orders = self.fetch_open_orders()
            if not open_orders:
                logger.debug(f"{self.exchange_name} no open orders found")
                return

            open_ids_now = {str(o["id"]) for o in open_orders if o.get("id")}

            # CRITICAL: Cancel everything EXCEPT current cycle's orders
            to_cancel = [oid for oid in open_ids_now if oid not in self.current_cycle_order_ids]

            if to_cancel:
                self.cancel_orders_by_ids(to_cancel)
                logger.info(
                    f"{self.exchange_name} removed {len(to_cancel)} stale orders | kept {len(self.current_cycle_order_ids)}")
            else:
                logger.debug(f"{self.exchange_name} no stale orders to cancel")

        except Exception as e:
            logger.error(f"{self.exchange_name} cancel_all_orders error: {e}")

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
            fake_id = f"dry_{int(time.time() * 1000000)}"
            self.current_cycle_order_ids.add(fake_id)  # Track even in dry-run
            return fake_id

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
                oid = str(resp.get("data", {}).get("order_id"))
                logger.info(f"{self.exchange_name} {side.upper()} {amount_str} @ {price_str} id={oid}")
                # CRITICAL: Track this order ID so it won't be cancelled
                self.current_cycle_order_ids.add(oid)
                return oid
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