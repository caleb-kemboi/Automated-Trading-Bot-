# adapters/backtest.py
import math, random
from .base import BaseAdapter
from config import SETTINGS

class BacktestAdapter(BaseAdapter):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.price = 90000.0
        self.tick = 0
        self.orders = {}

    def connect(self): print("[BACKTEST] Ready")

    def fetch_btc_last(self):
        self.tick += 1
        self.price *= (1 + math.sin(self.tick / 12) * 0.0007)
        return round(self.price, 2)

    def fetch_best_quotes(self): return self.price * 0.9999, self.price * 1.0001
    def fetch_open_orders(self): return list(self.orders.values())

    def cancel_orders_by_ids(self, ids):
        for oid in ids:
            self.orders.pop(oid, None)

    def create_limit(self, side, price, amount):
        oid = f"bt-{len(self.orders)+1}"
        self.orders[oid] = {"id": oid, "side": side, "price": price, "amount": amount}
        print(f"[BACKTEST] {side.upper()} {amount} @ {price:.8f}")
        return oid

    def price_to_precision(self, p): return round(p, 8)
    def amount_to_precision(self, a): return int(round(a))
    def get_limits(self): return {"min_amount": 1000, "min_cost": 1}
    def get_steps(self): return 1e-8, 1