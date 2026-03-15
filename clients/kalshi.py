"""
Kalshi v2 REST client with RSA-PSS request signing.

Auth: Upload your RSA public key at kalshi.com → API → Keys.
      Store the private key PEM file at KALSHI_PRIVATE_KEY_PATH.
      Set KALSHI_API_KEY_ID to the UUID shown in the dashboard.

Notes:
  - Prices in API responses are in cents (0–99); we normalise to 0–1.
  - Order counts are in contracts; 1 contract pays $1 if it resolves.
  - Demo base URL is used automatically when PAPER_TRADE=true.
"""
from __future__ import annotations

import base64
import os
import time
import uuid
from typing import Optional

import requests

from config.settings import get_settings
from utils.logging import get_logger
from utils.normalizer import MarketSchema, normalize_kalshi
from utils.retry import with_retry

log = get_logger(__name__)


class KalshiClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._private_key = None
        self._session = requests.Session()

    def _load_private_key(self):
        if self._private_key is not None:
            return self._private_key
        path = self.settings.kalshi_private_key_path
        if not os.path.exists(path):
            raise FileNotFoundError(f"Kalshi private key not found at {path}")
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        with open(path, "rb") as f:
            self._private_key = load_pem_private_key(f.read(), password=None)
        return self._private_key

    def _sign_headers(self, method: str, path: str, body: str = "") -> dict:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        ts_ms = str(int(time.time() * 1000))
        message = ts_ms + method.upper() + path + body
        key = self._load_private_key()
        signature = key.sign(
            message.encode(),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.settings.kalshi_api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts_ms,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        return self.settings.kalshi_base_url + path

    @with_retry()
    def get_markets(self, limit: int = 200, cursor: str = "") -> tuple[list[MarketSchema], str]:
        """Returns (markets, next_cursor). Empty cursor means no more pages."""
        params = {"limit": limit, "status": "open"}
        if cursor:
            params["cursor"] = cursor
        path = "/trade-api/v2/markets"
        headers = self._sign_headers("GET", path)
        resp = self._session.get(self._url(path), params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        markets = []
        for raw in data.get("markets", []):
            m = normalize_kalshi(raw)
            if m:
                markets.append(m)
        next_cursor = data.get("cursor", "")
        log.debug(f"Fetched {len(markets)} Kalshi markets")
        return markets, next_cursor

    def get_all_markets(self, max_pages: int = 5) -> list[MarketSchema]:
        all_markets: list[MarketSchema] = []
        cursor = ""
        for _ in range(max_pages):
            batch, cursor = self.get_markets(limit=200, cursor=cursor)
            all_markets.extend(batch)
            if not cursor or len(batch) < 200:
                break
        return all_markets

    @with_retry()
    def get_market(self, ticker: str) -> Optional[MarketSchema]:
        path = f"/trade-api/v2/markets/{ticker}"
        headers = self._sign_headers("GET", path)
        resp = self._session.get(self._url(path), headers=headers, timeout=30)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return normalize_kalshi(resp.json().get("market", {}))

    @with_retry()
    def place_market_order(
        self,
        ticker: str,
        side: str,
        cost_usd: float,
        yes_price: float,
    ) -> Optional[str]:
        """
        Place a market order.
        side: 'yes' or 'no'
        cost_usd: USD to spend
        yes_price: current yes price (0-1) — used to calculate contract count
        Returns platform order ID or None.
        """
        if self.settings.paper_trade:
            log.info(f"[PAPER] Kalshi order: {ticker} {side} ${cost_usd:.2f} @ {yes_price:.2f}")
            return f"paper-{ticker}"

        price_for_side = yes_price if side == "yes" else (1.0 - yes_price)
        count = max(1, int(cost_usd / price_for_side))

        import json
        body_dict = {
            "ticker": ticker,
            "action": "buy",
            "side": side,
            "type": "market",
            "count": count,
            "client_order_id": str(uuid.uuid4()),
        }
        body_str = json.dumps(body_dict)
        path = "/trade-api/v2/portfolio/orders"
        headers = self._sign_headers("POST", path, body_str)
        resp = self._session.post(self._url(path), data=body_str, headers=headers, timeout=30)
        resp.raise_for_status()
        order = resp.json().get("order", {})
        order_id = order.get("order_id")
        log.info(f"Kalshi order placed: {order_id}")
        return order_id

    @with_retry()
    def get_balance_usd(self) -> float:
        if self.settings.paper_trade:
            return self.settings.bankroll_kalshi_usd
        path = "/trade-api/v2/portfolio/balance"
        headers = self._sign_headers("GET", path)
        resp = self._session.get(self._url(path), headers=headers, timeout=30)
        resp.raise_for_status()
        return float(resp.json().get("balance", 0)) / 100  # cents → dollars
