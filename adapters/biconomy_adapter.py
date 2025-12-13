# adapters/biconomy_adapter.py — FIXED for bot compatibility
import os
import time
import hashlib
import requests
import logging
from typing import Optional, List
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
            "X-SITE-ID": "127"
        })

        self._error_cache = {}

    def _sign(self, params: dict) -> dict:
        if not self.secret:
            return params
        p = params.copy()
        p["api_key"] = self.key
        p["time"] = str(int(time.time() * 1000))
        query = "&".join(f"{k}={v}" for k, v in sorted(p.items()))
        p["sign"] = hashlib.md5((query + self.secret).encode()).hexdigest().upper()
        return p

    def _post(self, path: str, data: dict):
        signed = self._sign(data)
        r = self.session.post(BASE + path, data=signed, timeout=12)
        r.raise_for_status()
        return r.json()

    def _smart_log(self, level, key, message):
        """Log with deduplication"""
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

    def create_limit(self, side: str, price: float, amount: float) -> Optional[str]:
        if self.dry_run:
            logger.info(f"[DRY] {side.upper()} {amount:>8.0f} @ {price:.10f}")
            return "dry"

        payload = {
            "market": self.symbol,
            "side": "2" if side.lower() == "buy" else "1",
            "amount": str(int(round(amount))),
            "price": f"{price:.10f}",
            "type": "1"
        }

        try:
            resp = self._post("/api/v1/private/order/create", payload)
            code = resp.get("code", -1)
            msg = resp.get("msg", "").lower()

            if code == 0:
                oid = resp.get("result", {}).get("order_id", "unknown")
                logger.info(f"✓ BICONOMY {side.upper():4s} {amount:>8.0f} @ {price:.10f} [ID:{oid}]")
                return str(oid)

            # Silent failures (normal market making)
            if any(x in msg for x in ["balance", "insufficient", "price", "accuracy", "amount", "post only"]):
                return None

            # Log important errors with deduplication
            if code == 10003 or "ip" in msg or "forbidden" in msg:
                self._smart_log("warning", "ip_whitelist", f"BICONOMY ERROR: {resp.get('msg', 'IP not whitelisted')}")
            elif code == 10004:
                self._smart_log("warning", "api_disabled", f"BICONOMY ERROR: {resp.get('msg', 'API disabled')}")

            return None

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 403:
                self._smart_log("warning", "http_403", "BICONOMY ERROR: HTTP 403 - IP not whitelisted")
            return None
        except Exception:
            return None

    def fetch_open_orders(self):
        """Fetch open orders in standardized format for bot compatibility"""
        if self.dry_run:
            return []
        try:
            r = self._post("/api/v1/private/order/pending", {
                "market": self.symbol,
                "offset": "0",
                "limit": "100"
            })
            records = r.get("result", {}).get("records", [])
            # Return standardized format: [{"id": "..."}, ...]
            return [{"id": str(order.get("id", order.get("order_id")))}
                    for order in records if order.get("id") or order.get("order_id")]
        except:
            return []

    def cancel_orders_by_ids(self, ids: List[str]):
        """Cancel specific orders by ID with proper logging"""
        if self.dry_run:
            logger.info(f"[DRY] BICONOMY cancel {len(ids)} orders")
            return

        if not ids:
            return

        cancelled = 0
        for oid in ids:
            try:
                resp = self._post("/api/v1/private/order/cancel", {
                    "market": self.symbol,
                    "order_id": str(oid)
                })
                if resp.get("code") == 0:
                    cancelled += 1
            except:
                pass

        if cancelled > 0:
            logger.info(f"BICONOMY cancelled {cancelled} stale orders")

    def cancel_all_orders(self):
        """Cancel all open orders"""
        if self.dry_run:
            logger.info(f"[DRY] BICONOMY cancel all")
            return

        orders = self.fetch_open_orders()
        if orders:
            ids = [o["id"] for o in orders]
            self.cancel_orders_by_ids(ids)

    def connect(self):
        logger.info("Biconomy adapter ready")

    def fetch_btc_last(self) -> float:
        try:
            r = requests.get(f"{BASE}/api/v1/tickers", timeout=10).json()
            for t in r.get("ticker", []):
                if t.get("symbol") in ("BTC_USDT", "BTC-USDT", "BTCUSDT"):
                    return float(t["last"])
        except:
            pass
        return 92000.0

    def fetch_best_quotes(self):
        try:
            r = requests.get(f"{BASE}/api/v1/tickers", timeout=10).json()
            for t in r.get("ticker", []):
                if t.get("symbol") in (self.symbol, self.symbol.replace("_", "")):
                    bid = t.get("buy")
                    ask = t.get("sell")
                    if bid and ask:
                        return float(bid), float(ask)
        except:
            pass
        return None, None

    def price_to_precision(self, p: float) -> float:
        return round(p, 8)

    def amount_to_precision(self, a: float) -> int:
        return int(round(a))

    def get_limits(self) -> dict:
        return {"min_amount": 1, "min_cost": 1.0}

    def get_steps(self) -> tuple:
        return 1e-8, 1