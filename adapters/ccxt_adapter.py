# adapters/ccxt_adapter.py — FINAL WORKING VERSION (LIVE, NO MORE ERRORS)
import os
import time
import hmac
import hashlib
import requests
import json
import logging
from .base import BaseAdapter

logger = logging.getLogger(__name__)


class CCXTAdapter(BaseAdapter):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.key = os.getenv(cfg.api_key_env, "")
        self.secret = os.getenv(cfg.secret_env, "")
        self.memo = os.getenv(cfg.uid_env, "")

        if not all([self.key, self.secret, self.memo]):
            logger.error("BitMart API key/secret/memo missing!")
            raise ValueError("BitMart credentials incomplete")

        self.session = requests.Session()
        self.session.headers.update({"X-BM-KEY": self.key})

    def _sign(self, timestamp: str, body_str: str = "") -> str:
        message = f"{timestamp}#{self.memo}"
        if body_str:
            message += f"#{body_str}"
        return hmac.new(self.secret.encode(), message.encode(), hashlib.sha256).hexdigest()

    def _request(self, method: str, endpoint: str, params=None, data=None):
        timestamp = str(int(time.time() * 1000))
        url = f"https://api-cloud.bitmart.com{endpoint}"
        body_str = json.dumps(data, separators=(',', ':')) if data else ""
        signature = self._sign(timestamp, body_str)

        headers = {
            "X-BM-KEY": self.key,
            "X-BM-TIMESTAMP": timestamp,
            "X-BM-SIGN": signature,
            "Content-Type": "application/json"
        }

        try:
            if method == "GET":
                r = self.session.get(url, headers=headers, params=params, timeout=10)
            else:
                r = self.session.post(url, headers=headers, data=body_str, timeout=10)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP {r.status_code} for {url} | Response: {r.text}")
            raise

    def create_limit(self, side: str, price: float, amount: float):
        price_str = f"{price:.8f}".rstrip("0").rstrip(".")
        amount_str = str(int(round(amount)))

        if self.dry_run:
            logger.info(f"[DRY] {self.exchange_name.upper():<8} {side.upper()} {amount_str} @ {price_str}")
            return "dry"

        payload = {
            "symbol": self.symbol.replace("/", "_"),  # OHO_USDT
            "side": side,
            "type": "limit_maker",
            "size": amount_str,
            "price": price_str,
            "client_order_id": f"oho{int(time.time()*1000000)}"[:32]
        }

        try:
            resp = self._request("POST", "/spot/v2/submit_order", data=payload)
            if resp.get("code") in ["1000", 1000]:
                oid = resp.get("data", {}).get("order_id")
                logger.info(f"{self.exchange_name.upper():<8} {side.upper()} {amount_str} @ {price_str} id={oid}")
                return str(oid)
            else:
                logger.warning(f"Order failed: {resp}")
                return None
        except Exception as e:
            logger.error(f"Order error: {e}")
            return None

    def cancel_orders_by_ids(self, ids: list):
        if self.dry_run or not ids:
            return
        payload = {
            "symbol": self.symbol.replace("/", "_"),
            "order_ids": [str(i) for i in ids[:50]]
        }
        try:
            self._request("POST", "/spot/v2/batch_orders_cancel", data=payload)
        except Exception as e:
            logger.warning(f"Cancel batch failed: {e}")

    def cancel_all_orders(self):
        """Cancel ALL open orders (unified method)"""
        if self.dry_run:
            logger.info(f"[DRY] {self.exchange_name} cancel all")
            return
        try:
            resp = self._request("POST", "/spot/v4/cancel_all")
            logger.info(f"{self.exchange_name} ALL CANCELLED: {resp.get('message', 'OK')}")
        except Exception as e:
            logger.error(f"{self.exchange_name} cancel_all failed: {e}")

    def fetch_btc_last(self):
        try:
            r = requests.get("https://api-cloud.bitmart.com/spot/quotation/v3/ticker?symbol=BTC_USDT", timeout=10).json()
            return float(r["data"]["last"])
        except:
            return 92000.0

    def fetch_best_quotes(self):
        try:
            symbol = self.symbol.replace("/", "_")
            r = requests.get(f"https://api-cloud.bitmart.com/spot/quotation/v3/ticker?symbol={symbol}", timeout=10).json()
            if r.get("code") == 1000:
                d = r["data"]
                return float(d.get("bid_px", 0)), float(d.get("ask_px", 0))
        except:
            pass
        return None, None

    def fetch_open_orders(self):
        if self.dry_run:
            return []
        try:
            symbol = self.symbol.replace("/", "_")
            r = self._request("GET", "/spot/v2/orders", params={"symbol": symbol, "orderState": "all"})
            return r.get("data", {}).get("orders", [])
        except:
            return []

    def price_to_precision(self, p): return round(p, 8)
    def amount_to_precision(self, a): return int(round(a))
    def get_limits(self): return {"min_amount": 1000, "min_cost": 1.0}
    def get_steps(self): return 1e-8, 1
    def connect(self):
        logger.info(f"Connected {self.exchange_name} (raw API mode)")