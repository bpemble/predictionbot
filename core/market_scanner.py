"""
Discovers open markets from all enabled platforms.
"""
from __future__ import annotations

from clients.kalshi import KalshiClient
from clients.polymarket import PolymarketClient
from config.settings import get_settings
from db import repository
from utils.logging import get_logger
from utils.normalizer import MarketSchema

log = get_logger(__name__)

_poly = PolymarketClient()
_kalshi = KalshiClient()


def scan_all_markets() -> list[MarketSchema]:
    """
    Fetch markets from all enabled platforms, upsert to DB, return full list.
    """
    settings = get_settings()
    markets: list[MarketSchema] = []

    if settings.polymarket_enabled():
        try:
            poly_markets = _poly.get_all_markets(max_pages=3)
            log.info(f"Polymarket: fetched {len(poly_markets)} markets")
            markets.extend(poly_markets)
        except Exception as exc:
            log.error(f"Polymarket scan failed: {exc}")

    if settings.kalshi_enabled():
        try:
            kalshi_markets = _kalshi.get_all_markets(max_pages=5)
            log.info(f"Kalshi: fetched {len(kalshi_markets)} markets")
            markets.extend(kalshi_markets)
        except Exception as exc:
            log.error(f"Kalshi scan failed: {exc}")

    # Persist to DB
    for m in markets:
        try:
            repository.upsert_market(m.to_db_dict())
        except Exception as exc:
            log.debug(f"Failed to upsert market {m.id}: {exc}")

    log.info(f"Total markets scanned: {len(markets)}")
    return markets
