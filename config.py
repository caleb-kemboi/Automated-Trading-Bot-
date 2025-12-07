# config.py
from dataclasses import dataclass
from typing import List, Optional

@dataclass
class ExchangeConfig:
    id: str
    symbol: str
    btc_symbol: str
    enabled: bool = False
    dry_run: bool = True
    api_key_env: str = ""
    secret_env: str = ""
    uid_env: str = ""
    hostname_env: str = ""
    symbol_override: Optional[str] = None

@dataclass
class BotSettings:
    reference_multiplier: float = 0.000000011
    gap: float = 0.000001
    depth_min: int = 15
    depth_max: int = 20
    size_min: float = 10_000
    size_max: float = 25_000
    interval_min_s: float = 5
    interval_max_s: float = 10
    min_spread_bps: float = 5.0
    maker_guard_ticks: int = 2

SETTINGS = BotSettings()

EXCHANGES = [
    ExchangeConfig("bitmart",   "OHO/USDT", "BTC/USDT", True,  False, "BITMART_KEY", "BITMART_SECRET", "BITMART_UID", "BITMART_HOSTNAME"),
    ExchangeConfig("p2b",       "OHO/USDT",  "BTC/USDT", True, False,  "P2B_KEY",     "P2B_SECRET"),
    ExchangeConfig("dextrade",  "OHOUSDT",  "BTCUSDT",  True, False,  "DEXTRADE_KEY", "DEXTRADE_SECRET"),
    ExchangeConfig("biconomy",  "OHO_USDT", "BTC_USDT", True, False,  "BICONOMY_KEY", "BICONOMY_SECRET"),
    ExchangeConfig("tapbit",    "OHO/USDT", "BTC/USDT", True, False,  "TAPBIT_KEY",  "TAPBIT_SECRET"),
]