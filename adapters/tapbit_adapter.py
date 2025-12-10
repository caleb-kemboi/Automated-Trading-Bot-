import os
import time
import hmac
import hashlib
import requests
import logging
from .base import BaseAdapter

logger = logging.getLogger(__name__)

BASE = "https://openapi.tapbit.com"


class TapbitAdapter(BaseAdapter):
    """
    Correct Tapbit Spot API adapter.
    Signing uses:
        ACCESS-KEY
        ACCESS-SIGN (HMAC-SHA256)
        ACCESS-TIMESTAMP
    All spot order endpoints under /api/v1/spot/*
    """

    def __init__(self, cfg):
        super().__init__(cfg)

        self.key = os.getenv("TAPBIT_KEY", "") or getattr(cfg, "api_key_env", "")
        self.secret = os.getenv("TAPBIT_SECRET", "") or getattr(cfg, "secret_env", "")

        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json"
        })

    # --------------------------------------------------------
    # SIGNING
    # --------------------------------------------------------
    def _generate_signature(self, timestamp: str, method: str, path: str, body: str = ""):
        """
        Generate signature for Tapbit API
        Format: timestamp + method + path + body
        """
        if not self.secret:
            return ""

        # Create the message to sign
        message = f"{timestamp}{method}{path}{body}"

        # Generate HMAC-SHA256 signature
        signature = hmac.new(
            self.secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        return signature

    def _get_headers(self, method: str, path: str, body: str = ""):
        """Generate authentication headers for the request"""
        timestamp = str(int(time.time() * 1000))

        headers = {
            "ACCESS-KEY": self.key,
            "ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json"
        }

        if self.key and self.secret:
            signature = self._generate_signature(timestamp, method, path, body)
            headers["ACCESS-SIGN"] = signature

        return headers

    # --------------------------------------------------------
    # HTTP
    # --------------------------------------------------------
    def _post(self, path, data):
        """POST request with proper authentication"""
        import json
        body = json.dumps(data) if data else ""

        headers = self._get_headers("POST", path, body)

        r = self.session.post(
            BASE + path,
            data=body,
            headers=headers,
            timeout=10
        )

        # Log response for debugging
        if r.status_code != 200:
            logger.error(f"POST {path} failed: {r.status_code} - {r.text}")

        r.raise_for_status()
        return r.json()

    def _get(self, path, params=None):
        """GET request - public endpoints typically don't need authentication"""
        r = self.session.get(
            BASE + path,
            params=params or {},
            timeout=10
        )

        # Log response for debugging
        if r.status_code != 200:
            logger.error(f"GET {path} failed: {r.status_code} - {r.text}")

        r.raise_for_status()
        return r.json()

    # --------------------------------------------------------
    # PUBLIC
    # --------------------------------------------------------
    def connect(self):
        logger.info("Tapbit adapter ready")

    def fetch_btc_last(self):
        try:
            r = self._get("/api/v1/spot/market/ticker", {"symbol": "BTCUSDT"})
            if r.get("code") == 0 and "data" in r:
                return float(r["data"]["last"])
            else:
                logger.error(f"Unexpected response: {r}")
                return 92000.0
        except Exception as e:
            logger.error(f"tapbit btc_last error: {e}")
            return 92000.0

    def fetch_best_quotes(self):
        try:
            symbol = self.symbol.replace("/", "")
            r = self._get("/api/v1/spot/market/ticker", {"symbol": symbol})
            if r.get("code") == 0 and "data" in r:
                d = r["data"]
                return float(d["bid"]), float(d["ask"])
            else:
                logger.error(f"Unexpected response: {r}")
                return None, None
        except Exception as e:
            logger.error(f"tapbit quotes error: {e}")
            return None, None

    # --------------------------------------------------------
    # PRIVATE
    # --------------------------------------------------------
    def fetch_open_orders(self):
        if self.dry_run:
            return []

        try:
            resp = self._post("/api/v1/spot/open_order_list", {
                "symbol": self.symbol.replace("/", "")
            })
            if resp.get("code") == 0:
                return resp.get("data", [])
            else:
                logger.error(f"Open orders error: {resp}")
                return []
        except Exception as e:
            logger.error(f"Tapbit open orders error: {e}")
            return []

    def cancel_orders_by_ids(self, ids):
        if self.dry_run or not ids:
            return

        for oid in ids:
            try:
                resp = self._post("/api/v1/spot/cancel_order", {
                    "orderId": str(oid),
                    "symbol": self.symbol.replace("/", "")
                })
                if resp.get("code") != 0:
                    logger.warning(f"Tapbit cancel {oid} failed: {resp}")
            except Exception as e:
                logger.warning(f"Tapbit cancel {oid} exception: {e}")

    def create_limit(self, side, price, amount):
        if self.dry_run:
            logger.info(f"[DRY] TAPBIT {side.upper()} {amount} @ {price}")
            return "dry"

        payload = {
            "symbol": self.symbol.replace("/", ""),
            "side": side.upper(),  # BUY or SELL
            "orderPrice": f"{price:.10f}",
            "orderQty": str(amount),
            "orderType": "LIMIT",
            "timeInForce": "POST_ONLY"
        }

        try:
            resp = self._post("/api/v1/spot/order", payload)
            if resp.get("code") == 0 and "data" in resp:
                return str(resp["data"]["orderId"])
            else:
                logger.error(f"Create order failed: {resp}")
                return None
        except Exception as e:
            logger.error(f"Create order exception: {e}")
            return None

    # --------------------------------------------------------
    # PRECISION / LIMITS
    # --------------------------------------------------------
    def price_to_precision(self, p):
        return round(p, 8)

    def amount_to_precision(self, a):
        return int(a)

    def get_limits(self):
        return {"min_amount": 1, "min_cost": 1}

    def get_steps(self):
        return (1e-8, 1)