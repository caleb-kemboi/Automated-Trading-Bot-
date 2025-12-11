# config.py — Fully updated and corrected (December 11, 2025)
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
    # BTC peg: mid = BTC/USDT * reference_multiplier
    # Current market (Dec 11, 2025): OHO/USDT ≈ $0.00093–0.0012, BTC/USDT ≈ $90,000–93,000
    # Correct multiplier yields mid ≈ 0.00093–0.00105
    reference_multiplier: float = 1.05e-8

    # Random gap between each order (client requirement: e.g., between 0.000001 – 0.000002 OHO)
    gap_min: float = 0.000001
    gap_max: float = 0.000002

    # Random order depth per side (client requirement: between 15–20 orders on each side)
    depth_min: int = 15
    depth_max: int = 20

    # Random order size (client requirement: between 10,000 – 25,000 OHO)
    size_min: float = 10_000
    size_max: float = 25_000

    # Random refresh interval (client requirement: every 5–10 seconds)
    interval_min_s: float = 5
    interval_max_s: float = 10

    # Safety: distance from mid + maker guard
    min_spread_bps: float = 10.0    # Reduced to prevent over-shifting on tiny prices
    maker_guard_ticks: int = 3

SETTINGS = BotSettings()

EXCHANGES: List[ExchangeConfig] = [
    ExchangeConfig(
        id="bitmart",
        symbol="OHO/USDT",
        btc_symbol="BTC/USDT",
        enabled=True,
        dry_run=True,   # Test live on BitMart first (as client requested)
        api_key_env="BITMART_KEY",
        secret_env="BITMART_SECRET",
        uid_env="BITMART_UID",
        hostname_env="BITMART_HOSTNAME"
    ),
    ExchangeConfig(id="p2b", symbol="OHO/USDT", btc_symbol="BTC/USDT", enabled=False, dry_run=True, api_key_env="P2B_KEY", secret_env="P2B_SECRET"),
    ExchangeConfig(id="dextrade", symbol="OHOUSDT", btc_symbol="BTCUSDT", enabled=False, dry_run=True, api_key_env="DEXTRADE_KEY"),
    ExchangeConfig(id="biconomy", symbol="OHO_USDT", btc_symbol="BTC_USDT", enabled=False, dry_run=True, api_key_env="BICONOMY_KEY", secret_env="BICONOMY_SECRET"),
    ExchangeConfig(id="tapbit", symbol="OHOUSDT", btc_symbol="BTCUSDT", enabled=False, dry_run=True, api_key_env="TAPBIT_KEY", secret_env="TAPBIT_SECRET"),
]