# adapters/biconomy_adapter.py — FINAL WORKING VERSION (Dec 2025)
import os
import time
import hashlib
import requests
import logging
from typing import Optional, Tuple, List
from .base import BaseAdapter

logger = logging.getLogger(__name__)

BASE = "https://api.biconomy.com"


class BiconomyAdapter(BaseAdapter):
    def __init__(self, cfg):
        super().__init__(cfg)

        self.key = os.getenv("BICONOMY_KEY", "") or getattr(cfg, "api_key_env", "")
        self.secret = os.getenv("BICONOMY_SECRET", "") or getattr(cfg, "secret_env", "")

        self.session = requests.Session()
        self.session.headers.update({
            "X-BB-APIKEY": self.key,
            "Content-Type": "application/x-www-form-urlencoded",
            "X-SITE-ID": "127"          # Required for public tickers
        })

    # ------------------------------------------------------------------
    # SIGNING — Biconomy uses MD5 + api_key + secret_key + sorted params
    # ------------------------------------------------------------------
    def _sign(self, params: dict) -> dict:
        if not self.secret:
            logger.warning("Biconomy: SECRET missing")
            return params

        p = params.copy()
        p["api_key"] = self.key
        p["time"] = str(int(time.time() * 1000))  # Biconomy uses "time", not "timestamp"

        # Sort and build query string
        query = "&".join(f"{k}={v}" for k, v in sorted(p.items()))
        sign_string = query + self.secret
        signature = hashlib.md5(sign_string.encode()).hexdigest().upper()

        p["sign"] = signature
        return p

    # ------------------------------------------------------------------
    # HTTP HELPERS
    # ------------------------------------------------------------------
    def _get(self, path: str, params=None):
        r = self.session.get(BASE + path, params=params or {}, timeout=10)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, data: dict):
        signed = self._sign(data)
        r = self.session.post(BASE + path, data=signed, timeout=12)
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------
    # PUBLIC
    # ------------------------------------------------------------------
    def connect(self):
        logger.info("Biconomy adapter ready")

    def fetch_btc_last(self) -> float:
        try:
            r = self._get("/api/v1/tickers")
            for t in r.get("ticker", []):
                if t.get("symbol") in ("BTC_USDT", "BTC-USDT", "BTCUSDT"):
                    return float(t["last"])
        except Exception as e:
            logger.warning(f"Biconomy btc_last error: {e}")
        return 92000.0

    def fetch_best_quotes(self) -> Tuple[Optional[float], Optional[float]]:
        try:
            r = self._get("/api/v1/tickers")
            for t in r.get("ticker", []):
                if t.get("symbol") in (
                    self.symbol,
                    self.symbol.replace("_", "-"),
                    self.symbol.replace("-", "_"),
                    self.symbol.replace("_", ""),
                ):
                    bid = t.get("buy")
                    ask = t.get("sell")
                    if bid and ask:
                        return float(bid), float(ask)
        except Exception as e:
            logger.warning(f"Biconomy best quotes error: {e}")
        return None, None

    # ------------------------------------------------------------------
    # PRIVATE
    # ------------------------------------------------------------------
    def fetch_open_orders(self) -> List[dict]:
        if self.dry_run:
            return []
        try:
            resp = self._post("/api/v1/private/order/pending", {
                "market": self.symbol,
                "offset": "0",
                "limit": "100"
            })
            return resp.get("result", {}).get("records", [])
        except Exception as e:
            logger.debug(f"Biconomy open orders error: {e}")
            return []

    def cancel_orders_by_ids(self, ids: List[str]):
        if self.dry_run or not ids:
            return
        for oid in ids:
            try:
                self._post("/api/v1/private/order/cancel", {
                    "market": self.symbol,
                    "order_id": str(oid)
                })
            except Exception:
                pass  # silent

    def create_limit(self, side: str, price: float, amount: float) -> Optional[str]:
        if self.dry_run:
            logger.info(f"[DRY] BICONOMY {side.upper()} {amount:.0f} @ {price:.10f}")
            return "dry"

        # Biconomy: side = 1 (sell), 2 (buy)
        side_val = "2" if side.lower() == "buy" else "1"

        payload = {
            "market": self.symbol,           # e.g. OHO_USDT
            "side": side_val,
            "amount": f"{amount:.0f}",       # ← MUST BE INTEGER STRING (no decimals!)
            "price": f"{price:.10f}",
            "type": "1"                      # 1 = limit
        }

        try:
            resp = self._post("/api/v1/private/order/create", payload)

            if resp.get("code") != 0:
                logger.warning(f"Biconomy order failed: {resp.get('msg')}")
                return None

            oid = resp.get("result", {}).get("order_id") or resp.get("result", {}).get("id")
            if oid:
                logger.info(f"BICONOMY {side.upper()} {amount:.0f} @ {price:.10f}  id={oid}")
            return str(oid)

        except Exception as e:
            logger.warning(f"Biconomy create_limit error: {e}")
            return None

    # ------------------------------------------------------------------
    # PRECISION
    # ------------------------------------------------------------------
    def price_to_precision(self, p: float) -> float:
        return round(p, 8)

    def amount_to_precision(self, a: float) -> int:
        return int(round(a))

    def get_limits(self) -> dict:
        return {"min_amount": 1, "min_cost": 1.0}

    def get_steps(self) -> tuple:
        return 1e-8, 1