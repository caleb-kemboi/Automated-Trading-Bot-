# adapters/p2b_adapter.py — FULL UPDATED WITH cancel_all_orders
import os
import time
import hmac
import hashlib
import base64
import json
import requests
import logging
from typing import Optional, List, Tuple

from helpers.batch_cancel import BatchCancelMixin
from .base import BaseAdapter

logger = logging.getLogger(__name__)
BASE = "https://api.p2pb2b.com"


class P2BAdapter(BatchCancelMixin, BaseAdapter):
    def __init__(self, cfg):
        self.cfg = cfg
        self.exchange_name = cfg.id
        self.symbol = cfg.symbol
        self.btc_symbol = cfg.btc_symbol
        self.dry_run = cfg.dry_run

        self.key = os.getenv("P2B_KEY", "")
        self.secret = os.getenv("P2B_SECRET", "")
        self.session = requests.Session()

    # ---------------- Signing ---------------- #

    def _sign_request(self, endpoint: str, payload: dict) -> dict:
        payload = payload.copy()
        payload["request"] = endpoint
        payload["nonce"] = str(int(time.time() * 1000))

        payload_json = json.dumps(payload, separators=(",", ":"))
        payload_b64 = base64.b64encode(payload_json.encode()).decode()

        signature = hmac.new(
            self.secret.encode(),
            payload_b64.encode(),
            hashlib.sha512,
        ).hexdigest()

        return {
            "X-TXC-APIKEY": self.key,
            "X-TXC-PAYLOAD": payload_b64,
            "X-TXC-SIGNATURE": signature,
            "Content-Type": "application/json",
        }

    def _post(self, endpoint: str, data: dict):
        headers = self._sign_request(endpoint, data)
        r = self.session.post(
            BASE + endpoint,
            json=data,
            headers=headers,
            timeout=10,
        )
        r.raise_for_status()
        return r.json()

    # ---------------- Connection ---------------- #

    def connect(self):
        pass

    # ---------------- Market Data ---------------- #

    def fetch_btc_last(self) -> float:
        try:
            r = requests.get(
                BASE + "/api/v2/public/ticker",
                params={"market": "BTC_USDT"},
                timeout=10,
            )
            data = r.json()
            result = data.get("result") or {}

            if "last" in result:
                return float(result["last"])
            if isinstance(result.get("ticker"), dict):
                return float(result["ticker"].get("last", 92000.0))

        except Exception as e:
            logger.warning(f"p2b fetch_btc_last failed: {e}")

        return 92000.0

    def fetch_best_quotes(self) -> Tuple[Optional[float], Optional[float]]:
        try:
            r = requests.get(
                BASE + "/api/v2/public/ticker",
                params={"market": self.symbol.replace("/", "_")},
                timeout=10,
            )
            data = r.json()
            result = data.get("result") or {}

            if "bid" in result and "ask" in result:
                return float(result["bid"]), float(result["ask"])

            if isinstance(result.get("ticker"), dict):
                t = result["ticker"]
                return float(t.get("bid", 0)), float(t.get("ask", 0))

        except Exception as e:
            logger.warning(f"p2b fetch_best_quotes failed: {e}")

        return None, None

    # ---------------- Orders ---------------- #

    def fetch_open_orders(self) -> List[dict]:
        if self.dry_run:
            return []

        try:
            payload = {
                "market": self.symbol.replace("/", "_"),
                "offset": 0,
                "limit": 100,
            }
            r = self._post("/api/v2/orders", payload)

            if r.get("success"):
                records = r.get("result", {}).get("records", [])
                return [{"id": str(o["id"])} for o in records if o.get("id")]

        except Exception as e:
            logger.warning(f"p2b fetch_open_orders failed: {e}")

        return []

    def cancel_orders_by_ids(self, order_ids: List[str]):
        if self.dry_run or not order_ids:
            return

        def payload_func(batch):
            return [
                {
                    "market": self.symbol.replace("/", "_"),
                    "orderId": int(oid),
                }
                for oid in batch
            ]

        self._cancel_in_batches(
            order_ids,
            "/api/v2/order/cancel",
            payload_func,
        )

    # ---------------- CRITICAL FIX ---------------- #

    def cancel_all_orders(self):
        """
        Cancels ALL open P2B orders.
        Uses fetch_open_orders + batch cancel endpoint.
        """
        if self.dry_run:
            logger.info(f"[DRY] {self.exchange_name} skip cancel_all_orders()")
            return

        try:
            orders = self.fetch_open_orders()
            if not orders:
                logger.info(f"{self.exchange_name} no open orders to cancel")
                return

            order_ids = [o["id"] for o in orders if o.get("id")]
            if not order_ids:
                return

            logger.info(f"{self.exchange_name} cancelling {len(order_ids)} open orders")
            self.cancel_orders_by_ids(order_ids)

        except Exception as e:
            logger.warning(f"{self.exchange_name} cancel_all_orders failed: {e}")

    # ---------------- Placement ---------------- #

    def create_limit(self, side: str, price: float, amount: float) -> Optional[str]:
        if self.dry_run:
            return f"dry_{int(time.time() * 1_000_000)}"

        payload = {
            "market": self.symbol.replace("/", "_"),
            "side": side.lower(),
            "amount": f"{amount:.8f}",
            "price": f"{price:.10f}",
        }

        try:
            r = self._post("/api/v2/order/new", payload)
            if r.get("success"):
                oid = r.get("result", {}).get("orderId")
                if oid:
                    logger.info(
                        f"{self.exchange_name} {side.upper()} "
                        f"{amount:.0f} @ {price:.10f} id={oid}"
                    )
                    return str(oid)

        except Exception as e:
            logger.warning(f"p2b create_limit failed: {e}")

        return None

    # ---------------- Precision ---------------- #

    def price_to_precision(self, p):
        return round(p, 8)

    def amount_to_precision(self, a):
        return round(a, 8)

    def get_limits(self):
        return {"min_amount": 1e-8, "min_cost": 1.0}

    def get_steps(self):
        return 1e-8, 1e-8

    def get_precisions(self):
        return 8, 8
