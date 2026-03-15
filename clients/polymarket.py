"""
Polymarket CLOB client wrapper.

Auth flow:
  1. On first run, call derive_api_creds() — this signs a message with your
     EOA private key and returns API key/secret/passphrase.  Save to .env.
  2. On subsequent runs, instantiate with stored API creds.

Market discovery uses the Gamma REST API (no auth required).
Order placement uses the authenticated CLOB API.
"""
from __future__ import annotations

from typing import Optional

import requests

from config.settings import get_settings
from utils.logging import get_logger
from utils.normalizer import MarketSchema, normalize_polymarket
from utils.retry import with_retry

log = get_logger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"


class PolymarketClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._clob_client = None

    def _get_clob(self):
        """Lazily initialise the authenticated CLOB client."""
        if self._clob_client is not None:
            return self._clob_client
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds

            s = self.settings
            if not s.poly_api_key:
                raise ValueError(
                    "POLY_API_KEY not set. Run derive_api_creds() first."
                )
            creds = ApiCreds(
                api_key=s.poly_api_key,
                api_secret=s.poly_api_secret,
                api_passphrase=s.poly_api_passphrase,
            )
            self._clob_client = ClobClient(
                host=s.poly_host,
                key=s.poly_private_key,
                chain_id=s.poly_chain_id,
                creds=creds,
                signature_type=2,
            )
            return self._clob_client
        except ImportError:
            raise ImportError("py-clob-client not installed. Run: pip install py-clob-client")

    def derive_api_creds(self) -> dict:
        """
        One-time setup: derive API key/secret/passphrase from your private key.
        Prints the values — save them to .env.
        """
        from py_clob_client.client import ClobClient
        s = self.settings
        tmp = ClobClient(host=s.poly_host, key=s.poly_private_key, chain_id=s.poly_chain_id)
        creds = tmp.create_or_derive_api_creds()
        log.info("=== Polymarket API Credentials (save to .env) ===")
        log.info(f"POLY_API_KEY={creds.api_key}")
        log.info(f"POLY_API_SECRET={creds.api_secret}")
        log.info(f"POLY_API_PASSPHRASE={creds.api_passphrase}")
        return {"api_key": creds.api_key, "api_secret": creds.api_secret,
                "api_passphrase": creds.api_passphrase}

    @with_retry()
    def get_markets(self, limit: int = 200, offset: int = 0) -> list[MarketSchema]:
        """Fetch active markets from the Gamma API (no auth required)."""
        resp = requests.get(
            f"{GAMMA_BASE}/markets",
            params={
                "active": "true",
                "closed": "false",
                "limit": limit,
                "offset": offset,
                "order": "volume24hr",
                "ascending": "false",
            },
            timeout=30,
        )
        resp.raise_for_status()
        raw_list = resp.json()
        markets = []
        for raw in raw_list:
            m = normalize_polymarket(raw)
            if m:
                markets.append(m)
        log.debug(f"Fetched {len(markets)} Polymarket markets (offset={offset})")
        return markets

    def get_all_markets(self, max_pages: int = 5) -> list[MarketSchema]:
        """Paginate through Gamma API to get up to max_pages * 200 markets."""
        all_markets: list[MarketSchema] = []
        for page in range(max_pages):
            batch = self.get_markets(limit=200, offset=page * 200)
            all_markets.extend(batch)
            if len(batch) < 200:
                break
        return all_markets

    @with_retry()
    def get_market(self, condition_id: str) -> Optional[MarketSchema]:
        resp = requests.get(f"{GAMMA_BASE}/markets/{condition_id}", timeout=30)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return normalize_polymarket(resp.json())

    @with_retry()
    def place_market_order(
        self, token_id: str, side: str, amount_usd: float
    ) -> Optional[str]:
        """
        Place a market order.
        side: 'YES' or 'NO' — pass the corresponding token_id.
        amount_usd: USDC to spend.
        Returns platform order ID or None on failure.
        """
        if self.settings.paper_trade:
            log.info(f"[PAPER] Polymarket order: token={token_id} side={side} amount=${amount_usd:.2f}")
            return f"paper-{token_id[:8]}"
        try:
            from py_clob_client.clob_types import MarketOrderArgs
            clob = self._get_clob()
            args = MarketOrderArgs(token_id=token_id, amount=amount_usd)
            resp = clob.create_market_order(args)
            order_id = resp.get("orderID") or resp.get("order_id")
            log.info(f"Polymarket order placed: {order_id}")
            return order_id
        except Exception as exc:
            log.error(f"Polymarket order failed: {exc}")
            return None

    @with_retry()
    def get_balance_usdc(self) -> float:
        """Return available USDC balance on Polygon."""
        if self.settings.paper_trade:
            return self.settings.bankroll_poly_usd
        try:
            clob = self._get_clob()
            balance = clob.get_balance()
            return float(balance)
        except Exception as exc:
            log.warning(f"Could not fetch Polymarket balance: {exc}")
            return 0.0
