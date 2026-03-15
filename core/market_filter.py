"""
Filters and scores markets by information-asymmetry potential.

Key principle: the bot's edge is NOT latency — it's having a better model of
reality than the crowd. That means targeting markets where:
  1. Volume is mid-tier (high enough to trade, low enough that fewer
     sophisticated participants are watching)
  2. The topic is niche or complex enough that crowd wisdom is incomplete
  3. There's enough time remaining for the edge to play out
  4. The market is not already near certainty (those are usually correct)
"""
from __future__ import annotations

from config import constants
from db import repository
from utils.logging import get_logger
from utils.normalizer import MarketSchema

log = get_logger(__name__)


def filter_markets(markets: list[MarketSchema]) -> list[MarketSchema]:
    candidates = []
    reject_reasons: dict[str, int] = {}

    for m in markets:
        reason = _reject_reason(m)
        if reason:
            reject_reasons[reason] = reject_reasons.get(reason, 0) + 1
        else:
            candidates.append(m)

    # Score by alpha potential and take top N
    candidates.sort(key=_alpha_score, reverse=True)
    candidates = candidates[: constants.MAX_MARKETS_PER_SCAN]

    log.info(
        f"Filter: {len(candidates)} selected from {len(markets)} total. "
        f"Rejections: {reject_reasons}"
    )
    if candidates:
        scores = [(m.title[:45], round(_alpha_score(m), 2)) for m in candidates[:5]]
        log.info(f"Top alpha candidates: {scores}")
    return candidates


def _reject_reason(m: MarketSchema) -> str | None:
    if m.status != "open":
        return "not_open"

    if m.yes_price < constants.PRICE_FLOOR or m.yes_price > constants.PRICE_CEIL:
        return "price_at_extreme"

    if m.liquidity_usd < constants.MIN_LIQUIDITY_USD:
        return "low_liquidity"

    if m.volume_usd < constants.MIN_VOLUME_USD:
        return "low_volume"

    # Skip hyper-liquid markets — too many sophisticated players
    if m.volume_usd > constants.MAX_VOLUME_USD_EFFICIENCY:
        return "too_efficient"

    hours = m.hours_to_close()
    if hours is not None:
        if hours < constants.MIN_HOURS_TO_CLOSE:
            return "closing_too_soon"
        if hours > constants.MAX_DAYS_TO_CLOSE * 24:
            return "too_far_out"

    if repository.has_open_trade_for_market(m.id):
        return "already_have_position"

    return None


def _alpha_score(m: MarketSchema) -> float:
    """
    Score a market by its information-asymmetry potential.
    Higher = more likely we can find genuine edge.

    Factors:
      - Volume tier: prefer $10k–$500k range (not too efficient, not too thin)
      - Price uncertainty: markets near 0.5 have most uncertainty → most alpha potential
      - Time horizon: 7–21 days is the sweet spot
      - Niche indicator: lower volume within the passing range = less covered
    """
    score = 0.0

    # Volume tier score: penalise both extremes
    vol = m.volume_usd
    if vol < 10_000:
        score += 0.5     # thin — some alpha but hard to fill
    elif vol < 100_000:
        score += 2.0     # sweet spot
    elif vol < 400_000:
        score += 1.5     # reasonable
    else:
        score += 0.8     # getting efficient

    # Price distance from 0.5: markets near 0.5 have maximum uncertainty
    # but we also want some directional signal so we don't penalise too hard
    distance_from_half = abs(m.yes_price - 0.5)
    # Markets at 0.5 score 1.0; markets at 0.9/0.1 score ~0
    price_score = 1.0 - (distance_from_half / 0.5) * 0.6
    score += price_score

    # Time horizon: 7–21 days is ideal for our signal sources
    hours = m.hours_to_close()
    if hours is not None:
        days = hours / 24
        if 7 <= days <= 21:
            score += 1.5
        elif 3 <= days < 7:
            score += 1.0
        elif 21 < days <= 45:
            score += 0.8

    # Topic bonus: markets where resolution criteria can be AMBIGUOUS score higher.
    # These are the markets where our careful reading can find genuine edge.
    # Price-oracle markets (sports scores, asset prices above/below X) resolve
    # unambiguously and offer no criteria alpha regardless of topic.
    title_lower = m.title.lower()

    # Clear-resolution patterns: price oracles, game scores, point totals
    # These resolve on objective thresholds with no interpretation required.
    oracle_patterns = [
        "above $", "below $", "dip to $", "reach $", "price of",
        "o/u ", "over/under", "up or down", "vs.", " vs ",
        "spread", "moneyline",
    ]
    # Complex-resolution patterns: events requiring interpretation
    complex_keywords = {
        "fed", "rate hike", "rate cut", "recession", "inflation", "gdp",
        "election", "win the", "nominee", "bill", "congress", "senate",
        "president", "prime minister", "treaty", "sanction", "ceasefire",
        "invasion", "nuclear", "tariff", "trade deal", "etf approved",
        "merger", "acquisition", "bankrupt", "indicted", "arrest",
        "verdict", "supreme court", "impeach", "resign",
    }

    is_oracle = any(p in title_lower for p in oracle_patterns)
    is_complex = any(k in title_lower for k in complex_keywords)

    if is_complex and not is_oracle:
        score += 1.5   # strong bonus — resolution criteria alpha likely
    elif is_oracle:
        score -= 0.8   # penalty — resolves mechanically, no criteria edge

    return score
