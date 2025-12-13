import os, time, hmac, hashlib, json, logging, requests
from typing import List, Optional, Tuple

logger = logging.getLogger("adapters")
logger.setLevel(logging.INFO)


# ---------------- Batch Cancel Helper ---------------- #
class BatchCancelMixin:
    """
    Mixin class providing batch cancellation functionality for exchange adapters.

    Automatically handles:
    - Batching order IDs into safe chunks
    - Fallback to individual cancels if batch fails
    - Deduplication of order IDs
    - Logging of cancel operations
    """

    BATCH_SIZE = 50  # safe default for most APIs

    def _cancel_in_batches(self, order_ids: list, endpoint: str, payload_func, batch_size: int = None):
        """
        Generic batch cancel helper.

        Args:
            order_ids: list of order IDs to cancel
            endpoint: API path (e.g., "/spot/v2/batch_orders_cancel")
            payload_func: callable(batch_ids) -> dict payload
                         Example: lambda batch: {"symbol": "OHO_USDT", "order_ids": batch}
            batch_size: optional override for BATCH_SIZE (useful for exchanges with lower limits)

        The method automatically:
        1. Deduplicates order IDs
        2. Splits into batches of batch_size (or BATCH_SIZE)
        3. Attempts batch cancel via endpoint
        4. Falls back to individual cancels if batch fails
        5. Logs total cancelled count
        """
        if self.dry_run or not order_ids:
            return

        remaining = list({str(oid) for oid in order_ids})  # dedupe
        cancelled = 0
        chunk_size = batch_size or self.BATCH_SIZE

        # ---- Batch cancel ----
        for i in range(0, len(remaining), chunk_size):
            batch = remaining[i:i + chunk_size]
            try:
                payload = payload_func(batch)
                resp = self._request("POST", endpoint, data=payload)
                if resp.get("code") in (1000, "1000", 0):  # accept multiple success codes
                    cancelled += len(batch)
                else:
                    raise RuntimeError(f"Batch cancel rejected: {resp}")
            except Exception as e:
                logger.debug(f"{self.exchange_name} batch cancel failed: {e}, falling back to single cancels")
                # fallback to individual cancels
                for oid in batch:
                    try:
                        single_payload = payload_func([oid])
                        self._request("POST", endpoint, data=single_payload)
                        cancelled += 1
                    except Exception:
                        pass

        if cancelled > 0:
            logger.info(f"{self.exchange_name} cancelled {cancelled} stale orders")