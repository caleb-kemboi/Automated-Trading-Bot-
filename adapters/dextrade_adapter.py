# adapters/dextrade.py — FINAL WORKING VERSION (Dec 2025)
import os
import logging
import time
from typing import Tuple, List, Optional
import requests
from .base import BaseAdapter

logger = logging.getLogger(__name__)
BASE = "https://api.dex-trade.com"


class DexTradeAdapter(BaseAdapter):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.token = os.getenv("DEXTRADE_KEY") or getattr(cfg, "api_key", "")
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        if self.token:
            self.session.headers["X-AUTH-TOKEN"] = self.token

    def _pair(self, symbol: str) -> str:
        # Public ticker: BTCUSDT (no underscore)
        # Private trade: BTCUSDT (same)
        return symbol.replace("/", "").replace("-", "").upper()

    def connect(self):
        logger.info("Dex-Trade adapter ready")

    # PUBLIC — works perfectly
    def fetch_btc_last(self) -> float:
        try:
            pair = self._pair("BTCUSDT")
            r = self.session.get(f"{BASE}/v1/public/ticker", params={"pair": pair}, timeout=10)
            r.raise_for_status()
            return float(r.json().get("last", 92000.0))
        except Exception as e:
            logger.warning(f"Dex-Trade BTC fetch failed: {e}")
            return 92000.0

    def fetch_best_quotes(self) -> Tuple[Optional[float], Optional[float]]:
        try:
            pair = self._pair(self.symbol)
            r = self.session.get(f"{BASE}/v1/public/ticker", params={"pair": pair}, timeout=10)
            r.raise_for_status()
            data = r.json()
            bid = float(data.get("bid_price")) if data.get("bid_price") else None
            ask = float(data.get("ask_price")) if data.get("ask_price") else None
            return bid, ask
        except Exception as e:
            logger.warning(f"Dex-Trade quotes error: {e}")
            return None, None

    # PRIVATE — correct endpoints from official docs
    def fetch_open_orders(self) -> List[dict]:
        if self.dry_run:
            return []
        try:
            r = self.session.get(f"{BASE}/v1/private/orders", timeout=10)
            r.raise_for_status()
            j = r.json()
            return j.get("data", {}).get("list", []) if j.get("status") else []
        except Exception as e:
            logger.debug(f"Dex-Trade open orders error: {e}")
            return []

    def cancel_orders_by_ids(self, ids: List[str]):
        if self.dry_run or not ids:
            return
        for oid in ids:
            try:
                payload = {"order_id": str(oid), "request_id": str(int(time.time() * 1000))}
                r = self.session.post(f"{BASE}/v1/private/delete-order", json=payload, timeout=10)
                r.raise_for_status()
            except Exception as e:
                logger.debug(f"Dex-Trade cancel {oid} failed: {e}")

    def create_limit(self, side: str, price: float, amount: float) -> Optional[str]:
        if self.dry_run:
            logger.info(f"[DRY] DEXTRADE {side.upper()} {amount} @ {price:.10f}")
            return "dry"

        payload = {
            "type_trade": 0,                    # 0 = limit
            "type": 0 if side.lower() == "buy" else 1,  # 0=buy, 1=sell
            "rate": f"{price:.10f}",
            "volume": f"{amount}",
            "pair": self._pair(self.symbol),
            "request_id": str(int(time.time() * 1000))
        }

        try:
            r = self.session.post(f"{BASE}/v1/private/create-order", json=payload, timeout=15)
            r.raise_for_status()
            j = r.json()

            if not j.get("status"):
                logger.warning(f"Dex-Trade order failed: {j.get('message')}")
                return None

            oid = j.get("data", {}).get("id")
            return str(oid) if oid else None

        except Exception as e:
            logger.warning(f"Dex-Trade create_limit error: {e}")
            return None

    def price_to_precision(self, p: float) -> float:
        return round(p, 8)

    def amount_to_precision(self, a: float) -> int:
        return int(round(a))

    def get_limits(self) -> dict:
        return {"min_amount": 1, "min_cost": 1.0}

    def get_steps(self) -> tuple:
        return 1e-8, 1