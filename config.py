# config.py
from dataclasses import dataclass
from typing import Optional, List


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

EXCHANGES: List[ExchangeConfig] = [
    ExchangeConfig(
        id="bitmart",
        symbol="OHO/USDT",
        btc_symbol="BTC/USDT",
        enabled=True,
        dry_run=False,
        api_key_env="BITMART_KEY",
        secret_env="BITMART_SECRET",
        uid_env="BITMART_UID",
        hostname_env="BITMART_HOSTNAME"
    ),
    ExchangeConfig(
        id="p2b",
        symbol="OHO/USDT",
        btc_symbol="BTC/USDT",
        enabled=True,
        dry_run=False,
        api_key_env="P2B_KEY",
        secret_env="P2B_SECRET"
    ),
    ExchangeConfig(
        id="dextrade",
        symbol="OHOUSDT",
        btc_symbol="BTCUSDT",
        enabled=True,
        dry_run=False,
        api_key_env="DEXTRADE_KEY",
        secret_env="DEXTRADE_SECRET"
    ),
    ExchangeConfig(
        id="biconomy",
        symbol="OHO_USDT",
        btc_symbol="BTC_USDT",
        enabled=True,
        dry_run=False,
        api_key_env="BICONOMY_KEY",
        secret_env="BICONOMY_SECRET"
    ),
    ExchangeConfig(
        id="tapbit",
        symbol="OHO/USDT",
        btc_symbol="BTC/USDT",
        enabled=True,
        dry_run=False,
        api_key_env="TAPBIT_KEY",
        secret_env="TAPBIT_SECRET"
    ),
]
