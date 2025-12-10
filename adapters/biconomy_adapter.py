# adapters/biconomy_adapter.py
import os
import time
import hashlib
import requests
import logging
from typing import Optional
from .base import BaseAdapter

logger = logging.getLogger(__name__)

BASE = "https://api.biconomy.com"


class BiconomyAdapter(BaseAdapter):
    def __init__(self, cfg):
        super().__init__(cfg)

        self.key = os.getenv("BICONOMY_KEY", "") or getattr(cfg, "api_key_env", "")
        self.secret = os.getenv("BICONOMY_SECRET", "") or getattr(cfg, "secret_env", "")

        self.session = requests.Session()

        if self.key:
            self.session.headers.update({
                "X-BB-APIKEY": self.key,
                "Content-Type": "application/x-www-form-urlencoded",  # Changed from application/json
                "X-SITE-ID": "127"
            })

    def _sign(self, params: dict) -> dict:
        """Sign parameters using MD5 with secret key"""
        if not self.secret:
            logger.warning("Biconomy: SECRET missing — cannot sign private requests.")
            return params

        params = params.copy()

        # Add api_key to params
        params["api_key"] = self.key

        # Sort parameters alphabetically
        sorted_params = sorted(params.items())

        # Create parameter string
        query = "&".join(f"{k}={v}" for k, v in sorted_params)

        # Add secret_key to the end
        sign_string = query + "&secret_key=" + self.secret

        # Generate MD5 signature (uppercase)
        signature = hashlib.md5(sign_string.encode()).hexdigest().upper()

        params["sign"] = signature
        return params

    def _get(self, path, params=None):
        r = self.session.get(BASE + path, params=params or {}, timeout=10)
        r.raise_for_status()
        return r.json()

    def _post(self, path, data: dict):
        """POST request using form-data format"""
        signed = self._sign(data)
        # Use data parameter instead of json for form-data
        r = self.session.post(BASE + path, data=signed, timeout=10)
        r.raise_for_status()
        return r.json()

    def connect(self):
        logger.info("Biconomy adapter ready")

    def fetch_btc_last(self):
        try:
            r = self._get("/api/v1/tickers")
            tickers = r.get("ticker", [])  # Note: key is "ticker" not "data"
            for t in tickers:
                symbol = t.get("symbol", "")
                if symbol in ("BTC_USDT", "BTC-USDT", "BTCUSDT"):
                    return float(t["last"])
        except Exception as e:
            logger.error(f"biconomy btc_last error: {e}")
        return 92000.0

    def fetch_best_quotes(self):
        try:
            r = self._get("/api/v1/tickers")
            tickers = r.get("ticker", [])  # Note: key is "ticker" not "data"

            variants = {
                self.symbol,
                self.symbol.replace("_", "-"),
                self.symbol.replace("-", "_"),
                self.symbol.replace("_", ""),
            }

            for t in tickers:
                if t.get("symbol") in variants:
                    bid = t.get("buy")  # "buy" field for bid
                    ask = t.get("sell")  # "sell" field for ask
                    if bid and ask:
                        return float(bid), float(ask)

        except Exception as e:
            logger.error(f"biconomy best quotes error: {e}")

        return None, None

    def fetch_open_orders(self):
        if self.dry_run:
            return []

        try:
            resp = self._post(
                "/api/v1/private/order/pending",  # Correct endpoint
                {
                    "market": self.symbol,
                    "offset": "0",
                    "limit": "100"
                }
            )
            result = resp.get("result", {})
            return result.get("records", [])
        except Exception as e:
            logger.error(f"Biconomy open orders error: {e}")
            return []

    def cancel_orders_by_ids(self, ids):
        if self.dry_run or not ids:
            return

        for oid in ids:
            try:
                self._post(
                    "/api/v1/private/trade/cancel",  # Correct endpoint
                    {
                        "market": self.symbol,
                        "order_id": str(oid)
                    }
                )
            except Exception as e:
                logger.warning(f"Biconomy cancel {oid} failed: {e}")

    def create_limit(self, side, price, amount):
        if self.dry_run:
            logger.info(f"[DRY] BICONOMY {side.upper()} {amount} @ {price}")
            return "dry"

        # Map side: "buy" -> 2 (BID), "sell" -> 1 (ASK)
        side_value = 2 if side.lower() == "buy" else 1

        payload = {
            "market": self.symbol,
            "side": str(side_value),  # 1=ASK (sell), 2=BID (buy)
            "amount": f"{amount}",
            "price": f"{price:.10f}"
        }

        try:
            resp = self._post("/api/v1/private/trade/limit", payload)  # Correct endpoint

            # Check response code
            if resp.get("code") != 0:
                logger.error(f"Biconomy order failed: {resp.get('message')}")
                return None

            result = resp.get("result", {})
            oid = result.get("id")
            return str(oid) if oid else None
        except Exception as e:
            logger.error(f"Biconomy create_limit error: {e}")
            return None

    def price_to_precision(self, p):
        return round(p, 8)

    def amount_to_precision(self, a):
        return int(a)

    def get_limits(self):
        return {"min_amount": 1, "min_cost": 1}

    def get_steps(self):
        return (1e-8, 1)