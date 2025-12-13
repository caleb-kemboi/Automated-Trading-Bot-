# adapters/p2b_adapter.py — P2B Exchange Adapter (P2PB2B)
import os
import time
import hmac
import hashlib
import base64
import json
import requests
import logging
from typing import Optional, List, Tuple
from .base import BaseAdapter

logger = logging.getLogger(__name__)
BASE = "https://api.p2pb2b.com"


class P2BAdapter(BaseAdapter):
    """
    P2B (P2PB2B) Exchange Adapter

    Authentication uses:
    - X-TXC-APIKEY: API key
    - X-TXC-PAYLOAD: base64(json body)
    - X-TXC-SIGNATURE: HMAC-SHA512(payload, secret)

    Docs: https://github.com/P2B-team/p2b-api-docs
    """

    def __init__(self, cfg):
        super().__init__(cfg)
        self.key = os.getenv("P2B_KEY", "") or getattr(cfg, "api_key_env", "")
        self.secret = os.getenv("P2B_SECRET", "") or getattr(cfg, "secret_env", "")
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def _sign_request(self, endpoint: str, payload: dict) -> dict:
        """Generate authentication headers for P2B API"""
        # Add required fields
        payload["request"] = endpoint
        payload["nonce"] = str(int(time.time() * 1000))

        # Encode payload to base64
        payload_json = json.dumps(payload, separators=(',', ':'))
        payload_b64 = base64.b64encode(payload_json.encode()).decode()

        # Generate signature: HMAC-SHA512(payload_b64, secret)
        signature = hmac.new(
            self.secret.encode(),
            payload_b64.encode(),
            hashlib.sha512
        ).hexdigest()

        return {
            "X-TXC-APIKEY": self.key,
            "X-TXC-PAYLOAD": payload_b64,
            "X-TXC-SIGNATURE": signature
        }

    def _post(self, endpoint: str, data: dict):
        """POST request to private endpoint"""
        headers = self._sign_request(endpoint, data.copy())
        headers["Content-Type"] = "application/json"

        r = self.session.post(
            BASE + endpoint,
            json=data,
            headers=headers,
            timeout=10
        )
        r.raise_for_status()
        return r.json()

    def _get(self, endpoint: str, params: dict = None):
        """GET request to public endpoint"""
        r = self.session.get(BASE + endpoint, params=params or {}, timeout=10)
        r.raise_for_status()
        return r.json()

    # ============== PUBLIC ENDPOINTS ==============

    def connect(self):
        logger.info("P2B adapter ready")

    def fetch_btc_last(self) -> float:
        try:
            r = self._get("/api/v2/public/ticker", {"market": "BTC_USDT"})
            if r.get("success"):
                return float(r["result"]["ticker"]["last"])
            return 92000.0
        except Exception as e:
            logger.warning(f"P2B BTC fetch failed: {e}")
            return 92000.0

    def fetch_best_quotes(self) -> Tuple[Optional[float], Optional[float]]:
        try:
            # P2B uses underscore format: OHO_USDT
            market = self.symbol.replace("/", "_")
            r = self._get("/api/v2/public/ticker", {"market": market})

            if r.get("success"):
                ticker = r["result"]["ticker"]
                bid = float(ticker.get("bid")) if ticker.get("bid") else None
                ask = float(ticker.get("ask")) if ticker.get("ask") else None
                return bid, ask
            return None, None
        except Exception as e:
            logger.warning(f"P2B quotes error: {e}")
            return None, None

    # ============== PRIVATE ENDPOINTS ==============

    def fetch_open_orders(self) -> List[dict]:
        """Fetch open orders in standardized format"""
        if self.dry_run:
            return []
        try:
            market = self.symbol.replace("/", "_")
            payload = {
                "market": market,
                "offset": 0,
                "limit": 100
            }

            r = self._post("/api/v2/orders", payload)

            if r.get("success"):
                orders = r.get("result", {}).get("records", [])
                # Return standardized format: [{"id": "..."}, ...]
                return [{"id": str(order.get("id"))} for order in orders if order.get("id")]
            return []

        except Exception as e:
            logger.debug(f"P2B fetch orders error: {e}")
            return []

    def cancel_orders_by_ids(self, ids: List[str]):
        """Cancel specific orders by ID"""
        if self.dry_run:
            logger.info(f"[DRY] P2B cancel {len(ids)} orders")
            return

        if not ids:
            return

        market = self.symbol.replace("/", "_")
        cancelled = 0

        for oid in ids:
            try:
                payload = {
                    "market": market,
                    "orderId": int(oid)
                }
                r = self._post("/api/v2/order/cancel", payload)

                if r.get("success"):
                    cancelled += 1

            except Exception as e:
                logger.debug(f"P2B cancel {oid} failed: {e}")

        if cancelled > 0:
            logger.info(f"P2B cancelled {cancelled} stale orders")

    def cancel_all_orders(self):
        """Cancel all open orders"""
        if self.dry_run:
            logger.info(f"[DRY] P2B cancel all")
            return

        orders = self.fetch_open_orders()
        if orders:
            ids = [o["id"] for o in orders]
            self.cancel_orders_by_ids(ids)
            logger.info(f"P2B full cleanup complete")

    def create_limit(self, side: str, price: float, amount: float) -> Optional[str]:
        if self.dry_run:
            logger.info(f"[DRY] P2B {side.upper()} {amount} @ {price:.10f}")
            return "dry"

        market = self.symbol.replace("/", "_")

        payload = {
            "market": market,
            "side": side.lower(),  # 'buy' or 'sell'
            "amount": f"{amount:.8f}",
            "price": f"{price:.10f}"
        }

        try:
            r = self._post("/api/v2/order/new", payload)

            if r.get("success"):
                order_id = r.get("result", {}).get("orderId")
                if order_id:
                    logger.info(f"P2B {side.upper():4s} {amount:>8.0f} @ {price:.10f} id={order_id}")
                    return str(order_id)
            else:
                # Log rejection for debugging
                msg = r.get("message", "Unknown error")
                logger.debug(f"P2B order rejected: {msg}")

            return None

        except Exception as e:
            logger.warning(f"P2B create_limit error: {e}")
            return None

    # ============== PRECISION & LIMITS ==============

    def price_to_precision(self, p: float) -> float:
        return round(p, 8)

    def amount_to_precision(self, a: float) -> float:
        return round(a, 8)

    def get_limits(self) -> dict:
        return {"min_amount": 0.00000001, "min_cost": 1.0}

    def get_steps(self) -> tuple:
        return 1e-8, 1e-8