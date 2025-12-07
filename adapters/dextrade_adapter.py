# adapters/dextrade.py
import os, requests
from .base import BaseAdapter
from config import SETTINGS
import logging

logger = logging.getLogger(__name__)
BASE = "https://api.dex-trade.com"

class DexTradeAdapter(BaseAdapter):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.token = os.getenv("DEXTRADE_KEY")
        self.session = requests.Session()

    def connect(self): logger.info("Dex-Trade adapter ready")

    def fetch_btc_last(self):
        r = self.session.get(f"{BASE}/v1/public/ticker?pair=BTCUSDT").json()
        return float(r["data"]["last"])

    def fetch_best_quotes(self):
        r = self.session.get(f"{BASE}/v1/public/ticker?pair={self.symbol.replace('/', '')}").json()
        d = r["data"]
        bid = float(d["bid_price"]) / 10 ** 8 if d.get("bid_price") else None
        ask = float(d["ask_price"]) / 10 ** 8 if d.get("ask_price") else None
        return bid, ask

    def fetch_open_orders(self):
        r = self.session.get(f"{BASE}/v1/orders/open", headers={"X-AUTH-TOKEN": self.token}).json()
        return r.get("data", [])

    # In DexTradeAdapter.cancel_orders_by_ids()
    def cancel_orders_by_ids(self, ids):
        if self.dry_run or not ids:
            return
        headers = {"X-AUTH-TOKEN": self.token}
        for oid in ids:
            try:
                r = self.session.post(f"{BASE}/v1/order/cancel", json={"order_id": oid}, headers=headers, timeout=10)
                if r.status_code != 200:
                    logger.debug(f"Dex-Trade cancel {oid} returned {r.status_code}")
            except Exception as e:
                logger.debug(f"Dex-Trade cancel {oid} failed: {e}")

    def create_limit(self, side, price, amount):
        if self.dry_run:
            logger.info(f"[DRY] DEXTRADE {side.upper()} {amount} @ {price:.10f}")
            return "dry"
        headers = {"X-AUTH-TOKEN": self.token}
        payload = {
            "pair": self.symbol,
            "type": side.upper(),
            "price": price,
            "amount": int(amount),
            "order_type": "limit"
        }
        r = self.session.post(f"{BASE}/v1/trade", json=payload, headers=headers).json()
        return str(r["order_id"])

    def price_to_precision(self, p): return round(p, 8)
    def amount_to_precision(self, a): return int(round(a))
    def get_limits(self): return {"min_amount": 1000, "min_cost": 1}
    def get_steps(self): return 1e-8, 1