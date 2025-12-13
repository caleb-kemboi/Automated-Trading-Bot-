# main.py
import os
import time
import random
import signal
import logging

from dotenv import load_dotenv
load_dotenv()

from config import EXCHANGES, SETTINGS
from runner import run_once
from adapters.bitmart_adapter import BitMartAdapter
from adapters.biconomy_adapter import BiconomyAdapter
from adapters.tapbit_adapter import TapbitAdapter
from adapters.dextrade_adapter import DexTradeAdapter
from adapters.p2b_adapter import P2BAdapter
from adapters.backtest_adapter import BacktestAdapter

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
)
logger = logging.getLogger("oho_bot")

RUNNING = True


def stop(*_):
    global RUNNING
    RUNNING = False
    logger.info("Shutting down...")


signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)


def build_adapter(cfg):
    adapters = {
        "bitmart": BitMartAdapter,
        "p2b": P2BAdapter,
        "biconomy": BiconomyAdapter,
        "tapbit": TapbitAdapter,
        "dextrade": DexTradeAdapter,
        "backtest": BacktestAdapter,
    }
    return adapters[cfg.id](cfg)


def main():
    adapters = []

    for cfg in EXCHANGES:
        if not cfg.enabled:
            continue

        logger.debug(f"Initializing {cfg.id} adapter...")

        try:
            ad = build_adapter(cfg)
            ad.connect()          # try connecting first
            adapters.append(ad)   # add on success

        except Exception as e:
            error_msg = str(e)
            if len(error_msg) > 200:
                error_msg = error_msg[:200] + "..."

            if cfg.dry_run:
                # In dry mode → still add adapter
                logger.info(f"{cfg.id}: connect failed ({error_msg}) — continuing in dry-run mode")
                adapters.append(ad)
                continue
            else:
                logger.warning(f"{cfg.id}: connect failed ({error_msg}) — skipping this exchange")
                logger.debug(f"Full error:", exc_info=True)
                continue

    prev_ids = {}

    while RUNNING:
        start = time.time()

        for ad in adapters:
            key = ad.exchange_name   # safer unique identifier
            try:
                prev_ids[key] = run_once(ad, prev_ids.get(key))
            except Exception:
                logger.exception(f"Error on {ad.exchange_name}")

        sleep_time = random.uniform(SETTINGS.interval_min_s, SETTINGS.interval_max_s)
        time.sleep(max(0.1, sleep_time - (time.time() - start)))

    logger.info("Bot stopped cleanly.")


if __name__ == "__main__":
    main()
