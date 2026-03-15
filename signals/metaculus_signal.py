"""
Metaculus signal: retrieves community forecast for the closest matching question.
Free, no API key required.  Acts as a "wisdom of crowds" anchor.
"""
from __future__ import annotations

from clients.metaculus import MetaculusClient
from signals.base import SignalResult
from utils.logging import get_logger
from utils.normalizer import MarketSchema

log = get_logger(__name__)

_metaculus = MetaculusClient()


def run(market: MarketSchema) -> SignalResult:
    prob = _metaculus.get_best_match_probability(market.title)

    if prob is None:
        return SignalResult(
            source="metaculus",
            probability=market.yes_price,
            confidence=0.05,
            metadata={"reason": "No matching Metaculus question found"},
        )

    # Metaculus community forecasts are generally well-calibrated.
    # We assign moderate-high confidence when a match is found.
    distance = abs(prob - market.yes_price)
    confidence = 0.55 + min(0.20, distance * 1.5)

    return SignalResult(
        source="metaculus",
        probability=prob,
        confidence=confidence,
        metadata={"metaculus_probability": prob},
    )
