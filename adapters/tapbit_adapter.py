# adapters/tapbit.py — FIXED for bot compatibility
import json
import os
import time
import hmac
import hashlib
from typing import Optional, List, Set

import requests
import logging

from helpers.batch_cancel import BatchCancelMixin

logger = logging.getLogger(__name__)
BASE = "https://openapi.tapbit.com"


class TapbitAdapter(BatchCancelMixin):
    def __init__(self, cfg):
        self.cfg = cfg
        self.exchange_name = cfg.id
        self.symbol = cfg.symbol
        self.btc_symbol = cfg.btc_symbol
        self.dry_run = cfg.dry_run

        self.key = os.getenv("TAPBIT_KEY", "")
        self.secret = os.getenv("TAPBIT_SECRET", "")
        self.session = requests.Session()

        # Track current cycle's order IDs
        self.current_cycle_order_ids: Set[str] = set()

    def _get_headers(self, method: str, path: str, body: str = ""):
        timestamp = str(int(time.time() * 1000))
        message = f"{timestamp}{method}{path}{body}"
        signature = hmac.new(self.secret.encode(), message.encode(), hashlib.sha256).hexdigest()
        return {"ACCESS-KEY": self.key, "ACCESS-TIMESTAMP": timestamp, "ACCESS-SIGN": signature,
                "Content-Type": "application/json"}

    def _request(self, method: str, path: str, data: dict = None):
        """Unified request method for BatchCancelMixin compatibility."""
        body = json.dumps(data) if data else ""
        headers = self._get_headers(method.upper(), path, body)
        r = self.session.post(BASE + path, data=body, headers=headers, timeout=10)
        r.raise_for_status()
        return r.json()

    def _post(self, path, data):
        """Legacy method for backward compatibility."""
        return self._request("POST", path, data)

    def connect(self):
        logger.info(f"Connected {self.exchange_name} (Tapbit)")

    def fetch_btc_last(self) -> float:
        try:
            r = requests.get(BASE + "/api/v1/spot/market/ticker", params={"symbol": "BTCUSDT"},
                             timeout=10).json()
            if r.get("code") == 0:
                return float(r["data"]["last"])
        except Exception:
            pass
        return 92000.0

    def fetch_best_quotes(self):
        try:
            r = requests.get(BASE + "/api/v1/spot/market/ticker",
                             params={"symbol": self.symbol.replace("/", "")}, timeout=10).json()
            if r.get("code") == 0:
                d = r["data"]
                return float(d["bid"]), float(d["ask"])
        except Exception:
            pass
        return None, None

    def fetch_open_orders(self) -> List[dict]:
        if self.dry_run:
            return []
        try:
            resp = self._post("/api/v1/spot/open_order_list", {"symbol": self.symbol.replace("/", "")})
            if resp.get("code") == 0:
                return [{"id": str(o.get("orderId"))} for o in resp.get("data", []) if o.get("orderId")]
        except Exception:
            pass
        return []

    def cancel_all_orders(self):
        """SMART CANCEL: Cancel ONLY stale orders (preserves current cycle's orders)."""
        if self.dry_run:
            logger.info(f"[DRY] {self.exchange_name} skip cancel_all_orders()")
            return

        try:
            open_orders = self.fetch_open_orders()
            if not open_orders:
                return

            open_ids_now = {str(o["id"]) for o in open_orders if o.get("id")}
            to_cancel = [oid for oid in open_ids_now if oid not in self.current_cycle_order_ids]

            if to_cancel:
                self.cancel_orders_by_ids(to_cancel)
                logger.info(
                    f"{self.exchange_name} removed {len(to_cancel)} stale orders | kept {len(self.current_cycle_order_ids)}")
        except Exception as e:
            logger.error(f"{self.exchange_name} cancel_all_orders error: {e}")

    def cancel_orders_by_ids(self, order_ids: List[str]):
        """Cancel orders individually (Tapbit requires per-order cancel)."""
        if self.dry_run or not order_ids:
            return

        cancelled = 0
        for oid in order_ids:
            try:
                payload = {"orderId": str(oid), "symbol": self.symbol.replace("/", "")}
                resp = self._request("POST", "/api/v1/spot/cancel_order", payload)
                if resp.get("code") == 0:
                    cancelled += 1
            except Exception as e:
                logger.warning(f"{self.exchange_name} cancel failed for {oid}: {e}")

        if cancelled > 0:
            logger.info(f"{self.exchange_name} cancelled {cancelled} stale orders")

    def create_limit(self, side: str, price: float, amount: float) -> Optional[str]:
        if self.dry_run:
            fake_id = f"dry_{int(time.time() * 1000000)}"
            self.current_cycle_order_ids.add(fake_id)
            return fake_id

        payload = {
            "symbol": self.symbol.replace("/", ""),
            "side": side.upper(),
            "orderPrice": f"{price:.10f}",
            "orderQty": str(amount),
            "orderType": "LIMIT",
            "timeInForce": "POST_ONLY"
        }
        try:
            resp = self._post("/api/v1/spot/order", payload)
            if resp.get("code") == 0:
                oid = str(resp["data"]["orderId"])
                logger.info(f"{self.exchange_name} {side.upper()} {amount:.0f} @ {price:.10f} id={oid}")
                self.current_cycle_order_ids.add(oid)
                return oid
        except Exception:
            pass
        return None

    def price_to_precision(self, p):
        return round(p, 8)

    def amount_to_precision(self, a):
        return int(a)

    def get_limits(self):
        return {"min_amount": 1, "min_cost": 1}

    def get_steps(self):
        return (1e-8, 1)

    def get_precisions(self):
        return (8, 0)