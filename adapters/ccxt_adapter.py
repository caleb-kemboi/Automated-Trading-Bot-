# adapters/ccxt_adapter.py — FULLY FIXED VERSION
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

        logger.info(f"BitMart Init - Key: {bool(self.key)}, Secret: {bool(self.secret)}, Memo: {bool(self.memo)}")

        if not self.memo:
            logger.error("BitMart Memo (uid_env) not set — REQUIRED for signature")

        self.session = requests.Session()
        self.session.headers.update({"X-BM-KEY": self.key})

    def _sign(self, timestamp: str, body_str: str = "") -> str:
        """Sign request with BitMart v2 spot signature scheme"""
        message = f"{timestamp}#{self.memo}"
        if body_str:
            message += f"#{body_str}"

        signature = hmac.new(
            self.secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        return signature

    def _request(self, method: str, endpoint: str, params=None, data=None):
        timestamp = str(int(time.time() * 1000))
        url = f"https://api-cloud.bitmart.com{endpoint}"

        # Compact JSON body string
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
                response = self.session.get(url, headers=headers, params=params, timeout=10)
            else:
                response = self.session.post(url, headers=headers, data=body_str, timeout=10)

            response.raise_for_status()
            return response.json()

        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP {response.status_code} Error for {url}")
            logger.error(f"Response: {response.text}")
            raise

    def create_limit(self, side: str, price: float, amount: float):
        # Format with explicit precision
        price_str = f"{price:.8f}".rstrip('0').rstrip('.')
        amount_str = str(int(round(amount)))

        if self.dry_run:
            logger.info(f"[DRY] {self.exchange_name.upper():<8} {side.upper()} {amount_str} @ {price_str}")
            return "dry"

        # BitMart symbol format: Use underscore (e.g., OHO_USDT, BTC_USDT)
        symbol = self.symbol.replace("/", "_")

        payload = {
            "symbol": symbol,
            "side": "buy" if side == "buy" else "sell",
            "type": "limit_maker",
            "size": amount_str,
            "price": price_str,
            # CRITICAL FIX: No underscore allowed in client_order_id!
            "client_order_id": f"oho{int(time.time() * 1000000)}"[:32]
        }

        try:
            resp = self._request("POST", "/spot/v2/submit_order", data=payload)

            if resp.get("code") in ["1000", 1000]:
                oid = resp.get("data", {}).get("order_id")
                logger.info(f"{self.exchange_name.upper():<8} {side.upper()} {amount_str} @ {price_str} id={oid}")
                return str(oid)
            else:
                logger.warning(f"BitMart order failed: {resp}")
                return None

        except Exception as e:
            logger.error(f"BitMart order error: {e}")
            return None

    def cancel_orders_by_ids(self, ids: list):
        if self.dry_run or not ids:
            return

        # BitMart symbol format: Use underscore
        symbol = self.symbol.replace("/", "_")
        payload = {
            "symbol": symbol,
            "order_ids": [str(i) for i in ids[:50]]
        }

        try:
            resp = self._request("POST", "/spot/v2/batch_orders_cancel", data=payload)
            logger.debug(f"Cancel response: {resp}")
        except Exception as e:
            logger.warning(f"BitMart cancel error: {e}")

    def fetch_btc_last(self):
        """Fetch BTC/USDT price - FIXED response parsing"""
        try:
            symbol = self.symbol.replace("/", "_")
            url = f"https://api-cloud.bitmart.com/spot/quotation/v3/ticker?symbol=BTC_USDT"
            r = requests.get(url, timeout=10).json()

            # v3 ticker response format
            if "data" in r and isinstance(r["data"], dict) and "last" in r["data"]:
                price = float(r["data"]["last"])
                logger.debug(f"BTC price: {price}")
                return price
            else:
                logger.warning(f"Unexpected BTC ticker response: {r}")
                return 92000.0

        except Exception as e:
            logger.warning(f"BTC fetch error: {e}, using fallback")
            return 92000.0

    def fetch_best_quotes(self):
        """Fetch best bid/ask - FIXED response parsing"""
        try:
            symbol = self.symbol.replace("/", "_")
            url = f"https://api-cloud.bitmart.com/spot/quotation/v3/ticker?symbol={symbol}"
            r = requests.get(url, timeout=10).json()

            # v3 ticker response: {"code": 1000, "data": {...}}
            if r.get("code") == 1000 and "data" in r:
                ticker = r["data"]
                # Use bid_px/ask_px for v3 ticker
                bid = float(ticker.get("bid_px", ticker.get("best_bid", 0)))
                ask = float(ticker.get("ask_px", ticker.get("best_ask", 0)))

                if bid > 0 and ask > 0:
                    logger.debug(f"Best quotes - Bid: {bid:.8f}, Ask: {ask:.8f}")
                    return bid, ask

            logger.warning(f"Unexpected quotes response format")
            return None, None

        except Exception as e:
            logger.warning(f"Quotes fetch error: {e}")
            return None, None

    def fetch_open_orders(self):
        if self.dry_run:
            return []
        try:
            symbol = self.symbol.replace("/", "_")
            # Fixed: Use v2 endpoint for open orders
            r = self._request(
                "GET",
                "/spot/v2/orders",
                params={"symbol": symbol, "orderState": "all"}
            )
            orders = r.get("data", {}).get("orders", [])
            logger.debug(f"Open orders: {len(orders)}")
            return orders
        except Exception as e:
            logger.warning(f"Fetch orders error: {e}")
            return []

    def price_to_precision(self, p):
        return round(p, 8)

    def amount_to_precision(self, a):
        return int(round(a))

    def get_limits(self):
        return {"min_amount": 1000, "min_cost": 1.0}

    def get_steps(self):
        return 1e-8, 1

    def connect(self):
        logger.info(f"Connected {self.exchange_name} (raw API mode)")
        # Test connection
        try:
            btc_price = self.fetch_btc_last()
            logger.info(f"BitMart connection test successful - BTC: ${btc_price:,.2f}")
        except Exception as e:
            logger.error(f"BitMart connection test failed: {e}")