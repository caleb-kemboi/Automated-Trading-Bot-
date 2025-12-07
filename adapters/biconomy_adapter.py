# adapters/biconomy.py
import os, time, hashlib, requests
from typing import Optional, Tuple, List
from .base import BaseAdapter
from config import SETTINGS
import logging

logger = logging.getLogger(__name__)
BASE = "https://api.biconomy.com"

class BiconomyAdapter(BaseAdapter):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.key = os.getenv("BICONOMY_KEY")
        self.secret = os.getenv("BICONOMY_SECRET")
        self.session = requests.Session()
        self.session.headers.update({"X-BB-APIKEY": self.key})

    def _sign(self, params):
        params = params.copy()
        params["timestamp"] = str(int(time.time() * 1000))
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        params["signature"] = hashlib.md5((query + self.secret).encode()).hexdigest().lower()
        return params

    def _get(self, path, params=None):
        return self.session.get(BASE + path, params=self._sign(params or {})).json()

    def _post(self, path, data):
        return self.session.post(BASE + path, json=self._sign(data)).json()

    def connect(self): logger.info("Biconomy adapter ready")

    def fetch_btc_last(self):
        data = self._get("/api/v1/market/tickers")["data"]
        for t in data:
            if t["symbol"] == "BTC_USDT": return float(t["lastPrice"])
        return 92000.0

    def fetch_best_quotes(self):
        d = self._get("/api/v1/market/ticker", {"symbol": self.symbol})["data"]
        return float(d["bidPrice"]), float(d["askPrice"])

    def fetch_open_orders(self):
        return self._get("/api/v1/order/openOrders", {"symbol": self.symbol}).get("data", [])

    def cancel_orders_by_ids(self, ids):
        if self.dry_run or not ids: return
        for oid in ids:
            try: self._post("/api/v1/order/cancel", {"orderId": oid})
            except: pass

    def create_limit(self, side, price, amount):
        if self.dry_run:
            logger.info(f"[DRY] BICONOMY {side.upper()} {amount} @ {price:.10f}")
            return "dry"
        payload = {
            "symbol": self.symbol,
            "side": "BUY" if side == "buy" else "SELL",
            "type": "LIMIT",
            "quantity": str(int(amount)),
            "price": f"{price:.10f}",
            "timeInForce": "POST_ONLY"
        }
        resp = self._post("/api/v1/order/place", payload)
        return str(resp["data"]["orderId"])

    def price_to_precision(self, p): return round(p, 8)
    def amount_to_precision(self, a): return int(round(a))
    def get_limits(self): return {"min_amount": 1000, "min_cost": 1}
    def get_steps(self): return 1e-8, 1