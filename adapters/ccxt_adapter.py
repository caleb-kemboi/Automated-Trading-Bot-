# adapters/ccxt_adapter.py — DEBUG VERSION with detailed error logging
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

        # Debug: Log configuration
        logger.info(
            f"BitMart Init - Key present: {bool(self.key)}, Secret present: {bool(self.secret)}, Memo present: {bool(self.memo)}")

        if not self.memo:
            logger.error("BitMart Memo (uid_env) not set — REQUIRED for signature")

        self.session = requests.Session()
        self.session.headers.update({"X-BM-KEY": self.key})

    def _sign(self, timestamp: str, body_str: str = "") -> str:
        """Sign request with BitMart v2 spot signature scheme"""
        message = f"{timestamp}#{self.memo}"
        if body_str:
            message += f"#{body_str}"

        # Debug logging
        logger.debug(f"Signature message: {message[:100]}...")

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

        # Debug: Log request details
        logger.debug(f"Request: {method} {url}")
        logger.debug(f"Headers: {json.dumps({k: v[:20] + '...' if len(v) > 20 else v for k, v in headers.items()})}")
        logger.debug(f"Body: {body_str[:200]}...")

        try:
            if method == "GET":
                response = self.session.get(url, headers=headers, params=params, timeout=10)
            else:
                response = self.session.post(url, headers=headers, data=body_str, timeout=10)

            # Debug: Log response
            logger.debug(f"Response status: {response.status_code}")
            logger.debug(f"Response body: {response.text[:500]}")

            response.raise_for_status()
            return response.json()

        except requests.exceptions.HTTPError as e:
            # Enhanced error logging
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

        # BitMart symbol format: no separator
        symbol = self.symbol.replace("/", "_").replace("_", "")

        payload = {
            "symbol": symbol,
            "side": "buy" if side == "buy" else "sell",
            "type": "limit_maker",
            "size": amount_str,
            "price": price_str,
            "client_order_id": f"oho_{int(time.time() * 1000000)}"[:32]
        }

        # Debug: Log payload
        logger.debug(f"Order payload: {json.dumps(payload, indent=2)}")

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
            logger.error(f"BitMart order error: {e}", exc_info=True)
            return None

    def cancel_orders_by_ids(self, ids: list):
        if self.dry_run or not ids:
            return

        symbol = self.symbol.replace("/", "_").replace("_", "")
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
        try:
            symbol = self.btc_symbol.replace("/", "_")
            r = requests.get(
                f"https://api-cloud.bitmart.com/spot/v1/ticker?symbol={symbol}",
                timeout=10
            ).json()
            price = float(r["data"]["tickers"][0]["last_price"])
            logger.debug(f"BTC price: {price}")
            return price
        except Exception as e:
            logger.warning(f"BTC fetch error: {e}")
            return 92000.0

    def fetch_best_quotes(self):
        try:
            symbol = self.symbol.replace("/", "_")
            r = requests.get(
                f"https://api-cloud.bitmart.com/spot/v1/ticker?symbol={symbol}",
                timeout=10
            ).json()
            ticker = r["data"]["tickers"][0]
            bid = float(ticker["best_bid"])
            ask = float(ticker["best_ask"])
            logger.debug(f"Best quotes - Bid: {bid:.8f}, Ask: {ask:.8f}")
            return bid, ask
        except Exception as e:
            logger.warning(f"Quotes fetch error: {e}")
            return None, None

    def fetch_open_orders(self):
        if self.dry_run:
            return []
        try:
            symbol = self.symbol.replace("/", "_")
            r = self._request(
                "GET",
                "/spot/v1/order/open_orders",
                params={"symbol": symbol}
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
            self.fetch_btc_last()
            logger.info("BitMart connection test successful")
        except Exception as e:
            logger.error(f"BitMart connection test failed: {e}")