# adapters/ccxt_adapter.py
import ccxt
import os
import logging
from .base import BaseAdapter
from config import SETTINGS

logger = logging.getLogger(__name__)

class CCXTAdapter(BaseAdapter):
    def __init__(self, cfg):
        super().__init__(cfg)
        init_args = {
            'apiKey': os.getenv(cfg.api_key_env, ""),
            'secret': os.getenv(cfg.secret_env, ""),
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'},
            'timeout': 30000,
        }

        # BitMart: UID required
        if cfg.uid_env:
            uid = os.getenv(cfg.uid_env, "")
            if uid:
                init_args['uid'] = uid

        # BitMart: Correct hostname (critical!)
        if cfg.hostname_env:
            hostname = os.getenv(cfg.hostname_env)
            if hostname:
                init_args['hostname'] = hostname  # e.g. "api-cloud.bitmart.com"

        self.client = getattr(ccxt, cfg.id)(init_args)
        self._markets_loaded = False

    def connect(self):
        try:
            self.client.load_markets()
            self._markets_loaded = True

            if self.symbol not in self.client.symbols:
                raise RuntimeError(f"Symbol {self.symbol} not found on {self.exchange_name}")

            logger.info(f"Connected {self.exchange_name} — {self.symbol}")

        except Exception as e:
            # BitMart commonly fails load_markets() due to private endpoint auth requirements
            if "bitmart" in str(self.exchange_name).lower() or "401" in str(e) or "403" in str(e):
                logger.warning(f"{self.exchange_name}: load_markets failed (likely auth issue) — using limited mode")
                self._markets_loaded = False

                # Try verifying symbol using public ticker
                try:
                    self.client.fetch_ticker(self.symbol)
                    logger.info(f"Connected {self.exchange_name} — {self.symbol} (limited mode)")
                except Exception:
                    # BitMart frequently rejects ticker for new listings → skip failure
                    if self.exchange_name.lower() == "bitmart":
                        logger.warning(
                            f"{self.exchange_name}: ticker check skipped — BitMart often blocks public ticker for new tokens"
                        )
                        logger.info(f"Connected {self.exchange_name} — {self.symbol} (forced limited mode)")
                    else:
                        raise RuntimeError(f"Cannot verify symbol {self.symbol} on {self.exchange_name}")
            else:
                raise

    def fetch_btc_last(self) -> float:
        try:
            return float(self.client.fetch_ticker(self.btc_symbol)['last'])
        except Exception as e:
            logger.error(f"{self.exchange_name}: Failed to fetch BTC price: {e}")
            return 92000.0  # fallback

    def fetch_best_quotes(self):
        try:
            t = self.client.fetch_ticker(self.symbol)
            return t.get('bid'), t.get('ask')
        except Exception as e:
            logger.error(f"{self.exchange_name}: Failed to fetch quotes: {e}")
            return None, None

    def fetch_open_orders(self):
        if self.dry_run:
            return []
        try:
            return self.client.fetch_open_orders(self.symbol)
        except:
            return []

    def cancel_orders_by_ids(self, ids):
        if self.dry_run or not ids:
            return
        try:
            self.client.cancel_orders(list(ids), self.symbol)
        except:
            for oid in ids:
                try:
                    self.client.cancel_order(oid, self.symbol)
                except:
                    pass

    def create_limit(self, side, price, amount):
        price = self.price_to_precision(price)
        amount = self.amount_to_precision(amount)

        if self.dry_run:
            logger.info(f"[DRY] {self.exchange_name} {side.upper()} {amount} @ {price:.10f}")
            return "dry"

        params = {"postOnly": True}
        if self.exchange_name == "bitmart":
            params["timeInForce"] = "PO"

        try:
            o = self.client.create_order(self.symbol, "limit", side, amount, price, params)
            return str(o['id'])
        except Exception as e:
            logger.error(f"{self.exchange_name}: Order failed: {e}")
            return None

    def price_to_precision(self, p):
        if self._markets_loaded:
            return float(self.client.price_to_precision(self.symbol, p))
        return round(float(p), 8)

    def amount_to_precision(self, a):
        if self._markets_loaded:
            return float(self.client.amount_to_precision(self.symbol, a))
        return int(round(float(a)))

    def get_limits(self):
        if self._markets_loaded:
            return self.client.markets[self.symbol]['limits']
        return {"min_amount": 1000, "min_cost": 1.0}

    def get_steps(self):
        if self._markets_loaded:
            p = self.client.markets[self.symbol]['precision']
            return 10 ** -p['price'], 10 ** -p.get('amount', 0)
        return 1e-8, 1
