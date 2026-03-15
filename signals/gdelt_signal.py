"""
GDELT signal: news volume and tone → directional probability shift.
Free, no API key required.  Weak signal — low weight by default.
"""
from __future__ import annotations

from clients.gdelt import GDELTClient
from signals.base import SignalResult
from utils.logging import get_logger
from utils.normalizer import MarketSchema

log = get_logger(__name__)

_gdelt = GDELTClient()


def run(market: MarketSchema) -> SignalResult:
    # Extract 3-4 key terms for the query
    words = [w.strip("?.,!") for w in market.title.split() if len(w) > 3][:5]
    query = " ".join(words)

    result = _gdelt.query(query, days_back=3)

    if not result or result.get("article_count", 0) == 0:
        return SignalResult(
            source="gdelt",
            probability=market.yes_price,
            confidence=0.05,
            metadata={"reason": "No GDELT results", "query": query},
        )

    probability = _gdelt.tone_to_probability_shift(result, market.yes_price)
    article_count = result.get("article_count", 0)
    avg_tone = result.get("avg_tone", 0.0)

    # GDELT is a noisy signal — cap confidence at 0.35
    tone_magnitude = abs(avg_tone) / 10.0
    volume_factor = min(1.0, article_count / 30)
    confidence = min(0.35, (tone_magnitude * 0.5 + volume_factor * 0.3) * 0.6)

    return SignalResult(
        source="gdelt",
        probability=probability,
        confidence=confidence,
        metadata={
            "article_count": article_count,
            "avg_tone": round(avg_tone, 3),
            "query": query,
        },
    )
