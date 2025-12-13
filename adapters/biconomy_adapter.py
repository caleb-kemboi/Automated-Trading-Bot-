# adapters/biconomy_adapter.py — Doc-verified for bot compatibility
import os
import time
import hashlib
import requests
import logging
from typing import Optional, List

from helpers.batch_cancel import BatchCancelMixin

logger = logging.getLogger(__name__)

BASE = "https://api.biconomy.com"


class BiconomyAdapter(BatchCancelMixin):
    def __init__(self, cfg):
        self.cfg = cfg
        self.exchange_name = cfg.id
        self.symbol = cfg.symbol
        self.btc_symbol = cfg.btc_symbol
        self.dry_run = cfg.dry_run

        self.key = os.getenv("BICONOMY_KEY", "")
        self.secret = os.getenv("BICONOMY_SECRET", "")
        self.session = requests.Session()
        self.session.headers.update({
            "X-BB-APIKEY": self.key,
            "Content-Type": "application/x-www-form-urlencoded",
            "X-SITE-ID": "127"
        })

    def _sign(self, params: dict) -> dict:
        if not self.secret:
            return params
        p = params.copy()
        p["api_key"] = self.key
        p["time"] = str(int(time.time() * 1000))
        query = "&".join(f"{k}={v}" for k, v in sorted(p.items()))
        p["sign"] = hashlib.md5((query + self.secret).encode()).hexdigest().upper()
        return p

    def _post(self, path: str, data: dict):
        signed = self._sign(data)
        r = self.session.post(BASE + path, data=signed, timeout=12)
        r.raise_for_status()
        return r.json()

    def connect(self):
        logger.info(f"Connected {self.exchange_name} (Biconomy)")

    def fetch_btc_last(self) -> float:
        try:
            r = requests.get(BASE + "/api/v1/tickers", timeout=10).json()
            for t in r.get("ticker", []):
                if t.get("symbol") in ("BTC_USDT", "BTCUSDT"):
                    return float(t["last"])
        except Exception:
            pass
        return 92000.0

    def fetch_best_quotes(self):
        try:
            r = requests.get(BASE + "/api/v1/tickers", timeout=10).json()
            for t in r.get("ticker", []):
                if t.get("symbol") == self.symbol.replace("/", "_"):
                    return float(t.get("buy")), float(t.get("sell"))
        except Exception:
            pass
        return None, None

    def fetch_open_orders(self) -> List[dict]:
        if self.dry_run:
            return []
        try:
            r = self._post("/api/v1/private/order/pending",
                           {"market": self.symbol, "offset": "0", "limit": "100"})
            records = r.get("result", {}).get("records", [])
            return [
                {"id": str(o.get("id", o.get("order_id")))}
                for o in records if o.get("id") or o.get("order_id")
            ]
        except Exception:
            return []

    def cancel_orders_by_ids(self, order_ids: List[str]):
        """Doc-verified batch cancel using orders_json, max 10 per batch"""
        if self.dry_run or not order_ids:
            return

        def payload_func(batch):
            return {
                "orders_json": [
                    {"market": self.symbol.replace("/", "_"), "order_id": int(oid)}
                    for oid in batch
                ]
            }

        # Call doc-verified cancel_batch endpoint
        self._cancel_in_batches(order_ids, "/api/v1/private/trade/cancel_batch", payload_func, batch_size=10)

    def create_limit(self, side: str, price: float, amount: float) -> Optional[str]:
        if self.dry_run:
            return f"dry_{int(time.time() * 1000000)}"
        payload = {
            "market": self.symbol,
            "side": "2" if side.lower() == "buy" else "1",
            "amount": str(int(round(amount))),
            "price": f"{price:.10f}",
            "type": "1"
        }
        try:
            resp = self._post("/api/v1/private/order/create", payload)
            if resp.get("code") == 0:
                oid = resp.get("result", {}).get("order_id")
                logger.info(f"{self.exchange_name} {side.upper()} {amount:.0f} @ {price:.10f} id={oid}")
                return str(oid)
        except Exception:
            pass
        return None

    def price_to_precision(self, p):
        return round(p, 8)

    def amount_to_precision(self, a):
        return int(round(a))

    def get_limits(self):
        return {"min_amount": 1, "min_cost": 1.0}

    def get_steps(self):
        return 1e-8, 1

    def get_precisions(self):
        return (8, 0)
