# adapters/bitmart_raw_adapter.py  ← RENAME THIS FILE TO ccxt_adapter.py
import os
import time
import hmac
import hashlib
import requests
import logging
from .base import BaseAdapter

logger = logging.getLogger(__name__)

class CCXTAdapter(BaseAdapter):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.key = os.getenv(cfg.api_key_env, "")
        self.secret = os.getenv(cfg.secret_env, "")
        self.memo = os.getenv(cfg.uid_env, "")  # BitMart "Memo" = UID
        self.session = requests.Session()
        self.session.headers.update({"X-BM-KEY": self.key})

    def _sign(self, timestamp, method, path, body=""):
        message = f"{timestamp}#{self.memo}#{method.upper()}#{path}"
        if body:
            message += f"#{body}"
        signature = hmac.new(self.secret.encode(), message.encode(), hashlib.sha256).hexdigest()
        return signature

    def _request(self, method, endpoint, params=None, data=None):
        timestamp = str(int(time.time() * 1000))
        url = f"https://api-cloud.bitmart.com{endpoint}"
        body = data if data is None else requests.compat.json.dumps(data) if isinstance(data, dict) else data

        signature = self._sign(timestamp, method, endpoint, body)

        headers = {
            "X-BM-KEY": self.key,
            "X-BM-TIMESTAMP": timestamp,
            "X-BM-SIGN": signature,
            "Content-Type": "application/json"
        }

        if method == "GET":
            response = self.session.get(url, headers=headers, params=params, timeout=10)
        else:
            response = self.session.post(url, headers=headers, data=body, timeout=10)

        response.raise_for_status()
        return response.json()

    def create_limit(self, side, price, amount):
        # FINAL FIX: price as string with exactly 8 decimals
        price_str = f"{price:.8f}"
        amount_str = f"{amount:f}".rstrip("0").rstrip(".") if amount.is_integer() else f"{amount:.0f}"

        if self.dry_run:
            logger.info(f"[DRY] {self.exchange_name.upper():<8} {side.upper()} {amount_str} @ {price_str}")
            return "dry"

        payload = {
            "symbol": self.symbol.replace("/", ""),  # OHOUSDT
            "side": "buy" if side == "buy" else "sell",
            "type": "limit",
            "size": amount_str,
            "price": price_str,
            "time_in_force": "post_only",
            "client_order_id": f"oho_{int(time.time()*1000000)}"
        }

        try:
            resp = self._request("POST", "/spot/v2/submit_order", data=payload)
            if resp.get("code") == 1000:
                oid = resp["data"]["order_id"]
                logger.info(f"{self.exchange_name.upper():<8} {side.upper()} {amount_str} @ {price_str} id={oid}")
                return str(oid)
            else:
                logger.warning(f"BitMart order failed: {resp}")
                return None
        except Exception as e:
            logger.warning(f"BitMart raw API error: {e}")
            return None

    def cancel_orders_by_ids(self, ids):
        if self.dry_run or not ids: return
        payload = {"symbol": self.symbol.replace("/", ""), "order_ids": ids[:50]}  # max 50
        try:
            self._request("POST", "/spot/v2/batch_orders_cancel", data=payload)
        except:
            pass

    # Minimal required methods — others fall back safely
    def fetch_btc_last(self):
        try:
            r = requests.get("https://api-cloud.bitmart.com/spot/v1/ticker?symbol=BTC_USDT").json()
            return float(r["data"]["tickers"][0]["last_price"])
        except:
            return 92000.0

    def fetch_best_quotes(self):
        try:
            r = requests.get(f"https://api-cloud.bitmart.com/spot/v1/ticker?symbol={self.symbol.replace('/', '')}").json()
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
    def amount_to_precision(self, a): return round(a, 0)
    def get_limits(self): return {"min_amount": 1000, "min_cost": 1.0}
    def get_steps(self): return 1e-8, 1
    def connect(self): logger.info(f"Connected {self.exchange_name} (raw API mode)")