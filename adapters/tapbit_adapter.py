# adapters/tapbit.py — FIXED for bot compatibility
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
    def __init__(self, cfg):
        super().__init__(cfg)
        self.key = os.getenv("TAPBIT_KEY", "") or getattr(cfg, "api_key_env", "")
        self.secret = os.getenv("TAPBIT_SECRET", "") or getattr(cfg, "secret_env", "")
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        self._error_cache = {}

    def _generate_signature(self, timestamp: str, method: str, path: str, body: str = ""):
        if not self.secret:
            return ""
        message = f"{timestamp}{method}{path}{body}"
        return hmac.new(
            self.secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

    def _get_headers(self, method: str, path: str, body: str = ""):
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
        now = time.time()
        if key not in self._error_cache:
            self._error_cache[key] = {"count": 0, "last_time": now}
        if now - self._error_cache[key]["last_time"] > 60:
            self._error_cache[key] = {"count": 0, "last_time": now}
        self._error_cache[key]["count"] += 1
        self._error_cache[key]["last_time"] = now
        count = self._error_cache[key]["count"]
        if count == 1 or count == 10 or count == 50 or count % 100 == 0:
            suffix = f" (×{count})" if count > 1 else ""
            getattr(logger, level)(message + suffix)

    def _parse_error(self, status_code, text):
        if status_code == 403 and "cloudfront" in text.lower():
            return "CloudFront blocked"
        elif status_code == 403:
            return "Forbidden"
        elif status_code == 401:
            return "Auth failed"
        elif status_code >= 500:
            return "Server error"
        return f"HTTP {status_code}"

    def _post(self, path, data):
        import json
        body = json.dumps(data) if data else ""
        headers = self._get_headers("POST", path, body)
        r = self.session.post(BASE + path, data=body, headers=headers, timeout=10)
        if r.status_code != 200:
            error = self._parse_error(r.status_code, r.text)
            raise Exception(f"{error}")
        r.raise_for_status()
        return r.json()

    def _get(self, path, params=None):
        r = self.session.get(BASE + path, params=params or {}, timeout=10)
        if r.status_code != 200:
            error = self._parse_error(r.status_code, r.text)
            raise Exception(f"{error}")
        r.raise_for_status()
        return r.json()

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

    def fetch_open_orders(self):
        """Fetch open orders in standardized format for bot compatibility"""
        if self.dry_run:
            return []
        try:
            resp = self._post("/api/v1/spot/open_order_list", {
                "symbol": self.symbol.replace("/", "")
            })
            if resp.get("code") == 0:
                orders = resp.get("data", [])
                # Return standardized format: [{"id": "..."}, ...]
                return [{"id": str(order.get("orderId"))}
                        for order in orders if order.get("orderId")]
            return []
        except Exception as e:
            self._smart_log("error", "open_orders", f"TAPBIT ERROR: Open orders - {str(e)}")
            return []

    def cancel_orders_by_ids(self, ids):
        """Cancel specific orders by ID with proper logging"""
        if self.dry_run:
            logger.info(f"[DRY] TAPBIT cancel {len(ids)} orders")
            return

        if not ids:
            return

        cancelled = 0
        for oid in ids:
            try:
                resp = self._post("/api/v1/spot/cancel_order", {
                    "orderId": str(oid),
                    "symbol": self.symbol.replace("/", "")
                })
                if resp.get("code") == 0:
                    cancelled += 1
            except Exception as e:
                logger.warning(f"Cancel: {str(e)}")

        if cancelled > 0:
            logger.info(f"TAPBIT cancelled {cancelled} stale orders")

    def cancel_all_orders(self):
        """Cancel all open orders"""
        if self.dry_run:
            logger.info(f"[DRY] TAPBIT cancel all")
            return

        orders = self.fetch_open_orders()
        if orders:
            ids = [o["id"] for o in orders]
            self.cancel_orders_by_ids(ids)

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
            return None
        except Exception as e:
            self._smart_log("error", "create_order", f"TAPBIT ERROR: Order - {str(e)}")
            return None

    def price_to_precision(self, p):
        return round(p, 8)

    def amount_to_precision(self, a):
        return int(a)

    def get_limits(self):
        return {"min_amount": 1, "min_cost": 1}

    def get_steps(self):
        return (1e-8, 1)