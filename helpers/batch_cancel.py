import os, time, hmac, hashlib, json, logging, requests
from typing import List, Optional, Tuple

logger = logging.getLogger("adapters")
logger.setLevel(logging.INFO)


# ---------------- Batch Cancel Helper ---------------- #
class BatchCancelMixin:
    BATCH_SIZE = 50  # safe default for most APIs

    def _cancel_in_batches(self, order_ids: list, endpoint: str, payload_func):
        """
        Generic batch cancel helper.

        Args:
            order_ids: list of order IDs to cancel
            endpoint: API path
            payload_func: callable(batch_ids) -> dict payload
        """
        if self.dry_run or not order_ids:
            return

        remaining = list({str(oid) for oid in order_ids})  # dedupe
        cancelled = 0

        # ---- Batch cancel ----
        for i in range(0, len(remaining), self.BATCH_SIZE):
            batch = remaining[i:i + self.BATCH_SIZE]
            try:
                payload = payload_func(batch)
                resp = self._request("POST", endpoint, data=payload)
                if resp.get("code") in (1000, "1000", 0):  # accept multiple success codes
                    cancelled += len(batch)
                else:
                    raise RuntimeError(f"Batch cancel rejected: {resp}")
            except Exception as e:
                logger.debug(f"{self.exchange_name} batch cancel failed: {e}, falling back to single cancels")
                # fallback
                for oid in batch:
                    try:
                        single_payload = payload_func([oid])
                        self._request("POST", endpoint, data=single_payload)
                        cancelled += 1
                    except Exception:
                        pass

        if cancelled > 0:
            logger.info(f"{self.exchange_name} cancelled {cancelled} stale orders")
