"""
Perplexity research signal: deep research with cited sources.
Higher cost per call — used for high-edge opportunities.
"""
from __future__ import annotations

from clients.perplexity import PerplexityClient
from signals.base import SignalResult
from utils.logging import get_logger
from utils.normalizer import MarketSchema

log = get_logger(__name__)

_perplexity = PerplexityClient()


def run(market: MarketSchema) -> SignalResult:
    if not _perplexity.available():
        return SignalResult(
            source="research",
            probability=market.yes_price,
            confidence=0.05,
            metadata={"reason": "Perplexity API key not configured"},
        )

    result = _perplexity.research(market.title, market.yes_price)
    prob = result.get("probability_hint")

    if prob is None:
        return SignalResult(
            source="research",
            probability=market.yes_price,
            confidence=0.10,
            metadata={"reason": "Could not parse probability from Perplexity response"},
        )

    # Confidence is moderate-high when Perplexity returns a probability
    # but lower when the probability is very close to the market price
    # (suggests Perplexity didn't find strong evidence)
    distance = abs(prob - market.yes_price)
    confidence = 0.35 + min(0.40, distance * 2.0)

    return SignalResult(
        source="research",
        probability=prob,
        confidence=confidence,
        metadata={
            "summary": result.get("summary", "")[:500],
            "citations": result.get("citations", []),
        },
    )
