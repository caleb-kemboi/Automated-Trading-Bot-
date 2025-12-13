# config.py — CORRECTED with proper BitMart symbol format
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
    """
    Client Requirements (Original Specification):

    1) Reference price: Pegged to fixed ratio of BTC/USDT
       - Formula: mid_price = BTC/USDT * reference_multiplier
       - This is the EXACT MIDDLE between buy and sell orders

    2) Gap between orders: ABSOLUTE values in OHO units (NOT percentages)
       - Example: 0.000001 to 0.000002 OHO
       - Random gap chosen for each level

    3) Random order depth: Number of orders on each side
       - Example: 15-20 orders per side

    4) Random order size: Amount of OHO per order
       - Example: 10,000 to 25,000 OHO

    5) Random time interval: Seconds between refresh cycles
       - Example: 5-10 seconds

    CRITICAL: Never place sell orders below reference price OR buy orders above reference price
    """

    # 1) Reference price multiplier
    # Current market (Dec 11, 2025): OHO ≈ $0.00093-0.0012, BTC ≈ $90k-93k
    # Multiplier of 1.05e-8 yields mid ≈ 0.00093-0.00105
    reference_multiplier: float = 1.1e-8

    # 2) Gap between orders: ABSOLUTE OHO unit gaps
    gap_min: float = 0.000001  # Minimum gap (OHO units)
    gap_max: float = 0.000002  # Maximum gap (OHO units)

    # 3) Random order depth (orders per side)
    depth_min: int = 5   # Reduced from 15 to avoid balance issues
    depth_max: int = 10  # Reduced from 20

    # 4) Random order size (OHO per order)
    size_min: float = 5_000   # Reduced from 10,000
    size_max: float = 15_000  # Reduced from 25,000

    # 5) Random refresh interval (seconds)
    interval_min_s: float = 5  # Minimum seconds
    interval_max_s: float = 10  # Maximum seconds

    # Safety features (not in original requirements but recommended)
    maker_guard_ticks: int = 3  # Stay N ticks away from best bid/ask to avoid immediate fills


SETTINGS = BotSettings()

EXCHANGES: List[ExchangeConfig] = [
    ExchangeConfig(
        id="bitmart",
        symbol="OHO/USDT",
        btc_symbol="BTC/USDT",
        enabled=True,
        dry_run=False,  # Live mode
        api_key_env="BITMART_KEY",
        secret_env="BITMART_SECRET",
        uid_env="BITMART_UID",  # CRITICAL: Must be set!
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
        api_key_env="DEXTRADE_KEY"
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
        symbol="OHOUSDT",
        btc_symbol="BTCUSDT",
        enabled=True,
        dry_run=False,
        api_key_env="TAPBIT_KEY",
        secret_env="TAPBIT_SECRET"
    ),
]