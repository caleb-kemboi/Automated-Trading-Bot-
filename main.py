# main.py
import os, time, random, signal, logging
from config import EXCHANGES, SETTINGS
from runner import run_once
from adapters.ccxt_adapter import CCXTAdapter
from adapters.biconomy_adapter import BiconomyAdapter
from adapters.tapbit_adapter import TapbitAdapter
from adapters.dextrade_adapter import DexTradeAdapter
from adapters.backtest_adapter import BacktestAdapter

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
)
logger = logging.getLogger("oho_bot")

RUNNING = True
def stop(*_): global RUNNING; RUNNING = False; logger.info("Shutting down...")
signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)

def build_adapter(cfg):
    adapters = {
        "bitmart": CCXTAdapter,
        "p2b": CCXTAdapter,
        "biconomy": BiconomyAdapter,
        "tapbit": TapbitAdapter,
        "dextrade": DexTradeAdapter,
        "backtest": BacktestAdapter,
    }
    return adapters[cfg.id](cfg)

def main():
    adapters = []
    for cfg in EXCHANGES:
        if not cfg.enabled: continue
        try:
            ad = build_adapter(cfg)
            ad.connect()
            adapters.append(ad)
        except Exception as e:
            logger.error(f"Failed {cfg.id}: {e}")

    prev_ids = {}
    while RUNNING:
        start = time.time()
        for ad in adapters:
            try:
                prev_ids[ad.exchange_name] = run_once(ad, prev_ids.get(ad.exchange_name))
            except Exception as e:
                logger.exception(f"Error on {ad.exchange_name}")
        sleep_time = random.uniform(SETTINGS.interval_min_s, SETTINGS.interval_max_s)
        time.sleep(max(0.1, sleep_time - (time.time() - start)))

    logger.info("Bot stopped cleanly.")

if __name__ == "__main__":
    main()