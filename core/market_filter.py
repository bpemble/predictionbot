"""
Filters scanned markets down to those worth running signals on.
"""
from __future__ import annotations

from config import constants
from db import repository
from utils.logging import get_logger
from utils.normalizer import MarketSchema

log = get_logger(__name__)


def filter_markets(markets: list[MarketSchema]) -> list[MarketSchema]:
    """
    Apply all filters and return tradeable candidates.
    """
    candidates = []
    stats = {"total": len(markets), "pass": 0}
    reject_reasons: dict[str, int] = {}

    for m in markets:
        reason = _reject_reason(m)
        if reason:
            reject_reasons[reason] = reject_reasons.get(reason, 0) + 1
        else:
            candidates.append(m)
            stats["pass"] += 1

    log.info(
        f"Filter: {stats['pass']}/{stats['total']} markets pass. "
        f"Rejections: {reject_reasons}"
    )
    return candidates


def _reject_reason(m: MarketSchema) -> str | None:
    if m.status != "open":
        return "not_open"

    if m.yes_price < constants.PRICE_FLOOR or m.yes_price > constants.PRICE_CEIL:
        return "price_out_of_range"

    if m.liquidity_usd < constants.MIN_LIQUIDITY_USD:
        return "low_liquidity"

    if m.volume_usd < constants.MIN_VOLUME_USD:
        return "low_volume"

    hours = m.hours_to_close()
    if hours is not None:
        if hours < constants.MIN_HOURS_TO_CLOSE:
            return "closing_too_soon"
        if hours > constants.MAX_DAYS_TO_CLOSE * 24:
            return "too_far_out"

    # Skip if we already have an open trade in this market
    if repository.has_open_trade_for_market(m.id):
        return "already_have_position"

    return None
