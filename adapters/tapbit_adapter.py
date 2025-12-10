import os
import time
import hmac
import hashlib
import requests
import logging
from .base import BaseAdapter

logger = logging.getLogger(__name__)

BASE = "https://openapi.tapbit.com"


class TapbitAdapter(BaseAdapter):
    """
    Tapbit Spot API adapter with comprehensive logging.

    Authentication:
        - ACCESS-KEY: API key
        - ACCESS-SIGN: HMAC-SHA256 signature
        - ACCESS-TIMESTAMP: Unix timestamp in milliseconds

    Signature format: timestamp + method + path + ?queryString + body
    """

    def __init__(self, cfg):
        super().__init__(cfg)

        self.key = os.getenv("TAPBIT_KEY", "") or getattr(cfg, "api_key_env", "")
        self.secret = os.getenv("TAPBIT_SECRET", "") or getattr(cfg, "secret_env", "")

        # Log configuration status (without exposing secrets)
        if self.key and self.secret:
            logger.info(
                f"Tapbit adapter initialized | API Key: {self.key[:8]}...{self.key[-4:]} | Symbol: {getattr(cfg, 'symbol', 'N/A')}")
        else:
            logger.warning("Tapbit adapter initialized without credentials | Running in limited mode")

        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "OHO-Bot/1.0"
        })

        # Request counters for monitoring
        self._request_count = {"GET": 0, "POST": 0, "errors": 0}
        self._last_request_time = None

    # --------------------------------------------------------
    # SIGNING
    # --------------------------------------------------------
    def _generate_signature(self, timestamp: str, method: str, path: str, query_string: str = "", body: str = ""):
        """
        Generate HMAC-SHA256 signature for Tapbit API.

        Format: timestamp + method + path + ?queryString + body

        Args:
            timestamp: Unix timestamp in milliseconds
            method: HTTP method (GET, POST)
            path: API endpoint path
            query_string: URL query parameters (without leading ?)
            body: JSON request body

        Returns:
            Hexadecimal signature string
        """
        if not self.secret:
            logger.debug("No secret configured, skipping signature generation")
            return ""

        # Build signature message
        message = f"{timestamp}{method}{path}"

        if query_string:
            message += f"?{query_string}"

        if body:
            message += body

        # Log signature details (without exposing secret)
        logger.debug(
            f"Signature input | Method: {method} | Path: {path} | QueryLen: {len(query_string)} | BodyLen: {len(body)}")

        # Generate HMAC-SHA256 signature
        signature = hmac.new(
            self.secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        return signature

    def _get_headers(self, method: str, path: str, query_string: str = "", body: str = ""):
        """
        Generate authentication headers for API request.

        Returns:
            Dictionary of HTTP headers including authentication
        """
        timestamp = str(int(time.time() * 1000))

        headers = {
            "ACCESS-KEY": self.key,
            "ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json"
        }

        if self.key and self.secret:
            signature = self._generate_signature(timestamp, method, path, query_string, body)
            headers["ACCESS-SIGN"] = signature
            logger.debug(f"Auth headers generated | Timestamp: {timestamp} | Signature: {signature[:16]}...")
        else:
            logger.debug("No credentials, sending unauthenticated request")

        return headers

    # --------------------------------------------------------
    # HTTP
    # --------------------------------------------------------
    def _post(self, path, data):
        """
        Execute authenticated POST request.

        Args:
            path: API endpoint path
            data: Request payload dictionary

        Returns:
            JSON response dictionary

        Raises:
            HTTPError: On non-200 status codes
        """
        import json

        self._request_count["POST"] += 1
        request_id = f"POST-{self._request_count['POST']}"

        body = json.dumps(data) if data else ""
        headers = self._get_headers("POST", path, "", body)

        logger.info(f"[{request_id}] POST {path} | Payload keys: {list(data.keys()) if data else []}")

        start_time = time.time()

        try:
            r = self.session.post(
                BASE + path,
                data=body,
                headers=headers,
                timeout=10
            )

            elapsed = (time.time() - start_time) * 1000
            self._last_request_time = time.time()

            logger.info(
                f"[{request_id}] Response: {r.status_code} | Time: {elapsed:.0f}ms | Size: {len(r.content)} bytes")

            if r.status_code != 200:
                self._request_count["errors"] += 1
                logger.error(f"[{request_id}] HTTP {r.status_code} | Response: {r.text[:500]}")
                logger.error(f"[{request_id}] Request headers: {dict(r.request.headers)}")
                logger.error(f"[{request_id}] Request body: {body[:500]}")

            r.raise_for_status()

            response_data = r.json()

            # Log API-level errors
            if response_data.get("code") != 0:
                logger.error(
                    f"[{request_id}] API Error | Code: {response_data.get('code')} | Message: {response_data.get('msg', 'N/A')}")
            else:
                logger.debug(f"[{request_id}] API Success | Response keys: {list(response_data.keys())}")

            return response_data

        except requests.exceptions.Timeout:
            self._request_count["errors"] += 1
            logger.error(f"[{request_id}] Request timeout after 10s")
            raise
        except requests.exceptions.ConnectionError as e:
            self._request_count["errors"] += 1
            logger.error(f"[{request_id}] Connection error: {str(e)}")
            raise
        except requests.exceptions.HTTPError as e:
            # Already logged above
            raise
        except Exception as e:
            self._request_count["errors"] += 1
            logger.error(f"[{request_id}] Unexpected error: {type(e).__name__} - {str(e)}")
            raise

    def _get(self, path, params=None):
        """
        Execute GET request (public or authenticated).

        Args:
            path: API endpoint path
            params: Query parameters dictionary

        Returns:
            JSON response dictionary

        Raises:
            HTTPError: On non-200 status codes
        """
        self._request_count["GET"] += 1
        request_id = f"GET-{self._request_count['GET']}"

        # Build query string for signature
        query_string = ""
        if params:
            sorted_params = sorted(params.items())
            query_string = "&".join([f"{k}={v}" for k, v in sorted_params])

        headers = self._get_headers("GET", path, query_string, "")

        logger.info(f"[{request_id}] GET {path} | Params: {params or 'none'}")

        start_time = time.time()

        try:
            r = self.session.get(
                BASE + path,
                params=params or {},
                headers=headers,
                timeout=10
            )

            elapsed = (time.time() - start_time) * 1000
            self._last_request_time = time.time()

            logger.info(
                f"[{request_id}] Response: {r.status_code} | Time: {elapsed:.0f}ms | Size: {len(r.content)} bytes")

            if r.status_code != 200:
                self._request_count["errors"] += 1
                logger.error(f"[{request_id}] HTTP {r.status_code} | Response: {r.text[:500]}")

                # Additional diagnostics for 403 errors
                if r.status_code == 403:
                    logger.error(f"[{request_id}] 403 FORBIDDEN - Possible causes:")
                    logger.error(f"  1. IP not whitelisted in Tapbit API settings")
                    logger.error(f"  2. Invalid API key or secret")
                    logger.error(f"  3. Signature generation error")
                    logger.error(f"  4. API permissions not enabled for this endpoint")
                    logger.error(f"[{request_id}] Request URL: {r.url}")
                    logger.error(f"[{request_id}] Request headers: {dict(r.request.headers)}")

            r.raise_for_status()

            response_data = r.json()

            # Log API-level errors
            if response_data.get("code") != 0:
                logger.error(
                    f"[{request_id}] API Error | Code: {response_data.get('code')} | Message: {response_data.get('msg', 'N/A')}")
            else:
                logger.debug(f"[{request_id}] API Success | Response keys: {list(response_data.keys())}")

            return response_data

        except requests.exceptions.Timeout:
            self._request_count["errors"] += 1
            logger.error(f"[{request_id}] Request timeout after 10s")
            raise
        except requests.exceptions.ConnectionError as e:
            self._request_count["errors"] += 1
            logger.error(f"[{request_id}] Connection error: {str(e)}")
            raise
        except requests.exceptions.HTTPError as e:
            # Already logged above
            raise
        except Exception as e:
            self._request_count["errors"] += 1
            logger.error(f"[{request_id}] Unexpected error: {type(e).__name__} - {str(e)}")
            raise

    # --------------------------------------------------------
    # PUBLIC
    # --------------------------------------------------------
    def connect(self):
        """Initialize connection and log adapter status."""
        logger.info("=" * 60)
        logger.info("Tapbit Adapter Status")
        logger.info("=" * 60)
        logger.info(f"Base URL: {BASE}")
        logger.info(f"Symbol: {getattr(self, 'symbol', 'N/A')}")
        logger.info(f"Dry Run: {getattr(self, 'dry_run', False)}")
        logger.info(f"Credentials: {'✓ Configured' if (self.key and self.secret) else '✗ Missing'}")
        logger.info("=" * 60)

    def fetch_btc_last(self):
        """
        Fetch current BTC/USDT price.

        Returns:
            float: Last traded price, or 92000.0 on error
        """
        logger.debug("Fetching BTC/USDT last price")

        try:
            r = self._get("/api/v1/spot/market/ticker", {"symbol": "BTCUSDT"})

            if r.get("code") == 0 and "data" in r:
                price = float(r["data"]["last"])
                logger.info(f"BTC/USDT price fetched: ${price:,.2f}")
                return price
            else:
                logger.error(f"Unexpected ticker response: {r}")
                return 92000.0

        except Exception as e:
            logger.error(f"BTC price fetch failed: {type(e).__name__} - {str(e)}")
            return 92000.0

    def fetch_best_quotes(self):
        """
        Fetch best bid/ask prices for configured symbol.

        Returns:
            tuple: (bid_price, ask_price) or (None, None) on error
        """
        symbol = self.symbol.replace("/", "")
        logger.debug(f"Fetching quotes for {symbol}")

        try:
            r = self._get("/api/v1/spot/market/ticker", {"symbol": symbol})

            if r.get("code") == 0 and "data" in r:
                d = r["data"]
                bid = float(d["bid"])
                ask = float(d["ask"])
                spread = ask - bid
                spread_pct = (spread / bid) * 100 if bid > 0 else 0

                logger.info(
                    f"{symbol} quotes | Bid: {bid:.8f} | Ask: {ask:.8f} | Spread: {spread:.8f} ({spread_pct:.4f}%)")
                return bid, ask
            else:
                logger.error(f"Unexpected quotes response: {r}")
                return None, None

        except Exception as e:
            logger.error(f"Quotes fetch failed for {symbol}: {type(e).__name__} - {str(e)}")
            return None, None

    # --------------------------------------------------------
    # PRIVATE
    # --------------------------------------------------------
    def fetch_open_orders(self):
        """
        Fetch all open orders for configured symbol.

        Returns:
            list: Open orders data, or empty list on error
        """
        if self.dry_run:
            logger.debug("Dry run mode: skipping open orders fetch")
            return []

        symbol = self.symbol.replace("/", "")
        logger.debug(f"Fetching open orders for {symbol}")

        try:
            resp = self._post("/api/v1/spot/open_order_list", {"symbol": symbol})

            if resp.get("code") == 0:
                orders = resp.get("data", [])
                logger.info(f"Open orders fetched: {len(orders)} order(s) for {symbol}")

                if orders:
                    for order in orders:
                        logger.debug(f"  Order {order.get('orderId')} | {order.get('side')} | "
                                     f"Price: {order.get('orderPrice')} | Qty: {order.get('orderQty')} | "
                                     f"Status: {order.get('status')}")

                return orders
            else:
                logger.error(f"Open orders fetch failed: {resp}")
                return []

        except Exception as e:
            logger.error(f"Open orders exception: {type(e).__name__} - {str(e)}")
            return []

    def cancel_orders_by_ids(self, ids):
        """
        Cancel orders by order IDs.

        Args:
            ids: List of order IDs to cancel
        """
        if self.dry_run:
            logger.info(f"[DRY RUN] Would cancel {len(ids)} order(s): {ids}")
            return

        if not ids:
            logger.debug("No orders to cancel")
            return

        symbol = self.symbol.replace("/", "")
        logger.info(f"Cancelling {len(ids)} order(s) for {symbol}")

        success_count = 0
        fail_count = 0

        for oid in ids:
            try:
                resp = self._post("/api/v1/spot/cancel_order", {
                    "orderId": str(oid),
                    "symbol": symbol
                })

                if resp.get("code") == 0:
                    logger.info(f"✓ Order {oid} cancelled successfully")
                    success_count += 1
                else:
                    logger.warning(f"✗ Order {oid} cancel failed | Code: {resp.get('code')} | Msg: {resp.get('msg')}")
                    fail_count += 1

            except Exception as e:
                logger.warning(f"✗ Order {oid} cancel exception: {type(e).__name__} - {str(e)}")
                fail_count += 1

        logger.info(f"Cancel summary: {success_count} succeeded, {fail_count} failed")

    def create_limit(self, side, price, amount):
        """
        Create a limit order.

        Args:
            side: "buy" or "sell"
            price: Order price
            amount: Order quantity

        Returns:
            str: Order ID on success, None on failure, "dry" in dry run mode
        """
        if self.dry_run:
            logger.info(
                f"[DRY RUN] {side.upper()} order | Price: {price:.8f} | Qty: {amount} | Value: ${price * amount:.2f}")
            return "dry"

        symbol = self.symbol.replace("/", "")

        payload = {
            "symbol": symbol,
            "side": side.upper(),
            "orderPrice": f"{price:.10f}",
            "orderQty": str(amount),
            "orderType": "LIMIT",
            "timeInForce": "POST_ONLY"
        }

        logger.info(f"Creating LIMIT order | Symbol: {symbol} | Side: {side.upper()} | "
                    f"Price: {price:.8f} | Qty: {amount} | Value: ${price * amount:.2f}")

        try:
            resp = self._post("/api/v1/spot/order", payload)

            if resp.get("code") == 0 and "data" in resp:
                order_id = str(resp["data"]["orderId"])
                logger.info(f"✓ Order created successfully | Order ID: {order_id}")
                return order_id
            else:
                logger.error(f"✗ Order creation failed | Code: {resp.get('code')} | Msg: {resp.get('msg')}")
                logger.error(f"Order details: {payload}")
                return None

        except Exception as e:
            logger.error(f"✗ Order creation exception: {type(e).__name__} - {str(e)}")
            logger.error(f"Order details: {payload}")
            return None

    # --------------------------------------------------------
    # PRECISION / LIMITS
    # --------------------------------------------------------
    def price_to_precision(self, p):
        return round(p, 8)

    def amount_to_precision(self, a):
        return int(a)

    def get_limits(self):
        return {"min_amount": 1, "min_cost": 1}

    def get_steps(self):
        return (1e-8, 1)

    def get_stats(self):
        """
        Get adapter statistics for monitoring.

        Returns:
            dict: Request statistics
        """
        return {
            "requests": self._request_count,
            "last_request": self._last_request_time,
            "uptime": time.time() - (self._last_request_time or time.time())
        }