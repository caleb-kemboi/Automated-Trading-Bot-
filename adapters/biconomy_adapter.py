# adapters/biconomy_adapter.py
import os
import time
import hashlib
import requests
import logging
from typing import Optional
from .base import BaseAdapter
from config import SETTINGS

logger = logging.getLogger(__name__)
BASE = "https://api.biconomy.com"


class BiconomyAdapter(BaseAdapter):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.key = os.getenv("BICONOMY_KEY", "") or getattr(self.cfg, "api_key_env", "")
        self.secret = os.getenv("BICONOMY_SECRET", "") or getattr(self.cfg, "secret_env", "")
        self.session = requests.Session()
        # keep original header setup (safe even if key is empty)
        if self.key:
            self.session.headers.update({"X-BB-APIKEY": self.key, "X-SITE-ID": "127"})
        else:
            # still set site id for public calls if required
            self.session.headers.update({"X-SITE-ID": "127"})

    def _sign(self, params):
        if not self.secret:
            logger.warning("Biconomy: No SECRET set — skipping signature (public calls only)")
            return params
        params = params.copy()
        params["timestamp"] = str(int(time.time() * 1000))
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        params["signature"] = hashlib.md5((query + self.secret).encode()).hexdigest().lower()
        return params

    def _get(self, path, params=None):
        r = self.session.get(BASE + path, params=params or {}, timeout=10)
        r.raise_for_status()
        return r.json()

    def _post(self, path, data):
        data = self._sign(data)
        r = self.session.post(BASE + path, json=data, timeout=10)
        r.raise_for_status()
        return r.json()

    def connect(self):
        logger.info("Biconomy adapter ready")

    def fetch_btc_last(self):
        try:
            resp = self._get("/api/v1/tickers")
            data = resp.get("ticker", []) or []
            for t in data:
                sym = t.get("symbol", "")
                if sym in ("BTC_USDT", "BTC-USDT", "BTCUSDT"):
                    return float(t.get("last") or t.get("lastPrice") or t.get("last_price"))
            return 92000.0
        except Exception as e:
            logger.error(f"Biconomy BTC error: {e}")
            return 92000.0

    def fetch_best_quotes(self):
        try:
            resp = self._get("/api/v1/tickers")
            data = resp.get("ticker", []) or []
            # try multiple symbol formats
            variants = {
                self.symbol,
                (self.symbol.replace("_", "-") if self.symbol else ""),
                (self.symbol.replace("_", "") if self.symbol else ""),
                (self.symbol.replace("-", "_") if self.symbol else "")
            }
            for t in data:
                if t.get("symbol") in variants:
                    bid = t.get("buy") or t.get("bid")
                    ask = t.get("sell") or t.get("ask")
                    return float(bid), float(ask)
            return None, None
        except Exception as e:
            logger.error(f"Biconomy quotes error: {e}")
            return None, None

    def fetch_open_orders(self):
        # In dry_run mode, return empty list to avoid API calls
        if self.dry_run:
            return []
        
        # Try a few symbol variants to avoid 404 due to format mismatch.
        variants = []
        if hasattr(self, "symbol") and self.symbol:
            variants = [
                self.symbol,
                self.symbol.replace("_", "-"),
                self.symbol.replace("_", ""),
                self.symbol.replace("-", "_")
            ]
        else:
            variants = ["OHO_USDT", "OHO-USDT", "OHOUSDT", "OHOUSDT"]

        last_exc = None
        for sym in variants:
            try:
                resp = self._get("/api/v1/order/openOrders", {"symbol": sym})
                return resp.get("data", [])
            except requests.HTTPError as e:
                # 404 means endpoint doesn't exist or symbol not found - try next variant
                if e.response.status_code == 404:
                    last_exc = e
                    continue
                # Other HTTP errors (401, 403) are auth/permission issues - log and return empty
                elif e.response.status_code in (401, 403):
                    logger.debug(f"Biconomy fetch_open_orders: {e.response.status_code} - authentication/permission issue")
                    return []
                raise
            except Exception as e:
                last_exc = e
                continue
        
        # If all variants failed with 404, try without symbol parameter
        try:
            resp = self._get("/api/v1/order/openOrders")  # try without params
            return resp.get("data", [])
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                logger.debug(f"Biconomy fetch_open_orders: endpoint not found (404) - may require authentication or different endpoint")
            elif e.response.status_code in (401, 403):
                logger.debug(f"Biconomy fetch_open_orders: authentication required (401/403)")
            # Return empty list instead of raising in dry_run or when endpoint doesn't exist
            return []
        except Exception:
            # Return empty list for any other errors
            return []

    def cancel_orders_by_ids(self, ids):
        if self.dry_run or not ids:
            return
        for oid in ids:
            try:
                self._post("/api/v1/order/cancel", {"orderId": oid})
            except Exception as e:
                logger.warning(f"Biconomy cancel {oid} failed: {e}")

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

    def price_to_precision(self, p):
        return round(p, 8)

    def amount_to_precision(self, a):
        return int(round(a))

    def get_limits(self):
        return {"min_amount": 1000, "min_cost": 1}

    def get_steps(self):
        return 1e-8, 1
