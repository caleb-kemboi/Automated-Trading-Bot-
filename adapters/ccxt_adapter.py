# adapters/ccxt.py
import os, math, logging
import ccxt
from .base import BaseAdapter
from config import SETTINGS

logger = logging.getLogger(__name__)

class CCXTAdapter(BaseAdapter):
    def __init__(self, cfg):
        super().__init__(cfg)
        init_args = {
            'apiKey': os.getenv(cfg.api_key_env),
            'secret': os.getenv(cfg.secret_env),
            'uid': os.getenv(cfg.uid_env, ""),
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'},
        }
        # Only include hostname if it's actually set
        if cfg.hostname_env:
            hostname = os.getenv(cfg.hostname_env)
            if hostname:
                init_args['hostname'] = hostname
        self.client = getattr(ccxt, cfg.id)(init_args)

    def connect(self):
        self.client.load_markets()
        if self.symbol not in self.client.symbols:
            raise RuntimeError(f"{self.symbol} not found on {self.exchange_name}")
        logger.info(f"Connected {self.exchange_name} — {self.symbol}")

    def fetch_btc_last(self): return float(self.client.fetch_ticker(self.btc_symbol)['last'])
    def fetch_best_quotes(self):
        t = self.client.fetch_ticker(self.symbol)
        return t.get('bid'), t.get('ask')

    def fetch_open_orders(self):
        if self.dry_run:
            return []
        return self.client.fetch_open_orders(self.symbol)

    def cancel_orders_by_ids(self, ids):
        if self.dry_run or not ids: return
        try:
            self.client.cancel_orders(list(ids), self.symbol)
        except:
            for oid in ids:
                try: self.client.cancel_order(oid, self.symbol)
                except: pass

    def create_limit(self, side, price, amount):
        price = self.price_to_precision(price)
        amount = self.amount_to_precision(amount)
        if self.dry_run:
            logger.info(f"[DRY] {self.exchange_name} {side.upper()} {amount} @ {price:.10f}")
            return "dry"
        params = {"postOnly": True}
        if self.exchange_name == "bitmart": params["timeInForce"] = "PO"
        o = self.client.create_order(self.symbol, "limit", side, amount, price, params)
        return str(o['id'])

    def price_to_precision(self, p): return float(self.client.price_to_precision(self.symbol, p))
    def amount_to_precision(self, a): return float(self.client.amount_to_precision(self.symbol, a))
    def get_limits(self): return self.client.markets[self.symbol]['limits']
    def get_steps(self):
        p = self.client.markets[self.symbol]['precision']
        return 10 ** -p['price'], 10 ** -p.get('amount', 0)