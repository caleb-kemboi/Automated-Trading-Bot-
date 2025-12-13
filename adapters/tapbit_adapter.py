# adapters/tapbit.py — FIXED for bot compatibility
import json
import os
import time
import hmac
import hashlib
from typing import Optional, List

import requests
import logging

from helpers.batch_cancel import BatchCancelMixin
from .base import BaseAdapter

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

    def _get_headers(self, method: str, path: str, body: str = ""):
        timestamp = str(int(time.time() * 1000))
        message = f"{timestamp}{method}{path}{body}"
        signature = hmac.new(self.secret.encode(), message.encode(), hashlib.sha256).hexdigest()
        return {"ACCESS-KEY": self.key, "ACCESS-TIMESTAMP": timestamp, "ACCESS-SIGN": signature,
                "Content-Type": "application/json"}

    def _post(self, path, data):
        body = json.dumps(data) if data else ""
        headers = self._get_headers("POST", path, body)
        r = self.session.post("https://openapi.tapbit.com" + path, data=body, headers=headers, timeout=10)
        r.raise_for_status()
        return r.json()

    def connect(self):
        pass

    def fetch_btc_last(self) -> float:
        try:
            r = requests.get("https://openapi.tapbit.com/api/v1/spot/market/ticker", params={"symbol": "BTCUSDT"},
                             timeout=10).json()
            if r.get("code") == 0: return float(r["data"]["last"])
        except:
            pass
        return 92000.0

    def fetch_best_quotes(self):
        try:
            r = requests.get("https://openapi.tapbit.com/api/v1/spot/market/ticker",
                             params={"symbol": self.symbol.replace("/", "")}, timeout=10).json()
            if r.get("code") == 0:
                d = r["data"]
                return float(d["bid"]), float(d["ask"])
        except:
            pass
        return None, None

    def fetch_open_orders(self) -> List[dict]:
        if self.dry_run: return []
        try:
            resp = self._post("/api/v1/spot/open_order_list", {"symbol": self.symbol.replace("/", "")})
            if resp.get("code") == 0:
                return [{"id": str(o.get("orderId"))} for o in resp.get("data", []) if o.get("orderId")]
        except:
            pass
        return []

    def cancel_orders_by_ids(self, order_ids: List[str]):
        def payload_func(batch):
            return [{"orderId": str(oid), "symbol": self.symbol.replace("/", "")} for oid in batch]

        self._cancel_in_batches(order_ids, "/api/v1/spot/cancel_order", payload_func)

    def create_limit(self, side: str, price: float, amount: float) -> Optional[str]:
        if self.dry_run: return f"dry_{int(time.time() * 1000000)}"
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
                return oid
        except:
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
