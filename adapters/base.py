# adapters/base.py
from __future__ import annotations
from typing import Dict, List, Sequence, Optional, Tuple
import math

class BaseAdapter:
    def __init__(self, cfg):
        self.cfg = cfg
        self.exchange_name = cfg.id
        self.symbol = getattr(cfg, 'symbol_override', None) or cfg.symbol
        self.btc_symbol = cfg.btc_symbol
        self.dry_run = cfg.dry_run
        self._market = None

    def connect(self) -> None: raise NotImplementedError
    def fetch_btc_last(self) -> float: raise NotImplementedError
    def get_precisions(self) -> Tuple[int, int]: raise NotImplementedError
    def get_limits(self) -> Dict[str, Optional[float]]: raise NotImplementedError
    def get_steps(self) -> Tuple[float, float]: raise NotImplementedError
    def fetch_balances(self, currencies: Sequence[str]) -> Dict[str, Dict[str, float]]: raise NotImplementedError
    def fetch_best_quotes(self) -> Tuple[Optional[float], Optional[float]]: raise NotImplementedError
    def fetch_open_orders(self) -> List[dict]: raise NotImplementedError
    def cancel_all(self) -> None: raise NotImplementedError
    def cancel_orders_by_ids(self, order_ids: Sequence[str]) -> None: raise NotImplementedError
    def create_limit(self, side: str, price: float, amount: float) -> Optional[str]: raise NotImplementedError
    def price_to_precision(self, px: float) -> float: raise NotImplementedError
    def amount_to_precision(self, amt: float) -> float: raise NotImplementedError
