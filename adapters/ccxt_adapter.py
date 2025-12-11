# adapters/ccxt_adapter.py — FIXED SIGNATURE FOR BITMART V2 SPOT (includes body in prehash, no method/path)
import os
import time
import hmac
import hashlib
import requests
import json  # For compact dumps
import logging
from .base import BaseAdapter

logger = logging.getLogger(__name__)

class CCXTAdapter(BaseAdapter):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.key = os.getenv(cfg.api_key_env, "")
        self.secret = os.getenv(cfg.secret_env, "")
        self.memo = os.getenv(cfg.uid_env, "")  # Required Memo for signature
        if not self.memo:
            logger.warning("BitMart Memo (uid_env) not set — required for raw API signature")
        self.session = requests.Session()
        self.session.headers.update({"X-BM-KEY": self.key})

    def _sign(self, timestamp: str, body_str: str = "") -> str:
        # FIXED: Prehash = timestamp#memo#body_str (no method/path for v2 spot POST)
        message = f"{timestamp}#{self.memo}"
        if body_str:
            message += f"#{body_str}"
        signature = hmac.new(self.secret.encode(), message.encode(), hashlib.sha256).hexdigest()
        return signature

    def _request(self, method: str, endpoint: str, params=None, data=None):
        timestamp = str(int(time.time() * 1000))
        url = f"https://api-cloud.bitmart.com{endpoint}"

        # Compact JSON body string (no spaces, consistent)
        body_str = json.dumps(data, separators=(',', ':')) if data else ""

        signature = self._sign(timestamp, body_str)

        headers = {
            "X-BM-KEY": self.key,
            "X-BM-TIMESTAMP": timestamp,
            "X-BM-SIGN": signature,
            "Content-Type": "application/json"
        }

        if method == "GET":
            response = self.session.get(url, headers=headers, params=params, timeout=10)
        else:
            response = self.session.post(url, headers=headers, data=body_str, timeout=10)

        response.raise_for_status()
        return response.json()

    def create_limit(self, side: str, price: float, amount: float):
        price_str = f"{price:.8f}"
        amount_str = f"{int(round(amount))}"  # Whole units for OHO

        if self.dry_run:
            logger.info(f"[DRY] {self.exchange_name.upper():<8} {side.upper()} {amount_str} @ {price_str}")
            return "dry"

        payload = {
            "symbol": self.symbol.replace("/", ""),
            "side": "buy" if side == "buy" else "sell",
            "type": "limit",
            "size": amount_str,
            "price": price_str,
            "time_in_force": "post_only",
            "client_order_id": f"oho_{int(time.time()*1000000)}"
        }

        try:
            resp = self._request("POST", "/spot/v2/submit_order", data=payload)
            if resp.get("code") == "1000" or resp.get("code") == 1000:  # Some responses use string
                oid = resp.get("data", {}).get("order_id", "unknown")
                logger.info(f"{self.exchange_name.upper():<8} {side.upper()} {amount_str} @ {price_str} id={oid}")
                return str(oid)
            else:
                logger.warning(f"BitMart order failed: {resp}")
                return None
        except Exception as e:
            logger.warning(f"BitMart raw API error: {e}")
            return None

    # Rest unchanged (cancel, fetch, etc.)
    def cancel_orders_by_ids(self, ids: list):
        if self.dry_run or not ids: return
        payload = {"symbol": self.symbol.replace("/", ""), "order_ids": [str(i) for i in ids[:50]]}
        try:
            self._request("POST", "/spot/v2/batch_orders_cancel", data=payload)
        except Exception as e:
            logger.warning(f"BitMart cancel error: {e}")

    def fetch_btc_last(self):
        try:
            r = requests.get("https://api-cloud.bitmart.com/spot/v1/ticker?symbol=BTC_USDT", timeout=10).json()
            return float(r["data"]["tickers"][0]["last_price"])
        except:
            return 92000.0

    def fetch_best_quotes(self):
        try:
            r = requests.get(f"https://api-cloud.bitmart.com/spot/v1/ticker?symbol={self.symbol.replace('/', '')}", timeout=10).json()
            ticker = r["data"]["tickers"][0]
            return float(ticker["best_bid"]), float(ticker["best_ask"])
        except:
            return None, None

    def fetch_open_orders(self):
        if self.dry_run: return []
        try:
            r = self._request("GET", "/spot/v1/order/open_orders", params={"symbol": self.symbol.replace("/", "")})
            return r.get("data", {}).get("orders", [])
        except:
            return []

    def price_to_precision(self, p): return round(p, 8)
    def amount_to_precision(self, a): return int(round(a))
    def get_limits(self): return {"min_amount": 1000, "min_cost": 1.0}
    def get_steps(self): return 1e-8, 1
    def connect(self): logger.info(f"Connected {self.exchange_name} (raw API mode)")