# adapters/dextrade_adapter.py — FULL UPDATED (CANCEL FIXED)
import os
import logging
import time
from typing import Tuple, List, Optional
import requests

from helpers.batch_cancel import BatchCancelMixin
from .base import BaseAdapter

logger = logging.getLogger(__name__)

BASE = "https://api.dex-trade.com"


class DexTradeAdapter(BatchCancelMixin, BaseAdapter):
    def __init__(self, cfg):
        self.cfg = cfg
        self.exchange_name = cfg.id
        self.symbol = cfg.symbol
        self.btc_symbol = cfg.btc_symbol
        self.dry_run = cfg.dry_run

        self.token = os.getenv("DEXTRADE_KEY", "")
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        if self.token:
            self.session.headers["X-AUTH-TOKEN"] = self.token

    # ---------------- Helpers ---------------- #

    def _pair(self, symbol: str) -> str:
        return symbol.replace("/", "").replace("-", "").upper()

    # ---------------- Connection ---------------- #

    def connect(self):
        logger.info(f"Connected to Dex-Trade ({self.exchange_name})")

    # ---------------- Market Data ---------------- #

    def fetch_btc_last(self) -> float:
        try:
            r = self.session.get(
                f"{BASE}/v1/public/ticker",
                params={"pair": "BTCUSDT"},
                timeout=10,
            )
            r.raise_for_status()
            return float(r.json().get("last", 92000.0))
        except Exception as e:
            logger.warning(f"{self.exchange_name} fetch_btc_last failed: {e}")
            return 92000.0

    def fetch_best_quotes(self) -> Tuple[Optional[float], Optional[float]]:
        try:
            r = self.session.get(
                f"{BASE}/v1/public/ticker",
                params={"pair": self._pair(self.symbol)},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            return float(data.get("bid_price") or 0), float(data.get("ask_price") or 0)
        except Exception as e:
            logger.warning(f"{self.exchange_name} fetch_best_quotes failed: {e}")
            return None, None

    # ---------------- Orders ---------------- #

    def fetch_open_orders(self) -> List[dict]:
        if self.dry_run:
            return []

        try:
            r = self.session.get(f"{BASE}/v1/private/orders", timeout=10)
            r.raise_for_status()
            j = r.json()

            if j.get("status"):
                orders = j.get("data", {}).get("list", [])
                return [{"id": str(o.get("id"))} for o in orders if o.get("id")]

        except Exception as e:
            logger.warning(f"{self.exchange_name} fetch_open_orders failed: {e}")

        return []

    def cancel_orders_by_ids(self, order_ids: List[str]):
        if self.dry_run or not order_ids:
            return

        pair = self._pair(self.symbol)

        for oid in order_ids:
            for attempt in range(3):
                try:
                    payload = {
                        "order_id": str(oid),
                        "pair": pair,
                    }
                    r = self.session.post(
                        f"{BASE}/v1/private/delete-order",
                        json=payload,
                        timeout=10,
                    )
                    r.raise_for_status()
                    j = r.json()

                    if j.get("status"):
                        logger.info(f"{self.exchange_name} cancelled order {oid}")
                        break
                    else:
                        logger.warning(
                            f"{self.exchange_name} cancel failed for {oid}: {j}"
                        )

                except Exception as e:
                    logger.warning(
                        f"{self.exchange_name} cancel attempt {attempt + 1} failed "
                        f"for order {oid}: {e}"
                    )
                    time.sleep(0.5)

    # ---------------- CRITICAL FIX ---------------- #

    def cancel_all_orders(self):
        """
        Cancels ALL open Dex-Trade orders.
        Dex-Trade does not support true batch cancel — must cancel individually.
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

    # ---------------- Order Placement ---------------- #

    def create_limit(self, side: str, price: float, amount: float) -> Optional[str]:
        if self.dry_run:
            return f"dry_{int(time.time() * 1_000_000)}"

        payload = {
            "type_trade": 0,
            "type": 0 if side.lower() == "buy" else 1,
            "rate": f"{price:.10f}",
            "volume": f"{amount}",
            "pair": self._pair(self.symbol),
            "request_id": str(int(time.time() * 1000)),
        }

        try:
            r = self.session.post(
                f"{BASE}/v1/private/create-order",
                json=payload,
                timeout=15,
            )
            r.raise_for_status()
            j = r.json()

            if j.get("status"):
                oid = j.get("data", {}).get("id")
                if oid:
                    logger.info(
                        f"{self.exchange_name} {side.upper()} "
                        f"{amount:.0f} @ {price:.10f} id={oid}"
                    )
                    return str(oid)

        except Exception as e:
            logger.warning(f"{self.exchange_name} create_limit failed: {e}")

        return None

    # ---------------- Precision / Limits ---------------- #

    def price_to_precision(self, p):
        return round(p, 8)

    def amount_to_precision(self, a):
        return int(round(a))

    def get_limits(self):
        return {"min_amount": 1, "min_cost": 1.0}

    def get_steps(self):
        return 1e-8, 1

    def get_precisions(self):
        return 8, 0
