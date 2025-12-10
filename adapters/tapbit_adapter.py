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

        # Error deduplication
        self._error_cache = {}

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

        message = f"{timestamp}{method}{path}{body}"
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

    def _smart_log(self, level, key, message):
        """Log with deduplication - only log 1st, 10th, 50th occurrence"""
        now = time.time()

        if key not in self._error_cache:
            self._error_cache[key] = {"count": 0, "last_time": now}

        # Reset if 60s passed
        if now - self._error_cache[key]["last_time"] > 60:
            self._error_cache[key] = {"count": 0, "last_time": now}

        self._error_cache[key]["count"] += 1
        self._error_cache[key]["last_time"] = now
        count = self._error_cache[key]["count"]

        # Log at 1, 10, 50, 100, etc
        if count == 1 or count == 10 or count == 50 or count % 100 == 0:
            suffix = f" (×{count})" if count > 1 else ""
            getattr(logger, level)(message + suffix)

    def _parse_error(self, status_code, text):
        """Extract clean error message"""
        if status_code == 403 and "cloudfront" in text.lower():
            return "CloudFront blocked"
        elif status_code == 403:
            return "Forbidden"
        elif status_code == 401:
            return "Auth failed"
        elif status_code >= 500:
            return "Server error"
        return f"HTTP {status_code}"

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

        if r.status_code != 200:
            error = self._parse_error(r.status_code, r.text)
            raise Exception(f"{error}")

        r.raise_for_status()
        return r.json()

    def _get(self, path, params=None):
        """GET request - public endpoints typically don't need authentication"""
        r = self.session.get(
            BASE + path,
            params=params or {},
            timeout=10
        )

        if r.status_code != 200:
            error = self._parse_error(r.status_code, r.text)
            raise Exception(f"{error}")

        r.raise_for_status()
        return r.json()

    # --------------------------------------------------------
    # PUBLIC
    # --------------------------------------------------------
    def connect(self):
        pass

    def fetch_btc_last(self):
        try:
            r = self._get("/api/v1/spot/market/ticker", {"symbol": "BTCUSDT"})
            if r.get("code") == 0 and "data" in r:
                return float(r["data"]["last"])
            return 92000.0
        except Exception as e:
            self._smart_log("error", "btc_price", f"TAPBIT ERROR: BTC price - {str(e)}")
            return 92000.0

    def fetch_best_quotes(self):
        try:
            symbol = self.symbol.replace("/", "")
            r = self._get("/api/v1/spot/market/ticker", {"symbol": symbol})
            if r.get("code") == 0 and "data" in r:
                d = r["data"]
                return float(d["bid"]), float(d["ask"])
            return None, None
        except Exception as e:
            self._smart_log("error", "quotes", f"TAPBIT ERROR: Quotes - {str(e)}")
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
            return []
        except Exception as e:
            self._smart_log("error", "open_orders", f"TAPBIT ERROR: Open orders - {str(e)}")
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
                    logger.warning(f"Cancel failed: {oid}")
            except Exception as e:
                logger.warning(f"Cancel: {str(e)}")

    def create_limit(self, side, price, amount):
        if self.dry_run:
            logger.info(f"[DRY] {side.upper()} {amount} @ {price}")
            return "dry"

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
            if resp.get("code") == 0 and "data" in resp:
                order_id = str(resp["data"]["orderId"])
                logger.info(f"✓ TAPBIT {side.upper():4s} {amount:>8.0f} @ {price:.10f} [ID:{order_id}]")
                return order_id
            else:
                # Silent rejection (normal market making)
                return None
        except Exception as e:
            self._smart_log("error", "create_order", f"TAPBIT ERROR: Order - {str(e)}")
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