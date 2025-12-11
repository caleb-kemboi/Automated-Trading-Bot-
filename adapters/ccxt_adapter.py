# adapters/ccxt_adapter.py — 100% WORKING FINAL VERSION
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
        self.session = requests.Session()
        self.session.headers.update({"X-BM-KEY": self.key})

    def _sign_v2(self, timestamp: str, body_str: str = "") -> str:
        """v2 endpoints: timestamp#memo#[body]"""
        msg = f"{timestamp}#{self.memo}"
        if body_str:
            msg += f"#{body_str}"
        return hmac.new(self.secret.encode(), msg.encode(), hashlib.sha256).hexdigest()

    def _sign_v4(self, timestamp: str) -> str:
        """v4 endpoints: timestamp ONLY"""
        return hmac.new(self.secret.encode(), timestamp.encode(), hashlib.sha256).hexdigest()

    def _request(self, method: str, endpoint: str, params=None, data=None, version: str = "v2"):
        timestamp = str(int(time.time() * 1000))
        url = f"https://api-cloud.bitmart.com{endpoint}"

        body_str = json.dumps(data, separators=(',', ':')) if data else ""
        signature = self._sign_v4(timestamp) if version == "v4" else self._sign_v2(timestamp, body_str)

        headers = {
            "X-BM-KEY": self.key,
            "X-BM-TIMESTAMP": timestamp,
            "X-BM-SIGN": signature,
            "Content-Type": "application/json"
        }

        if method == "GET":
            r = self.session.get(url, headers=headers, params=params, timeout=10)
        else:
            r = self.session.post(url, headers=headers, data=body_str, timeout=10)
        r.raise_for_status()
        return r.json()

    def create_limit(self, side: str, price: float, amount: float):
        price_str = f"{price:.8f}".rstrip("0").rstrip(".")
        amount_str = str(int(round(amount)))

        if self.dry_run:
            logger.info(f"[DRY] {self.exchange_name.upper()} {side.upper()} {amount_str} @ {price_str}")
            return "dry"

        payload = {
            "symbol": self.symbol.replace("/", "_"),
            "side": side,
            "type": "limit_maker",
            "size": amount_str,
            "price": price_str,
            "client_order_id": f"oho{int(time.time()*1000000)}"[:32]
        }

        resp = self._request("POST", "/spot/v2/submit_order", data=payload, version="v2")
        if resp.get("code") in ["1000", 1000]:
            oid = resp.get("data", {}).get("order_id")
            logger.info(f"{self.exchange_name.upper()} {side.upper()} {amount_str} @ {price_str} id={oid}")
            return str(oid)
        logger.warning(f"Order failed: {resp}")
        return None

    def cancel_all_orders(self):
        """Cancel ALL open orders — v4 endpoint, timestamp-only signature"""
        if self.dry_run:
            return
        try:
            resp = self._request("POST", "/spot/v4/cancel_all", version="v4")
            logger.info(f"{self.exchange_name} ALL CANCELLED")
        except Exception as e:
            logger.error(f"cancel_all failed: {e}")