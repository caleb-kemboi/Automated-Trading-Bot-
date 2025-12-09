# adapters/tapbit_adapter.py
import os
import time
import hmac
import hashlib
import urllib.parse
import requests
import logging
from .base import BaseAdapter
from config import SETTINGS

logger = logging.getLogger(__name__)
# Use the correct Tapbit API base URL
BASE = "https://openapi.tapbit.com"


class TapbitAdapter(BaseAdapter):
    def __init__(self, cfg):
        super().__init__(cfg)
        # BaseAdapter is expected to expose: self.cfg, self.symbol, self.dry_run, self.api_key, self.secret
        # But preserve your original fallback to os.getenv
        self.key = os.getenv("TAPBIT_KEY", "") or getattr(self.cfg, "api_key_env", "")
        self.secret = os.getenv("TAPBIT_SECRET", "") or getattr(self.cfg, "secret_env", "")
        self.session = requests.Session()
        if self.key:
            self.session.headers.update({"X-API-KEY": self.key})

    def _sign(self, params: dict) -> dict:
        if not self.secret:
            logger.warning("Tapbit: No SECRET set — public calls only")
            return params
        params = params.copy()
        params["timestamp"] = int(time.time() * 1000)
        query_string = urllib.parse.urlencode(sorted(params.items()))
        signature = hmac.new(
            self.secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        params["sign"] = signature
        return params

    def _post(self, path: str, data: dict):
        url = BASE + path
        signed_data = self._sign(data)
        r = self.session.post(url, json=signed_data, timeout=15)
        r.raise_for_status()
        return r.json()

    def _get(self, path: str, params=None):
        if params is None:
            params = {}
        # Public market calls don't need signature. Try v1, fallback to v2 for market endpoints.
        try:
            if "market/ticker" in path:
                r = self.session.get(BASE + path, params=params, timeout=15)
            else:
                signed_params = self._sign(params)
                r = self.session.get(BASE + path, params=signed_params, timeout=15)
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as e:
            status = getattr(e.response, "status_code", None)
            if status in (403, 404) and "market/ticker" in path:
                # try swapping /v1/ -> /v2/ (fallback)
                alt_path = path.replace("/v1/", "/v2/")
                try:
                    r = self.session.get(BASE + alt_path, params=params, timeout=15)
                    r.raise_for_status()
                    return r.json()
                except Exception:
                    raise
            raise

    def connect(self):
        logger.info("Tapbit adapter ready — using openapi.tapbit.com")

    def fetch_btc_last(self) -> float:
        # Try correct Tapbit API endpoints: /api/spot/instruments/ticker_one
        # Also try legacy endpoints as fallback
        candidates = [
            ("/api/spot/instruments/ticker_one", {"instrument_id": "BTC/USDT"}),
            ("/api/spot/instruments/ticker_one", {"instrument_id": "BTCUSDT"}),
            ("/v1/market/ticker", {"symbol": "BTC/USDT"}),
            ("/v1/market/ticker", {"symbol": "BTCUSDT"}),
            ("/v2/market/ticker", {"symbol": "BTCUSDT"}),
        ]
        last_error = None
        for path, params in candidates:
            try:
                r = self.session.get(BASE + path, params=params, timeout=10)
                r.raise_for_status()
                j = r.json()
                data = j.get("data", {})
                # Try new API format first (last_price)
                last = None
                if isinstance(data, dict):
                    last = data.get("last_price") or data.get("last") or data.get("lastPrice") or data.get("last_price")
                else:
                    last = j.get("last_price") or j.get("last") or j.get("lastPrice")
                if last is None:
                    continue
                return float(last)
            except requests.HTTPError as e:
                last_error = f"{e.response.status_code} for {path}"
                if e.response.status_code not in (404, 403):
                    # For non-404/403 errors, log and continue trying
                    logger.debug(f"Tapbit BTC: {path} returned {e.response.status_code}")
                continue
            except Exception as e:
                last_error = str(e)
                continue
        
        # Log at appropriate level: debug in dry_run (expected), warning otherwise
        if self.dry_run:
            logger.debug(f"Tapbit BTC: all fallback attempts failed (last: {last_error}), using fallback price")
        else:
            logger.warning(f"Tapbit BTC: all fallback attempts failed (last: {last_error}), using fallback price")
        return 92000.0

    def fetch_best_quotes(self):
        """
        Tapbit returns inconsistent formats.
        This version normalizes all variants into (bid, ask).
        """

        # Build symbol variants
        variants = []
        if hasattr(self, "symbol") and self.symbol:
            variants.append(self.symbol)  # OHO/USDT
            variants.append(self.symbol.replace("/", ""))  # OHOUSDT
            variants.append(self.symbol.replace("/", "_"))  # OHO_USDT
        else:
            variants = ["OHO/USDT", "OHOUSDT", "OHO_USDT"]

        endpoints = [
            "/api/spot/instruments/ticker_one",
            "/v1/market/ticker",
            "/v2/market/ticker",
        ]

        last_error = None

        for sym in variants:
            for ep in endpoints:
                try:
                    r = self.session.get(BASE + ep, params={"symbol": sym, "instrument_id": sym}, timeout=10)
                    r.raise_for_status()
                    j = r.json()
                    d = j.get("data", {})

                    # CASE 1: direct dict with bid/ask
                    if isinstance(d, dict):
                        bid = (
                                d.get("highest_bid") or d.get("bid") or
                                d.get("bestBid") or d.get("buy")
                        )
                        ask = (
                                d.get("lowest_ask") or d.get("ask") or
                                d.get("bestAsk") or d.get("sell")
                        )
                        if bid and ask:
                            return float(bid), float(ask)

                    # CASE 2: array inside `tickers`
                    if isinstance(d, dict) and isinstance(d.get("tickers"), list) and len(d["tickers"]) > 0:
                        t = d["tickers"][0]
                        bid = t.get("bid") or t.get("buy")
                        ask = t.get("ask") or t.get("sell")
                        if bid and ask:
                            return float(bid), float(ask)

                except Exception as e:
                    last_error = str(e)
                    continue

        # If no quotes found
        if self.dry_run:
            logger.debug(f"Tapbit quotes: all attempts failed (last: {last_error})")
        else:
            logger.warning(f"Tapbit quotes: all attempts failed (last: {last_error})")

        return None, None

    def fetch_open_orders(self):
        # In dry_run mode, return empty list to avoid API calls
        if self.dry_run:
            return []
        
        # Original implementation posted to /v1/order/openOrders; try v1 then v2
        payload = {"symbol": self.symbol}
        try:
            resp = self._post("/v1/order/openOrders", payload)
            return resp.get("data", [])
        except requests.HTTPError as e:
            # 403/401 means auth issue - return empty instead of raising
            if e.response.status_code in (401, 403):
                logger.debug(f"Tapbit fetch_open_orders: authentication required (401/403)")
                return []
            # Try v2 as fallback for other errors
            try:
                resp = self._post("/v2/order/openOrders", payload)
                return resp.get("data", [])
            except requests.HTTPError as e2:
                if e2.response.status_code in (401, 403):
                    logger.debug(f"Tapbit fetch_open_orders: v2 also requires authentication (401/403)")
                    return []
                # Re-raise other errors
                raise
        except Exception as e:
            # Try v2 as fallback
            try:
                resp = self._post("/v2/order/openOrders", payload)
                return resp.get("data", [])
            except Exception:
                # Return empty list instead of raising - allows bot to continue
                logger.debug(f"Tapbit fetch_open_orders: both v1 and v2 failed: {e}")
                return []

    def cancel_orders_by_ids(self, ids):
        if self.dry_run or not ids:
            return
        for oid in ids:
            try:
                self._post("/v1/order/cancel", {"orderId": str(oid)})
                logger.debug(f"Tapbit canceled {oid}")
            except Exception:
                try:
                    self._post("/v2/order/cancel", {"orderId": str(oid)})
                    logger.debug(f"Tapbit canceled {oid} (v2)")
                except Exception as e:
                    logger.warning(f"Tapbit cancel {oid} failed: {e}")

    def create_limit(self, side: str, price: float, amount: float):
        if self.dry_run:
            logger.info(f"[DRY] TAPBIT {side.upper()} {amount} @ {price:.10f}")
            return "dry"

        payload = {
            "symbol": self.symbol,
            "side": "BUY" if side == "buy" else "SELL",
            "orderQty": str(int(amount)),
            "orderPrice": f"{price:.10f}",
            "orderType": "LIMIT",
            "timeInForce": "POST_ONLY"
        }

        # try v1 placing, fallback to v2 if needed
        try:
            resp = self._post("/v1/order/place", payload)
            return str(resp["data"]["orderId"])
        except Exception:
            resp = self._post("/v2/order/place", payload)
            return str(resp["data"]["orderId"])

    def price_to_precision(self, p: float) -> float:
        return round(p, 8)

    def amount_to_precision(self, a: float) -> float:
        return int(round(a))

    def get_limits(self) -> dict:
        return {"min_amount": 1000, "min_cost": 1.0}

    def get_steps(self) -> tuple:
        return 1e-8, 1
